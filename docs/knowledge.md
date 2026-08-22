# Kuwait Ambulance Green Wave — System Handbook

## What this programme is
The Kuwait Ambulance Green Wave is a traffic-signal preemption simulation for
Kuwait. It simulates, on the real Kuwaiti road network, a system in which
traffic-light cameras detect an ambulance running its emergency lights and the
traffic-management centre opens a "green corridor" along the ambulance's
route: each signal ahead turns green for the ambulance's approach (so the cars
in front of it clear the way) and red for conflicting traffic, then returns
to normal after the ambulance passes. It also gives ordinary drivers an early
green when they are alone at an empty junction, routes ambulances with
Dijkstra, predicts congestion with Markov chains, records every decision as an
auditable case, and explains itself through a set of web pages. It is a
planning, evaluation and training simulator — not a certified live control
system.

## Who built it and where it lives
Created for a Kuwait traffic-engineering initiative by Mohammed Al-Asad with
Claude (Anthropic). Public GitHub repository:
https://github.com/myalasad/kuwait-ambulance-sim — releases v1.0 through
the current version carry notes on what each added and the measured results.
The code is Python (SUMO/TraCI) with a FastAPI web server and plain HTML/JS
pages; it opens in VS Code with ready-made launch configurations.

## Technology stack
- Eclipse SUMO 1.27 microsimulation (installed via pip as eclipse-sumo),
  controlled live through the TraCI API from Python 3.9.
- Road networks from OpenStreetMap via the Overpass API, converted with
  netconvert; background traffic generated with randomTrips and scaled live.
- FastAPI + WebSocket server (run_live.py) streaming simulation snapshots to
  Leaflet/canvas web pages; no build step, no Node.
- Pure-Python algorithms: Dijkstra router, Markov predictor (DTMC + CTMC),
  BM25 retrieval for the copilot; the Anthropic API (Haiku 4.5 / Sonnet 5) for
  synthesized copilot answers when a key is present.

