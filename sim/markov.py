"""Self-feeding Markov-chain traffic predictor (DTMC + CTMC).

Each monitored road edge is described by a 4-state congestion process,
classified from mean speed as a fraction of the limit:

    0 FREE       v/v_lim >= 0.70
    1 SLOW       0.40 - 0.70
    2 CONGESTED  0.15 - 0.40
    3 JAMMED     <  0.15

Two chains are estimated from the same observations:

* **DTMC** — the state is sampled every ``sample_period_s`` (default 30 s of
  simulated time) and consecutive samples are counted as transitions,
  giving a one-step matrix P (Laplace-smoothed).  P^n forecasts n steps
  ahead; the stationary distribution pi gives the long-run share of time a
  corridor spends in each state.

* **CTMC** — sojourn times and observed jumps estimate a generator matrix
  Q (q_ij = jumps i->j / total time in i).  The transient distribution
  P(t) = expm(Q t) forecasts the state at ANY horizon t — exactly what
  anticipatory routing needs ("what will this edge look like in 73 s, when
  the ambulance reaches it?").

The model **feeds itself constantly**: every simulation step contributes
observations, counts persist to ``data/markov_<scenario>.json`` and are
reloaded on the next run, so the matrices sharpen across sessions.  Edges
with too little individual history fall back to a pooled per-road-class
chain, so forecasts degrade gracefully instead of guessing.

Honesty note: here the chains learn from SUMO's simulated traffic (itself
calibrated to the Kuwaiti hourly profile).  The estimator is exactly the
one MOI would run on real detector data — swap the sampler's input and
nothing else changes.

All linear algebra is exact pure Python on 4x4 matrices — no dependencies.
"""
import json
import os

import traci

N_STATES = 4
STATE_NAMES = ("FREE", "SLOW", "CONGESTED", "JAMMED")
STATE_SPEED_FACTOR = (0.90, 0.55, 0.28, 0.10)   # of the speed limit
_THRESHOLDS = (0.70, 0.40, 0.15)


def classify(speed, limit):
    r = speed / max(limit, 0.1)
    if r >= _THRESHOLDS[0]:
        return 0
    if r >= _THRESHOLDS[1]:
        return 1
    if r >= _THRESHOLDS[2]:
        return 2
    return 3


# ------------------------------------------------------------ 4x4 algebra

def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(N_STATES))
             for j in range(N_STATES)] for i in range(N_STATES)]


def mat_pow(p, n):
    result = [[1.0 if i == j else 0.0 for j in range(N_STATES)]
              for i in range(N_STATES)]
    base = [row[:] for row in p]
    while n > 0:
        if n & 1:
            result = mat_mul(result, base)
        base = mat_mul(base, base)
        n >>= 1
    return result


def expm(q, t):
    """Matrix exponential expm(Q t) via scaling-and-squaring + Taylor.
    Exact enough for generator matrices of this size (|entries| small)."""
    # scale so the norm is comfortably below 1
    norm = max(sum(abs(x) for x in row) for row in q) * abs(t)
    squarings = 0
    while norm > 0.5:
        norm /= 2.0
        squarings += 1
    scale = t / (2 ** squarings)
    a = [[q[i][j] * scale for j in range(N_STATES)] for i in range(N_STATES)]
    # Taylor series sum A^k / k!
    result = [[1.0 if i == j else 0.0 for j in range(N_STATES)]
              for i in range(N_STATES)]
    term = [row[:] for row in result]
    for k in range(1, 18):
        term = mat_mul(term, a)
        term = [[x / k for x in row] for row in term]
        result = [[result[i][j] + term[i][j] for j in range(N_STATES)]
                  for i in range(N_STATES)]
    for _ in range(squarings):
        result = mat_mul(result, result)
    # clamp tiny negatives from truncation and renormalise rows
    for i in range(N_STATES):
        row = [max(0.0, x) for x in result[i]]
        s = sum(row) or 1.0
        result[i] = [x / s for x in row]
    return result


def stationary(p, iters=200):
    """Stationary distribution of a DTMC by power iteration."""
    pi = [1.0 / N_STATES] * N_STATES
    for _ in range(iters):
        pi = [sum(pi[i] * p[i][j] for i in range(N_STATES))
              for j in range(N_STATES)]
        s = sum(pi) or 1.0
        pi = [x / s for x in pi]
    return pi


# ----------------------------------------------------------------- chains

