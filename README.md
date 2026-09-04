# Sample Airflow DAGs — Fault & Intake agents

A standalone rebuild of the Intake and Fault ingestion DAGs against the real client
files (`Intake data/`, `Fault data/`), with MinIO dropped — files are read straight
off local disk. See `my-boss-has-told-squishy-thacker.md` for the full design brief.

## What each table means

| Table | Layer | Meaning |
|---|---|---|
| `ran.raw_intake_site` | bronze | Current on-air batch from the 4G On-Air file, filtered (latest `Date of Intg`, `Status = On-Air`, `Locked status != Yes`). |
| `ran.site_db` | bronze | The full ECGI cell database, unfiltered — a superset of the on-air set. |
| `ran.intake_site` | **gold** | On-air site × cell (cell-wise: a 3-cell site is 3 rows). The on-air set every later stage scopes to. |
| `ran.alarm_monitoring_raw` | bronze | The live alarm feed, unfiltered — every alarm, duplicates and all. |
| `ran.alarm_node_level` | reference | Equipment Type + Tech → match level (`Cell` \| `Site` \| `Femto Cell`). |
| `ran.alarm_vs_kpi` | reference | Alarm code catalogue — severity, KPI impact, and the `flag_block_progress` gate. |
| `ran.alarm_monitoring` | **gold** | On-air-matched, classified alarms. `flag_block_progress = true` on any row ⇒ the site is blocked. |

## Bootstrap

The Fault feed DAG asserts its two reference tables exist, so load those first:

```bash
docker compose up -d
# wait for the Airflow UI at http://localhost:8080 (admin / admin)

docker compose exec airflow-scheduler airflow dags trigger ingest_fault_alarm_equipment_type
docker compose exec airflow-scheduler airflow dags trigger ingest_fault_alarm_library

# then the intake DAG, which chains into ingest_fault_alarm_monitoring on completion
docker compose exec airflow-scheduler airflow dags trigger ingest_intake_4g
```

`ingest_fault_alarm_monitoring` has `schedule=None` — it only runs via the
`trigger_fault` task at the end of `ingest_intake_4g`, so the alarm gold rebuild
never runs against a stale intake set.

## Verification

```bash
docker compose exec airflow-scheduler airflow dags list-import-errors   # must be empty

docker compose exec postgres psql -U airflow -d airflow -c "
  SELECT 'raw_intake_site', count(*) FROM ran.raw_intake_site
  UNION ALL SELECT 'site_db', count(*) FROM ran.site_db
  UNION ALL SELECT 'intake_site', count(*) FROM ran.intake_site
  UNION ALL SELECT 'alarm_monitoring_raw', count(*) FROM ran.alarm_monitoring_raw
  UNION ALL SELECT 'alarm_node_level', count(*) FROM ran.alarm_node_level
  UNION ALL SELECT 'alarm_vs_kpi', count(*) FROM ran.alarm_vs_kpi
  UNION ALL SELECT 'alarm_monitoring', count(*) FROM ran.alarm_monitoring;
"
```

### Expected counts, against these sample files

| Table | Expected | Note |
|---|---|---|
| `ran.alarm_node_level` | 20 | |
| `ran.alarm_vs_kpi` | 185 | 208 rows − 15 null (`#N/A`) codes, 8 duplicate codes collapse on upsert. |
| `ran.alarm_monitoring_raw` | 3475 | |
| `ran.raw_intake_site` | 3 | The sample 4G On-Air file has exactly 3 rows, all in the current on-air batch — all 3 survive filtering (there's no older batch in this sample for the "latest date" filter to exclude). |
| `ran.intake_site` | 9 | 3 on-air sites × 3 cells each. |
| `ran.site_db` | **20**, not 999 — see below | |
| `ran.alarm_monitoring` | **1** — see below | |

**`ran.site_db` — a real discrepancy in the sample file, not a bug.** The brief's
verification table expects 999 rows (matching the brief's own note that "the ECGI
file has 999 rows where production has 120,225"). The workbook does have 999 row
slots after the header, but only the first **20** carry any data — rows 22–1000 are
genuinely empty (`<row r="…" .../>` with no cells at all, confirmed both via
`openpyxl` and by inspecting the sheet XML directly). Loading only the 20 real rows
is correct; padding out to 999 with blank rows would be manufacturing data that
isn't in the file. Reported here rather than silently forced to match.

**`ran.alarm_monitoring` — 1 row, and it's a real blocker.** With only 3 on-air
sites and 20 real `site_db` rows, the on-air alarm match is expected to be small
(per the brief's own §5). Diagnostic:

- 3475 raw alarms → 1 matches an on-air site/cell.
- That row: `Equipment Type = MACRO_CELL` (node level `Cell`, from
  `alarm_node_level`), `Equipment ID = RNA1401002903_5` = `site_db.cell_name` for
  site `RNA1401002903` (on-air, 3 cells). `Alarm Code = 1371` →
  `alarm_vs_kpi.flag_block_progress = YES` → **site `RNA1401002903` is blocked.**
- No `site`-level match: none of the sample alarm feed's `enbid=` values
  (parsed from `Equipment Sub ID`) appear in `site_db.enodebid` for the 3 on-air
  sites.

**`flag_block_progress` spot-check.** The brief calls for 46 true / 162 false.
That count is over the *raw file's* 208 rows (46 `YES` + 162 not-`YES` = 208); the
*loaded* `ran.alarm_vs_kpi` table has 185 rows (15 null-code rows dropped, 8
duplicate codes collapsed on upsert), so its true/false split is 43/142, not
46/162. Both numbers are internally consistent — pick whichever the check is
actually meant to validate (raw file vs. loaded table).

## Out of scope

MinIO/S3 and the `bronze.*` control plane; `ran.neighbour_sites` (needs the EMS
config dump, not present); 5G intake. See the brief for the full list.
