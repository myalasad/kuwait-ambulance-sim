# ChatGPT Master Prompt — Ministry Presentation (copy everything below the line)

---

## ROLE

You are a senior transport-systems engineer and executive communications specialist. You have delivered intelligent-transport-system (ITS) proposals to national ministries. You write for decision-makers who are intelligent but not traffic engineers, and you never pad, never hedge, and never let a slide say something a minister could not repeat confidently.

## MISSION

Produce a **ministry-grade 8-slide presentation deck** (16:9) about the project described in the FACT BASE below, plus **speaker notes** written for **5 presenters** (referred to only as "Presenter 1" … "Presenter 5" — never invent or use names).

If you have a Python tool available, **generate a real `.pptx` file** using `python-pptx` and give me the download link. If you cannot, output the deck slide-by-slide in the exact format specified under OUTPUT.

## AUDIENCE AND STAKES

The audience is a government ministry (interior / public works / health). They control the traffic signals and the ambulance fleet. They will decide whether this becomes a funded pilot. They care, in this order:

1. Does it save lives / minutes, and can you prove the number?
2. Does it make the road **less safe** for everybody else? (This is the question that kills projects like this.)
3. What does it do to normal, everyday traffic when no ambulance is anywhere near?
4. Who is accountable when it misbehaves, and what happens when it fails?
5. What exactly are you asking us for?

## HARD CONSTRAINTS

- **Exactly 8 slides.** Not 7, not 9.
- **Every single line must be self-explanatory** to both the presenter and the viewer. A reader who sees only the slide, with no narration, must understand it. A presenter who has never opened the code must be able to read the line aloud and defend it.
- **Simple language.** No unexplained jargon. If a technical term is unavoidable (preemption, microsimulation, Markov chain), it must be explained in the same breath, in plain words.
- **Neat and clean.** Generous whitespace, strong hierarchy, one idea per block. This is being presented to a ministry — it must look institutional, not like a student project or a startup pitch.
- **Never overstate.** This is a simulation and a planning tool, not a deployed life-safety system. Any claim must be traceable to the FACT BASE. **Do not invent statistics, costs, timelines, casualty figures, or response-time benchmarks.** If you feel a number is missing, write `[NEEDS MINISTRY DATA]` instead of inventing one.
- **Speaker notes on every slide**, containing: the presenter number, the spoken opening line, the 2–4 points to make, and pre-written answers to the hostile questions that slide invites.

## REQUIRED COVERAGE (all of it must appear somewhere in the 8 slides)

Beyond the emergency green corridor itself, the deck **must** give real estate to these three areas — they are what turns this from a gadget into a traffic-management proposal:

**A. Normal traffic organization** — what the system does for ordinary drivers when there is no ambulance at all. The demand-responsive rule: nobody sits at a red light for an empty crossing, but the moment more than one approach is occupied everyone gets the fair fixed timer, no favourites. This is the slide that proves the system is not "ambulances win, everyone else loses."

**B. Risk management** — the failure modes and what happens in each: signal command failure, centre failure, over-held junction (starvation), ambulance stuck inside the junction box, vehicle disappearing, operator error. Every one must have a stated fail-safe. Include the scope-of-use limitation honestly — it builds credibility rather than damaging it.

**C. Traffic management highlights** — the governance layer: the Decision-Making Matrix (a published, lexicographically weighted ruling table where a higher criterion always beats a lower one), automatic arbitration between two ambulances, referral to a human supervisor when the machine cannot decide, the full audit trail, and the network-wide effect on all traffic — not just the ambulance.

---

## FACT BASE (everything below is verified from the working system — use it, do not contradict it, do not add to it)

### What the project is

A microscopic traffic simulation of Kuwait City in which traffic-signal cameras detect an ambulance running its emergency lights, and the traffic-management centre opens a **green corridor** along the ambulance's route. Each signal ahead switches — after a proper amber transition — to green for the ambulance's approach, so the queue in front of it discharges, while conflicting movements are held red. Once the ambulance passes, each junction returns to its normal programme.

Built on **Eclipse SUMO** (the industry-standard open-source traffic microsimulator, from the German Aerospace Center — used by transport authorities and researchers worldwide), controlled live from Python via TraCI.

### The road model — what is real and what is not