class _Chain:
    __slots__ = ("C", "T", "J", "n", "last", "last_t")

    def __init__(self):
        self.C = [[0.0] * N_STATES for _ in range(N_STATES)]  # DTMC counts
        self.T = [0.0] * N_STATES                             # sojourn s
        self.J = [[0.0] * N_STATES for _ in range(N_STATES)]  # CTMC jumps
        self.n = 0
        self.last = None
        self.last_t = None

    def observe(self, state, now):
        if self.last is not None:
            dt = now - self.last_t
            self.C[self.last][state] += 1.0
            self.T[self.last] += dt
            if state != self.last:
                self.J[self.last][state] += 1.0
        self.last = state
        self.last_t = now
        self.n += 1

    # --- estimators ---

    def P(self, alpha=0.5):
        p = []
        for i in range(N_STATES):
            row = [self.C[i][j] + alpha for j in range(N_STATES)]
            # weak self-transition prior keeps sparse rows near-diagonal
            row[i] += 2.0
            s = sum(row)
            p.append([x / s for x in row])
        return p

    def Q(self):
        q = [[0.0] * N_STATES for _ in range(N_STATES)]
        for i in range(N_STATES):
            t_i = max(self.T[i], 1e-6)
            off = 0.0
            for j in range(N_STATES):
                if j != i:
                    q[i][j] = self.J[i][j] / t_i
                    off += q[i][j]
            q[i][i] = -off
        return q

    def to_json(self):
        return {"C": self.C, "T": self.T, "J": self.J, "n": self.n}

    @classmethod
    def from_json(cls, d):
        c = cls()
        c.C = [list(map(float, row)) for row in d["C"]]
        c.T = list(map(float, d["T"]))
        c.J = [list(map(float, row)) for row in d["J"]]
        c.n = int(d["n"])
        return c


