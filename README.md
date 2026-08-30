# Kuwait City Ambulance Green Wave

A microscopic traffic simulation of **downtown Kuwait City** (real road network
from OpenStreetMap, bounded roughly by the First Ring Road and the Arabian Gulf)
in which **traffic-light cameras detect an ambulance running its emergency
lights** and the traffic-management centre opens a **green corridor** along the
ambulance's route: each signal ahead switches — after a proper amber
transition and an all-red clearance — to green for the ambulance's approach,
so the queue in front of it discharges, while every conflicting movement is
held on **solid red** for the whole hold. Once the ambulance passes, each
junction returns to its normal programme.

**showcase** — the downtown network with three fixed-density districts baked
into its demand — is the **default of three scenarios** (`sim/config.py`:
`scenario: str = "showcase"`); **downtown** (the detailed core) and **metro**
(all six governorates) are the others. The scenario is chosen in code via
`SimConfig(scenario=...)`: there is no `--scenario` flag on `run_live.py` or
`run_headless.py`, and the dashboard's network-model selector was removed in
v4.6.

Built on [Eclipse SUMO](https://eclipse.dev/sumo/) (the industry-standard
traffic microsimulator) controlled live from Python via **TraCI**.

## The control model

1. **Camera detection** — every signalized junction carries a camera whose
   field of view is the real approach roadway, built by walking the road
   graph up to `camera_range_m` (200 m) upstream of the stop lines;
   detection fires only when the unit with active lights is physically
   inside that field of view, never inferred from the vehicle's own route
   or position feed.
2. **Confirmation** — the first camera hit confirms the vehicle to the control
   centre, which knows the dispatched route.
3. **Green wave** — signals along the route are preempted in sequence, based
   on the ambulance's **ETA** (`greenwave_lead_s`, 25 s), never earlier than
   `greenwave_distance_m` (800 m) nor later than `greenwave_min_m` (160 m)
   out. ETA-based activation matters: a fixed distance would let an ambulance
   crawling through a jam hold junctions ahead for minutes and gridlock the
   cross streets.
4. **Preempting a junction** = amber (3 s) for conflicting greens, an all-red
   clearance interval (2 s), then the held state goes on: the ambulance's
   approach on **protected green**, every other approach on **solid red**
   for the whole hold. The held state is **built explicitly**, never
   inherited from a programme phase — a real phase can leave a compatible
   cross flow green, and while an ambulance is coming through, everything
   not on its route must read STOP. The all-red is what makes the solid red
   safe: the junction box is emptied *before* the hold seals it. Signals are
   never switched dark. A flashing-amber (yield) hold for cross approaches,
   hardening to solid red within `flash_harden_eta_s` (12 s ETA) or
   `flash_harden_min_m` (60 m), exists as the opt-in `flash_amber = True`
   path; it is **off by default** (`flash_amber = False`, i.e. solid red).
5. **Guards** — a single hold is capped at `max_hold_s` (90 s), after which
   cross traffic is guaranteed a normal cycle (`preempt_cooldown_s`).
   Distance is *not* an exemption — a unit stalled short of the stop line
   still hits the cap. The one exemption is a unit physically inside the
   junction box, which may not be sealed in by a red mid-crossing, and even
   that is bounded: once it has stood still in there for 10 s
   (`BOX_STALL_RELEASE_S`) it is no longer crossing, the cap applies, and
   the release cycles the cross streets — which is what actually drains the
   box.
6. **Recovery** — after the ambulance passes (+2 s clearance) the junction
   ambers down and resumes its normal signal plan.

All parameters live in [sim/config.py](sim/config.py).

## Setup (macOS / Linux / Windows)

```bash
cd kuwait-ambulance-sim
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/download_map.py       # fetch downtown Kuwait City from OSM
python scripts/build_network.py      # netconvert + 3 h background traffic (cfg.demand_hours)
```

`build_network.py` targets the **downtown** scenario by default; `--scenario
metro` builds the all-six-governorates network the same way. **showcase**
(the 3-district demo) is *derived* from the downtown demand and is built by
`python scripts/build_showcase.py` — `build_network.py` refuses it by
argparse choice. Since showcase is what the simulation *runs* by default,
build it too. Which scenario then runs is chosen with
`SimConfig(scenario=...)` in code.

