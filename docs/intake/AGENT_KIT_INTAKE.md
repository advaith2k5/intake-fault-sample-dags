# Agent Kit — Intake Agent (NS-QAW)

**Status:** design (no implementation yet) · **Runs on:** the generic [Agent Harness](./AGENT_HARNESS_ARCHITECTURE.md) (DeepAgents).
**Kit:** `kits/intake/` · **Model:** via `model_gateway` → :8317 proxy · **Context/KG:** deferred (colleague).

> The first concrete kit that proves the harness. Defines the Intake Agent as **data** —
> soul + tools + skills + memory + model — that a generic Edge worker loads and *becomes*.
> No code; tools are described by **contract** (name · in → out · effect).

---

## 0. TL;DR

- **Job:** take a **new site** from the cell plan → **register it into the candidate world**
  (`ns_site` / `ns_site_cell`) → **classify + check eligibility (gate G0)** → **open its
  workflow** (`ns_workflow`) → **log the evidence** (`ns_workflow_event`) → hand off to Fault.
- **Site-level:** one workflow **per new site** (not per cell). 5 new sites → 5 workflows.
- **Mostly deterministic:** the tools do the real work; the LLM narrates the Evidence→
  Hypothesis→RCA→Action trace the UI shows. So a **cheap model** suffices.
- **Never touches `dim_site`/`dim_cell`** — canonical dims stay clean; candidates promote later.

---

## 1. Soul — who this agent is (`soul.md`)

> *"You are the **Intake Agent** for autonomous new-site certification. When a new site's
> cell plan arrives, you register it into the **candidate** world, verify it is eligible and
> correctly classified, open its certification workflow, and record clear evidence for the
> next agent. You are precise and conservative: you register only what the plan states, you
> never modify the live network model, and you never guess a value — if a field is missing
> you flag it. Your output is a clean, auditable intake record and an open workflow."*

**Operating principles**
- Candidate-only — write `ns_*`, **never** `dim_site`/`dim_cell` (promotion happens on certification, not here).
- One workflow **per site**; evidence is captured **per cell** inside the site.
- Deterministic decisions (classification, eligibility) come from **tools/gates**, not vibes.
- Every action is journaled; nothing silent.

---

## 2. Tools (atomic · deterministic · testable)

The agent's *hands*. Each does one thing; the LLM composes them via the skill.

| Tool | In → Out | Effect / writes |
|---|---|---|
| `read_cell_plan(source)` | csv/path → `{sites[], cells[]}` | pure read + parse of the cell plan (adapter; dummy-file now) |
| `validate_plan(site)` | site record → `{ok, missing[], warnings[]}` | pure check — required fields (ID, CID/enbId, PCI, band…), sane ranges |
| `classify_site(site)` | site record → `{is_new, class:New-Capacity\|New-Coverage\|Existing}` | pure — from `siteType` + `n_cells` |
| `check_eligibility(site)` | site record → `{on_air, unlocked, dt_feasible, eligible}` **(G0)** | pure gate; sources stubbed until the on-air tracker adapter lands (§11) |
| `resolve_neighbors(site)` | site → `{tier1[], neighbours[]}` | pure — nearest existing cells in the cluster (from the plan / distance) |
| `upsert_ns_site(site)` | site → `site_id` | **writes `ns_site`** (candidate) |
| `upsert_ns_site_cell(cells)` | cells[] → n | **writes `ns_site_cell`** (full plan fidelity) |
| `open_workflow(site_id)` | site_id → `workflow_id` | **writes `ns_workflow`** (status=running, stage=intake) |
| `append_event(workflow_id, stage, agent, gate, verdict, evidence)` | … → event_id | **writes `ns_workflow_event`** (replayable) |

- **Shared-lib tools:** `upsert_*`, `open_workflow`, `append_event` come from `harness/common_tools` (reused by every agent). Only `read_cell_plan`, `validate_plan`, `classify_site`, `check_eligibility`, `resolve_neighbors` are Intake-specific.
- **All tools are LLM-independent** — they run without a model. The model only sequences them and writes the narrative.

---

## 3. Skills (composed procedures · `SKILL.md`)

The agent's *how-to*. A skill is a playbook the LLM follows, calling tools.

### `site-intake` (the main playbook)
```
1. read_cell_plan(source)                          → sites[], cells[]
2. for each NEW site (classify_site.is_new):
     a. validate_plan(site)                         → block on hard-missing fields
     b. classify_site(site)                          → class
     c. resolve_neighbors(site)                      → tier1[], neighbours[]
     d. upsert_ns_site(site) + upsert_ns_site_cell   → registered
     e. check_eligibility(site)  (GATE G0)           → eligible?
     f. open_workflow(site_id)                       → workflow_id
     g. append_event(intake, verdict, evidence)      → the Evidence→…→Action trace
     h. hand off: set stage → fault
```

