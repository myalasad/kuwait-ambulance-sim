# Answers to the 235 deck-intake questions

Source of truth: the working system at `~/Desktop/kuwait-ambulance-sim`
(v3.1), its `docs/knowledge.md` handbook, `sim/config.py`, the release notes
(v1.0–v3.1) and the live operations log `data/operations.jsonl`
(114,375 records, 52 simulation sessions).

Legend: **✅** = verified from the system · **⚠️** = honest limitation, say it
out loud · **❓** = only Mohammed can answer (logistics / preference / team).

---

## 1. Purpose, audience, format (Q1–10)

1. ❓ Not recorded anywhere in the project. **What the project files show:** the
   deck already generated (`Kuwait_Ambulance_Green_Corridor.pptx/.pdf`) was
   specified as a **ministry-grade presentation**, not a Huawei/KFAS pitch.
2. ❓ The written spec assumed **a government ministry (Interior / Public Works /
   Health)** — the people who own the signals and the ambulance fleet.
3. ❓ The stated objective in the spec: **get a funded pilot**, in this order of
   audience concern — (i) does it save minutes and can you prove the number,
   (ii) does it make the road less safe for everyone else, (iii) what does it do
   to normal traffic with no ambulance, (iv) who is accountable when it
   misbehaves, (v) what exactly are you asking for.
4. ❓ Not recorded.
5. ✅/❓ The existing deck spec is **exactly 8 slides**. Whether that is a rule
   or your choice, only you know.
6. ✅ The existing deck is written for **5 presenters** ("Presenter 1…5", no
   names used anywhere).
7. ❓ Not recorded — the 8-slide/5-presenter split implies roughly 1–2 slides
   each.
8. ❓ Your call. Speaker notes already exist per slide in the generated deck
   (opening line, 2–4 points, pre-written answers to hostile questions).
9. ❓ No rubric or template exists in the project folder.
10. ❓ No Huawei/KFAS branding assets in the project. Design direction on record:
    institutional, deep navy/charcoal opening and closing, white body slides,
    signal-green and signal-red accents used sparingly, traffic-signal head as
    the repeated motif, Calibri/Arial body, no gradients, no clip-art.

---

## 2. Proposal vs. final system (Q11–18)