## Run

**Live website** (seven pages, all served from one local server):

```bash
python run_live.py                   # open http://127.0.0.1:8642
```

| Page | What it does |
|---|---|
| `/` **Live Map** | Kuwait City map with 3D-look signal housings and ambulance sprites, Dijkstra route overlays, per-approach signal heads, "PURPOSELY ENABLED / BACK TO NORMAL" junction labels, per-ambulance lights toggles, dispatch controls, Kuwait sim clock, priority-conflict banner with supervisor grant buttons. A junction under emergency control blinks its **amber** lamp for the whole hold — that blink is the map's "emergency in progress here" indicator, not a yield state shown to drivers, who see solid red on every approach off the route |
| `/driver` | The phone in the ambulance cab: heading-up navigation on the shared route, "next signal will be GREEN" card, live speed beside the posted-limit sign with the EMERGENCY EXEMPT badge, arrival time on the Kuwait clock |
| `/how` | A continuously looping, bilingual (EN/AR) animated explainer of the whole cycle — camera detection, amber + all-red, corridor green, the no-fine speed exemption, back to normal — built for non-engineers |
| `/navigation` | The Dijkstra corridor per ambulance: route on the network, node-by-node analysis (distance, ETA, signals, live preemption state) |
| `/operations` | Real-time typed operations feed with severity filters and search, the full case ledger (P/A/D cases with durations and outcomes), pending supervisor decisions |
| `/protocol` | The complete operating rulebook: dual-ambulance arbitration, operator referral, error fail-safes, why-did-it-disappear guarantees, data provenance, scope-of-use |
| `/copilot` | Operations Copilot: retrieval-backed bilingual Q&A over the project's own docs and live operations data (`rag/`), plus per-corridor Markov-chain views |

Every operation also persists to `data/operations.jsonl` for after-action
review.

**Keep it running forever** (independent of any Claude session or usage
limits — this is a plain local application):

```bash
./scripts/run_forever.sh             # restart-on-crash watchdog
./scripts/install_autostart.sh       # macOS LaunchAgent: start at login, keep alive
```

**Headless comparison** (same seed, same dispatches, preemption on vs off):

```bash
python run_headless.py --compare --minutes 10
```

It runs whatever `SimConfig()` defaults to — today the **showcase**
scenario, not downtown, whatever its own docstring says. A downtown A/B
needs the default changed in code first; any published downtown comparison
figure predates that default change and should be re-measured before it is
quoted.

**Self-contained replay page** (no server, no internet, no map tiles — the
road network itself is embedded; share the file or host it anywhere):

```bash
python run_headless.py --minutes 10 --replay replay.html
```

In VS Code: open the folder, accept the `.venv` interpreter, and use the
ready-made launch configurations in the Run & Debug panel.

## Project layout

| Path | Purpose |
|---|---|
| `scripts/download_map.py` | Overpass API extract of downtown Kuwait City |
| `scripts/build_network.py` | netconvert → SUMO net, randomTrips background traffic, ambulance vType, scenario config |
| `scripts/build_showcase.py` | showcase scenario: three fixed-density districts baked into the downtown demand |
| `sim/config.py` | all tunable parameters (camera range, wave distance, amber times, hospitals) |
| `sim/preemption.py` | camera detection + green-wave controller (per-junction state machine: amber → preempt → clear → amber → normal) |
| `sim/ambulance.py` | dispatcher: lat/lon → nearest edge, routing, insertion |
| `sim/router.py` | own Dijkstra over the edge graph — one-way streets and turn restrictions respected, live/predicted travel-time weights |
| `sim/actuation.py` | demand-responsive early green for ordinary traffic |
| `sim/markov.py` | self-feeding Markov-chain traffic predictor (DTMC + CTMC) with validated forecasts |
| `sim/places.py` | real-name registry: human labels for junctions, roads and corridors |
| `sim/operations.py` | structured operations log and P/A/D case tracking |
| `sim/traffic_profile.py` | Kuwait demand calendar and traffic-level presets |
| `sim/sumo_env.py` | locates the SUMO installation and exposes its binaries |
| `sim/runner.py` | TraCI wrapper: stepping, subscriptions, snapshots for the web layer |
| `sim/metrics.py` | ambulance run KPIs |
| `rag/` | Operations Copilot retrieval pipeline: ingest, index, answer, evaluate |
| `web/server.py` | FastAPI + WebSocket live dashboard backend |
| `web/static/` (`index` / `driver` / `how` / `navigation` / `operations` / `protocol` / `copilot` `.html`) | the seven live pages — Live Map (Leaflet + canvas overlay) and the rest |
| `web/replay_export.py`, `web/static/replay_template.html` | self-contained replay page generator |
| `run_live.py`, `run_headless.py` | entry points |