- **Real:** the road network, one-way system, turn restrictions and signal locations, taken directly from OpenStreetMap. Two selectable models: (1) **downtown Kuwait City**, detailed, **78 signalized junctions**; (2) **all six governorates** (Capital, Hawalli, Farwaniya, Mubarak Al-Kabeer, Ahmadi, Jahra) at arterial level — **13,300 road segments, 223 signalized junctions**, a real Ministry of Health hospital per governorate, 100 named places. Residential side streets are not modelled in the six-governorate version — say so plainly.
- **Calibrated, not measured:** background traffic demand follows the published shape of a Kuwaiti weekday (07:00 morning peak by default; 01:00–05:00 gives near-empty night streets — verified 14 vehicles at 03:00 vs 202 at 07:00). Kuwait exposes no public live traffic feed. A ministry file of hourly counts (`hour,multiplier`) drops straight in and overrides the calibrated profile.
- **Synthetic:** the individual signal timing plans (static 90 s cycles generated per junction). The real ministry timing plans can be substituted directly into the network file when provided.
- Simulation runs at **20 ms per step**, on an ordinary computer, with no internet connection required.

### How the green corridor works — the five steps

1. **Camera detection.** Every signalized junction has a virtual camera that recognises an ambulance with active emergency lights up to **200 m** along its approaches.
2. **Confirmation.** The first camera hit confirms the vehicle to the control centre, which already knows the dispatched route. *Detection confirms; the route predicts* — which is how a junction knows the ambulance is coming before its own camera sees it.
3. **Green wave.** Signals along the route are preempted in sequence based on the ambulance's **estimated time of arrival** — the signal switches when ETA drops below **25 seconds**, but never earlier than **800 m** out and never later than **160 m** out. *ETA-based activation is the critical design choice:* a fixed-distance trigger would let an ambulance crawling through a jam hold junctions ahead for minutes and gridlock the cross streets.
4. **Preempting a junction** = **3 s amber** to conflicting greens, then **2 s all-red clearance** so vehicles trapped in the box can leave, then the controller jumps to and holds **the junction's own real programme phase** that serves the ambulance's approach. Holding a real phase — rather than a hand-crafted "everything red" state — is what real preemption controllers do, and it keeps compatible movements and drain paths alive so the intersection cannot deadlock itself. **Signals are never switched dark; dark signals cause collisions.**
5. **Recovery.** After the ambulance passes (+**2 s** clearance) the junction ambers down and resumes its normal signal plan. The map labels it "PURPOSELY ENABLED", then "BACK TO NORMAL".

### Normal traffic organization — demand-responsive signals (no ambulance involved)

If exactly **one** approach of a junction is occupied and every other approach has been empty for **3 s**, the junction moves — through its own amber — to the phase serving that approach. Nobody sits at a red light for an empty crossing. The early green ends the moment any other approach becomes occupied (after a **5 s** minimum green), when the lone traffic has passed, or at the **30 s** cap. A **10 s** per-junction cooldown prevents flip-flopping. **With more than one approach occupied, the junction returns to the fair fixed timer — no favourites.** Ambulance preemption always outranks this rule. Every grant and release is logged; the map labels the junction "EARLY GREEN · lone traffic".

Measured at identical peak demand, this feature alone produced **+9.1% mean network speed** and **−42.5% halted vehicles** across all traffic.

### Risk management — failure modes and fail-safes

| Failure | Built-in response |
|---|---|
| Any signal command fails (communication/state error) | Junction is immediately reverted to its normal programme — never left frozen in a corridor state; the case closes with status `error` and an error-severity alert reaches the operator |
| The control centre / simulation loop dies | The failure is broadcast to every connected screen (no silent freeze); a watchdog restarts the service. In a real deployment the equivalent fail-safe is the **local junction controller falling back to its own fixed-time plan** when the centre stops responding |
| A junction is held too long (starvation) | A single hold is capped at **90 s**; beyond it cross traffic is guaranteed a normal cycle (**20 s cooldown**) unless the ambulance is already at the stop line; release and re-arm are both logged |
| Ambulance physically inside the junction box | The green is held until it is physically clear — a restoring signal can never box it in mid-crossing |
| Ambulance disappears from the map | Nothing leaves silently: every ambulance has a case open from dispatch to close, and the close reason is always logged (arrived / lights switched off by operator / stuck-vehicle artefact / unexpected removal → immediate error) |
| Operator needs to stop everything | Arm/disarm the whole preemption system with one control; disarming releases every junction through the normal amber-down sequence, and is logged |

