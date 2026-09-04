# Intake Agent — rebased onto Gold (`ran.intake_site`)

Record of re-basing the Intake kit off the **old synthetic world** onto the **real ingestion
pipeline output**. Part of the agent spin-up (see `docs/AGENTS/AGENT_SPINUP.md`).

## Before (deleted / retired)
- Parsed a synthetic `cellplan.csv` (`/data/cellplan.csv` or MinIO inbox) with pandas.
- Wrote the domain tables `ran.ns_site`, `ran.ns_site_cell`, `ran.ns_cluster_site` — **all
  dropped in the reset; no longer exist.**
- The site/cell facts were re-derived in the kit.

## After (this change)
- **Reads the Gold table `ran.intake_site`** — the on-air site/cell set already produced by the
  ingestion pipeline's `gold_join` task. No file parsing, no domain writes.

  > **Which DAG produces it (Phase 12a).** `gold_join` now lives in **`ingest_intake_4g`**, the
  > `@daily` head of the split ingestion chain
  > (`ingest_intake_4g` → {`ingest_fault_alarm_monitoring`, `ingest_cm_audit_ems`};
  > `ingest_cm_audit_ems` → `ingest_kpi_rca_4g_counters`, all edges declared as rows in
  > `bronze.dag_trigger_edge`). **Both topologies currently ship in the same image and the
  > production cutover has NOT happened** — `ingest_site_pipeline` still runs `gold_join` on the
  > production path and the split DAGs are paused. Either way the kit is unaffected: it reads the
  > *table*, never a DAG. Current topology: `docs/OVERVIEW/PIPELINE_TECHNICAL.md` §3a.
- Intake's only job now: **open the certification workflow per on-air site and record the G0 gate.**
- Neighbour count read from `ran.neighbour_sites`.

## Data source
| Reads | Writes |
|---|---|
| `ran.intake_site` (site_id, cell_id, cell_name, site_type, vcu_name, status) | `ran.ns_workflow` (open) |
| `ran.neighbour_sites` (tier-1 count) | `ran.ns_workflow_event` (G0 evidence) |

## Flow
```
ran.intake_site (Gold)  ──group by site──▶  per site:
   assess_site() → open_workflow() → Decision(evidence)  →  log_intake_event()  →  stage=intake:passed
```
- **`assess_site()`** — on-air is confirmed by presence in `intake_site`; G0 validates cell coverage +
  classification. Deterministic, LLM-free.
- **`decide()`** — `assess → open → Decision` (Layer-1 verdict + Layer-2 evidence cards). Returns the
  new `workflow_id`.
- Removed tool: `register_site()` (wrote `ns_site` — redundant now the pipeline resolves the on-air set).

## Files
- `kits/intake/tools.py` — rewritten (gold reads; dropped `register_site`).
- `kits/intake/run.py` — rewritten (`_load_onair_sites()` reads `ran.intake_site`; no pandas/CSV).

## New dependency
`ran.ns_workflow` + `ran.ns_workflow_event` re-added to `09_ns_qaw.sql` (the harness needs them; they
were dropped in the reset and never restored).

## Status
Code rewritten. **Not yet run** end-to-end via the edge-worker (pending). `soul.md` /
`manifest.yaml` unchanged (soul still describes the intake gate correctly).
