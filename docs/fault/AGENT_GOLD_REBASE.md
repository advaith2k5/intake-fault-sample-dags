# Fault Agent — rebased onto Gold (`ran.alarm_monitoring`)

Re-based the Fault kit off the synthetic world onto the real pipeline output. Part of the agent
spin-up (`docs/AGENTS/AGENT_SPINUP.md`).

## Before (deleted / retired)
- Read a synthetic alarm workbook (`/data/alarms.xlsx`) via `fault_engine.py` and re-did the
  FM read + RCA in the kit; wrote per-workflow rows to `ran.ns_fault_alarm` (dropped table).
- `fault_engine.py` **deleted**.

## After (this change)
- **Reads the Gold table `ran.alarm_monitoring`** — alarms already on-air-matched, resolved to
  site/cell, and flagged (`flag_block_progress`) by the ingestion pipeline (`alarm_gold`).
- **Gate (deterministic, no LLM):** any alarm with `flag_block_progress = true` ⇒ **blocked**;
  else **passed**. No alarm parsing, no domain writes.

## Data source
| Reads | Writes |
|---|---|
| `ran.alarm_monitoring` (alarm_code, severity, cell_id, equipment_type, flag_block_progress) | `ran.ns_workflow` (advance to fault) |
| — | `ran.ns_workflow_event` (fault evidence) |

## Flow
```
sites at intake:passed  →  read alarm_monitoring for the site  →  gate:
    any flag_block_progress → BLOCKED     else → PASSED
  →  log_fault_event()  →  advance ns_workflow (fault:blocked | fault:passed)
```

## Files
- `kits/fault/tools.py` — rewritten (`run_fault_check` reads gold; `decide` gates on block flag).
- `kits/fault/run.py` — rewritten (picks `intake:passed` from `ns_workflow`; no `fault_engine`).

## Run result (demo gold)
19 intake-passed sites → **2 blocked** (the two sites with a blocking alarm in `alarm_monitoring`),
**17 passed**. Matches the gold exactly.

## Status
Rewritten + run end-to-end. `soul.md` / `manifest.yaml` unchanged. Uncommitted.
