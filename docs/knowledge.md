# Kuwait Ambulance Green Wave — System Handbook

## What this programme is
The Kuwait Ambulance Green Wave is a traffic-signal preemption simulation for
Kuwait. It simulates, on the real Kuwaiti road network, a system in which
traffic-light cameras detect an ambulance running its emergency lights and the
traffic-management centre opens a "green corridor" along the ambulance's
route: each signal ahead turns green for the ambulance's approach (so the cars
in front of it clear the way) while cross approaches flash amber (yield),
hardening to red as the unit closes in, then returns to normal after the
ambulance passes. It also gives ordinary drivers an early
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
  0.5×–16×, pause/reset, network model (downtown, all governorates, or the
  3-district showcase), and the Kuwait clock start hour. KPIs, active ambulances with lights toggles,
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
- **Protocol (/protocol)** — the full operating rulebook — lettered
  categories A–I (39 rules, A1…I4) plus a parameter appendix — including
  fail-safes, arbitration, the speed exemption, early green,
  predictive routing, data provenance and the scope-of-use disclaimer.
- **How it works (/how)** — three continuous animated loops with English and
  Arabic captions: for the ambulance, for the normal driver, and normal vs
  ambulance; plus the measured headline numbers.
- **Copilot (/copilot)** — this assistant: plain-language questions answered
  from the system's records and documentation with citations, alongside the
  Markov traffic analytics table.

