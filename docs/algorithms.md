# Algorithms — what each one computes, and why it is here

Reference for the Kuwait Ambulance Green Wave. Every algorithm below is
implemented in this repository in pure Python (no solver libraries), and
every one exists to answer a specific operational question. Where a method
has a known failure mode, it is stated rather than hidden.

---

## 1. Dijkstra's shortest path — the ambulance's route
**File:** `sim/router.py` · **Question:** which way should this unit drive?

The search graph is **edge-based**: a node of the search is a road *edge*,
and an arc is a legal edge-to-edge connection taken from the OSM network as
netconvert encoded it. This is why one-way streets and banned turns are
respected exactly — an illegal turn simply is not an arc.

Weights are **travel times**, not distances. The weight of entering an edge
is chosen in this order:

1. the CTMC-predicted travel time at the horizon we would arrive (§3), else
2. the live travel time SUMO reports for that edge right now, else
3. free flow (edge length ÷ speed limit).

Everything is clamped to no better than free flow and no worse than 20
minutes, because SUMO reports absurd values for empty or fully blocked edges.

Three entry points:

| Function | Shape | Used for |
|---|---|---|
| `route(a, b)` | one origin → one destination | the mission's actual route |
| `route_to_many(a, targets)` | one origin → many destinations, **one** search | ranking hospitals from a scene on the return leg |
| `cost_from_many(origins, b)` | many origins → one destination, **one backward search** on the reversed graph | ranking every hospital's travel time to an incident |

The multi-target forms matter: ranking six hospitals used to cost six full
searches per dispatch. One search answers all of them.

**Time-dependence and its caveat.** The weight is evaluated at
`horizon = cumulative time so far`, so the route avoids congestion that will
exist when the unit arrives, not merely congestion that exists now. Strictly,
Dijkstra requires the FIFO property (leaving later never arrives earlier);
predicted weights can violate it, so the returned path is not provably
optimal under its own weights. In practice the deviation is small, and the
alternative — ignoring the forecast — is worse. This is stated in the code.

---

## 2. DTMC — discrete-time Markov chain (a corridor's personality)
**File:** `sim/markov.py` · **Question:** what does this corridor do *usually*?

Every 30 simulated seconds, each monitored corridor's mean speed is
classified into one of four states by its ratio to the speed limit:

| State | v / v_limit |
|---|---|
| 0 FREE | ≥ 0.70 |
| 1 SLOW | 0.40 – 0.70 |
| 2 CONGESTED | 0.15 – 0.40 |
| 3 JAMMED | < 0.15 |

Consecutive samples are counted as transitions into a 4×4 matrix **C**.
Normalising the rows gives the one-step transition matrix **P**, with
Laplace smoothing (α = 0.5) plus a weak self-transition prior, so a corridor
with three observations does not claim certainty.

**Stationary distribution π** — the long-run share of time a corridor spends
in each state — is found by power iteration (200 steps): repeatedly apply
π ← πP until it stops moving. That is the number the Copilot page shows as a
corridor's character.

---

## 3. CTMC — continuous-time Markov chain (what happens in 73 seconds)
**File:** `sim/markov.py` · **Question:** what will this road be like when the ambulance actually gets there?

The DTMC can only answer in whole 30-second steps. Routing needs arbitrary
horizons, so the same observations also estimate a **generator matrix Q**:

    q(i→j) = (number of observed jumps i→j) / (total time spent in i)
    q(i,i) = −Σ q(i→j)

The state distribution at any horizon *t* is the matrix exponential:

    P(t) = e^(Qt)

**How e^(Qt) is computed** (`expm`): scaling-and-squaring. The matrix is
halved repeatedly until its norm is comfortably below 0.5, a 17-term Taylor
series is summed on the small matrix, and the result is squared back up. Rows
are then clamped and renormalised to kill truncation noise. Pure Python on
4×4 matrices, validated against closed-form two-state solutions in
`tests/test_markov.py`.

**Turning a forecast into a routing weight — and the trap.** The anticipatory
weight is the *expected travel time*, not length ÷ expected speed:

    E[L/v] = (L / v_limit) · Σ p_s / factor_s        ← what the router uses
    L / E[v]                                          ← wrong, always optimistic

By Jensen's inequality the second form under-estimates, by up to **2.7×** on
exactly the bimodal FREE-or-JAMMED forecasts this predictor exists to detect.
`predicted_traveltime()` is the routing call; `predicted_speed()` survives for
display only and says so.

**Caching.** Within one 30-second sampling tick the chains and states cannot
change, so e^(Qt) is a pure function of (chain, state, horizon). Results are
cached in 15-second horizon buckets and cleared each tick. Without this, a
single dispatch recomputed thousands of matrix exponentials and blocked the
server for 2–4 seconds.

**Graceful degradation.** A corridor needs 40 of its own observations before
its personal chain is trusted; below that it falls back to a pooled chain for
its road class. Counts persist to `data/markov_<scenario>.json` and reload, so
the estimates sharpen across sessions.

---

## 4. Forecast scoring — Brier score and skill (the honesty check)
**File:** `sim/markov.py` · **Question:** is the model actually any good?