class TrafficMarkov:
    """Samples the network, maintains the chains, forecasts, persists."""

    def __init__(self, net, cfg, root, ops=None):
        self.cfg = cfg
        self.ops = ops
        self.places = getattr(ops, "places", None)
        self.path = os.path.join(
            root, "data", f"markov_{cfg.scenario}.json")
        self.period = cfg.markov_sample_s
        self.chains = {}          # key -> _Chain
        self.state_now = {}       # edge id -> current state
        self._edge_class = {}     # edge id -> pooled class key
        self._limits = {}         # edge id -> speed limit
        self._next_sample = 0.0
        self._loaded_obs = 0
        self.sessions = 1            # how many sessions fed the chains
        # forecast ledger: every sample, a 5-minute forecast per corridor is
        # filed and later SCORED against what actually happened — the CTMC
        # must beat two naive baselines (persistence, climatology) or the
        # page says so.  Nothing here is decorative.
        self.horizon_s = 300.0
        self._pending = []           # (due_t, edge, p_ctmc, persist, clim)
        self.scores = {"ctmc": [0, 0, 0.0], "persistence": [0, 0, 0.0],
                       "climatology": [0, 0, 0.0]}   # [hits, n, brier_sum]
        self.recent = []             # last scored forecasts for display
        self.routing_evidence = {"compared": 0, "differed": 0,
                                 "predicted_saving_s": 0.0}
        # Forecast cache.  Chains and current states only change on the
        # sample tick, so within a tick expm(Q, t) is a pure function of
        # (chain, state, horizon).  The router calls forecast() once per
        # edge relaxation — thousands of times per Dijkstra — and without
        # this cache each call recomputes a matrix exponential (measured:
        # 2.2-4.4 s per dispatch, 97% of it here).  Horizons are quantised
        # to 15 s buckets; both caches are cleared every sample tick.
        self._fc_cache = {}       # (chain id, state, bucket) -> row
        self._q_cache = {}        # chain id -> generator matrix
        self._slice_i = 0         # rotating sampling slice (see update)
        # bumped whenever the forecasts above are invalidated, so consumers
        # that memoise per-edge weights derived from them (Router) know
        # exactly when to drop their own caches — and only then
        self.generation = 0

        # Monitor where traffic actually CHANGES state: the approaches to
        # signalized junctions (queues form and clear there) first, then the
        # major arterials.  Everything else pools by road class.
        approaches, arterials = [], []
        for e in net.getEdges():
            if not e.allows("passenger"):
                continue
            eid = e.getID()
            self._limits[eid] = e.getSpeed()
            major = e.getPriority() >= 9 or e.getLaneNumber() >= 3
            self._edge_class[eid] = "class:major" if major else "class:minor"
            if e.getLength() < 40:
                continue
            if e.getToNode().getType().startswith("traffic_light"):
                approaches.append((0 if major else 1, eid))
            elif major and e.getLength() > 80:
                arterials.append((2, eid))
        ranked = sorted(approaches) + sorted(arterials)
        self.monitored = [eid for _, eid in ranked][:cfg.markov_max_edges]
        self.session_scores = {"ctmc": [0, 0, 0.0],
                               "persistence": [0, 0, 0.0],
                               "climatology": [0, 0, 0.0]}
        self._load()

    # ------------------------------------------------------------- feeding

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
            # Refuse history recorded at a different sampling period: C, T
            # and J are ALL per-interval quantities, so none of them can be
            # rescaled — the honest move is to drop them and relearn.
            saved = data.get("period_s")
            if saved is not None and abs(float(saved) - self.period) > 1e-6:
                self.sessions = int(data.get("sessions", 0)) + 1
                if self.ops:
                    self.ops.emit(0.0, "system",
                                  f"Markov history in "
                                  f"{os.path.basename(self.path)} "
                                  f"was recorded at {float(saved):g} s "
                                  f"sampling but markov_sample_s is now "
                                  f"{self.period:g} s — the DTMC counts and "
                                  f"the CTMC jump/sojourn arrays are both "
                                  f"per-interval, so the chains were "
                                  f"discarded and relearn from zero", "warn")
                return
            for key, d in data.get("chains", {}).items():
                self.chains[key] = _Chain.from_json(d)
            # Count only what the banner claims to count: corridors in the
            # CURRENT monitored set.  Chains for edges that fell out of the
            # set (markov_max_edges lowered, different network) stay in
            # self.chains — they return if the set grows again — but they
            # are not "loaded observations" for these corridors.
            mon = set(self.monitored)
            self._loaded_obs = sum(c.n for k, c in self.chains.items()
                                   if k in mon)
            self.sessions = int(data.get("sessions", 0)) + 1
            sc = data.get("scores")
            if sc:
                for k in self.scores:
                    if k in sc:
                        self.scores[k] = [int(sc[k][0]), int(sc[k][1]),
                                          float(sc[k][2])]
            ev = data.get("routing_evidence")
            if ev:
                self.routing_evidence.update(ev)
        except (ValueError, KeyError, OSError):
            self.chains = {}

    def save(self):
        try:
            with open(self.path, "w") as f:
                json.dump({"period_s": self.period,
                           "sessions": self.sessions,
                           "scores": self.scores,
                           "routing_evidence": self.routing_evidence,
                           "chains": {k: c.to_json()
                                      for k, c in self.chains.items()}}, f)
        except OSError:
            pass

    # sampling is spread over this many slices so no single frame carries
    # the whole 160-edge sweep (measured: an all-at-once tick added ~370 ms
    # to one frame in 60 at 5000 vehicles); each edge is still observed
    # exactly every `period` seconds
    N_SLICES = 60

    def update(self, now):
        """Call every simulation step; each edge is sampled every `period`
        sim-seconds, staggered across N_SLICES slices of the step grid.
        The deadline grid catches up if steps are coarser than a slice,
        so the per-edge cadence stays `period` for any step length."""
        dt = self.period / self.N_SLICES
        fired = 0
        while now >= self._next_sample - 1e-9 and fired < self.N_SLICES:
            self._next_sample += dt
            fired += 1
            i = self._slice_i
            self._slice_i = (i + 1) % self.N_SLICES
            batch = self.monitored[i::self.N_SLICES]
            for eid in batch:
                try:
                    speed = traci.edge.getLastStepMeanSpeed(eid)
                except traci.TraCIException:
                    continue
                state = classify(speed, self._limits.get(eid, 13.9))
                self.state_now[eid] = state
                self.chains.setdefault(eid, _Chain()).observe(state, now)
                self.chains.setdefault(self._edge_class[eid],
                                       _Chain()).observe(state, now)
            # the ledger files and scores in the same slices as sampling,
            # so no single frame carries the full 160-corridor sweep
            self._score_due(now)
            self._file_forecasts(now, batch)
            if self._slice_i == 0:
                # full rotation complete: forecasts may see the fresh
                # chains (within a rotation they stay cached — the same
                # <=period staleness the all-at-once tick had)
                self._fc_cache.clear()
                self._q_cache.clear()
                self.generation += 1
        if self._next_sample <= now:
            self._next_sample = now + dt

    # ------------------------------------------- forecast ledger (observable)

    def _file_forecasts(self, now, edges=None):
        for eid in (self.monitored if edges is None else edges):
            chain = self._chain_for(eid)
            if chain is None or chain.n < self.cfg.markov_min_obs:
                continue
            state = self.state_now.get(eid, 0)
            p_ctmc = expm(chain.Q(), self.horizon_s)[state]
            persist = [1.0 if s == state else 0.0 for s in range(N_STATES)]
            clim = stationary(chain.P())
            self._pending.append((now + self.horizon_s, eid, state,
                                  p_ctmc, persist, clim))
        # bound memory: drop anything far overdue (should not happen)
        if len(self._pending) > 20000:
            self._pending = self._pending[-20000:]

    def _score_due(self, now):
        keep = []
        for item in self._pending:
            due, eid, from_state, p_ctmc, persist, clim = item
            if due > now + 1e-6:
                keep.append(item)
                continue
            actual = self.state_now.get(eid)
            if actual is None:
                continue
            for name, dist in (("ctmc", p_ctmc), ("persistence", persist),
                               ("climatology", clim)):
                hit = max(range(N_STATES), key=lambda k: dist[k]) == actual
                brier = sum((dist[k] - (1.0 if k == actual else 0.0)) ** 2
                            for k in range(N_STATES))
                for sc in (self.scores[name], self.session_scores[name]):
                    sc[1] += 1
                    sc[0] += 1 if hit else 0
                    sc[2] += brier
            self.recent.append({
                "edge": eid,
                "road": (self.places.road(eid) if self.places else eid),
                "made_t": due - self.horizon_s, "due_t": due,
                "from": STATE_NAMES[from_state],
                "predicted": STATE_NAMES[max(range(N_STATES),
                                             key=lambda k: p_ctmc[k])],
                "p_predicted": round(max(p_ctmc), 2),
                "actual": STATE_NAMES[actual],
                "hit": max(range(N_STATES), key=lambda k: p_ctmc[k]) == actual,
            })
            if len(self.recent) > 40:
                self.recent.pop(0)
        self._pending = keep

    @staticmethod
    def _skill(scores):
        out = {}
        for name, (hits, n, brier) in scores.items():
            out[name] = {"hit_rate": round(hits / n, 3) if n else None,
                         "brier": round(brier / n, 4) if n else None,
                         "n": n}
        c, cl, pe = out["ctmc"], out["climatology"], out["persistence"]
        def bss(ref):
            if not c["n"] or ref["brier"] in (None, 0):
                return None
            return round(1.0 - c["brier"] / ref["brier"], 3)
        # Brier skill score: 1 = perfect, 0 = no better than the baseline,
        # negative = worse than the baseline.  This is the number that
        # decides whether the predictor earns its place.
        out["skill_vs_climatology"] = bss(cl)
        out["skill_vs_persistence"] = bss(pe)
        return out

    def accuracy(self):
        return {"all_time": self._skill(self.scores),
                "this_session": self._skill(self.session_scores)}

    def corridor_detail(self, eid):
        """Everything needed to SEE the chains for one corridor: counts,
        the DTMC matrix P, the CTMC generator Q, the stationary distribution,
        the current-state forecast rows, and its recent scored forecasts."""
        chain = self.chains.get(eid)
        if chain is None:
            return None
        state = self.state_now.get(eid, 0)
        p = chain.P()
        q = chain.Q()
        return {
            "edge": eid,
            "road": (self.places.corridor(eid) if self.places
                     else {"road": eid, "area": "", "dir": ""}),
            "observations": chain.n,
            "state_now": STATE_NAMES[state],
            "states": list(STATE_NAMES),
            "dtmc": {
                "counts": [[int(x) for x in row] for row in chain.C],
                "P": [[round(x, 3) for x in row] for row in p],
                "stationary": [round(x, 3) for x in stationary(p)],
                "step_s": self.period,
            },
            "ctmc": {
                "sojourn_s": [round(x) for x in chain.T],
                "jumps": [[int(x) for x in row] for row in chain.J],
                "Q": [[round(x, 5) for x in row] for row in q],
                "forecast": {f"{h}s": [round(x, 3) for x in expm(q, h)[state]]
                             for h in (60, 300, 900)},
            },
            "recent_forecasts": [r for r in self.recent if r["edge"] == eid][-8:],
        }

    def observations(self):
        """Distinct corridor samples taken.  Each sample also feeds the
        pooled road-class chain (see the sampling loop in update()), so
        summing every chain would report exactly 2x the measurements
        actually made."""
        # list() first: the web thread reads this while the sim thread
        # inserts chains (atomic snapshot avoids "dict changed size")
        return sum(c.n for k, c in list(self.chains.items())
                   if not k.startswith("class:"))

    # ---------------------------------------------------------- forecasting

    def _chain_for(self, eid):
        c = self.chains.get(eid)
        if c is not None and c.n >= self.cfg.markov_min_obs:
            return c
        return self.chains.get(self._edge_class.get(eid))

    def forecast(self, eid, horizon_s):
        """State distribution of the edge `horizon_s` seconds from now
        (CTMC transient), or None without enough history.  Cached per
        (chain, state, 15 s horizon bucket) until the next sample tick."""
        chain = self._chain_for(eid)
        if chain is None or chain.n < self.cfg.markov_min_obs:
            return None
        state = self.state_now.get(eid, 0)
        bucket = int(max(0.0, horizon_s) // 15.0)
        key = (id(chain), state, bucket)
        row = self._fc_cache.get(key)
        if row is None:
            q = self._q_cache.get(id(chain))
            if q is None:
                q = chain.Q()
                self._q_cache[id(chain)] = q
            row = expm(q, bucket * 15.0)[state]
            self._fc_cache[key] = row
        return row

    def predicted_traveltime(self, eid, horizon_s, length):
        """Expected travel time (s) over `length` m of this edge at the
        arrival horizon: E[L/v] = L/limit * sum_s p_s / factor_s.
        NOT L / E[v] — by Jensen's inequality that is always optimistic,
        by up to 2.7x on exactly the bimodal FREE/JAMMED forecasts this
        predictor exists to detect.  This is the anticipatory weight the
        router charges."""
        dist = self.forecast(eid, horizon_s)
        if dist is None:
            return None
        limit = self._limits.get(eid)
        if not limit:
            return None
        return length / limit * sum(dist[s] / STATE_SPEED_FACTOR[s]
                                    for s in range(N_STATES))

    def predicted_speed(self, eid, horizon_s):
        """Expected mean speed (m/s) of the edge at the arrival horizon,
        from the CTMC forecast; None when history is insufficient.

        DISPLAY / AVAILABILITY ONLY — do NOT divide a length by this to
        price an edge (E[L/v] != L/E[v]; use predicted_traveltime, which
        is what the router does).  The router calls it only to ask
        "does this edge have a usable forecast at all?"."""
        dist = self.forecast(eid, horizon_s)
        if dist is None:
            return None
        limit = self._limits.get(eid)
        if not limit:
            return None
        return limit * sum(dist[s] * STATE_SPEED_FACTOR[s]
                           for s in range(N_STATES))

    # ------------------------------------------------------------ analytics

    def summary(self, top=12):
        """Per-corridor analytics for the copilot and the dashboard."""
        rows = []
        for eid in self.monitored:
            chain = self.chains.get(eid)
            if chain is None or chain.n < self.cfg.markov_min_obs:
                continue
            p = chain.P()
            pi = stationary(p)
            congested_share = pi[2] + pi[3]
            desc = (self.places.corridor(eid) if self.places
                    else {"road": eid, "area": "", "dir": "", "class": ""})
            rows.append({
                "edge": eid,
                "road": desc["road"], "area": desc["area"],
                "dir": desc["dir"], "class": desc["class"],
                "observations": chain.n,
                "state_now": STATE_NAMES[self.state_now.get(eid, 0)],
                "stationary": {STATE_NAMES[s]: round(pi[s], 3)
                               for s in range(N_STATES)},
                "congested_share": round(congested_share, 3),
                "p_jam_5min": round(
                    expm(chain.Q(), 300.0)[self.state_now.get(eid, 0)][3], 3),
            })
        rows.sort(key=lambda r: r["congested_share"], reverse=True)
        cls = self.chains.get("class:major")
        return {
            "total_observations": self.observations(),
            # the same samples pooled by road class, reported separately so
            # it can never be added into the corridor total again
            "pooled_class_observations": sum(
                c.n for k, c in list(self.chains.items())
                if k.startswith("class:")),
            "loaded_from_previous_sessions": self._loaded_obs,
            "sessions": self.sessions,
            "corridors_with_history": sum(
                1 for e in self.monitored
                if (self.chains.get(e) and
                    self.chains[e].n >= self.cfg.markov_min_obs)),
            "monitored_edges": len(self.monitored),
            "sample_period_s": self.period,
            "horizon_s": self.horizon_s,
            "accuracy": self.accuracy(),
            "state_mix_now": {STATE_NAMES[k]: sum(
                1 for e in self.monitored if self.state_now.get(e, 0) == k)
                for k in range(N_STATES)},
            "routing_evidence": dict(self.routing_evidence),
            "recent_forecasts": self.recent[-10:],
            "network_major_stationary": (
                {STATE_NAMES[s]: round(stationary(cls.P())[s], 3)
                 for s in range(N_STATES)}
                if cls and cls.n >= self.cfg.markov_min_obs else None),
            "top_corridors": rows[:top],
        }
