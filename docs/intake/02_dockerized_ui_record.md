# Intake Stage — Dockerized + War-Room UI (02)

**Date:** 2026-07-19 · **Branch:** `wise_uc` · **Status:** ✅ live end-to-end in Docker.

The generic Edge Worker + the read API + the console War-Room — all containerized, verified.

---

## The corrected model (vs the earlier mistake)
NOT a per-agent service. **One generic `edge-worker`; agents spin up *on* it** (agent = data → the
worker loads a kit and *becomes* that agent). Intake today, Fault/Config next — same worker.

## Services (4)
| Service | Role | Port |
|---|---|---|
| `postgres` | the `ns_*` data | 5432 |
| **`edge-worker`** (NEW) | generic agent runtime — `POST /run {agent_id}` loads a bundle kit, runs the DeepAgent | 8090 |
| `context_api` | war-room reads (`/ns-qaw/*`) + `POST /ns-qaw/run-intake` (proxies the worker) | 8013 |
| `console` | the Hinode SiteCert War-Room UI | 8080 |

LLM via the host proxy (`:8317`) for now (not `model_gateway` yet). Bundle **mounted** into the
worker for live iteration. Tenant `org_demo/ws_ran`.

## Files added
- `edge/harness/worker.py` — FastAPI generic worker (`/health`, `/agents`, `/run`, `/runs/{id}`).
- `edge/harness/Dockerfile` + `requirements.txt` — the edge-worker image.
- `docker-compose.yml` — `edge-worker` service (bundle + CSV mounted, proxy via `host.docker.internal`).
- `context_layer/repositories/ran/ns_qaw.py` — reads (overview / sites / site detail / events).
- `brain/context_api/.../routers/ns_qaw_router.py` — `/ns-qaw/*` + run trigger.
- `console/src/components/HinodeSiteCertView.tsx` — the war-room (+ wired into nav/routing).

## The loop (verified live)
```
console "Run Intake" ─► context_api /ns-qaw/run-intake ─► edge-worker /run{intake}
   ─► loads intake kit ─► DeepAgent (per new site) ─► writes ns_* ─► context_api reads ─► war-room
```
`POST /ns-qaw/run-intake` → `{"status":"started"}` → sites appeared 1→2→3→4→5 in the container;
edge-worker logs `✓ TOK_NEW_01 … ✓ TOK_NEW_05`. `overview={total:5, running:5, by_stage:{fault:5}}`.

## The UI (what you see)
- **Header** — Hinode·SiteCert + live pulse + **Run Intake** button.
- **Overview** — New sites · In progress · Awaiting/blocked · Certified + a stage pipeline strip.
- **Filters** (All / In progress / Needs you / Certified) + **Table ⇄ Cards** toggle.
- **Sites table** — SITE · TYPE · STAGE · LEAD · STATUS · CELLS · AGE (live, 4s poll).
- **Agent view** (click a site) — stage stepper · the Intake DeepAgent's **Evidence → Hypothesis →
  RCA → Action** (the real LLM-authored trace) · G0 PASS + REPLAY · full workflow timeline · the cells.

## Open / next
- **Fault** agent (needs alarm dataset) · **CM-audit/GPL** (data in hand) — new kits on the same worker.
- Route LLM through `model_gateway` (currently direct to proxy).
- Landing-page Hinode card (currently a nav entry → war-room).
