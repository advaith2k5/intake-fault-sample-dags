# Intake Stage — Build Record (01)

**Date:** 2026-07-19 · **Branch:** `wise_uc` · **Status:** ✅ built + run live.

What was built for the **Intake Agent** (first agent on the generic Agent Harness) and how it ran.

---

## 1. What was built (files)

**Data Layer** — `data_layer/postgres/init/ran/09_ns_qaw.sql` (additive; applied live)
- `ns_agent` (registry — agent = a row), `ns_site`, `ns_site_cell`, `ns_workflow` (state machine, one per site),
  `ns_workflow_event` (append-only replayable evidence), `ns_fault_alarm`, `ns_config_deviation`.

**Edge — the generic Agent Harness** — `edge/harness/`
- `settings.py` · `db.py` (workflow + evidence primitives: `register_agent`, `open_workflow`, `advance_stage`, `append_event`) ·
  `model.py` (LLM via the :8317 proxy) · `runtime.py` (`build_agent` = DeepAgent from a kit's soul + tools; `run`).
- Domain-agnostic — it never knows "Intake".

**Brain / Bundle Registry — the Intake kit** — `brain/bundle_registry/bundles/ns_qaw/kits/intake/`
- `soul.md` (identity + procedure) · `manifest.yaml` (declarative binding) · `tools.py` (domain tools) ·
  `skills/site-intake/SKILL.md` · `run.py` (driver — fires the agent per new site).

---

## 2. The agent (as data)

- **Registry row:** `ns_agent('intake', kit_ref='kits/intake', model='claude-sonnet-4-6', tools=[…], skills=['site-intake'])`.
- **Tools (deterministic, 4):** `assess_site` (validate + classify + gate **G0** + neighbours), `register_site`
  (writes `ns_site`/`ns_site_cell`), `open_workflow` (harness), `log_intake_event` (harness — writes the
  Evidence→Hypothesis→RCA→Action + advances stage → fault).
- **Runtime:** DeepAgents (LangGraph) → generic worker loads the kit and *becomes* the Intake agent per site.

---

## 3. How it ran

`python run.py TOK_Cluster_CellPlan_flat.csv` → fired on the **5 NEW sites** (New-Capacity ×3, New-Coverage ×2;
the 60 Existing cells are neighbours/tier-1).

**Result (live):**
```
ns_site = 5 · ns_site_cell = 15 · ns_workflow = 5 · ns_workflow_event = 5
all 5 workflows → current_stage = fault (queued), lead_agent = Fault
```

Sample verdict (LLM-authored, grounded in real data):
> *"TOK_NEW_01 is a valid New-Capacity site with 3 cells, all G0 eligibility criteria met; no blocking issues at intake."*
> Evidence: Validation ok · On-air/Unlocked/DT (stubbed) · Classification New-Capacity · G0 eligible · Cells [PCI 62,60,61] · 60 tier-1 neighbours.

---

## 4. Engineering notes / decisions

- **DeepAgents `write_todos` overhead** — the planning middleware re-plans between every tool call. For a linear
  4-step intake this blew the recursion limit. Fix: put site facts in the prompt + collapse the 4 pre-checks into
  one `assess_site` tool → 4 hops; recursion_limit bumped to 80. ~30s/site.
- **numpy adaptation** — pandas `np.float64`/`np.int64` aren't adapted by psycopg2 (leaked as `np.float64(...)`
  into SQL). Fix: `_v()` converts numpy scalars to native Python via `.item()`.
- **Run in background** — 5 sites × ~30s > the 2-min foreground cap; runs via a background task.

---

## 5. Open items (carried forward)

- **Eligibility source** — `on_air`/`unlocked`/`dt_feasible` stubbed `true` until the on-air-tracker adapter exists.
- **Memory (LangMem)** — disabled; turns on when the Context plane is ready (colleague).
- **UI** — next: `context_api` read endpoints + the war-room (landing card → drill-down → agent view) over
  `ns_workflow` / `ns_workflow_event`.
- **Next stages** — Fault (needs alarm dataset) · CM-audit/GPL (data in hand).
