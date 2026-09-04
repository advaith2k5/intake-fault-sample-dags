# Sample Airflow DAGs — Fault & Intake agents

## Context

This is a standalone learning project, separate from the NineOne Platform repo. The goal
is to understand two RAN data-processing agents — **Intake** and **Fault** — by rebuilding
their ingestion DAGs against the real client files, which have already been copied into
this folder.

The real platform lands every source file in MinIO (S3) first, then DAGs download it back
out. Those same five files also sit on local disk in that repo at
`data_layer/seed/data_rktn/{FAULT,Intake} Agent/` — and `Fault data/` + `Intake data/` here
are a byte-for-byte copy of them. So MinIO would be a pure upload-then-download round trip
here. It is deliberately dropped: files are read straight off disk. Nothing else about the
pipeline changes, because all the interesting logic is in parsing, filtering and joining.

**Domain, in one paragraph.** A newly built 4G site goes On-Air and must be certified before
carrying commercial traffic (stages: Intake · Fault · Config · KPI-RCA · Change · Drive-test ·
Certify). **Intake** establishes which sites are newly on-air and what cells they have.
**Fault** then asks, for those sites only, whether any active alarm is a blocker. Fault
depends on Intake through exactly one clause in its gold SQL:
`AND site_id IN (SELECT site_id FROM ran.intake_site)`.

Decisions taken: config-driven but self-contained (a trimmed local `sources.yaml`, no
`bronze.*` control-plane tables, no adapter abstraction); Postgres in docker-compose;
four DAGs mirroring the real DAG ids.

---

## Target layout

```
Sample Project/
  docker-compose.yml          # airflow (webserver+scheduler) + postgres
  requirements.txt            # polars, fastexcel, psycopg2-binary, pyyaml
  README.md                   # bootstrap + run order
  sql/
    01_schema.sql             # the 7 ran.* tables
  dags/
    _common.py                # shared read → rename → filter → load helpers
    ingest_intake_4g.py
    ingest_fault_alarm_equipment_type.py
    ingest_fault_alarm_library.py
    ingest_fault_alarm_monitoring.py
    sql/
      intake_gold.sql
      alarm_gold.sql
  ingestion/sources.yaml      # EXISTING — trim in place
  Fault data/ · Intake data/  # EXISTING — mounted read-only
  docs/ · DB_Models.xlsx      # EXISTING — reference only, never read by code
```

Mounts: `./Fault data` → `/opt/airflow/data/fault`, `./Intake data` → `/opt/airflow/data/intake`
(both `:ro`), `./dags` → `/opt/airflow/dags`, `./ingestion` → `/opt/airflow/ingestion`.

Base image `apache/airflow:2.10.5-python3.11` (matches the platform). Postgres 16 with
`sql/01_schema.sql` mounted into `/docker-entrypoint-initdb.d/`.

---

## Step 1 — Trim `ingestion/sources.yaml`

Keep only these five entries; delete every other source and the whole `dag_edges:` block
(the trigger is explicit in the DAG here). Per entry: drop `protocol: s3`, `connection_id`,
`size_mb`, `on_missing_file`; set `protocol: local` and rewrite `source_uri` to the mounted
directory. **`file_pattern` values are already correct — all five match the real filenames
as-is.** Keep `format`, `file_pattern`, `target_table`, `pk_columns`, `filters`,
`bool_columns`, `schema.expected_columns`, `schema.sheet_hints` verbatim.

| source_id | source_uri | target_table | load strategy |
|---|---|---|---|
| `intake_4g_on_air` | `/opt/airflow/data/intake/` | `ran.raw_intake_site` | upsert on `site_id` |
| `intake_ecgi_site_db` | `/opt/airflow/data/intake/` | `ran.site_db` | truncate-reload |
| `fault_alarm_monitoring` | `/opt/airflow/data/fault/` | `ran.alarm_monitoring_raw` | truncate-reload |
| `fault_alarm_node_level` | `/opt/airflow/data/fault/` | `ran.alarm_node_level` | upsert on `(equipment_type, tech)` |
| `fault_alarm_library` | `/opt/airflow/data/fault/` | `ran.alarm_vs_kpi` | upsert on `alarm_code` |

`alarm_vs_kpi` is an **upsert, not truncate-reload** — deliberately. A reissued catalogue
that omits an alarm must not silently delete flags the gold SQL still joins on.

