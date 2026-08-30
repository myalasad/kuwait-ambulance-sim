# Session handoff — Kuwait Ambulance Green-Wave Simulation

*The state record. A fresh session should read THIS FILE FIRST and trust it
over recollection. Every claim in "Current state" was verified against the
code on the date shown; per-version history lives in `git tag -n40` and the
GitHub releases, deliberately not here.*

## What this project is

A SUMO 1.27 + TraCI simulation of ambulance traffic-signal preemption on real
Kuwait road networks, with a live web dashboard, built to pitch to MOI and a
Huawei executive board. Everything must be **observable and real — nothing
"just for SHOW"**: real street/junction names, real hospitals, all six
governorates, honest measured claims.

- Repo: https://github.com/myalasad/kuwait-ambulance-sim (public; every
  version tagged and released through v4.7 — release notes carry the
  measured numbers; cite them instead of re-measuring)
- Run: `.venv/bin/python run_live.py --port 8642` (the owner runs this in
  their own terminal with `ANTHROPIC_API_KEY` exported for the copilot)
- Pages: `/` live map · `/driver` phone view · `/navigation` Dijkstra/nodal ·
  `/operations` ops log + cases + DMM · `/protocol` rulebook (categories A–I)
  · `/how` 3-scene explainer · `/copilot` RAG Q&A + Markov skill
- Handbook (RAG corpus + human docs): `docs/knowledge.md`
- Tests: `.venv/bin/python tests/test_markov.py` (closed-form DTMC/CTMC + forecast-cache validation)

## Architecture map (one line each)

| Module | Role |
|---|---|
| `sim/config.py` | SCENARIOS (showcase=DEFAULT, downtown, metro), SimConfig knobs, hospitals/areas |
| `sim/runner.py` | Simulation orchestrator: snapshots, step loop; hourly demand scale for downtown/metro only — showcase launches `--scale 1.000` and never calls `setScale` |
| `sim/preemption.py` | Green-wave controller: the held state is built explicitly and commanded with `setRedYellowGreenState`, then the original programme+phase is restored; DMM arbitration, camera/enforcement events |
| `sim/actuation.py` | Demand-responsive early green with 120 m upstream detection zones + fairness self-audit |
| `sim/ambulance.py` | Dispatcher: hospital-only origins, two-leg missions, insertion watchdog, progress-based stuck detection + reroute |
| `sim/router.py` | Time-dependent Dijkstra (CTMC-predicted speeds), nodal analysis |
| `sim/markov.py` | DTMC + CTMC per corridor, forecast ledger scored vs persistence/climatology (Brier skill) |
| `sim/places.py` | Real-name registry: OSM names, J-codes, "Street × Street" junctions |
| `sim/operations.py` | Ops log ring + JSONL persistence + P/A/D case counters |
| `sim/traffic_profile.py` | Kuwaiti weekday/weekend hourly calendar × easy/medium/extreme levels (downtown/metro only — the showcase bakes its densities) |
| `rag/` | BM25 + entity filters, Haiku 4.5 → Sonnet 5 tiering, extractive fallback without key |
| `web/server.py` | Hub: warm-up fast-forward, absolute-clock pacing, all /api endpoints |
| `web/static/` | Leaflet + canvas: A/B snapshot interpolation, 3-lamp fixtures, 3D cars |
| `scripts/build_network.py` | netconvert + randomTrips + vtypes/sumocfg generation (per scenario) |

## Current state — VERIFIED 2026-08-30

Verified directly, not recalled:

- Last release: tag **v4.7** = **b259932**. HEAD may sit a doc commit or two
  ahead of it — check `git describe --tags` rather than trusting a hash
  written here. `data/markov_*.json` shows modified whenever the sim has
  run; that is the forecast ledger persisting itself, not stray work.
- `.venv/bin/python tests/test_markov.py` passes.
- The dashboard runs the **3-district showcase only** (dense core / normal
  ring / light waterfront). There is no model, day, traffic-level or clock
  control in the UI; `SimConfig(scenario="downtown"|"metro")` still serves
  headless runs.
