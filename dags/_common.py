"""Shared read -> rename -> filter -> load helpers for the Intake / Fault DAGs.

Pipeline shape, per source:

    load_source_config(source_id) -> discover_file(cfg) -> read_frame(path, cfg)
        -> apply_schema(df, cfg) -> apply_filters(df, cfg) -> cast_bools(df, cfg)
        -> load(df, cfg, conn)

Plus two standalone helpers (parse_excel_serial_date / parse_jst_timestamp) that a
DAG calls explicitly on the one or two columns that need them -- there are only
four such columns in the whole project, so a config-driven "date column" concept
would be more machinery than the problem needs.
"""

from __future__ import annotations

import glob
import io
import logging
import os
from pathlib import Path
from typing import Any

import polars as pl
import psycopg2
import psycopg2.extras
import yaml

logger = logging.getLogger(__name__)

SOURCES_YAML = Path(os.environ.get("SOURCES_YAML", "/opt/airflow/ingestion/sources.yaml"))

# The '-' sentinel shows up in almost every column of every file (Locked status,
# Close Time, Remarks, ...). Blank string counts too, once whitespace is stripped.
NULL_SENTINELS = {"-", ""}

# {"YES", "TRUE", "1"} -> true, anything else (including null/blank) -> false.
TRUE_VALUES = {"YES", "TRUE", "1"}