This file is the single most valuable reference copied over: the intake filter
`Locked status != "Yes"` exists **only here** — it is in no `.md`, no `.html` and no diagram.

---

## Step 2 — `sql/01_schema.sql`

Seven tables in schema `ran`. Column lists come from `DB_Models.xlsx`; where it is ambiguous
or wrong, `sources.yaml` and the platform's `data_layer/postgres/init/ran/09_ns_qaw.sql` win.

| Table | Layer | Key |
|---|---|---|
| `ran.raw_intake_site` | bronze | PK `site_id` |
| `ran.site_db` | bronze | identity `id`, no natural PK |
| `ran.intake_site` | **gold** | PK `(site_id, cell_id)` |
| `ran.alarm_monitoring_raw` | bronze | identity `id`, no natural PK |
| `ran.alarm_node_level` | reference | PK `(equipment_type, tech)` |
| `ran.alarm_vs_kpi` | reference | PK `alarm_code` |
| `ran.alarm_monitoring` | **gold** | PK `(site_id, cell_id, alarm_code)` |

Every table carries `org_id` (default `'rakuten'`), `workspace_id` (default `'rmi'`),
`created_at`, `modified_at`.

Typing rules that matter — each of these is a real trap in this data:

- **`alarm_code` is TEXT, never INT.** Real values include `PM_1010382`,
  `aniExtAlmContactAlarm_Battery Failure`, `Unknown Host`.
- `event_time` / `close_time` / `last_occurrence_time` → `timestamptz`; `date_of_intg` and
  `date_of_on_air` → `date`; `locked`, `flag_block_progress`, `flag_notify_alarm` → `boolean`.
- Everything else `text`. Do not try to type `ageing` (free text: `"8 Minute(s) 0 Second(s)"`)
  or `count`.
- `ran.alarm_monitoring` spells the column **`percieved_severity`** (sic) — the platform DDL
  has the typo and `alarm_gold.sql` depends on it. `alarm_monitoring_raw` spells it correctly.
- `ran.alarm_vs_kpi` keeps an `impact` column even though the workbook has none — it loads NULL.

Do **not** build `neighbour_sites`: it needs the EMS config dump, which is not in this folder.

Two `DB_Models.xlsx` defects to correct rather than copy: it names two different tables
`Alarm_monitoring` (disambiguate as `alarm_monitoring` vs `alarm_monitoring_raw`), and it
declares the bronze PK as `alarm_code` — wrong, since 3,475 raw rows share only 71 codes.

---

## Step 3 — `dags/_common.py`

One module, roughly: `load_source_config(source_id)` → `discover_file(cfg)` →
`read_frame(path, cfg)` → `apply_schema(df, cfg)` → `apply_filters(df, cfg)` →
`cast_bools(df, cfg)` → `load(df, cfg, conn)`.

- **YAML** parsed once at module import with `pyyaml`; DAGs pass a `source_id`.
- **File discovery**: `glob(source_uri + file_pattern)`, newest by mtime. Raise a clear
  error naming both the directory and the pattern if nothing matches.
- **Reading**: Polars throughout. `pl.read_csv(..., infer_schema_length=0)` for CSV;
  `pl.read_excel(..., engine="calamine", sheet_name=<resolved>)` for **both** xlsx and xlsb —
  calamine (via `fastexcel`) reads `.xlsb` natively, so `pyxlsb` is not needed.
- **Sheet resolution**: first `sheet_hints` entry matching case-insensitively, else sheet 0.
- **Column mapping**: for each `expected_columns` target, walk its synonym list and match a
  real header case-insensitively after stripping. Keep this simple — no fuzzy matching, but
  **do** log every unmatched target, because that is how you notice a file changed shape.
- **Filters**: support the three ops the manifest uses — `max` (keep rows where the column
  equals its maximum), `eq`, `ne`.
- **Bool cast**: `{"YES","TRUE","1"}` → true, else false.
- **Load**: `psycopg2.extras.execute_values`. If `pk_columns` → `INSERT ... ON CONFLICT (pk)
  DO UPDATE`, with a `WHERE <pk> IS NOT NULL` guard on the SELECT. Else → `DELETE FROM
  <table>` then plain insert. Return the row count actually persisted.