## How to install and run
1. Clone the repository and create a virtual environment:
   `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
2. Download the map: `.venv/bin/python scripts/download_map.py`
   (add `--scenario metro` for the all-governorates model).
3. Build the network and demand: `.venv/bin/python scripts/build_network.py`
   (again `--scenario metro` for the metro model). Pre-built network files
   ship in the repository, so steps 2–3 can be skipped.
4. Start the dashboard: `.venv/bin/python run_live.py`, then open
   http://127.0.0.1:8642 in a browser.
5. Headless comparison runs: `.venv/bin/python run_headless.py --compare`
   prints ambulance travel times with vs without preemption and can export a
   self-contained replay page with `--replay replay.html`.
6. The copilot answers in grounded mode when the environment variable
   ANTHROPIC_API_KEY is set in the terminal that starts the server;
   without a key it runs in free extractive mode showing matching records.
7. `scripts/run_forever.sh` restarts the server automatically if it crashes;
   `scripts/install_autostart.sh` installs a macOS LaunchAgent so it starts
   at login.

## The pages (web interface)
- **Live Map (/)** — the real road network on a dark map: 3D cars, one
  3-lamp signal fixture per approach with a colour-coded direction chevron,
  junction name tags (J-codes and real street names), ambulance vans with
  flashing light bars and their dashed Dijkstra corridor, labels such as
  PURPOSELY ENABLED, EARLY GREEN, BACK TO NORMAL. Controls: dispatch
  (origin hospital or auto-nearest, incident scene from a map click or a
  named area), preemption on/off, follow ambulance, simulation speed
  0.5×–16×, pause/reset, network model (downtown or all governorates), and
  the Kuwait clock start hour. KPIs, active ambulances with lights toggles,
  the operations feed and a legend are in the side panel.
- **Driver (/driver)** — the phone mounted in the ambulance: heading-up
  navigation with the corridor drawn ahead, the next signal's state ("will
  be GREEN"), speed beside the posted-limit sign with an EMERGENCY EXEMPT
  badge, arrival time on the Kuwait clock, and the mission phase (to scene,
  loading patient, to hospital).
- **Navigation (/navigation)** — the selected ambulance's Dijkstra route on
  a network mini-map and a node-by-node table: junction name, street,
  cumulative distance, ETA, and whether the signal is purposely enabled.
- **Operations (/operations)** — the real-time operations feed (filterable
  by warnings, errors, decisions, text), the case ledger (P-, A- and
  D-cases with status, duration, outcome), pending supervisor decisions
  with grant buttons, the arrival-time comparison (with vs without
  preemption) and the Decision-Making Matrix with a live feed of rulings.
- **Protocol (/protocol)** — the full operating rulebook, numbered sections
  1–9, including fail-safes, arbitration, the speed exemption, early green,
  predictive routing, data provenance and the scope-of-use disclaimer.
- **How it works (/how)** — three continuous animated loops with English and
  Arabic captions: for the ambulance, for the normal driver, and normal vs
  ambulance; plus the measured headline numbers.
- **Copilot (/copilot)** — this assistant: plain-language questions answered
  from the system's records and documentation with citations, alongside the
  Markov traffic analytics table.

## Dispatch and missions
Ambulances always originate at a hospital. Origin "Auto" picks the hospital
with the shortest Dijkstra travel time to the scene. The incident scene is a
map click or a named area. A mission has three phases: to scene; loading the
patient at the scene (40 s stop, during which the corridor is paused so cross
streets are not held for a stationary vehicle); and hot return, when the
ambulance is automatically rerouted to the nearest hospital by travel time and
the corridor follows the new route. Each phase is logged on the ambulance's
A-case; arrival is logged at the named hospital with travel time, distance,
average speed and the planned ETA.

## Detection and the green corridor (preemption)
Every signalized junction has a virtual camera that recognises an ambulance
with active lights up to 200 m along its approaches (camera_range_m). The
first detection confirms the ambulance to the control centre, which knows its
planned route. Signals along the route are switched when the ambulance's
ETA drops below 25 s (greenwave_lead_s), never later than 160 m out
(greenwave_min_m) and never earlier than 800 m out (greenwave_distance_m).
Switching means: 3 s of amber to conflicting traffic (yellow_time_s), a 2 s
all-red clearance (allred_time_s), then the junction's own programme phase
that serves the ambulance's approach is held. Signals are never switched
dark. After the ambulance passes (plus 2 s clearance, and only once it is
physically out of the junction box) the junction returns to its normal
programme through amber. A single hold is capped at 90 s (max_hold_s); after
that cross traffic gets at least 20 s of normal cycling (preempt_cooldown_s).
The map labels such a junction PURPOSELY ENABLED and then BACK TO NORMAL.

## Two ambulances at one junction (arbitration)
A junction already serving a corridor keeps serving it (continuity rule). Two
fresh requests with a distance margin greater than 20 m (arbitration_tie_m):
the nearest is granted, the other queues, and the arbitration is logged. A
margin of 20 m or less is a tie: the controller declares itself unable to
decide, leaves the junction on its normal programme (the safe state), opens a
D-case and refers the choice to the operator with grant buttons; if nobody
decides within 8 s (operator_timeout_s) the default policy grants the nearest.

## The Decision-Making Matrix (DMM)
The controller rules lexicographically — a higher criterion always beats a
lower one, so every ruling is deterministic and auditable: SAFETY (fail-safe
on errors, never restore over an occupied box) > CONTINUITY (a junction keeps
its current corridor) > SIGNAL TIMER / PROXIMITY (enable at ETA ≤ 25 s;
nearest wins with a clear margin) > FAIRNESS (90 s hold cap, cooldown) >
HUMAN REFERRAL (ties go to the supervisor, 8 s timeout to policy). The matrix
table on the Operations page lists each situation, its deciding criterion,
the ruling and where it is logged, with a live feed of actual rulings.

## Early green for ordinary drivers (demand-responsive signals)
When exactly one approach of a junction is occupied and every other approach
has been empty for 3 s (lone_confirm_s), the junction moves through its own
amber to the phase serving that approach. The early green ends the moment any
other approach becomes occupied (after a 5 s minimum green, lone_min_green_s),
when the lone traffic has passed, or at the 30 s cap (lone_max_hold_s); then
the normal fair timer resumes. A 10 s per-junction cooldown
(actuation_cooldown_s) prevents flip-flopping. Ambulance preemption always
outranks early green. Measured at identical peak demand: +9.1% mean network
speed and −42.5% vehicles sitting halted.

## Speed-limit exemption — no fines
A dispatched ambulance with active lights may run at up to 150% of the posted
limit (speed_exemption_factor), capped at 140 km/h (ambulance_max_kmh). The
junction camera doubles as an enforcement camera: when it measures the
ambulance above the limit with lights on, it logs "exemption applies, NO
CITATION issued" instead of a fine. Lights off means no exemption.

## Routing (Dijkstra) and predictive routing (Markov chains)
Routes are computed by the system's own Dijkstra over the road network's
edge graph, so one-way streets and turn restrictions are respected. Edge
weights are travel times. With the Markov predictor active, Dijkstra is
time-dependent: each road is weighed by its predicted speed at the moment
the ambulance will reach it, so routes avoid where congestion will be. Every
monitored corridor is a 4-state congestion chain — FREE (speed ≥ 70% of the
limit), SLOW (40–70%), CONGESTED (15–40%), JAMMED (< 15%) — estimated as a
discrete-time Markov chain (DTMC, 30 s steps, stationary distribution = share
of time spent in each state) and a continuous-time Markov chain (CTMC,
generator matrix, transient probabilities at any horizon via the matrix
exponential). Observations are taken every 30 s (markov_sample_s), persist per
scenario in data/markov_<scenario>.json and are reloaded on every start, so the
model keeps feeding itself; corridors with fewer than 40 observations
(markov_min_obs) fall back to pooled road-class chains. The same route object
serves both the driver's navigation and the signal controller.

## Arrival-time comparison (with vs without preemption)
Each completed run is split into free-flow driving, measured traffic delay and
measured signal wait. The "without preemption" estimate adds the expected red-
light wait at every signal on the route from that junction's real programme:
E[w] = r²/2C (r = red time, C = cycle). Because that term is quadratic in the
signal timer, the timer is the highest-weight variable; no-traffic bounds
isolate it. First measured run: 165 s with the wave vs 308 s estimated
without — 143 s recovered at signal timers across 8 junctions.

## Time of day and demand
One flat peak-rate demand base is scaled live each hour by a calibrated
Kuwaiti weekday profile (sharp 06:30–08:30 peak, 13:00–15:00 afternoon peak,
17:00–21:00 evening peak, near-empty 01:00–05:00). The Kuwait clock start
hour is selectable on the Live Map; the simulation restarts at that hour.
Verified: 14 vehicles on the downtown grid at 03:00 versus 202 at 07:00.

## Network models and places
Two models: Downtown Kuwait City (detailed; every street; sublane model and
rescue lanes) and All governorates (metro arterials: motorways, trunks,
primary and secondary roads across Capital, Hawalli, Farwaniya, Mubarak
Al-Kabeer, Ahmadi and Jahra — 13,300 edges, 223 signalized junctions). Real
MoH hospitals per governorate (Amiri, Al-Sabah, Mubarak Al-Kabeer,
Farwaniya, Al-Adan, Al-Jahra). About 100 named incident areas, grouped by
governorate; at startup only places that snap to the modelled network are
offered.

## Names and codes
Every road is labelled from its real OpenStreetMap name (English name when
available, otherwise the Arabic name; class-based fallback such as "Expressway
link near Shuwaikh Industrial" for unnamed connectors). Every signalized
junction gets a short code (J-001, J-002, …) plus a name from the real
streets that meet there, e.g. "J-048 · Abdullah Al-Salem Street × Al Soor
Street", a category (Motorway interchange / Arterial junction / Street
junction) and its nearest area. Case codes: P-nnn = preemption case (one
junction purposely enabled for one corridor, opened at enablement, closed at
restoration), A-nnn = ambulance case (dispatch to arrival), D-nnn = decision
case (a referred conflict). AMB_n = ambulance n of the session.

## Why an ambulance disappears or loses its lights
Nothing leaves the map silently. The close reason on the A-case is always one
of: arrived (run complete); teleported by SUMO's congestion resolver after
being physically stuck for more than 180 s (position jumps; a simulation
artefact, not a comms loss); lights switched off by the operator (it stops
requesting priority); removed unexpectedly (logged as an error); or a
dashboard reset.

## Errors and fail-safes
If any signal command fails, the junction reverts immediately to its normal
programme and the P-case closes with status error. If the simulation loop
dies, the server broadcasts the error to every dashboard and offers Reset;
the run_forever.sh watchdog restarts the process. A junction is never restored
while the ambulance is inside its box.

## Data provenance — what is real
Real: the road networks, one-way system, turn restrictions and signal
locations (OpenStreetMap), real street names, real hospital locations.
Calibrated, not measured: background demand follows the published shape of
Kuwaiti weekday traffic because Kuwait exposes no public live traffic feed;
real hourly counts (for example from the licensed xMap Kuwait road-traffic
catalog) can be dropped into data/real_counts.csv as hour,multiplier rows to
override the profile. Synthetic: individual signal timing plans (static
72–90 s cycles generated per junction). The Markov chains learn from
simulated traffic here; the identical estimator ingests real detector data.

## Measured results
Ambulance runs up to +117% faster through congested corridors (same traffic,
same route, with vs without preemption); +9.1% mean network speed and −42.5%
halted vehicles from early green at identical peak demand; Dijkstra ETAs
within about 3–4% of actual travel times; motion rendering verified uniform
(zero stalls) at display frame rate; 20 ms per simulation step on the
six-governorate network.

## The copilot itself
The copilot is a retrieval-augmented assistant. Its corpus is built from the
system's own records (one document per case per session), the protocol, this
handbook, the README, the configuration reference, module documentation and
live Markov analytics. Retrieval is hybrid: exact-entity filters (AMB ids,
case codes, junction codes) plus BM25 ranking and a recency preference — free
and local. Answers are synthesized by Claude Haiku 4.5 (escalating to Sonnet 5
for reports or when a first pass lacks citations) under strict grounding
rules: answer only from retrieved context, cite sources in brackets, say when
the record does not contain the answer. It is read-only by construction — it
can never issue signal commands. Without an API key it runs in extractive mode.

## Limitations and scope
This is a planning, evaluation and training simulator. It is not a certified
life-safety control system and must not be connected to live signal hardware
without full engineering, redundancy and regulatory review. Decisions about
real dispatch or real signal control must not be made on simulation output
alone. Residential side streets are not modelled in the metro scenario.
