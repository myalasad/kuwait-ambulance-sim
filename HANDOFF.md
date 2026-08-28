# Session handoff — Kuwait Ambulance Green-Wave Simulation

*Written 2026-08-28, updated at v3.7. This file lets a fresh Claude Code
session (or a colleague) pick up the project without the original
conversation. Update it whenever a session ends with open items.*

## What this project is

A SUMO 1.27 + TraCI simulation of ambulance traffic-signal preemption on real
Kuwait road networks, with a live web dashboard, built to pitch to MOI and a
Huawei executive board. Everything must be **observable and real — nothing
"just for SHOW"**: real street/junction names, real hospitals, all six
governorates, honest measured claims.

- Repo: https://github.com/myalasad/kuwait-ambulance-sim (public; every
  version tagged and released, v1.0 … v3.5 — release notes carry the measured
  numbers, cite them instead of re-measuring)
- Run: `.venv/bin/python run_live.py --port 8642` (the owner runs this in
  their own terminal with `ANTHROPIC_API_KEY` exported for the copilot)
- Pages: `/` live map · `/driver` phone view · `/navigation` Dijkstra/nodal ·
  `/operations` ops log + cases + DMM · `/protocol` rulebook (categories A–I)
  · `/how` 3-scene explainer · `/copilot` RAG Q&A + Markov skill
- Handbook (RAG corpus + human docs): `docs/knowledge.md`
- Tests: `python -m pytest tests/` (closed-form DTMC/CTMC validation)

## Architecture map (one line each)

| Module | Role |
|---|---|
| `sim/config.py` | SCENARIOS (downtown/metro), SimConfig knobs, hospitals/areas |
| `sim/runner.py` | Simulation orchestrator: hourly demand scale, snapshots, step loop |
| `sim/preemption.py` | Green-wave controller: phase-hold preemption, DMM arbitration, camera/enforcement events |
| `sim/actuation.py` | Demand-responsive early green with 120 m upstream detection zones + fairness self-audit |
| `sim/ambulance.py` | Dispatcher: hospital-only origins, two-leg missions, insertion watchdog, progress-based stuck detection + reroute |
| `sim/router.py` | Time-dependent Dijkstra (CTMC-predicted speeds), nodal analysis |
| `sim/markov.py` | DTMC + CTMC per corridor, forecast ledger scored vs persistence/climatology (Brier skill) |
| `sim/places.py` | Real-name registry: OSM names, J-codes, "Street × Street" junctions |
| `sim/operations.py` | Ops log ring + JSONL persistence + P/A/D case counters |
| `sim/traffic_profile.py` | Kuwaiti weekday/weekend hourly calendar × easy/medium/extreme levels |
| `rag/` | BM25 + entity filters, Haiku 4.5 → Sonnet 5 tiering, extractive fallback without key |
| `web/server.py` | Hub: warm-up fast-forward, absolute-clock pacing, all /api endpoints |
| `web/static/` | Leaflet + canvas: A/B snapshot interpolation, 3-lamp fixtures, 3D cars |
| `scripts/build_network.py` | netconvert + randomTrips + vtypes/sumocfg generation (per scenario) |

## Current state (v3.7, all committed and released)

v3.7 — smooth at 5,000+ vehicles: SUMO step/snapshot/serialise run in a
worker thread (event loop never blocks; mode switches show progress, worst
frame gap 447 ms through a full rebuild); teleporting vehicles no longer
poison JSON frames with infinite coordinates (the hidden freeze cause);
Markov sampling staggered (no 370 ms tick spike); snapshot 55->9 ms
(batched projection, lean subscriptions); client renders cars from baked
sprites with world-coordinate interpolation (sub-pixel verified); hospital
READY-FLEET model (2 units/hospital, 180 s turnaround, live badges) ends
same-gate convoys. Live proof at Extreme: frames p50 500/p95 551/max 817 ms
across 3 dispatches.

## Previous state (v3.6)

v3.6 (2026-08-28) — the no-freeze + realism release:

1. **Dispatch freeze fixed at the root** — the CTMC forecast was recomputed
   for every Dijkstra edge relaxation (2.2–4.4 s blocking the frame loop);
   now cached per 30 s sample tick (unit-tested), hospital ranking is ONE
   backward Dijkstra (`Router.cost_from_many`), the return leg is one
   forward multi-target Dijkstra (`route_to_many`), signal programmes are
   cached. Measured: ~220 ms per dispatch, worst live ws frame gap 736 ms.
2. **Nearest-AVAILABLE-unit rotation** — hospitals ranked in one pass;
   within `dispatch_rotation_tolerance` (25%) the least-loaded hospital
   wins; the ruling is logged on the dispatch event.
3. **Flashing-amber preemption** — cross approaches carry SUMO's real 'o'
   yield state during a hold, hardening to red at `flash_harden_eta_s`
   (12 s) / `flash_harden_min_m` (60 m) with hysteresis. A/B (2 samples ×
   4 identical seeded missions): corridor parity with hard red, flash
   visible ~60% of hold time. Protocol rule A6; all viewers blink it.

v3.4/v3.5 anti-freeze stack underneath: warm-up fast-forward (~29 s wall),
assertive ambulance vType, insertion watchdog (re-place at 60 s; a failed
re-place now closes the case), progress-based stuck detection with honest
"no faster corridor exists" events.

## Open items

1. **Extreme-gridlock physics limit** — at weekend Extreme one mission took
   1992 s for 6.6 km with repeated legitimate "no faster corridor exists"
   verdicts. Offered but not yet requested: **peak-hour ambulance staging**
   (pre-positioned units inside congested districts, nearest-staged-unit
   dispatch, before/after measurement). Build it only when the owner says go.
2. The copilot needs `ANTHROPIC_API_KEY` in the server's environment for
   generative answers (extractive fallback works without it).

## Working rules for assistant sessions

- **Never kill or restart the owner's server on port 8642.** Verify with
  headless runs or a temporary server on port 8643, and kill 8643 when done.
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