## Routing

Dispatch computes each route with our own **Dijkstra** over the network's
edge graph ([sim/router.py](sim/router.py)) — one-way streets and OSM turn
restrictions respected, edges weighted by **live travel times** and, where the
Markov predictor has enough history (`markov_routing`, on by default), by the
**anticipated** travel time at the arrival horizon — `E[L/v]`, not `L/E[v]`,
so a bimodal free/jammed forecast is not read optimistically
([sim/markov.py](sim/markov.py), `predicted_traveltime`). The route is then
assigned to the vehicle via TraCI. The signal controller reads the same route
(via `getNextTLS`), which is how a junction knows the ambulance is coming
before its camera sees it: detection confirms, the route predicts.

## Demand calibration ("real Kuwaiti traffic")

On **downtown** and **metro**, background traffic follows an hourly profile
calibrated to published Kuwaiti weekday patterns (07:00 morning peak by
default; see [sim/traffic_profile.py](sim/traffic_profile.py) for the full
provenance statement), applied live as SUMO's demand scale and re-scaled as
the sim clock advances. `day_type` / `traffic_level` / `start_hour` /
`demand_hours` in [sim/config.py](sim/config.py) select the day shape,
intensity and time of day.

The **default showcase scenario does none of that**: its three district
densities are baked into the route file (`static_demand: True`), the scale is
pinned at 1.000 and the clock never scales it — which is why it opens dense
and warm-starts from cache. The day, traffic-level and clock controls that
used to sit on the dashboard were removed in v4.6 for exactly this reason.

Kuwait exposes no public live traffic feed, so the profile is *calibrated,
not measured*. When MOI/Municipality counts are available, put them in
`data/real_counts.csv` (`hour,multiplier`) and restart — they are read at
simulation start and override the weekday shape.

## Versions

- `v1.0` — green-wave core: camera detection, ETA-based wave, phase-hold
  preemption, live map, replay export, validated comparison harness.
- `v2.0` — operations & cases system, dual-priority arbitration with operator
  referral and supervisor override, own Dijkstra router + navigation page,
  protocol page, Kuwait demand calendar, 3D map visuals, lights control,
  watchdog/autostart.
- `v2.1`–`v4.7` — see `git tag` / `git log --oneline`. Each release message
  states the claims measured *at that time*; several describe behaviour later
  replaced (notably the flashing-amber hold, retired in favour of solid red
  in v4.3, and the pre-v4.2 downtown default). Read `docs/knowledge.md` and
  `HANDOFF.md` for the current state, not the older release notes.

To push to GitHub (needs your login once):
`brew install gh && gh auth login && gh repo create kuwait-ambulance-sim --private --source . --push && git push --tags`

## Notes & realism caveats

- Signal placement comes from OSM `highway=traffic_signals` tags plus
  netconvert's guessing; downtown Kuwait City is well mapped, but individual
  signal plans are synthetic (static programs), not the real KMoI timings.
- Ambulances carry the emergency speed exemption: up to 150% of the posted
  limit (capped at 140 km/h) with lights active; every enforcement camera
  logs the exempt pass with "NO CITATION issued". At signals they still
  benefit from the corridor rather than red-running.
- SUMO's sublane model is **on by default** (`lateral_resolution = 0.8` in
  `sim/config.py`), so cars form rescue lanes for the blue-light device; set
  it to `0.0` to disable it if the simulation feels slow.
- Map data © OpenStreetMap contributors (ODbL).
