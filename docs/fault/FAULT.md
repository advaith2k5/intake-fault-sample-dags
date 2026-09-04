# Fault Agent (Fault stage) — Method & Reference

**Stage:** Fault — stage 2 of the site-certification workflow (per the live timeline:
`Intake · Fault · Config · KPI RCA · Change · Drive-test · Certify`).
**What this is:** the reference for how the Fault agent checks a new site's live alarm state and does
**correlated-cascade root-cause analysis** to decide whether it can proceed or is blocked.

---

## 1. In plain English
A new site that passed Config gets a fault check. The agent reads the site's **live alarm feed**
and asks: *is this site healthy enough to carry traffic?* When a site is in alarm, it doesn't just
count alarms — it does **real RCA**: from many correlated alarms it names the **one root cause** and
its fix. A site with a **service-affecting fault** (a cell out of service) **cannot be certified** and
is blocked until the field fix.

## 2. Inputs
| File | Role | Shape |
|---|---|---|
| `TOK_Cluster_Alarms.xlsx` | the FM feed | `SiteFaultFlags` (per-site gate input, all 25 sites) · `ActiveAlarms` (individual alarms) · `AlarmHistory` (cleared) · `AlarmDictionary` (meaning + recommended action) |

Alarm model: **3GPP TS 32.111 / ITU-T X.733** — `perceivedSeverity` (CRITICAL/MAJOR/MINOR/WARNING),
`probableCause`, `specificProblem`, `serviceAffecting`, `correlationId`, `isRootCause`, `moPath`,
rich `additionalText`.

## 3. The key idea — correlated cascades with a marked root
Alarms arrive as **cascades**: several share a `correlationId`, and exactly one is
`isRootCause=YES`; the rest are consequences. The agent groups by cascade and reports the root, e.g.:
- **TOK_NEW_02** — `COR-30022-A` (4 alarms): **root `RfPortVswrOverThreshold`** (VSWR 2.31 vs 1.80,
  RF port B) → `CellOutOfService` (Sec2) → `RetAntennaCommunicationFailure` → `TemperatureHigh`.
- **TOK_NEW_05** — **two roots**: `FronthaulLinkDown` (CRITICAL) and `TimeSyncHoldover` (MAJOR).

## 4. The method (deterministic engine)
`fault_engine.py` (in the kit) — no LLM guessing; the DeepAgent narrates what this produces:
1. Look up the site's `SiteFaultFlags` row → the per-site verdict (`inAlarm`, `highestSeverity`,
   `serviceAffecting`, `activeAlarmCount`, `rootCauseAlarm`, `cellsAffected`).
2. Pull its `ActiveAlarms`, group by `correlationId`, mark the `isRootCause` alarm per cascade.
3. Join `AlarmDictionary` for each alarm's meaning + **recommendedAction**.
4. Return the gate + cascades for the agent to write and narrate.

## 5. The gate
| Condition | Outcome |
|---|---|
| `inAlarm=NO` (clear) | ✅ **passed** → advances |
| `serviceAffecting=YES` **or** `highestSeverity=CRITICAL` | ⛔ **blocked** (a cell is out of service — cannot certify) |
| `MAJOR` (non-service-affecting) | ⚠️ **awaiting_human** (needs_review) |
| `MINOR/WARNING` only | ✅ **passed** with a note |

## 6. What it writes
- **`ns_fault_alarm`** — one row per active alarm: `severity · alarm_code · cell_name ·
  service_affecting · correlation_id · is_root_cause · probable_cause · recommended_action ·
  mo_path · additional_text`.
- **`ns_workflow_event`** — one Fault-stage event with the Evidence→Hypothesis→RCA→Action trace
  (the RCA names the single root cause; the action is the dictionary's remediation).
- **`ns_workflow`** — advanced to `current_stage=fault` with the gate's stage_status/status.

## 7. Result on our data
Fault runs on the config-passed sites → **2 pass / 2 blocked**:
| Site | Alarm state | Gate | Root cause |
|---|---|---|---|
| TOK_NEW_01 | clear | ✅ passed | — |
| TOK_NEW_02 | CRITICAL · service-affecting | ⛔ blocked | RF-port VSWR on Sec2 |
| TOK_NEW_04 | clear | ✅ passed | — |
| TOK_NEW_05 | CRITICAL · service-affecting | ⛔ blocked | fronthaul down + sync holdover |

Real agent-authored RCA (verified): *"4 active alarms in cascade COR-30022-A. Root cause is an RF
port VSWR over-threshold on Sec2; the cell-out-of-service and downstream alarms are consequences.
Site cannot be certified while Sec2 is unable to carry traffic."*

## 8. The kit (agent = data)
`brain/bundle_registry/bundles/ns_qaw/kits/fault/`:
`fault_engine.py` (deterministic RCA) · `tools.py` (`run_fault_check` + `log_fault_event`) ·
`chat.py` (read-only: `fault_status`, `site_alarms`, `root_causes`) · `soul.md` · `skills/fault-triage/` ·
`run.py` (fires on config-passed sites). Loaded by the one generic edge-worker (DeepAgents SDK).

## 9. UI
- **War-room drill-down** → the **FAULT · ROOT-CAUSE ANALYSIS** panel: the cascade grouped, the
  root highlighted, its recommended action, consequences listed. Blocked sites show coral `BLOCKED`.
- **Agent Mode** (`VIEW AGENTIC FLOW`) → the Fault stage card with the E→H→RCA→A detail.
- **Fault Agent chat** — ask "what's the root cause on TOK_NEW_02?" and it answers from live data.

## 10. Related
Intake: `docs/INTAKE/` · Config/CM-audit: `docs/CM_AUDIT/CM_AUDIT.md` ·
Orchestration/ingest: `docs/ORCHESTRATOR/ORCHESTRATOR.md`.