**Scope of use (state this on the deck, do not bury it):** this is a planning, evaluation and training simulator. It is **not** a certified life-safety control system and must not be connected to live signal hardware without the full engineering, redundancy and regulatory review that real emergency-vehicle-preemption deployments require — conflict-monitor hardware, fail-to-normal guarantees, and ministry approval. Decisions about real dispatch or real signal control must not be made on simulation output alone.

### Traffic management highlights — the governance layer

**The Decision-Making Matrix.** Every ruling the controller makes comes from one published table. Criteria are weighted **lexicographically** — a higher row always beats every row below it:

1. **Safety (absolute)** — command failure, or ambulance inside the junction box → fail-safe to normal / hold until physically clear
2. **Continuity** — a junction already serving one corridor keeps serving it; the follower is queued (switching allegiance mid-approach would trap both streams in the box)
3. **Signal timer** — lights on, signal on route, ETA ≤ 25 s → enable the corridor
4. **Proximity** — two corridors requesting the same junction, margin > **20 m** → grant the nearest, queue the other
5. **Fairness (starvation guard)** — held > 90 s with the ambulance still far out → release, 20 s cross-traffic cooldown, then re-arm
6. **Human referral** — margin ≤ 20 m, machine criteria tie → the controller declares itself **unable to decide**; the junction stays on its normal programme (the safe state) and the conflict goes to a supervisor with one-click grant buttons; if no human decides within **8 s**, the default policy (nearest) applies and is logged as a policy decision
7. **No priority basis** — lights off → corridors released, drives as normal traffic
8. **Legal exemption** — above the posted limit with lights on → no citation, pass logged
9. **Nearest hospital** — scene reached → auto-reroute by travel time
10. **Demand** — no ambulance, one approach occupied → early green

**Audit trail.** Every operation persists to a structured log for after-action review — currently over 77,000 recorded operations across cases, with each ambulance and each junction carrying its own case identifier from open to close.

### The supporting intelligence

- **Own route planner:** Dijkstra over the network's edge graph, respecting one-way streets and turn restrictions, weighted by live travel times. The driver's screen and the signal corridor consume the *same* route object.
- **Predictive routing:** every monitored corridor is modelled as a 4-state congestion chain (FREE / SLOW / CONGESTED / JAMMED), estimated both as a discrete-time and a continuous-time Markov chain from the same observations. The router weighs each road by its **predicted** speed at the moment the ambulance will reach it — so routes avoid where congestion *will be*, not just where it is. Observations accumulate every 30 s and persist across sessions; sparse corridors fall back to pooled road-class chains rather than guessing; the transition mathematics is verified against closed-form solutions in the test suite. The identical estimator ingests real detector data unchanged.
- **Scene, then hospital:** on reaching the incident the ambulance stops for **40 s** of patient loading — the corridor is **paused** during loading so cross streets are not held for a stationary vehicle — then auto-reroutes to the nearest hospital by travel time, with the corridor following the new route.
- **Speed exemption with an audit trail:** with lights active the ambulance may run at up to **150%** of the posted limit (absolute cap **140 km/h**), consistent with the emergency exemption in Kuwaiti traffic law. The enforcement camera is the same camera that detects the corridor: it logs "exemption applies — NO CITATION issued" instead of a fine. Lights off = no exemption.
- **Operator interfaces:** a live control-room map, an in-cab driver screen (heading-up navigation, "next signal will be green", live speed beside the posted limit), a searchable operations feed with a full case ledger, the published protocol rulebook, a plain-language question-and-answer assistant grounded in the operations record with citations, and a continuously looping **bilingual Arabic/English** animated explainer for non-engineers.

### Measured results

- Across **33 logged missions**: mean arrival **348 s with** the green corridor vs an estimated **430 s without** it in the same traffic — about **19% faster**, roughly **80 seconds saved per run**.
- **Method, stated honestly:** the "with" time is measured in the simulation; the "without" time is the system's own per-junction counterfactual estimate of the signal waits the corridor removed (standard r²/2C signal-delay formula, same traffic). A seeded side-by-side comparison harness (same seed, same dispatches, preemption on vs off) also exists.
- Demand-responsive signals at identical peak demand: **+9.1% mean network speed**, **−42.5% halted vehicles** — network-wide, for all road users.