Data-hygiene rules to implement here, all observed in the real files:

1. Alarm CSV is **UTF-8 with BOM** → `encoding='utf-8-sig'`.
2. Alarm CSV has **embedded newlines inside quoted fields** — every timestamp is
   `"2026-08-05\n21:18:07 JST"`. A line-based read will corrupt it.
3. Timestamp normalisation: newline → space, strip the ` JST` suffix, parse, treat as `+09`.
4. **`-` is the universal null sentinel** across nearly every column in every file.
5. Strip whitespace from headers *and* values. Real headers include `" Ticket ID"`,
   `" Status"`, `" Vendor"`; real values include `'MAJOR   '`, `'KANTO    '`.
6. ECGI numbers arrive as floats and `ECGI` itself in scientific notation
   (`4.401184897793E12`) — read as string. `Date of On Air` is an Excel serial.

---

## Step 4 — `ingest_intake_4g`

`schedule="@daily"`, `catchup=False`, `max_active_runs=1`. Two parallel branches converging
on a join, matching the real DAG:

```
extract_load_4g   ─┐
                   ├─► gold_join ─► trigger_fault
extract_load_ecgi ─┘
```

- `extract_load_4g` — reads `4G_On-Air_*.xlsb`, applies all three filters
  (`Date of Intg == max`, `Status == "On-Air"`, `Locked status != "Yes"`), upserts
  `ran.raw_intake_site` on `site_id`.
- `extract_load_ecgi` — reads `ECGI_*.xlsx`, no filters, truncate-reloads `ran.site_db`.
- `gold_join` — runs `dags/sql/intake_gold.sql`: inner join `raw_intake_site r` to
  `site_db s` on `r.site_id = s.site_id`, derive **`cell_id = 'Sec' || s.cellid`**, upsert
  `ran.intake_site` on `(site_id, cell_id)`. Result is **cell-wise**: a site with 3 cells
  produces 3 rows.
- `trigger_fault` — `TriggerDagRunOperator(trigger_dag_id="ingest_fault_alarm_monitoring")`.

Note the join key is `4G.SARF ID` = `ECGI.Site ID` — the synonym list in `sources.yaml`
already maps `site_id: ["SARF ID", "Site ID", "SITE_ID"]`, so this resolves automatically.

---

## Step 5 — The two Fault reference DAGs

Both are single-source, `schedule="@daily"`, shape `extract_load` only.

- `ingest_fault_alarm_equipment_type` → `ran.alarm_node_level` (20 rows: Equipment Type +
  Tech → Node Level). Note the source file also has an `Equipment ID` column that is
  **prose instructions, not data** — it is not mapped and should not be loaded.
- `ingest_fault_alarm_library` → `ran.alarm_vs_kpi`. Expect **185 rows from 208**: 15 have a
  literal `#N/A` alarm code and are dropped by the NOT NULL guard, 8 codes are duplicated and
  collapse on conflict.

---

## Step 6 — `ingest_fault_alarm_monitoring`

`schedule=None` — it must not run before Intake is fresh, so it is started only by the
trigger from `ingest_intake_4g`.

```
check_references ─► extract_load_alarms ─► alarm_gold
```

- **`check_references`** — assert `ran.alarm_node_level` and `ran.alarm_vs_kpi` are both
  non-empty, failing with an explicit message. This guards a real landmine recorded in
  `sources.yaml`: when `alarm_vs_kpi` had no producer, the `LEFT JOIN ... COALESCE` in the
  gold SQL made **every alarm non-blocking** — all rows present, the blocking decision
  silently flattened to a constant. A green DAG that decides nothing is the worst outcome.
- **`extract_load_alarms`** — reads `*.alarm-monitoring.csv`, truncate-reloads
  `ran.alarm_monitoring_raw` (3,475 rows, no filtering — raw keeps every alarm).