def _load_all_sources() -> dict[str, dict[str, Any]]:
    with open(SOURCES_YAML, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    return {s["source_id"]: s for s in doc["sources"]}


def load_source_config(source_id: str) -> dict[str, Any]:
    sources = _load_all_sources()
    if source_id not in sources:
        raise KeyError(f"Unknown source_id {source_id!r} -- not in {SOURCES_YAML}")
    return sources[source_id]


def discover_file(cfg: dict[str, Any]) -> Path:
    """glob(source_uri + file_pattern), newest by mtime."""
    pattern = os.path.join(cfg["source_uri"], cfg["file_pattern"])
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(
            f"No file matching {cfg['file_pattern']!r} in {cfg['source_uri']!r} "
            f"(source_id={cfg['source_id']!r})"
        )
    newest = max(matches, key=lambda p: os.path.getmtime(p))
    return Path(newest)


def _resolve_sheet(path: Path, hints: list[str]) -> str:
    import fastexcel

    sheet_names = fastexcel.read_excel(str(path)).sheet_names
    lower_names = {name.lower(): name for name in sheet_names}
    for hint in hints:
        if hint.lower() in lower_names:
            return lower_names[hint.lower()]
    return sheet_names[0]


def _stringify_excel(df: pl.DataFrame) -> pl.DataFrame:
    """Cast every numeric column to clean text.

    calamine hands back real Excel numeric cells as floats -- ECGI as
    4401184897793.0, CellID as 1.0, enodeBid as 331605.0. A plain str() cast keeps
    the trailing ".0" (or worse, drifts into scientific notation for the big ECGI
    values), which then fails every downstream text join (enbid=331605 parsed from
    the alarm feed against a site_db.enodebid of "331605.0"). Whole-number floats
    become clean int strings; genuine decimals (Azimuth, Latitude, tilts) keep their
    fractional part.
    """
    exprs = []
    for name, dtype in zip(df.columns, df.dtypes):
        col = pl.col(name)
        if dtype in (pl.Float32, pl.Float64):
            exprs.append(
                pl.when(col.is_null())
                .then(None)
                .when(col == col.floor())
                .then(col.cast(pl.Int64).cast(pl.Utf8))
                .otherwise(col.cast(pl.Utf8))
                .alias(name)
            )
        elif dtype != pl.Utf8:
            exprs.append(col.cast(pl.Utf8).alias(name))
        else:
            exprs.append(col)
    return df.with_columns(exprs)


def read_frame(path: Path, cfg: dict[str, Any]) -> pl.DataFrame:
    fmt = cfg["format"]
    if fmt == "csv":
        # utf-8-sig strips the BOM; embedded newlines inside quoted fields (every
        # timestamp is "2026-08-05\n21:18:07 JST") need the file decoded whole and
        # fed through polars' RFC4180-aware quote handling rather than read line by
        # line. infer_schema_length=0 forces every column to string on read.
        text = path.read_text(encoding="utf-8-sig")
        return pl.read_csv(io.StringIO(text), infer_schema_length=0)
    if fmt in ("xlsx", "xlsb"):
        sheet = _resolve_sheet(path, cfg["schema"].get("sheet_hints", []))
        df = pl.read_excel(path, engine="calamine", sheet_name=sheet)
        return _stringify_excel(df)
    raise ValueError(f"Unsupported format {fmt!r} for source_id={cfg['source_id']!r}")


def _canonical_column(cfg: dict[str, Any], raw_name: str) -> str:
    """Resolve a raw header name (as written in sources.yaml filters/bool_columns)
    to the canonical column apply_schema renamed it to."""
    expected = cfg["schema"]["expected_columns"]
    raw_lower = raw_name.strip().lower()
    for canonical, synonyms in expected.items():
        if raw_lower == canonical.lower() or raw_lower in {s.strip().lower() for s in synonyms}:
            return canonical
    return raw_name


def apply_schema(df: pl.DataFrame, cfg: dict[str, Any]) -> pl.DataFrame:
    """Rename matched headers to their canonical target name, strip header and
    value whitespace, and fold the '-' null sentinel to a real null.

    Real headers arrive padded (" Ticket ID", " Status", " Vendor"); real values do
    too ('MAJOR   ', 'KANTO    '). No fuzzy matching -- case-insensitive exact match
    on the stripped header only -- but every expected column that finds no match is
    logged, because that's how a changed file shape gets noticed.
    """
    header_lookup = {c.strip().lower(): c for c in df.columns}
    rename_map: dict[str, str] = {}
    for canonical, synonyms in cfg["schema"]["expected_columns"].items():
        matched = None
        for candidate in [canonical, *synonyms]:
            key = candidate.strip().lower()
            if key in header_lookup:
                matched = header_lookup[key]
                break
        if matched is None:
            logger.warning(
                "apply_schema[%s]: no column in file matched target %r (tried %s)",
                cfg["source_id"], canonical, [canonical, *synonyms],
            )
            continue
        rename_map[matched] = canonical

    df = df.select(list(rename_map.keys())).rename(rename_map)

    str_cols = [name for name, dtype in zip(df.columns, df.dtypes) if dtype == pl.Utf8]
    df = df.with_columns(
        [
            pl.when(pl.col(c).str.strip_chars().is_in(list(NULL_SENTINELS)))
            .then(None)
            .otherwise(pl.col(c).str.strip_chars())
            .alias(c)
            for c in str_cols
        ]
    )
    return df


def apply_filters(df: pl.DataFrame, cfg: dict[str, Any]) -> pl.DataFrame:
    """Support the three ops the manifest uses: max, eq, ne.

    Filter `column` values in sources.yaml are the raw header names (e.g.
    "Date of Intg"), resolved back to the canonical name apply_schema already
    renamed the frame to.
    """
    for f in cfg.get("filters", []):
        col = _canonical_column(cfg, f["column"])
        op = f["op"]
        if op == "max":
            # Same numeric-serial-vs-already-a-date ambiguity as
            # parse_excel_serial_date: try a numeric max first (a raw Excel
            # serial, or any genuinely numeric filter column); if nothing parsed
            # as a number, fall back to a plain value max -- correct as-is for an
            # ISO "YYYY-MM-DD" date string, which sorts lexicographically the same
            # as chronologically.
            numeric = pl.col(col).cast(pl.Float64, strict=False)
            threshold = df.select(numeric.max()).item()
            if threshold is not None:
                df = df.filter(numeric == threshold)
            else:
                text_threshold = df.select(pl.col(col).max()).item()
                df = df.filter(pl.col(col) == text_threshold)
        elif op == "eq":
            df = df.filter(pl.col(col) == f["value"])
        elif op == "ne":
            df = df.filter((pl.col(col) != f["value"]) | pl.col(col).is_null())
        else:
            raise ValueError(f"Unsupported filter op {op!r} on column {col!r}")
    return df


def cast_bools(df: pl.DataFrame, cfg: dict[str, Any]) -> pl.DataFrame:
    bool_cols = cfg.get("bool_columns", [])
    if not bool_cols:
        return df
    exprs = [
        pl.col(c).str.to_uppercase().is_in(list(TRUE_VALUES)).fill_null(False).alias(c)
        for c in bool_cols
        if c in df.columns
    ]
    return df.with_columns(exprs)


def parse_excel_serial_date(column: str) -> pl.Expr:
    """Excel serial -> date, e.g. 46232 -> 2026-08-05. Base is 1899-12-30 (not
    1900-01-01) to absorb Excel's fictitious 1900-02-29.

    calamine resolves a date-*formatted* cell straight to a real date rather than
    handing back the underlying serial number -- by the time _stringify_excel has
    run, that shows up here as an already-ISO "YYYY-MM-DD" string, not "46232".
    Whether that happens depends on the cell's number format in the source file,
    not the column, so handle both: try ISO first, fall back to the serial-number
    formula for a genuinely raw numeric cell.
    """
    col = pl.col(column)
    from_iso = col.str.strptime(pl.Date, "%Y-%m-%d", strict=False)
    serial = col.cast(pl.Float64, strict=False)
    from_serial = (pl.date(1899, 12, 30) + pl.duration(days=serial)).cast(pl.Date)
    return pl.coalesce([from_iso, from_serial]).alias(column)


def parse_jst_timestamp(column: str) -> pl.Expr:
    """"2026-08-05\\n21:18:07 JST" -> timestamptz. Newline -> space, drop the JST
    suffix, parse naive, then attach the Asia/Tokyo offset (Japan has no DST, so
    this is exactly +09 year-round)."""
    cleaned = (
        pl.col(column)
        .str.replace_all("\n", " ")
        .str.replace_all(" JST", "")
        .str.strip_chars()
    )
    return (
        cleaned.str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S", strict=False)
        .dt.replace_time_zone("Asia/Tokyo")
        .alias(column)
    )


def get_conn():
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "postgres"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "airflow"),
        user=os.environ.get("PGUSER", "airflow"),
        password=os.environ.get("PGPASSWORD", "airflow"),
    )