### The three asks of the ministry

1. **Real signal timing plans.** The road layout is real; the timing plans are synthetic. With the actual plans, the results become a forecast for named junctions.
2. **Real traffic counts.** A small hourly file plugs straight in and replaces the calibrated demand profile. (Commercial Kuwait traffic datasets exist and are procurable if ministry counts are not available.)
3. **One pilot corridor.** One hospital-to-area corridor: simulated first, then trialled on the street using existing camera and controller infrastructure. No new city-wide hardware network is being requested.

---

## RECOMMENDED SLIDE MAP (follow it unless you can justify better)

1. **Title** — the name, one sentence on what it is, the credibility line (real OpenStreetMap roads, Eclipse SUMO, ministry, date).
2. **The problem** — minutes lost at red lights; a stopped queue has nowhere to move aside; crossing on red is the most dangerous moment of the journey. End on the goal in one line.
3. **What we built** — the model: real roads, real traffic behaviour, built for non-engineers. Carry the 78 / 223 / 13,300 figures and the honest "real vs calibrated vs synthetic" distinction.
4. **How the green corridor works** — the five steps, left to right, with the amber → all-red → green sequence visible. Make the ETA-based trigger explicit.
5. **Normal traffic organization** — what ordinary drivers get: no red light for an empty crossing, fair timers whenever anyone else is waiting, ambulance priority always outranks. Carry the +9.1% / −42.5% network figures here.
6. **Risk management** — the failure table, each with its fail-safe, plus the scope-of-use statement. This slide must feel like it was written by someone who expects to be audited.
7. **How decisions are made, and what we measured** — the Decision-Making Matrix (safety beats continuity beats timing beats proximity beats fairness, then human referral), the audit trail, and the 430 s → 348 s result with its method stated.
8. **Where we go from here** — the three asks, and the closing line.

## DESIGN DIRECTION

- Institutional and restrained. Deep navy or charcoal for the opening and closing slides, white or very light grey for the body slides.
- One accent colour used sparingly for the "good" state (a green that reads as a traffic signal green), one for the "risk/problem" state (a signal red). Do not use both at full strength on the same block.
- Repeat one visual motif across the deck — the traffic-signal head is the natural choice, and it earns its place because the whole project is about what that head is showing.
- Titles 34–44pt bold; section headers 18–24pt bold; body 12–16pt; captions 10pt muted. Left-align body text; centre only titles.
- Minimum 0.5" margins; consistent gaps; never let text overflow its container.
- **Do not use:** decorative accent stripes or colour bars under titles or along the edge of cards, gradient backgrounds, stock photography of generic ambulances, clip-art icons, or a wall of bullets. These read as filler.
- Use a table for the risk slide and for the decision matrix — they *are* tables, and a minister reads a table faster than prose.
- Fonts: Calibri or Arial for body; a serif such as Cambria for headings if you want contrast. Nothing exotic.

## OUTPUT

If you can run Python: build the deck with `python-pptx` at 13.333 × 7.5 inches, put the speaker notes in each slide's notes field, and give me the file. Then, in your reply, print a short table of the 8 slide titles with the presenter number for each.

If you cannot run Python, output for each slide, in order:

```
SLIDE n — [title]
LAYOUT: [one line describing the visual arrangement]
ON-SLIDE TEXT: [every word that appears on the slide, exactly as it should read]
VISUAL: [the chart, table, diagram or motif, described precisely enough to build]
SPEAKER NOTES (Presenter n): [opening line, points to make, answers to the likely hostile questions]
```

## BEFORE YOU ANSWER — SELF-CHECK

1. Is every on-slide line understandable with no narration? Read each one cold and ask whether a minister could repeat it.
2. Is there a number anywhere that is not in the FACT BASE? If yes, delete it or mark it `[NEEDS MINISTRY DATA]`.
3. Do normal traffic organization, risk management, and the decision/audit layer each have real space — not one bullet each?
4. Does the deck answer "does this make the road less safe for everyone else?" before the audience has to ask it?
5. Exactly 8 slides, 5 presenters, no names anywhere?
6. Would you be comfortable standing behind every line of this in front of a ministry, on the record?

Do not ask me clarifying questions first — produce the deck, then list at the end any assumptions you made and anything you would want confirmed.