### Reusable skills (shared, other agents use them too)
- **`plan-validation`** — required-field + range checks → `{ok, missing[]}`.
- **`eligibility-classification`** — the G0 + New/Existing logic, as a named capability.

---

## 4. Memory (LangMem) — deferred, minimal now

Context/KG is your colleague's area, so Intake runs with **memory off / minimal** for now.
When the Context plane is ready, Intake's `memory.yaml` turns on:
- **Semantic:** cluster naming conventions, GPL nuances, "PCIs that recur."
- **Episodic:** past intakes ("TOK-NEW-03 blocked on power last time").
- **Procedural:** learned classification tweaks per operator.

Until then: intake is fully functional without memory (the plan is self-contained).

---

## 5. Model

Intake is **light reasoning** (tool sequencing + a short narrative). Recommend a **cheap,
fast** model — `claude-haiku-4-5` — with `claude-sonnet-4-6` as the safe default if the
classification narrative needs more nuance. Selected per-agent in the manifest; swappable
via `model_gateway`.

---

## 6. Manifest (`manifest.yaml`) — the declarative binding

```
agent_id:      intake
name:          Intake Agent
kind:          stage-agent
soul_ref:      soul.md
model:         claude-haiku-4-5        # sonnet-4-6 fallback
tools:         [read_cell_plan, validate_plan, classify_site, check_eligibility,
                resolve_neighbours, upsert_ns_site, upsert_ns_site_cell,
                open_workflow, append_event]
skills:        [site-intake, plan-validation, eligibility-classification]
memory:        {enabled: false}        # Context plane deferred
writes:        [ns_site, ns_site_cell, ns_workflow, ns_workflow_event]
gate:          G0 (eligibility)
handoff:       fault
```

The registry row is just: `agent('intake', kit_ref='kits/intake', status='active')`. The
harness reads it, loads the kit, and the worker *is* the Intake Agent.

---

## 7. Inputs / outputs (table mapping)

| | |
|---|---|
| **Input** | cell plan (`TOK_Cluster_CellPlan_flat.csv`) via `read_cell_plan` |
| **Writes** | `ns_site` (1/site) · `ns_site_cell` (n/site) · `ns_workflow` (1/site) · `ns_workflow_event` (intake event) |
| **Never writes** | `dim_site`, `dim_cell` (canonical — promotion only, later) |
| **Output** | 5 candidate sites registered, 5 workflows opened at `stage=fault (queued)`, each with a journaled intake event |

---

## 8. The Evidence → Hypothesis → RCA → Action it emits

This is what the **agent-details UI** renders (and REPLAY plays back). Maps to the Intake screen:

| Panel | Content |
|---|---|
| **Evidence** | On-air tracker: *fully on-air → Eligible* · Lock state: *unlocked → Ready* · Site type: *New-Capacity, 3 cells → Classified* · DT feasibility: *reachable → Drive-testable* |
| **Hypothesis / steps** | Read on-air tracker (G0) · Classified site type · Resolved tier-1 · Opened case |
| **RCA (ready)** | *"`TOK_NEW_01` is eligible and classified — case opened, fault check queued."* |
| **Action** | *"Case opened — fault check starts automatically."* |

*(Real today: site type, n cells, classification, neighbours. Stubbed until adapters land:
on-air / unlocked / DT-feasibility — see §11.)*

---

## 9. Gate G0 — eligibility (deterministic)

`eligible = on_air AND unlocked AND classified`. Owned by the harness gate function (not the
LLM). Pass → open workflow, hand to Fault. Fail → skip (site stays with the TI team), logged.

---

## 10. Run walkthrough (one new site)

1. Trigger fires with the cell-plan source → harness loads the Intake kit onto a worker.
2. Agent runs `site-intake`: reads plan → picks `TOK_NEW_01` (New-Capacity, 3 cells) →
   validates → classifies → resolves neighbours → **writes `ns_site` + 3 `ns_site_cell`** →
   G0 eligible → **opens `ns_workflow`** → **appends the intake event** (the Evidence→…→Action above).
3. Worker releases. War-room now shows a row: `TOK_NEW_01 · New-Capacity · stage=fault · lead=Intake→Fault · running`.

---

## 11. Kit folder + open items

```
kits/intake/
  soul.md                          §1
  manifest.yaml                    §6
  tools/  read_cell_plan · validate_plan · classify_site · check_eligibility · resolve_neighbours
  skills/ site-intake/SKILL.md · plan-validation/SKILL.md · eligibility-classification/SKILL.md
  memory.yaml                      §4 (disabled for now)
```

**Open items**
- **Eligibility data source** — `on_air` / `unlocked` / `dt_feasible` aren't in the cell plan; stub `true` for the demo, or point `check_eligibility` at the on-air tracker adapter when it exists.
- **Memory** — turn on when the Context plane is ready (colleague).
- **Model** — start on `haiku-4-5`; bump to `sonnet-4-6` if the classification narrative needs it.