def load(df: pl.DataFrame, cfg: dict[str, Any], conn) -> int:
    """execute_values upsert (pk_columns set) or truncate-reload (else). Returns
    the row count actually persisted."""
    table = cfg["target_table"]
    pk_columns = cfg.get("pk_columns") or []
    columns = df.columns

    if pk_columns:
        # The NOT NULL guard: a row missing any pk column can't be upserted (and
        # shouldn't silently become a NULL-keyed row). This is exactly how the 15
        # #N/A-alarm-code rows in the alarm library get dropped before the insert.
        guard = pl.all_horizontal([pl.col(c).is_not_null() for c in pk_columns])
        before = df.height
        df = df.filter(guard)
        dropped = before - df.height
        if dropped:
            logger.warning(
                "load[%s]: dropped %d/%d rows with a null pk column %s",
                cfg["source_id"], dropped, before, pk_columns,
            )

        # Postgres refuses a single multi-row `ON CONFLICT DO UPDATE` that hits the
        # same conflict target twice in one statement ("cannot affect row a second
        # time") -- a real hazard here, since the alarm library repeats 8 alarm
        # codes. Collapse in-batch duplicates ourselves first, last row wins (the
        # same result a sequence of single-row upserts in file order would give).
        before = df.height
        df = df.unique(subset=pk_columns, keep="last", maintain_order=True)
        collapsed = before - df.height
        if collapsed:
            logger.info(
                "load[%s]: collapsed %d in-batch duplicate(s) on pk %s (last wins)",
                cfg["source_id"], collapsed, pk_columns,
            )

    rows = [tuple(row) for row in df.rows()]
    if not rows:
        logger.warning("load[%s]: nothing to persist into %s", cfg["source_id"], table)
        if not pk_columns:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {table}")
            conn.commit()
        return 0

    col_list = ", ".join(columns)

    with conn.cursor() as cur:
        if pk_columns:
            update_cols = [c for c in columns if c not in pk_columns]
            set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
            set_clause = (set_clause + ", " if set_clause else "") + "modified_at = now()"
            sql = (
                f"INSERT INTO {table} ({col_list}) VALUES %s "
                f"ON CONFLICT ({', '.join(pk_columns)}) DO UPDATE SET {set_clause}"
            )
        else:
            cur.execute(f"DELETE FROM {table}")
            sql = f"INSERT INTO {table} ({col_list}) VALUES %s"

        psycopg2.extras.execute_values(cur, sql, rows)
    conn.commit()

    logger.info("load[%s]: persisted %d rows into %s", cfg["source_id"], len(rows), table)
    return len(rows)


def run_extract_load(source_id: str, *, date_columns: list[str] | None = None,
                      timestamp_columns: list[str] | None = None) -> int:
    """Convenience wrapper for the single-source DAGs: the full
    discover -> read -> schema -> filter -> bool -> load chain in one call."""
    cfg = load_source_config(source_id)
    path = discover_file(cfg)
    logger.info("run_extract_load[%s]: reading %s", source_id, path)

    df = read_frame(path, cfg)
    df = apply_schema(df, cfg)
    # Filters (e.g. "Date of Intg" == max) run on the raw text/numeric-string form,
    # same as every other filter column -- date/timestamp parsing happens last, so
    # it never has to be undone for a filter to compare against it.
    df = apply_filters(df, cfg)
    df = cast_bools(df, cfg)

    for col in date_columns or []:
        if col in df.columns:
            df = df.with_columns(parse_excel_serial_date(col))
    for col in timestamp_columns or []:
        if col in df.columns:
            df = df.with_columns(parse_jst_timestamp(col))

    conn = get_conn()
    try:
        return load(df, cfg, conn)
    finally:
        conn.close()