- **`alarm_gold`** — runs `dags/sql/alarm_gold.sql`: `DELETE FROM ran.alarm_monitoring`, then
  rebuild. Port the platform's SQL essentially verbatim
  (`brain/ingestion/plugins/ingestion/transforms/alarm_gold.py`):

  1. `JOIN ran.alarm_node_level nl ON nl.equipment_type = r.equipment_type AND upper(nl.tech)
     = CASE upper(r.technology) WHEN 'LTE' THEN '4G' WHEN 'NR' THEN '5G' ELSE upper(...) END`
     — the technology vocabularies genuinely differ between the three files.
  2. `JOIN LATERAL` with two arms UNIONed:
     - `node_level = 'cell'` → `s.cell_name = r.equipment_id`, yields `(site_id, 'Sec'||cellid)`
     - `node_level = 'site'` → `s.enodebid = (regexp_match(r.equipment_sub_id,
       'enbid=(\d+)'))[1]`, yields `(site_id, '')`
     Both arms carry `AND s.site_id IN (SELECT site_id FROM ran.intake_site)`.
  3. `LEFT JOIN ran.alarm_vs_kpi vk ON vk.alarm_code = r.alarm_code`
  4. `flag_notify_alarm = NOT COALESCE(vk.flag_block_progress, false)`
  5. `DISTINCT ON (m.site_id, m.cell_id, r.alarm_code)` — matches the target PK.

  The node-level join is an **INNER** join: ~48% of alarm rows (`FEMTO_CELL`, `ODSC_CELL`,
  `IDSC_CELL`, `DRAN_CELL`, …) have no mapping in the 20-row reference table and drop out.
  That is the real behaviour, not a bug — but count them, see verification below.

---

## Step 7 — README

Bootstrap order (needed once, because the fault DAG asserts its references exist):
`docker compose up -d` → trigger `ingest_fault_alarm_equipment_type` and
`ingest_fault_alarm_library` → trigger `ingest_intake_4g`, which chains into
`ingest_fault_alarm_monitoring`. Plus a short "what each table means" table.

---

## Verification

1. `docker compose up -d`; Airflow UI reachable at `localhost:8080`; all four DAGs parse
   with no import errors (`docker compose exec airflow-scheduler airflow dags list-import-errors`
   must be empty).
2. Run the bootstrap order above; all four DAGs green.
3. Row-count assertions:

   | Table | Expected |
   |---|---|
   | `ran.alarm_node_level` | 20 |
   | `ran.alarm_vs_kpi` | **185** (208 − 15 null codes, 8 dupes collapsed) |
   | `ran.alarm_monitoring_raw` | **3475** |
   | `ran.site_db` | 999 |
   | `ran.raw_intake_site` | > 0, and strictly fewer than the file's rows (filters applied) |
   | `ran.intake_site` | > 0, ≥ `raw_intake_site` (cell-wise fan-out) |
   | `ran.alarm_monitoring` | see #5 |

4. Spot-checks: `flag_block_progress` in `alarm_vs_kpi` should be **46 true / 162 false**;
   every `intake_site.cell_id` matches `^Sec\d+$`; no `alarm_monitoring` row has a
   `site_id` absent from `intake_site`.
5. **Expect gold to be small, and diagnose it if it is zero.** These are sample extracts —
   the ECGI file has 999 rows where production has 120,225 — so the on-air site set may
   barely intersect the alarm feed's equipment IDs. If `ran.alarm_monitoring` comes back
   empty, do **not** call the DAG done. Run the three-way diagnostic and report it:
   how many raw alarms survive the node-level join; of those, how many `equipment_id`
   values appear in `site_db.cell_name`; and how many extracted `enbid` values appear in
   `site_db.enodebid`. An honest "0 rows because the sample files don't overlap, here is
   the evidence" is a correct result; a silent 0 is not.

---

## Explicitly out of scope

- MinIO / S3, and the `bronze.source_registry` · `ingest_run` · `source_file_log` ·
  `dag_trigger_edge` control plane.
- `ran.neighbour_sites` (needs the EMS dump zip, not present).
- The `docs/` `.md` / `.mmd` / `.png` files and `DB_Models.xlsx` — **reference for humans
  only, never read by code.** Worth reading first: `docs/fault/FAULT_4G.md` (newer and more
  correct than its `.html`, which is missing §4.1 and states the wrong PK for `alarm_vs_kpi`),
  `docs/intake/intake-4g-onair.standalone.html` (the only copy of the intake diagram), and
  both `AGENT_GOLD_REBASE.md`. Ignore `FAULT.md`, `AGENT_KIT_INTAKE.md` and the two
  `0*_record.md` files — they describe an older synthetic-demo generation with different
  files and deleted code, and following them would mislead.