A model that is never scored is decoration. Every sampling tick files a
sealed 5-minute-ahead forecast for each corridor. Five minutes later it is
scored against what happened, using the **Brier score** (mean squared error of
the probability vector — lower is better), alongside two naive baselines:

- **persistence** — "it will be whatever it is now"
- **climatology** — "it will be whatever this corridor usually is" (π from §2)

The reported figure is the **skill score**, 1 − (model Brier ÷ baseline
Brier): positive means better than the baseline, zero means no better. The
Copilot page prints the verdict as measured, including when it is unflattering.

---

## 5. Union-Find — grouping a divided junction into one junction
**File:** `sim/actuation.py` · **Question:** which signal heads are really the same junction?

A large interchange is several signal *nodes* sharing one name. Judged
separately, one node can be truthfully "empty" while its siblings hold the
queues — which is how an early green could once be granted at a visibly busy
junction. Nodes are merged into complexes with a disjoint-set (union-find)
structure, joined when a short connector (< 60 m) runs between them or their
stop lines sit within 90 m. The lone-approach test then applies to the whole
complex.

---

## 6. Physical clearance test — ground truth over wiring
**File:** `sim/actuation.py` · **Question:** is anyone actually standing at this junction?

The signal's controlled-link map knows only the edges wired to it; ramp stubs
and service roads feeding the same box are invisible to it. For each junction
the system therefore precomputes the edges that physically *arrive* at it —
those ending within 75 m of the junction centre and coming from outside it —
and an early green is refused while any of them holds a vehicle. This is
measured from live positions, not from the wiring diagram.

---

## 7. Expected signal delay — r² / 2C
**File:** `sim/ambulance.py` · **Question:** how much time does a red light cost, and how much did the corridor save?

For a vehicle arriving at a uniformly random point in a signal cycle, the
expected wait is:

    E[wait] = r² / (2C)

where **r** is the red time for *the ambulance's own movement* (cycle minus
the green that serves that movement, summed across the phases that serve it)
and **C** is the cycle length. The delay is **quadratic in the red time**,
which is why the signal timer, not the distance, is the dominant variable in
the with-versus-without comparison. Movements that are always green are
charged zero; junctions whose movement cannot be resolved are excluded from
the total and counted separately rather than silently guessed.

---

## 8. Dispatch — nearest ready unit, with a headway rule
**File:** `sim/ambulance.py` · **Question:** which hospital answers this call?

1. One backward Dijkstra (§1) ranks every hospital by travel time to the scene.
2. Hospitals with no ready crew are removed (the ready-fleet model: each
   hospital stations a fixed number of crews; a crew that delivers a patient
   rejoins a pool after a turnaround period).
3. The nearest remaining hospital answers. Only within a narrow tie margin may
   order change, and only for **gate headway** — a hospital that launched a
   unit seconds ago yields to an equally close peer so departures do not stack
   at one gate. Any such re-ordering is logged with the seconds it cost.
4. If no crew is available anywhere, the call **queues** (FIFO, keeping its
   original timestamp) and the next crew to finish turnaround is dispatched
   automatically. The response clock starts at the *call*, so queue waiting is
   counted in the published p50/p90, not hidden.

---

## 9. BM25 — the Copilot's retrieval
**File:** `rag/index.py` · **Question:** which records answer this question?

Okapi BM25 (k₁ = 1.5, b = 0.75) over the system's own corpus: one document per
case per session, plus the protocol, handbook, README and live analytics.
Each term contributes

    idf · f·(k₁+1) / (f + k₁·(1 − b + b·len/avg_len))

so repeated terms saturate and long documents are not unfairly favoured.
Before ranking, exact-entity filters (`AMB_3`, case `P-012`, junction codes)
hard-filter the candidate set, and question words are stripped. Retrieval is
free and local; only the final synthesis calls a language model, and without
an API key the system answers extractively from the matched records.

---

## 10. Supporting numerical methods

- **Spherical Mercator projection** (`web/static/index.html`) — positions are
  projected once per network frame into world coordinates, so rendering costs
  one multiply-add per vehicle per frame instead of a full projection. Verified
  sub-pixel against Leaflet.
- **Linear interpolation with bounded extrapolation** — the simulation steps
  about twice a second; the client interpolates between the last two snapshots
  and may extrapolate slightly past the newest one, so motion stays smooth
  across a late frame. Large jumps (teleports) snap instead of sliding.
- **Exponential moving average** — the inter-frame interval is smoothed
  (0.7 old / 0.3 new) so one slow frame does not visibly change vehicle speed.
- **Circular mean** — an approach's heading is the circular mean of its lane
  bearings, computed through sine and cosine sums, because averaging 350° and
  10° arithmetically gives 180°: the exact opposite direction.
- **Hysteresis** — state changes that would otherwise flicker (a corridor
  hardening to red, a queue-flush request) are held with a margin so a
  borderline measurement cannot oscillate the signal.

---

## What is *not* an algorithm here

The traffic itself is Eclipse SUMO's microsimulation: car-following,
lane-changing and junction logic are SUMO's, not ours. This project supplies
the control layer above it — detection, routing, preemption, actuation,
prediction and the audit trail — and every number it publishes is measured
from a run, not asserted.