## Dispatch and missions
Ambulances always originate at a hospital, under a READY-FLEET model: each
hospital stations hospital_ready_units (2) ready ambulances. A dispatch
commits one; a crew that delivers a patient rejoins the RECEIVING hospital's
pool only after unit_turnaround_s (180 s) of restocking, logged as "unit
READY again at X (n/2)". Origin "Auto" dispatches the nearest AVAILABLE
unit: one backward Dijkstra from the scene ranks every hospital's current
travel time in a single pass; only hospitals with a ready unit are
candidates, and among those within the rotation tolerance
(dispatch_rotation_tolerance, 25%) of the fastest, the one with the fewest
crews already out responds — real EMS coverage, and never a convoy of
consecutive units from a single gate. If every fleet is committed or in
turnaround, the call QUEUES — real EMS never conjures a convoy out of one
gate — and the next crew to finish turnaround responds automatically
(logged: "CALL QUEUED (position N)" then "QUEUED CALL now served after
T s"). A gate-headway rule (gate_headway_s, 8 s) makes a hospital that
just launched a unit yield to an equally-close peer, so departures never
stack at one gate. Whenever the responding hospital is not the nearest one, the full
ruling is written on the dispatch event ("X is nearest but has no ready
units (0/2 — crews on mission or in turnaround); Y responds in T s with 1/2
ready"). Live ready counts appear under every hospital marker on the map; a
CALLS-WAITING chip appears in the side panel whenever the queue is
non-empty, with the oldest call's wait. Response-time accounting starts
the clock at the CALL (a queued call's wait counts): call-to-scene p50 and
p90 — overall and per governorate — plus the worst queue wait appear on
the live map panel and in a table on the Operations page. These are the
numbers an EMS board asks for first, and the queue mechanism is measured
by them, not just narrated.
The incident scene is a map click or a named area. Background demand is
scaled by demand_factor (0.6 by default): the Kuwaiti calendar SHAPE is
kept while the vehicle count is reduced for a fluid presentation — set it
to 1.0 for the full calibrated demand. On the map, a junction under
emergency control blinks its RED lamp and ring for the entire hold (the
corridor approach's own fixture shows its green), and a live ticker shows
each action — detection confirmed, junction enabled, queue flush, call
queued/served, no-citation, arrival — the moment it happens; every pill is
a real operations event, and periodic status lines are emitted only when
their numbers actually changed. A mission has three phases: to scene; loading the
patient at the scene (40 s stop, during which the corridor is paused so cross
streets are not held for a stationary vehicle); and hot return, when the
ambulance is automatically rerouted to the nearest hospital by travel time and
the corridor follows the new route. Each phase is logged on the ambulance's
A-case; arrival is logged at the named hospital with travel time, distance,
average speed and the planned ETA.

## Detection and the green corridor (preemption)
Every signalized junction has a camera whose field of view is the physical
approach roadway up to 200 m from the stop line (camera_range_m), built by
walking the real road graph upstream. Detection is junction-side sensing:
the camera reports an ambulance only when the vehicle, with active lights,
is physically inside that field of view — never inferred from the vehicle's
own route. The first detection confirms the ambulance to the control
centre, which knows its planned route; a unit no camera has seen receives
no corridor. Signals along the route are switched when the ambulance's
ETA drops below 25 s (greenwave_lead_s), never later than 160 m out
(greenwave_min_m) and never earlier than 800 m out (greenwave_distance_m).
Switching means: 3 s of amber to conflicting traffic (yellow_time_s), a 2 s
all-red clearance (allred_time_s), then the ambulance's approach is held on
protected green while every cross approach shows a FLASHING AMBER yield
state (flash_amber) — "signal overridden, cross carefully when clear" — so
vehicles caught inside the box can drain out instead of being sealed in by a
hard red. The flash hardens to solid red when the ambulance is close in
TIME (flash_harden_eta_s, 12 s) — clearance ahead of an ambulance is a time
quantity: at speed it hardens ~180 m out, in crawling traffic the flash
persists until the unit is genuinely near — and always within
flash_harden_min_m (60 m) whatever the speed. When hardened, the cross fixtures blink RED (the physical signal state
is red — flashing red means absolute stop), so a junction under emergency
control BLINKS for its entire hold and every driver can see it is an
emergency. The transition is logged with the live distance and
time-to-junction (e.g. "AMB_8 140 m / 9 s out — cross flash hardened to
RED"); a hysteresis band keeps the junction from flickering between the
two if arbitration hands the junction to a farther unit. Queue-flush
lookahead: a CONGESTED approach ahead on the route (live Markov state) is
enabled with flush_lead_factor (2x) extra activation lead so its standing
queue drains before the unit arrives — the corridor looks after signals
ahead, not just the next one, and each early enable is justified in the
log ("enabled EARLY (queue flush): this approach is congested..."). On the map the cross
approaches' fixtures and chevrons blink amber at ~1 Hz — the blink is the
real SUMO signal state ('o'), not a cosmetic effect: background drivers
genuinely yield on it. Signals are never switched
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
Before any early green is granted the junction must be PHYSICALLY CLEAR:
no vehicle standing within junction_clear_radius_m (75 m) of the junction
centre on any approach other than the one being served, measured from live
vehicle positions. This matters because the signal's wiring map knows only
the edges wired to it — ramp stubs, service roads and parallel carriageways
feeding the same junction box are invisible to it, so "every other approach
is empty" could be true of the map while cars stood on the road. Blocked
attempts are counted in the self-audit as proximity_blocked (measured: 188
blocks against 178 grants over 900 s of dense showcase traffic, with zero
grants having another direction occupied on the road).

At a divided junction — several signal nodes carrying one name, such as a
dual-carriageway interchange — the lone-approach test covers the WHOLE
complex: internal connector edges between the sibling nodes never count as
a lone arrival, and if any sibling's external approach carried traffic
within the quiet window, no early green fires anywhere in the complex
(counted in the self-audit as complex_blocked). One physical junction,
one fairness decision.
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
weights are travel times. The same route object serves both the driver's
navigation and the signal controller.

## How the DTMC and the CTMC are used — observable functions
Every monitored corridor (the approaches to signalized junctions first, then
the major arterials; 160 corridors) is classified every 30 s into one of four
congestion states from its mean speed as a fraction of the limit: FREE
(≥ 70%), SLOW (40–70%), CONGESTED (15–40%), JAMMED (< 15%). Two chains are
estimated from those same observations and each has a distinct job:

- The **DTMC** (discrete-time Markov chain) counts transitions between
  consecutive 30-second samples into a one-step transition matrix P
  (Laplace-smoothed). Its stationary distribution π is the long-run share of
  time the corridor spends in each state — this is the "time spent
  congested" figure in the corridor analytics table, and the climatology
  baseline used in validation.
- The **CTMC** (continuous-time Markov chain) estimates a generator matrix Q
  from sojourn times and observed jumps (q_ij = jumps i→j ÷ time spent in i).
  Its transient distribution P(t) = expm(Q·t) gives the probability of each
  state at ANY future horizon t. This is what routing uses: the router weighs
  each road by its CTMC-forecast speed at the moment the ambulance will reach
  it (time-dependent Dijkstra), so routes avoid where congestion will be
  rather than where it is. The 5-minute jam probability in the analytics
  table is expm(Q·300 s) from the corridor's current state.

Validation, not assertion: every 30 s a 5-minute forecast is filed for every
corridor with enough history and later scored against the state that
actually occurred, alongside two naive baselines — persistence ("stays as it
is now") and climatology (the stationary distribution). The Copilot page
shows hit rate, Brier score and the Brier skill score (1 = perfect, 0 = no
better than the baseline, negative = worse) for this session and all-time,
with a plain verdict. At every dispatch the router also computes the
live-only route and records whether the predictive route differed and by how
much — the "predictive routing" evidence line. Results so far: on the
six-governorate peak the CTMC shows strong skill over persistence (Brier
skill ≈ 0.46 on 5,440 forecasts) because it knows transient slowdowns
revert; skill over climatology is only earned when corridors actually change
state, which requires realistic peak demand on the arterials.

Self-feeding: observations persist per scenario in data/markov_<scenario>.json
(with the scores and the routing evidence) and reload on every start, so the
matrices sharpen across sessions; corridors with fewer than 40 observations
(markov_min_obs) fall back to pooled road-class chains instead of guessing.
All the linear algebra is exact pure Python on 4×4 matrices, verified against
closed-form solutions in tests/test_markov.py. Each corridor's P, Q,
stationary distribution, counts and recent scored forecasts can be opened
from the analytics table.

## Arrival-time comparison (with vs without preemption)
Each completed run is split into free-flow driving, measured traffic delay and
measured signal wait. The "without preemption" estimate adds the expected red-
light wait at every signal on the route from that junction's real programme:
E[w] = r²/2C (r = red time, C = cycle). Because that term is quadratic in the
signal timer, the timer is the highest-weight variable; no-traffic bounds
isolate it. First measured run: 165 s with the wave vs 308 s estimated
without — 143 s recovered at signal timers across 8 junctions.

## Traffic scenarios: day type, traffic level and time of day
Demand is one flat peak-rate base scaled live each hour by a calendar and a
traffic level, both chosen on the Live Map (the simulation restarts at the
chosen hour):
- **Day type.** Weekday (Sunday–Thursday): a sharp 06:30–08:30 work/school
  peak, then congested from 13:00 through about 21:00, near-empty
  01:00–05:00. Weekend (Friday–Saturday): quiet from 01:00 until noon, then
  congested from 13:00 right through to midnight.
- **Traffic level.** Easy (×0.45 of the calibrated baseline), Medium (×1.0),
  Extreme (×1.8). Under Extreme traffic several approaches of most junctions
  are occupied at once, so the early-green rule for ordinary drivers rarely
  applies and junctions run their fair fixed timers — by design (Protocol
  D4). The Live Map shows the live share of busy junctions on fair timers,
  and a traffic check is logged every five minutes.
The demand volume is anchored to Kuwait's ~2.4 million registered vehicles
(PACI) and an operator estimate for the downtown core (a weekend evening at
Extreme ≈ 3,000+ vehicles on the core network at once); Google Maps publishes
travel times, not counts, so no count data comes from it. Early green carries
a permanent self-audit shown on the Live Map: grants, holds ended because
other traffic arrived, and fairness violations (a hold continuing after other
traffic has waited beyond the minimum green). Honesty note: until v4.1 that
counter could never increment — it was a constant dressed as a measurement,
and the "0 violations" figure quoted in earlier releases meant nothing. It
now arms on every release of an early green and counts a violation when the
junction is still showing the served phase more than 2 s after the release
command while another approach is occupied — the one way the guarantee can
actually break — alongside max_other_wait_s, the longest any other approach
waited behind an early green, which varies run to run.

## Network models and places
The programme OPENS on the 3-district showcase: because its densities are
baked into the demand rather than scaled by the clock, the city is already
full the moment the dashboard loads — measured 3.5 s from launching
run_live.py to a live frame with 734 vehicles on the streets (SUMO start
2.5 s + a 0.3 s cached-state load), versus minutes of waiting for a
clock-scaled rush hour to build up. The calendar models (downtown, metro)
remain one click away in the Network model selector for "what does a real
Tuesday at 17:00 look like".
Three models (the showcase is the DEFAULT): Downtown Kuwait City (detailed; every street; sublane model and
rescue lanes) and All governorates (metro arterials: motorways, trunks,
primary and secondary roads across Capital, Hawalli, Farwaniya, Mubarak
Al-Kabeer, Ahmadi and Jahra — 13,300 edges, 223 signalized junctions).
Showcase — 3 districts (downtown): the downtown network with three
fixed-density districts baked into the demand — a dense core keeping 100% of
routed downtown trips, a normal ring keeping ~45% and a light waterfront
keeping ~10%, each trip kept or dropped by a deterministic hash of its
vehicle id by the district its route starts in — so lone-driver early greens
and dense-junction fair timers are on screen at once. Its demand is static:
the clock does not scale it, the warm state always caches, and the Live Map
hides the day, traffic-level and start-hour controls; the district circles
are drawn on the map. Real
MoH hospitals per governorate (Amiri, Al-Sabah, Mubarak Al-Kabeer,
Farwaniya, Al-Adan, Al-Jahra). 100 named incident areas across the two
models (21 downtown, 79 metro), grouped by governorate; at startup only
places that snap to the modelled network are offered.

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

## Why an ambulance can be slow or stuck — and what the system does
Under Extreme traffic an ambulance can be physically blocked: a corridor
clears the signal, not a gridlocked junction box. The system responds three
ways. Rescue lanes: the sublane model is active on both network models, so
cars pull aside and the ambulance filters through queues. Adaptive reroute
(Protocol C8): an ambulance that advances less than 40 m in 25 s re-plans
from its current position; a corridor at least 10% + 5 s faster is applied
immediately and the signal corridor follows; if the alternatives are equally
congested it holds course and logs "checked for a faster corridor: none
exists". Preemption itself: the corridor discharges the queue in front of
the ambulance at each signal. In city-wide saturation, physics wins — which
is the honest argument for staging ambulances forward during peak hours
(future work), not a simulation defect.

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
90 s cycles generated per junction (netconvert --tls.cycle.time 90)). The
Markov chains learn from
simulated traffic here; the identical estimator ingests real detector data.

## Measured results
Ambulance runs up to +117% faster through congested corridors (same traffic,
same route, with vs without preemption); +9.1% mean network speed and −42.5%
halted vehicles from early green at identical peak demand; Dijkstra ETAs
within about 3–4% of actual travel times; motion rendering verified uniform
(zero stalls) at display frame rate; 20 ms per simulation step on the
six-governorate network. Dispatch latency: a random dispatch used to block
the server for 2.2–4.4 s (the CTMC forecast was recomputed for every edge
relaxation of every Dijkstra — thousands of matrix exponentials per
dispatch); with the per-tick forecast cache (results are exact within one
30 s sample tick) and the one-pass hospital ranking, the same dispatch
completes in well under half a second and the map never freezes on the
button. At the Extreme load (5,000+ vehicles) SUMO's own physics step is
90% of the frame cost (~330 ms typical); the server therefore runs the
step, the snapshot and the JSON serialisation in a worker thread so the
event loop — websockets, every page, the dispatch button — never blocks,
and the browser bridges a late frame by extrapolating motion. The Markov
sampling of 160 corridors, which used to land as one ~370 ms spike every
30 s, is spread across the step grid (each corridor still observed every
30 s exactly). Snapshot production batch-projects all vehicle positions in
one call, and the map renders cars from pre-baked sprites with one
multiply-add per axis per car instead of per-car projection — smooth at
5,000 cars. Scenario/mode switches rebuild SUMO off the event loop with a
progress indicator instead of freezing the screen (measured: worst frame
gap 447 ms through a full teardown, relaunch and Extreme warm-up). The
post-warm-up city state is CACHED per scenario/day/level/hour
(data/warmstate_*.xml.gz): the next start or mode switch at the same
setting loads it in seconds — cars appear immediately, no warm-up bar. A
vehicle mid-teleport reports no valid position and is excluded from the
frame — one infinite coordinate used to make the entire frame unparseable
in the browser, the hidden cause of multi-second freeze bursts during
teleport storms at Extreme.

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