- `run_live.py` watches `sim/`, `web/`, `rag/` and itself and **re-execs on
  any source change** (~3 s, cached city). `--no-reload` disables it.

Per-version history is NOT kept here - `git tag -n40` and the GitHub
releases hold it, with the measured numbers each version claimed.

### Attempted and REVERTED (do not re-attempt blindly)

One-unit-per-physical-junction preemption arbitration (union-find complex
grouping in preemption + complex-keyed operator referral + deadlock
backstop). It removed the mutual stall it targeted (459/589/474 s to
0/2/0.5 per run) but an A/B on identical seeds measured it WORSE overall:
missions completed 14 vs 20, ambulance red-light wait 1684 s vs 470 s
(signal-caused - traffic wait was flat), and it steered SUMO into a
segfault on 1 of 4 seeds. Root cause of the regression: continuity had NO
LIVENESS TEST, so a stalled incumbent (traced: 24 m from the stop line,
unmoving for 330+ s) held a whole crossing and starved three other units;
the deadlock backstop could never fire because it required 2+ holders and
the rule guaranteed 1. Reverted at b259932, never committed.

Narrower approach proposed, NOT built: (1) give continuity a liveness test
so a non-moving incumbent forfeits its claim; (2) detect the specific
mutual block (A stopped at a red held for B while B is stopped at a red
held for A) and resolve only that pair; (3) leave per-node granting alone,
since the A/B shows it is not the aggregate problem.

## Open items

1. **Extreme-gridlock physics limit** — at weekend Extreme one mission took
   1992 s for 6.6 km with repeated legitimate "no faster corridor exists"
   verdicts (measured at v3.5; the run is in that release note). Since v4.6
   there is no UI path to that load: reproducing it needs
   `SimConfig(scenario="downtown", day_type="weekend", traffic_level="extreme")`
   in a headless run, or a reset posted to `/api/command`. Offered but not yet
   requested: **peak-hour ambulance staging** (pre-positioned units inside
   congested districts, nearest-staged-unit dispatch, before/after
   measurement) — unbuilt; the only mention in the tree is `docs/knowledge.md`
   naming it future work. Build it only when the owner says go.
2. The copilot needs `ANTHROPIC_API_KEY` in the server's environment for
   generative answers (extractive fallback works without it).

## Working rules for assistant sessions

- **Never kill or restart the owner's server on port 8642.** Verify with
  headless runs or a temporary server on port 8643, and kill 8643 when done.
- Since v4.7 that rule is no longer only about kill/restart commands:
  **editing any `.py` under `sim/`, `web/` or `rag/`, or `run_live.py` itself,
  restarts his server** — the watcher closes TraCI and re-execs (~3 s). Do not
  touch watched source while he is demoing unless he confirms he started it
  with `--no-reload`.
- **Never handle API keys** — the owner exports theirs in their own terminal.
- Real names everywhere (no raw OSM ids in user-facing text); neat
  categorization; honest metrics with baselines — this is board-pitch
  material.
- Commit style: tag + GitHub release for every shipped version
  (`.tools/gh_2.97.0_macOS_arm64/bin/gh release create ...`).
- Diagnosing "still broken after a fix": first compare the running process
  start time (`ps -axo pid,lstart,command | grep run_live`) against the fix's
  commit time.

## Hard-won SUMO lessons (do not relearn these)

- Joined TLS ids get `GS_`/`cluster_` prefixes — map node→tls via
  controlled-link internal lanes, or junction lookups silently miss.
- Use `setPhase`/`setPhaseDuration` for actuation holds, never
  `setRedYellowGreenState` — otherwise preemption's save/restore breaks.
- Background vehicles at permissive `'g'` links still yield-block preempted
  corridors; grant protected `'G'` for the whole approach.
- `--scale` / `setScale` only affects *future* insertions.
- Speed-based stuck detection resets in stop-and-go creep — use odometer
  progress (`traci.vehicle.getDistance`). `time-to-teleport` never fires for
  creeping vehicles.
- SUMO's default `impatience 0` makes emergency vehicles wait forever at
  unsignalized merges.
- Overpass: use a named set (`->.roads`) or later statements overwrite the
  default set and drop turn restrictions.