11. ✅ Yes — the screenshots are the **final system, v3.1** (releases v1.0 →
    v3.1; last commit "v3.1: protocol as a categorized rulebook; Markov chains
    made observable").
12. ✅ **Present the final system.** The single-intersection concept no longer
    describes anything you built. Two selectable network models exist:
    downtown Kuwait City (detailed, **78 signalized junctions**) and all six
    governorates (**13,300 road segments, 223 signalized junctions**).
13. ✅ Yes — deliberately, in versioned steps, each with its own release note.
14. ✅ Capability-driven expansion, visible in the release history: v1.0
    green-wave core → v2.0 operations/cases/arbitration/Dijkstra → v2.3
    two-leg missions → v2.4 demand-responsive signals for *ordinary* traffic →
    v2.5 time-of-day demand → v2.6 all six governorates → v2.7 3D visuals for
    non-engineer audiences → v2.8 RAG copilot + Markov predictor → v2.9–v3.1
    real names, analytics, categorized rulebook. The driver each time was the
    same: an emergency corridor is not credible to a ministry unless you can
    also answer "what does this do to everyone else" and "what happens when it
    fails".
15. ✅ Recommended: **do not spend a slide on the earlier scope.** Present the
    final system; keep proposal→final evolution as a one-line Q&A answer
    ("the proposal scoped one junction as a proof of concept; the delivered
    system runs the real network because a corridor only exists across
    junctions").
16. ❓ Your call. Repo name: *Kuwait City Ambulance Green Wave*. Deck name
    already used: **"Kuwait Ambulance Green Corridor"** — that is the stronger
    of the two for a ministry.
17. ✅ It is the simulator UI title / project name, not an official system name.
18. ✅ **An integrated system containing both**, and this is the honest and the
    stronger positioning: an emergency green corridor *plus* demand-responsive
    signals for ordinary traffic *plus* a governance/audit layer.

---

## 3. Software and the road model (Q19–30)

19. ✅ **Eclipse SUMO 1.27** (installed via pip as `eclipse-sumo`), driven live
    from **Python 3.9 via TraCI**; **FastAPI + WebSocket** server
    (`run_live.py`); plain HTML/JS front end with **Leaflet + canvas** (no
    Node, no build step); `netconvert` and `randomTrips` from the SUMO
    toolchain; Overpass API for map extraction. **Not** used: OSMnx, NetworkX,
    Mapbox — the Dijkstra router, the Markov predictor and the BM25 retrieval
    are all **pure Python written for this project**. The copilot calls the
    Anthropic API (Claude Haiku 4.5, escalating to Sonnet 5) when a key is
    present.
20. ✅ Yes — OpenStreetMap via Overpass, converted with `netconvert`. Map data
    © OpenStreetMap contributors (ODbL).
21. ✅ **Real geometry**, not redrawn: real roads, real one-way system, real
    turn restrictions, real signal locations, real street names. Downtown model
    is bounded roughly by the First Ring Road and the Gulf. ⚠️ The metro model
    keeps **arterials only** (motorway / trunk / primary / secondary) —
    residential side streets are not modelled there. Say that plainly.
22. ✅ Metro model: **13,300 road segments (edges)**. Downtown model: every
    street inside the bound (detailed, sublane model on).
23. ✅ **78** signalized junctions downtown; **223** across the six
    governorates.
24. ✅ **Real** — one Ministry of Health general hospital per governorate at its
    real coordinates: Amiri, Al-Sabah, Mubarak Al-Kabeer, Farwaniya, Al-Adan,
    Al-Jahra (plus Dar Al Shifa as an east anchor in the downtown model).
25. ⚠️ **Synthetic vehicles on a calibrated demand shape** — not Kuwaiti
    count data. Kuwait exposes no public live traffic feed.
26. ✅ `randomTrips` generates one flat peak-rate origin–destination base
    (downtown insertion period 1.3 s; metro 0.25 s ≈ 14,400 veh/h metro-wide at
    peak), which is then **scaled live every simulated hour**
    (`traci.simulation.setScale`) by a Kuwaiti weekday profile.
27. ✅ Any start hour 00–23, selected on the Live Map (`start_hour`); the
    simulation restarts at that hour. `demand_hours` = 3 h of base demand by
    default.
28. ✅ Yes — intentional. The profile encodes a sharp 06:30–08:30 morning peak,
    a 13:00–15:00 afternoon peak, a 17:00–21:00 evening peak and near-empty
    01:00–05:00 streets. **Verified: 14 vehicles on the downtown grid at 03:00
    vs 202 at 07:00.**
29. ✅ Yes — by selecting the clock hour; the same base demand is re-scaled, so
    the runs are comparable.
30. ⚠️ From the **published shape** of Kuwaiti weekday traffic, not measured
    counts (provenance statement in `sim/traffic_profile.py`). A ministry file
    of hourly counts drops straight in as `data/real_counts.csv`
    (`hour,multiplier`) and overrides the calibrated profile. The licensed
    **xMap Kuwait road-traffic catalog** is cited in the Protocol page as the
    procurable commercial alternative.

---

## 4. The "AI" question — answer this one carefully (Q31–41)

31–33. ⚠️ **There is no trained neural network in the control loop, and you
must not imply there is.** What exists is three distinct things:

- **A deterministic, published rule system** — the Decision-Making Matrix,
  lexicographically ordered. This is the signal controller. It is
  *algorithmic*, and that is a strength in front of a ministry: every ruling is
  reproducible and auditable, which no learned policy can promise today.
- **A statistically learned predictor** — the Markov chains (below). These are
  *estimated from observed data*, they self-feed, and they are scored against
  baselines. That is legitimately machine learning in the statistical sense.
- **A genuine LLM** — the Operations Copilot is retrieval-augmented Q&A over
  the system's own records, answered by **Claude Haiku 4.5 / Sonnet 5** with
  inline citations, read-only by construction.

Recommended wording: **"an intelligent adaptive control algorithm with a
learned traffic predictor and an AI operations assistant."** Never "AI traffic
control". If a judge pushes: "the control decisions are deterministic on
purpose — a ministry has to be able to audit why a light went green."

34. ✅ **No reinforcement learning.** Still future work, exactly as the proposal
    said. Say so; it is a clean answer.
35–41. ✅ The learned model is the **Markov congestion predictor**
   (`sim/markov.py`):
   - **Inputs:** every 30 s, each monitored corridor's mean speed as a fraction
     of its posted limit (160 corridors: signal approaches first, then
     arterials).
   - **State space:** 4 states — FREE (≥70%), SLOW (40–70%), CONGESTED
     (15–40%), JAMMED (<15%).
   - **Two estimators from the same observations.** A **DTMC**: one-step
     transition matrix P (Laplace-smoothed) whose stationary distribution π is
     the long-run share of time a corridor spends in each state. A **CTMC**:
     generator matrix Q from sojourn times and observed jumps
     (q_ij = jumps i→j ÷ time spent in i), with transient distribution
     P(t) = expm(Q·t).
   - **Output / use:** the CTMC forecast speed of each edge **at the moment the
     ambulance will actually reach it** feeds a time-dependent Dijkstra — the
     route avoids where congestion *will be*, not where it is. The 5-minute jam
     probability shown in the analytics table is expm(Q·300 s).
   - **Training:** online, unsupervised, self-feeding. Observations persist per
     scenario in `data/markov_<scenario>.json` and reload every start, so the
     matrices sharpen across sessions. Corridors with fewer than 40
     observations fall back to pooled road-class chains rather than guessing.
   - **Objective:** none in the RL sense — it is a maximum-likelihood estimate
     of transition rates, not a policy optimizing a reward.
   - **Validation (you have this — use it):** every 30 s a 5-minute forecast is
     filed for each eligible corridor and later scored against what actually
     happened, against two baselines — persistence and climatology. Reported as
     hit rate, Brier score and **Brier skill score**. Result on the
     six-governorate peak: **skill ≈ 0.46 over persistence on 5,440
     forecasts**. ⚠️ Skill over climatology is only earned when corridors
     actually change state. The 4×4 linear algebra is verified against
     closed-form solutions in `tests/test_markov.py`.
   - ⚠️ It currently learns from simulated traffic. The identical estimator
     ingests real detector data unchanged — that is the deployment claim.

---

## 5. Normal signal control (Q42–56)

42. ✅ **Demand-responsive early green** (`sim/actuation.py`), not a full
    adaptive optimizer. Rule: if **exactly one** approach is occupied and every
    other approach has been empty for **3 s**, the junction moves — through its
    own amber — to the phase serving that approach. Nobody sits at a red for an
    empty crossing.
43–45. ⚠️ **Occupancy, not queue length.** Detection is a per-lane
   subscription to `LAST_STEP_VEHICLE_NUMBER`; an approach is "occupied" if any
   controlled lane has ≥1 vehicle. No waiting time, no queue-length estimate,
   no weighted combination. Say it as designed simplicity, not as a measurement:
   *"the junction only needs to know whether anyone is there."*
46. ✅ Every simulation step — 0.5 s of simulated time — using one batched
    subscription round-trip, not per-lane polling.
47. ⚠️ **No.** Cycle length is untouched (static 90 s programmes).
48. ⚠️ Only in the sense that an early green is granted and then ended; it does
    not re-compute splits.
49. ✅ Yes — it **jumps to** the phase serving the occupied approach (a phase
    skip within the junction's real programme), then returns to the normal
    timer.
50. ✅ **5 s** minimum green on an early green (`lone_min_green_s`).
51. ✅ **30 s** cap on an early green (`lone_max_hold_s`); **90 s** cap on an
    ambulance corridor hold (`max_hold_s`).
52. ✅ **3 s amber** (`yellow_time_s`) and **2 s all-red clearance**
    (`allred_time_s`), plus **2 s** of corridor hold after the ambulance passes.
53. ⚠️ **No pedestrians and no pedestrian phases** — zero crossings in the built
    network. This is a real limitation and a judge may find it. The honest
    answer: *"pedestrian phases are a required addition before any street
    trial; the architecture treats them as another conflicting movement that
    the amber/all-red sequence already protects."*
54. ✅ Three guards. (a) An early green ends **the moment any other approach
    becomes occupied** (after the 5 s minimum) — with more than one approach
    occupied, everyone gets the fair fixed timer, no favourites. (b) An
    ambulance hold is capped at **90 s**, after which cross traffic is
    guaranteed **20 s** of normal cycling unless the ambulance is already at the
    stop line. (c) Every grant, release and re-arm is logged.
55. ✅ A **10 s per-junction cooldown** between early greens
    (`actuation_cooldown_s`), plus the 5 s minimum green.
56. ✅ Fairness is the 90 s hold cap + 20 s cooldown (corridor) and the 30 s cap
    + 10 s cooldown (early green). ⚠️ There is no per-vehicle maximum-wait
    guarantee.

---

## 6. The ambulance, detection and hardware (Q57–77)

57–58. ✅ Ambulances **always originate at a hospital**. Origin "Auto" picks the
   hospital with the shortest **Dijkstra travel time** to the scene.
59. ✅ Yes — a map click, or a named area from ~100 named places grouped by
    governorate.
60. ✅ **Travel time**, not straight-line distance — one-ways, turn restrictions
    and live congestion decide. (Same for the auto-reroute to hospital.)
61. ✅ Yes — multiple ambulances run simultaneously, and the arbitration layer
    exists precisely for that (continuity → proximity → human referral).
62. ⚠️ Acknowledge instead: dual-ambulance conflict is **handled**, but you have
    only a handful of logged referral cases (3 referred, 2 decided, 1 moot in
    the current log). It is demonstrated, not statistically exercised.
63. ✅ It is a SUMO vehicle of type `ambulance` with the blue-light device;
    downtown runs use SUMO's **sublane model** (`lateral_resolution = 0.8`) so
    civilian cars form a rescue lane. Sublane is off in the metro model for
    speed.
64. ✅ Up to **150 % of the posted limit**, absolute cap **140 km/h**, only with
    lights on.
65. ⚠️ It does **not** run red lights and does not drive against traffic. It
    gets the corridor instead. The only rule relaxation is speed, and the
    enforcement camera logs *"exemption applies — NO CITATION issued"*.
66. ✅ Both — rescue-lane formation via the sublane model in the downtown model,
    **plus** signal preemption. ⚠️ The measured benefit you can defend is the
    **signal-wait component**; that is what the counterfactual isolates.
67–70. ✅ **Camera detection is the final design** (the proposal's
   proximity-tag idea was replaced). Every signalized junction has a virtual
   camera that recognises an ambulance **running its emergency lights** up to
   **200 m** along its approaches. The first camera hit **confirms** the vehicle
   to the control centre, which already knows the dispatched route — *detection
   confirms, the route predicts*, which is how a junction knows the ambulance is
   coming before its own camera sees it. Identity is by **active emergency
   lights** (visual), not ANPR, RFID, V2X or GPS beacons. The cyan dots on the
   map are the **named incident areas / scene markers**; the cyan ring on a
   junction is **EARLY GREEN**.
71–76. ✅ Deployment answer, and it is your strongest commercial line — **no new
   city-wide hardware network is requested**:
   - **Reuse:** existing junction cameras where they exist, existing signal
     controllers, existing dispatch and ambulance telemetry, existing
     communications back-haul.
   - **In the ambulance:** nothing new is strictly required by the design (the
     lights themselves are the signal). ⚠️ In practice a GPS/AVL feed from
     dispatch is the cheaper and more reliable confirmation channel than
     computer vision — see 73.
   - **Roadside:** a camera and a link at each corridor junction that lacks one.
   - **Controllers:** the system **interfaces** with existing controllers — it
     commands a **phase change inside the junction's own programme**, which is
     exactly what standard emergency-vehicle-preemption inputs already do. No
     controller replacement is implied by the architecture. ⚠️ Any real
     deployment still requires conflict-monitor hardware, fail-to-normal
     guarantees and regulatory review.
73. ✅ **Yes — GPS/dispatch data is easier than computer vision**, and you
    should say so first: dispatch already knows which vehicle is responding and
    where it is. The camera path matters because it works for vehicles the
    centre is not tracking and because it doubles as the audit and enforcement
    record. Best answer: **use both — GPS predicts, the camera confirms.**
77. ❓ Not known. Do not guess a vendor in the room. Say: *"we designed to the
    generic preemption interface every modern controller exposes; matching it
    to the deployed KMoI equipment is one of the three things we are asking for."*

---

## 7. Routing (Q78–84)

78. ✅ Yes — the project's **own Dijkstra** over the network's edge graph
    (`sim/router.py`), respecting one-ways and OSM turn restrictions. Not
    SUMO's router. The driver's screen and the signal corridor consume the
    **same route object**.
79. ✅ **Travel time.** With `markov_routing` on, the weight is the
    **CTMC-predicted** travel time on that edge **at the horizon when the
    ambulance will reach it** (time-dependent Dijkstra), not the current one.
80. ✅ Yes — live edge travel times, and the predictive weights update as the
    chains sample every 30 s. At every dispatch the router also computes the
    live-only route and **records whether the predictive route differed and by
    how much** — that is your predictive-routing evidence line.
81. ⚠️ **No.** Signal state and preemption are not in the edge cost. Worth
    naming as future work: routing that prefers a corridor you can actually
    open.
82. ✅ Yes — the mission reroutes automatically at the scene (to the nearest
    hospital by travel time) and the corridor follows the new route.
83. ✅ Honest answer: Dijkstra is **exact**, has no admissible-heuristic
    complications under time-dependent weights, and the networks are small
    enough that runtime is irrelevant (20 ms per simulation step for the whole
    six-governorate network). A* would be an optimization, not a capability
    change; contraction hierarchies pay off at continental scale with static
    weights, which is the opposite of this problem.
84. ✅ **Keep it for Q&A.** A minister does not need the algorithm name; give it
    one clause on the "what we built" slide ("our own shortest-path routing over
    the real street graph") and hold the defence in your pocket.

---

## 8. Preemption logic (Q85–100)

85–87. ✅ The five steps: **camera detection (200 m) → confirmation against the
   dispatched route → ETA-based activation → amber + all-red + phase hold →
   recovery.** Activation is by **estimated time of arrival**: the signal
   switches when the ambulance's ETA to it drops below **25 s**, but **never
   earlier than 800 m out and never later than 160 m out.** *This is the design
   choice worth defending on the slide*: a fixed-distance trigger would let an
   ambulance crawling through a jam hold junctions ahead for minutes and
   gridlock the cross streets.
88. ✅ No fixed limit — every junction on the route is armed and each preempts
    itself when its own ETA condition fires, so in practice one or two are held
    at a time while the rest are pending.
89. ✅ **Never immediately green.** Conflicting greens get **3 s amber**, then
    **2 s all-red clearance** so anything trapped in the box can leave, and only
    then the corridor phase is held.
90. ✅ Technically this is **preemption** — the normal programme is interrupted
    and a specific phase is held for a specific vehicle. It is not "priority" in
    the transit-signal-priority sense (green extension / red truncation within
    the cycle). Use **preemption** and define it in the same breath: *"the
    junction's normal programme is interrupted, then handed straight back."*
91. ✅ **Phase-hold preemption** = the controller jumps to, and holds, **the
    junction's own real programme phase** that serves the ambulance's approach —
    rather than writing a hand-crafted "everything red" state. This is what real
    preemption controllers do, and it keeps compatible movements and drain paths
    alive so the intersection cannot deadlock itself. (Implementation note:
    `setPhase`, never `setRedYellowGreenState`, so save/restore survives.)
    **Signals are never switched dark — dark signals cause collisions.**
92. ✅ Yes — if the serving phase is already green it is simply held, with no
    amber transition needed.
93. ✅ Yes — that is what the amber + all-red sequence is doing.
94. ✅ Yes — phases are skipped to reach the serving phase.
95. ✅ It never truncates an amber it has started; the amber and all-red
    intervals always run in full.
96. ✅ **Correct — it cannot.** 3 s amber and 2 s all-red are unconditional
    before any corridor green.
97. ✅ After the ambulance passes (+2 s clearance, and **only once it is
    physically out of the junction box**) the junction ambers down and resumes
    **its normal signal plan**. The map labels it "PURPOSELY ENABLED", then
    "BACK TO NORMAL".
98. ⚠️ It **resumes the normal programme** — it does not rebalance queues or
    recompute from current traffic. Recovery of the cross street then happens
    through ordinary cycling (helped by early green once demand thins). Name
    this as the honest limitation and as the obvious next feature.
99. ⚠️ Prevention is **up front, not after**: the 90 s hold cap with a 20 s
    guaranteed cross-traffic cooldown, the ETA-based trigger that refuses to
    hold early, and the corridor **pause during the 40 s patient loading** so
    cross streets are never held for a stationary ambulance. ⚠️ You have **not
    measured** post-corridor cross-street recovery — see the gap list.
100. ✅ Deterministic and published: **continuity** (a junction already serving
     one corridor keeps serving it — switching allegiance mid-approach would
     trap both streams in the box), then **proximity** (margin > 20 m → nearest
     granted, other queued, arbitration logged), then **human referral**
     (margin ≤ 20 m is a tie → the controller declares itself *unable to
     decide*, the junction **stays on its normal programme — the safe state** —
     and a D-case goes to a supervisor with one-click grant buttons; if nobody
     decides within **8 s**, the default policy grants the nearest and logs it
     as a policy decision).

---

## 9. Results — the section that decides the deck (Q101–134)

### What you actually have

✅ **42 logged arrival analyses** in the current operations log (the master
fact base cited 33; the log has since grown). Recomputed from
`data/operations.jsonl` — exported for you as
`ANSWERS_DATA_arrival_analysis.csv`:

| Metric | Value |
|---|---|
| Runs (n) | **42** |
| Arrival time **with** the corridor | mean **361 s**, sd 164 s, range 114–1073 s |
| Arrival time **without** (estimated) | mean **447 s**, sd 175 s, range 143–1102 s |
| Time saved | mean **85 s**, sd 55 s, range 0–182 s |
| Percentage saved | mean **19.7 %**, median 18.9 %, range 0–46.4 % |

Other measured figures on record:

- **Early green, network-wide, no ambulance involved** (full-peak A/B, same
  seed): **+9.1 % mean network speed, −42.5 % halted vehicles** — for *all*
  road users. This is your answer to "does this only help ambulances".
- **First measured downtown run:** 165 s with the wave vs 308 s estimated
  without — **143 s recovered at signal timers across 8 junctions**.
- **Headline ceiling:** ambulance runs up to **+117 % faster** through
  congested corridors (same traffic, same route, with vs without).
- **Markov forecast skill:** Brier skill ≈ **0.46** over persistence on 5,440
  scored 5-minute forecasts.
- **Performance:** 20 ms wall-clock per simulation step on the six-governorate
  network (~25× real time).
- **Audit volume:** 114,375 logged operations across 52 sessions. ⚠️ 111,472 of
  those are early-green events — if you quote the total, be ready for that.

### Question by question

101–103. ✅ See the table above. The line to put on the slide:
   **"430 s → 348 s"** (the 33-run figure already in the deck) or the updated
   **"447 s → 361 s, about 85 seconds saved per run, ~20 % faster"** across 42
   runs. Pick one and use it everywhere.
104. ✅ Yes — that is exactly what the analysis isolates: each run is split into
     free-flow driving, measured traffic delay and measured signal wait, and the
     "without" figure adds back the expected red-light wait at every signal on
     the route. Mean signal wait removed: **85 s**.
105. ⚠️ **Not recorded as a stop count.** Recoverable from the logs if you want
     it.
106. ✅ Per run, yes — every arrival logs distance, duration and average speed
     (e.g. 41 km/h on the 3.8 km downtown route; 92–108 km/h on cross-governorate
     arterial runs). ⚠️ No OFF-condition average speed except through the
     counterfactual.
107–108. ✅ Both logged per run: route length in km and **number of signals on
   route** (logged at dispatch, e.g. "4.1 km, 8 signals on route, ETA 168 s").
109–112. ⚠️ **This is the weakest point in the evidence and you should fix it
   before the presentation.** You have 42 runs, but they came from 52
   exploratory sessions at different hours, scenarios and demand levels — not a
   designed experiment. **Seed is fixed at 42**; there is no multi-seed
   campaign, so the mean/sd above describe *variation across different
   missions*, not sampling error on a repeated one. A seeded side-by-side
   harness **does** exist (`run_headless.py --compare`: same seed, same
   dispatches, preemption on vs off, deterministic routing so both runs drive
   identical routes) — it is simply not the source of the headline numbers.
113–114. ⚠️ Not as a controlled sweep. The machinery is there (clock hour
   selector, live demand scaling) — it has not been run as an experiment.
115–118. ⚠️ **The three-condition comparison the consultant is asking for
   (Fixed → Adaptive → Adaptive + Emergency Priority) does not exist yet.**
   Both switches exist independently (`preemption_enabled`,
   `actuation_enabled`), so all four cells are producible from the existing
   harness. **This is the single highest-value thing to run before the deck is
   designed.**
119–125. ⚠️ Network-wide vehicle counts and halted counts are collected live;
   the **+9.1 % / −42.5 %** pair is the only published network-wide result.
   Average civilian travel time, average delay, queue lengths and throughput are
   **not currently exported** — they are all standard SUMO outputs and can be
   turned on.
126–128. ⚠️ **Not measured.** You cannot currently answer "what does a corridor
   cost the cross street", and a government engineer will ask. The design
   answers (90 s cap, 20 s guaranteed cooldown, corridor paused during patient
   loading, ETA trigger) are mitigations, not measurements. **Measure this.**
129–133. ✅ The screenshot figures are a **completed ambulance run** with
   **preemption on**: *AMB_1 arrived at Dar Al Shifa Hospital, 336 s for 3.8 km,
   avg 41 km/h, planned ETA 221 s.* Its paired analysis record:
   **336 s with the green wave vs 393 s estimated without — 57 s (≈15 %) saved
   at signal timers.** ✅ And yes, you have **repeats of that exact route**:
   334 s, 336 s, 336 s across three runs (with the same 393 s counterfactual) —
   run-to-run spread of 2 s. That is a genuinely good consistency line.
   ⚠️ Note the planned ETA (221 s) badly under-estimated the actual (336 s) on
   that run — see Q230.
134. ✅ Yes. Every operation is already persisted to `data/operations.jsonl`
     (JSON Lines), and I have exported the arrival analyses as
     `ANSWERS_DATA_arrival_analysis.csv` (columns: `with_s`, `without_est_s`,
     `saved_s`, `pct_saved`) — hand that straight over for charting.

### ⚠️ Method statement — put this on the slide, do not hide it

The **"with" time is measured** in the simulation. The **"without" time is the
system's own per-junction counterfactual**: it adds back the expected red-light
wait at every signal on the route, computed from that junction's real
programme with the standard signal-delay formula **E[w] = r²/2C** (r = red
time, C = cycle). Because that term is quadratic in the signal timer, the timer
is the highest-weight variable, and no-traffic bounds isolate it. It is an
estimate, not a second run — say so before you are asked. The seeded A/B
harness is the answer to "prove it with two real runs".

---

## 10. Lives saved — what you may and may not claim (Q135–140)

135. ✅ Recommended claim: **none about mortality.** Claim minutes.
136–138. ❓ No survival-rate source exists in the project, and no mentor
   guidance is recorded.
138. ✅ Yes, external peer-reviewed sources may be used — but only to say what
     the literature says, attributed, and never multiplied into a Kuwait
     casualty figure you have not computed.
139. ✅ **Yes — avoid any direct percentage reduction in mortality.** It is
     unsupported by anything you built and it is the fastest way to lose a
     technical audience.
140. ✅ **Yes, use the safer claim:** *"we reduce a controllable component of
     emergency response time — the time an ambulance spends stopped at red
     lights — by about 85 seconds per run in simulation."* That sentence is
     defensible line by line.

---

## 11. Safety and failure (Q141–154)

141. ✅ You have a real safety story and it is one of the deck's strongest
     slides — the whole Protocol page (sections 1–9) exists for this.
142. ✅ By never writing a signal state by hand. The controller **holds one of
     the junction's own real programme phases**, whose conflict matrix
     `netconvert` already guarantees. Conflicting approaches are not
     representable in a real phase.
143–144. ✅ Both preserved unconditionally: **3 s amber, 2 s all-red**, before
   every corridor green, and amber again on the way back to normal.
145. ✅ Nothing leaves silently. Every ambulance carries an **A-case from
     dispatch to close**, and the close reason is always logged: arrived /
     teleported by SUMO's congestion resolver after >180 s stuck (a simulation
     artefact, logged as such) / lights switched off by the operator / removed
     unexpectedly (logged as an **error**) / dashboard reset.
146. ✅ In the simulator: the failure is **broadcast to every connected screen**
     (no silent freeze) and the `run_forever.sh` watchdog restarts the service.
     In a real deployment the equivalent fail-safe is the **local junction
     controller falling back to its own fixed-time plan** when the centre stops
     responding — that is the sentence a ministry wants to hear.
147. ✅ Same as 146. Plus: if **any individual signal command fails**, that
     junction is **immediately reverted to its normal programme** — never left
     frozen in a corridor state — the P-case closes with status `error`, and an
     error-severity alert reaches the operator.
148. ✅ **Yes — fail-to-normal.** The normal signal plan is the safe state
     everywhere in the design, including on an unresolved arbitration tie.
149–150. ✅ Yes. **Arm/disarm the whole preemption system with one control** —
   that is the "Signal preemption" toggle on the dashboard. Disarming releases
   every held junction **through the normal amber-down sequence**, and the
   action is logged.
151–152. ✅ Yes — `data/operations.jsonl`, structured, typed and severity-tagged
   (114,375 records, 52 sessions, 522 warnings, 5 errors, 5 decisions). Each
   preemption is its own **P-case**, opened at enablement and closed at
   restoration, so every enable **and** every return to normal is a paired,
   timed record. Searchable and filterable on the /operations page.
153. ✅ A false detection causes **unnecessary priority, not unsafe behaviour** —
     the junction still runs a legal phase with full amber and all-red, still
     obeys the 90 s cap, and still logs everything. That is the direct answer,
     and it is a good one.
154. ⚠️ **Not implemented** — in the simulator a vehicle's lights are the
     credential. The deployment answer: authenticate the *dispatch* record, not
     the vehicle's appearance — a corridor opens only for a vehicle with an open
     case in the dispatch system, cross-checked by camera confirmation. Present
     it as designed-for, not built.

---

## 12. Reading the dashboard (Q155–164)

155–156. ✅ Element by element:
   - **3-lamp signal fixture, one per approach**, standing to the right of its
     traffic with a colour-coded **direction chevron** — "who is this light
     for, and may they go", in one glyph. Plus a white **stop bar** across the
     approach.
   - **Junction housing** (the larger 3-lamp badge) — the junction summary at
     overview zoom; up close it appears as the badge for a special mode.
   - **"PURPOSELY ENABLED"** — this junction is currently held for an ambulance
     corridor. **"BACK TO NORMAL"** — it has been handed back. **"EARLY GREEN ·
     lone traffic"** (cyan) — a normal driver got an early green because they
     were alone.
   - **Ambulance van sprite** with a flashing light bar, and its **dashed
     Dijkstra corridor** drawn ahead of it.
   - **Cyan markers** — the named incident areas / selectable scenes.
   - **Hospital icons** — real MoH hospital locations.
   - **3D cars** in ten deterministic colours, rotating with their interpolated
     heading.
   - **Junction name tags** — J-codes plus real street names, e.g.
     *"J-048 · Abdullah Al-Salem Street × Al Soor Street"*.
157. ✅ **"PURPOSELY ENABLED"** is the plain-language label for *this junction
     is under preemption right now* — chosen deliberately over "preempted" so a
     non-engineer viewer reads it as intentional, not as a malfunction.
158. ✅ Typed, severity-tagged operations: dispatch, lifecycle, camera
     detection, preempt start, phase changes, restore, actuation grants and
     releases, enforcement (exempt passes, no citation), reroutes, arrivals,
     arrival analyses, arbitration, referred decisions, teleports, errors,
     system events. Filterable by warnings / errors / decisions / free text,
     with the full **case ledger** (P-, A-, D-cases with status, duration,
     outcome).
159–160. ❓ I cannot see your two screenshots. Given the version history, the
   one showing **3-lamp per-approach fixtures with direction chevrons** and
   **real street/junction names** is the later, final product (v2.7.2 / v2.9+).
161–164. ✅ All producible: the map already draws the **planned route as a
   highlighted dashed corridor** and labels **which junctions are currently
   preempted**; the /navigation page lists the route node by node with live
   preemption state. For a clean chrome-free capture, run the server and
   screenshot the page in full-screen, or use the self-contained
   `replay.html` export. Tell me when you want these captured and I will drive
   the browser and produce them.

---

## 13. Authorship, credit and novelty (Q165–180)

165–172. ❓ Only you can assign this. **What the record says:** the handbook
   states *"Created for a Kuwait traffic-engineering initiative by Mohammed
   Al-Asad with Claude (Anthropic)"*, and every git tag carries
   `Co-Authored-By: Claude`.
173. ⚠️ **Yes — the code was written with Claude, and the repository says so in
   two places (the public handbook and every commit tag).** Do not let this be
   discovered rather than declared. The strong framing, and it is true: *"the
   engineering decisions, the control model, the safety rules and the
   validation design are ours; we used an AI coding assistant to implement them
   quickly, and we documented it in the repository from day one."* You can
   defend every parameter in the system, which is the actual test.
174. ❓ No Huawei code, template, API, model or mentorship appears anywhere in
   the project. If any was given, credit it; do not invent it.
175–180. ✅ The honest novelty claim, in order of strength:
   - **Not the algorithm.** Emergency-vehicle preemption is established
     technology. Say so first — it buys you credibility for everything after.
   - **It is the integration:** dispatch → hospital selection → predictive
     routing → camera detection → corridor preemption → arbitration → audit →
     recovery, as **one working system on the real Kuwaiti network**, with the
     ordinary-traffic controller in the same loop rather than bolted on.
   - **It is the governance layer.** A published, lexicographically weighted
     Decision-Making Matrix; a machine that **declares itself unable to decide**
     and refers to a human; a case ledger where every enable is paired with its
     return to normal. That is unusual in student work and it is exactly what a
     ministry buys.
   - **It is the scale of the model** — six governorates, 223 signalized
     junctions, real geometry, running 25× faster than real time on a laptop.
   - ✅ **Yes — position it as a deployment architecture with an evidence
     harness, not as an academic traffic algorithm.**

---

## 14. Deployment path and the ask (Q181–204)

181–186. ✅ On record as the recommended path: **one pilot corridor first** —
   a single hospital-to-area corridor, simulated first, then trialled on the
   street using existing camera and controller infrastructure. Then a hospital
   district, then the Capital network. Not nationwide, not on this slide.
187–191. ❓ Not decided in the project. The realistic answer, and it is fine to
   say it: **it needs at least two ministries** — the traffic authority owns the
   signals, the health authority owns the ambulances and the dispatch data — and
   the pilot's first deliverable is the interface agreement between them.
192–194. ⚠️ **No costing, no hardware quantities, no pilot duration exist.**
   Do not invent them. Write `[NEEDS MINISTRY DATA]` on the slide, exactly as
   the existing deck spec requires — a blank marked as a known blank reads as
   discipline; an invented number reads as a bluff.
195. ✅ Suggested, derived from what you already measure: (i) measured ambulance
     signal-wait reduction on the corridor vs the pre-pilot baseline; (ii) no
     increase in cross-street delay beyond an agreed threshold; (iii) zero
     safety incidents attributable to preemption; (iv) 100 % of enable events
     paired with a logged return to normal.
196–203. ✅ **Yes — low infrastructure cost is still the central selling point,
   and it is now stronger than in the proposal**, because the detection design
   deliberately avoids V2I/RFID/DSRC. Reused: **existing junction cameras,
   existing signal controllers, existing dispatch and ambulance telemetry,
   existing back-haul.** ⚠️ Do not claim fibre/5G coverage or Huawei cloud
   assets you have not verified.
204. ❓ Your call. Recommendation: **do not force Huawei products into the
   architecture.** Present the architecture as vendor-neutral (camera → edge →
   centre → controller) and note that the edge/cloud tier is exactly where a
   Huawei stack would sit — if the audience is Huawei, that lands better than a
   product name pasted onto a box.

---

## 15. Framing, tone and design (Q205–224)

205. ❓ Your call. Vision 2035 is in the original proposal, not in the system.
     One line at most, on the closing slide.
206. ✅ Recommended: yes, but fold it into the problem slide rather than
     spending a whole slide on it.
207–210. ❓ No Kuwait government sources and no international comparables are
   collected in the project. ✅ Researching them is fine — recommendation:
   **keep comparables in Q&A**, not on the slides. On stage they invite
   "so why not just buy the American system"; in Q&A they prove the approach is
   proven.
211–216. ❓ On record for the existing deck: **institutional, ministry-grade and
   restrained** — the audience must be able to repeat every line. If the room is
   actually Huawei/KFAS, shift only the visual register, never the claims.
217–219. ✅ On record: deep navy or charcoal opening and closing, white/light
   grey body slides, **one signal-green accent** for the good state and **one
   signal-red** for the risk state, never both at full strength in the same
   block; the **traffic-signal head** as the repeated motif. Explicitly ruled
   out: accent stripes, gradient backgrounds, stock ambulance photography,
   clip-art, walls of bullets.
220–221. ❓ Unknown. Design so the deck reads perfectly with zero animation.
222–223. ❓ Your call. ✅ You *can* demo live: the server runs locally with no
   internet, and there is a **self-contained `replay.html`** (network embedded,
   no server, no map tiles, no connection) that is the safe fallback.
224. ✅ **Yes — build the deck so it stands alone.** Then the demo is upside,
     not a dependency. Take screenshots in advance regardless.

---

## 16. The story, the risks and the timing (Q225–235)

225. ❓ Your sentence to choose. The strongest one available from what you built:
     **"An ambulance should never wait at a red light — and nobody else should
     wait longer because of it."**
226. ❓ Your call. Recommendation: **lead with the ambulance, close with the
     network.** The ambulance gets the room's attention; the +9.1 % / −42.5 %
     network result is what makes it fundable.
227. ✅ Yes — with one addition, because the addition is the part that survives
     hostile questioning: *"We turn the ambulance's route into a temporary green
     corridor, hand every junction straight back to its normal programme, and
     keep the rest of the network under demand-responsive control — and every
     one of those decisions is logged."*
228. ✅ The things a judge will not expect, in order: (i) the machine
     **refuses to decide** a close call and refers it to a human within 8 s;
     (ii) **early green makes ordinary traffic 9 % faster** — the emergency
     feature is not paid for by everyone else; (iii) the whole thing is
     **auditable case by case**, 114,375 records deep; (iv) a **plain-language
     copilot** answers questions about the operations record with citations;
     (v) **six governorates at 25× real time on a laptop.**
229. ⚠️ The weakest part, stated plainly: **the baseline.** Your headline
     "without" figure is a per-junction counterfactual, not a second measured
     run; the seeded A/B harness exists but has not been run as the campaign
     that produces the headline. Second weakest: **no measurement of what a
     corridor costs the cross street.** Third: **no pedestrians in the model.**
230. ⚠️ The question I would most expect: *"Your own planned ETA was 221 s and
     the ambulance took 336 s — why should I believe your travel-time model?"*
     The honest answer: the ETA is a free-flow-plus-prediction estimate and it
     is optimistic under peak demand; the *comparison* between with and without
     is unaffected because both use the same traffic and the same route.
     Runner-up: *"Is the AI actually AI?"* — see section 4.
231–232. ❓ No mentor or judge feedback is recorded. ⚠️ Claims in the original
   proposal that no longer hold: single intersection; proximity-tag detection
   (now camera + route); queue-length-based adaptive timing (it is
   occupancy-based early green, and it does not retime the cycle); and real
   dispatch as "future work" when the simulator now models dispatch end to end.
233. ❓ Yours to say.
234. ⚠️ Two, and you should treat them as untrusted until re-measured: **the
   +117 % headline** (a best-case congested corridor, not a mean) and **the
   19.7 % mean** (pooled across 42 heterogeneous missions from exploratory
   sessions, not a designed experiment).
235. ❓ Unknown.

---

## The three asks of the ministry (already on record — keep them)

1. **Real signal timing plans.** The road layout is real; the timing plans are
   synthetic. With the real plans, the results become a forecast for named
   junctions.
2. **Real traffic counts.** A small hourly file plugs straight in and replaces
   the calibrated profile.
3. **One pilot corridor.** One hospital-to-area corridor: simulated first, then
   trialled on the street using existing camera and controller infrastructure.
   **No new city-wide hardware network is being requested.**

---

## What is missing, and what I recommend running before the deck is designed

The consultant is right that the evidence section decides the deck. Four gaps,
all fixable with the code you already have:

1. **The three-condition experiment.** Fixed-time → Adaptive (early green) →
   Adaptive + Emergency Priority. Both switches already exist
   (`preemption_enabled`, `actuation_enabled`); all four cells are producible.
2. **A multi-seed campaign.** Same dispatch schedule, 10+ seeds per condition,
   so you can quote a mean **with a confidence interval** instead of a pooled
   range. `run_headless.py --compare` already fixes routing across compared runs
   so both drive identical routes.
3. **Civilian impact during a corridor.** Cross-street delay, queue length and
   network throughput during and after preemption — the question that kills
   projects like this, and the one number you cannot currently produce. All are
   standard SUMO outputs.
4. **A demand sweep.** Low / medium / high (or 03:00 / 13:00 / 07:00) so the
   result is a curve, not a point.

Say the word and I will run all four and hand back a clean CSV plus the charts.
