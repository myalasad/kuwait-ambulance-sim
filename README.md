# Kuwait City Ambulance Green Wave

A microscopic traffic simulation of **downtown Kuwait City** (real road network
from OpenStreetMap, bounded roughly by the First Ring Road and the Arabian Gulf)
in which **traffic-light cameras detect an ambulance running its emergency
lights** and the traffic-management centre opens a **green corridor** along the
ambulance's route: each signal ahead switches — after a proper amber
transition — to green for the ambulance's approach, so the queue in front of it
discharges, while every conflicting movement is held red. Once the ambulance
passes, each junction returns to its normal programme.

Built on [Eclipse SUMO](https://eclipse.dev/sumo/) (the industry-standard
traffic microsimulator) controlled live from Python via **TraCI**.

## The control model

1. **Camera detection** — every signalized junction has a virtual camera that
   recognises an ambulance with active lights up to `camera_range_m` (200 m)
   along its approaches.
2. **Confirmation** — the first camera hit confirms the vehicle to the control
   centre, which knows the dispatched route.
3. **Green wave** — signals along the route are preempted in sequence, based
   on the ambulance's **ETA** (`greenwave_lead_s`, 25 s), never earlier than
   `greenwave_distance_m` (800 m) nor later than `greenwave_min_m` (160 m)
   out. ETA-based activation matters: a fixed distance would let an ambulance
   crawling through a jam hold junctions ahead for minutes and gridlock the
   cross streets.
4. **Preempting a junction** = amber (3 s) for conflicting greens, an all-red
   clearance interval (2 s) so vehicles trapped in the box can leave, then the
   controller **jumps to and holds the real programme phase** that serves the
   ambulance's approach green — the queue in front of the ambulance
   discharges, conflicts wait at red. Holding a real phase (rather than a
   hand-crafted "everything red" state) is what real preemption controllers
   do, and it keeps compatible movements and drain paths alive so the
   intersection cannot deadlock itself. Signals are never switched dark —
   dark signals cause collisions.
5. **Guards** — a single hold is capped at `max_hold_s` (90 s), after which
   cross traffic is guaranteed a normal cycle (`preempt_cooldown_s`) unless
   the ambulance is already at the stop line; the hold also persists while
   the ambulance is physically inside the junction, so a restoring signal can
   never box it in mid-crossing.
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
python scripts/build_network.py      # netconvert + 2 h background traffic
```

## Run

**Live website** (Leaflet map of Kuwait City, moving traffic, per-approach
signal states, dispatch controls, KPIs, control-centre event log):

```bash
python run_live.py                   # open http://127.0.0.1:8642
```

- Pick an origin (Amiri Hospital by default), click **Dispatch**, then click a
  destination on the map — or use **random destination**.
- Toggle **Signal preemption** off to watch the same city without the green
  wave; the event log narrates every camera detection and junction preemption.

**Headless comparison** (same seed, same dispatches, preemption on vs off):

```bash
python run_headless.py --compare --minutes 10
```

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
| `sim/config.py` | all tunable parameters (camera range, wave distance, amber times, hospitals) |
| `sim/preemption.py` | camera detection + green-wave controller (per-junction state machine: amber → preempt → clear → amber → normal) |
| `sim/ambulance.py` | dispatcher: lat/lon → nearest edge, routing, insertion |
| `sim/runner.py` | TraCI wrapper: stepping, subscriptions, snapshots for the web layer |
| `sim/metrics.py` | ambulance run KPIs |
| `web/server.py` | FastAPI + WebSocket live dashboard backend |
| `web/static/index.html` | live dashboard (Leaflet + canvas overlay) |
| `web/replay_export.py`, `web/static/replay_template.html` | self-contained replay page generator |
| `run_live.py`, `run_headless.py` | entry points |

## Notes & realism caveats

- Signal placement comes from OSM `highway=traffic_signals` tags plus
  netconvert's guessing; downtown Kuwait City is well mapped, but individual
  signal plans are synthetic (static programs), not the real KMoI timings.
- The ambulance obeys signals like any vehicle — the measured benefit is the
  green wave itself, not red-running.
- SUMO's sublane model is **on by default** (`lateral_resolution = 0.8` in
  `sim/config.py`), so cars form rescue lanes for the blue-light device; set
  it to `0.0` to disable it if the simulation feels slow.
- Map data © OpenStreetMap contributors (ODbL).
