"""Demand-responsive signal control for ordinary traffic (early green).

A driver alone at a red light on an otherwise empty junction should not sit
out a fixed timer.  This module watches every signalized junction's
approaches; when EXACTLY ONE approach is occupied and every other approach
has been empty for ``lone_confirm_s``, the junction moves — through its own
amber phase — to the programme phase serving that approach and holds it.

Fairness is absolute the moment it matters:
* if any other approach becomes occupied, the early green ends (after the
  ``lone_min_green_s`` minimum) and the junction resumes its normal
  fixed-time cycle — with several approaches occupied everyone gets the
  timer, no favourites;
* an early green never lasts more than ``lone_max_hold_s`` (default 30 s);
* per-junction cooldown between grants prevents thrash.

Ambulance preemption always outranks this module: junctions the green-wave
controller is using (active or under a referred decision) are left alone,
and a claim is dropped instantly if preemption arrives.

Mechanism: holds use setPhase/setPhaseDuration INSIDE the junction's own
signal programme — never setRedYellowGreenState — so the programme id stays
intact for the preemption controller to save and restore, and the "fair
timers" state is simply the programme running normally.
"""
import traci
import traci.constants as tc

AMBERING = "ambering"
SERVING = "serving"


class DemandResponsiveController:
    def __init__(self, cfg, ops, enabled=True, net=None):
        self.cfg = cfg
        self.ops = ops
        self.net = net
        self.enabled = enabled
        self.tls_info = {}      # tls -> approach lanes, serving phase map
        self.claims = {}        # tls -> {"mode","edge","serve","until","since"}
        self.cooldown = {}      # tls -> sim time until next grant allowed
        self.pending = {}       # tls -> (edge, first seen lone) confirmation
        self.granted_total = 0
        self._modes = {"fair": 0, "lone": 0, "occupied": 0}
        self._last_seen = {}    # tls -> {edge: last sim time it had traffic}
        # Permanent self-audit.  The release condition in _advance ends a
        # hold on the FIRST step where another approach is occupied and the
        # minimum green has been served, so "still holding while others
        # wait" cannot arise while the claim is alive.  It can only arise if
        # the release did not take effect, so the fairness tripwire is armed
        # AFTER the release command and fires when the junction is still
        # showing the early-green phase more than ``_release_grace_s`` later
        # while another approach is occupied — a phase command overridden by
        # the programme or silently dropped.  That is the one way this
        # guarantee can actually break, so the counter CAN leave zero; if it
        # stays at zero it means every early green really did end.
        # ``max_other_wait_s`` is the quantity that varies on every run: how
        # long another approach waited before the hold ended (bounded by
        # lone_min_green_s by construction).
        self.released = {}      # tls -> {"serve","edge","at"}: armed tripwire
        self._release_grace_s = 2.0    # wind-down allowed after the command
        self._release_watch_s = 5.0    # how long the tripwire stays armed
        self.complex_ext = {}      # tls -> {external edge: zone lanes} of
        #                            its multi-node junction complex
        self._all_complex_edges = {}
        self._seen_any = {}        # complex external edge -> last seen t
        self._lane_res = {}        # this step's lane occupancy
        self.audit = {"grants": 0, "extensions": 0, "violations": 0,
                      "complex_blocked": 0, "proximity_blocked": 0,
                      "ended_for_other_traffic": 0,
                      "max_other_wait_s": 0.0}
        self.skipped_nonconflict = 0
        self._build()

    def mode_counts(self):
        """How many junctions are in use (an approach carried traffic within
        ``lone_quiet_s``), how many of those have several approaches in use
        (fair timers by design) and how many a single approach in use
        (early-green candidates) — plus the self-audit.

        ``audit["holds"]`` is the POPULATION every other audit number is a
        subset of: every early-green hold, whether it was granted from
        another phase (``grants``) or was an extension of a green already
        running (``extensions``).  ``ended_for_other_traffic`` counts
        endings of BOTH kinds, so it must never be presented beside
        ``grants`` alone — it would read as more endings than grants."""
        audit = dict(self.audit)
        # granted_total is incremented at every claim-creation site, so this
        # cannot drift if a third kind of hold is ever added
        audit["holds"] = self.granted_total
        return {**self._modes, "early": self.granted_total,
                "audit": audit,
                "arbitrated_junctions": len(self.tls_info),
                "nonconflict_excluded": self.skipped_nonconflict}

    def resubscribe(self):
        """Re-arm every lane detector this module depends on.

        loadState wipes ALL TraCI subscriptions, so after a warm start the
        occupancy readings come back empty and early green silently dies:
        every junction reads as unoccupied, no grant can ever fire, and the
        fairness numbers report a city with no traffic in it.  The runner
        calls this after loading a cached state."""
        for info in self.tls_info.values():
            for lanes in info["approach"].values():
                for lane in lanes:
                    traci.lane.subscribe(lane, [tc.LAST_STEP_VEHICLE_NUMBER])
            for lanes in info.get("near", {}).values():
                for lane in lanes:
                    traci.lane.subscribe(lane, [tc.LAST_STEP_VEHICLE_NUMBER])
        for lanes in self._all_complex_edges.values():
            for lane in lanes:
                traci.lane.subscribe(lane, [tc.LAST_STEP_VEHICLE_NUMBER])

    # ------------------------------------------------------------- topology

    def _build(self):
        for tls_id in traci.trafficlight.getIDList():
            try:
                links = traci.trafficlight.getControlledLinks(tls_id)
                logics = traci.trafficlight.getAllProgramLogics(tls_id)
            except traci.TraCIException:
                continue
            if not logics:
                continue
            approach = {}   # in-edge -> set of lanes in its DETECTION ZONE
            for group in links:
                if group:
                    in_lane = group[0][0]
                    approach.setdefault(in_lane.rsplit("_", 1)[0],
                                        set()).add(in_lane)
            # extend every approach upstream to the detection-zone length:
            # the final edge before a merged junction is often a 5-40 m
            # connector stub that cannot see a queue 30 m behind it
            for edge_id in list(approach):
                approach[edge_id] |= self._zone_lanes(edge_id)
            if len(approach) < 2:
                continue    # nothing to arbitrate on a one-approach signal
            phases = logics[0].phases
            link_edges = [g[0][0].rsplit("_", 1)[0] if g else None
                          for g in links]
            serve = {}
            for edge in approach:
                best, score = None, 0
                for i, phase in enumerate(phases):
                    if "y" in phase.state:
                        continue
                    s = sum(1 for li, e in enumerate(link_edges)
                            if e == edge and li < len(phase.state)
                            and phase.state[li] in "Gg")
                    if s > score:
                        best, score = i, s
                if best is not None:
                    serve[edge] = best
            # a signal whose approaches are all served by ONE phase (mid-block
            # pedestrian crossings, paired one-way carriageways) has no
            # conflicting movements to arbitrate — early green there would be
            # a meaningless "decision", so it is excluded from this module
            if len(set(serve.values())) < 2:
                self.skipped_nonconflict += 1
                continue
            for lanes in approach.values():
                for lane in lanes:
                    traci.lane.subscribe(lane, [tc.LAST_STEP_VEHICLE_NUMBER])
            self.tls_info[tls_id] = {"approach": approach, "serve": serve,
                                     "phases": phases,
                                     "near": {}, "centre": None}
        self._build_near_approaches()
        self._build_complexes()

    def _build_near_approaches(self):
        """Physical ground truth for the lone-approach doctrine.

        The controlled-links map only knows the edges wired to the signal;
        ramp stubs, service roads and carriageways feeding the same box are
        invisible to it, so "every other approach is empty" could be true of
        the MAP while cars stood at the junction.  For every junction we
        therefore collect the edges that physically ARRIVE at it — those
        ending inside ``junction_clear_radius_m`` of the junction centre and
        coming from outside it — and require them to be empty too."""
        if self.net is None:
            return
        R = self.cfg.junction_clear_radius_m
        arrivals = []
        for e in self.net.getEdges():
            eid = e.getID()
            if eid.startswith(":") or not e.allows("passenger"):
                continue
            arrivals.append((eid, e.getToNode().getCoord(),
                             e.getFromNode().getCoord(),
                             {l.getID() for l in e.getLanes()}))
        for tls_id, info in self.tls_info.items():
            pts = []
            for lanes in info["approach"].values():
                for lane in lanes:
                    try:
                        pts.append(self.net.getLane(lane).getShape()[-1])
                    except Exception:
                        pass
            if not pts:
                continue
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            info["centre"] = (cx, cy)
            near = {}
            for eid, (tx, ty), (fx, fy), lanes in arrivals:
                if (tx - cx) ** 2 + (ty - cy) ** 2 > R * R:
                    continue                    # does not end at this box
                if (fx - cx) ** 2 + (fy - cy) ** 2 <= R * R:
                    continue                    # leaves the box: an exit
                near[eid] = lanes
            info["near"] = near
            for lanes in near.values():
                for lane in lanes:
                    traci.lane.subscribe(lane, [tc.LAST_STEP_VEHICLE_NUMBER])

    def _junction_physically_clear(self, info, edge):
        """No vehicle standing at the junction on any approach other than
        the served one — measured on the road, not on the wiring map.
        Returns (clear, evidence dict of blocking edge -> vehicle count)."""
        served = info["approach"].get(edge, set())
        blockers = {}
        for eid, lanes in info["near"].items():
            if eid == edge or lanes & served:
                continue                        # the served stream itself
            n = sum(self._lane_res.get(l, {})
                    .get(tc.LAST_STEP_VEHICLE_NUMBER, 0) for l in lanes)
            if n:
                blockers[eid] = n
        return (not blockers), blockers

    def _build_complexes(self):
        """A divided junction is several signal nodes carrying one name;
        the lone-approach doctrine must apply to the WHOLE complex.  Two
        nodes are siblings when a short edge (<60 m) leaves one and is an
        approach of the other — the internal carriageway connectors."""
        tls_in, tls_out, tls_pos = {}, {}, {}
        for tls_id in traci.trafficlight.getIDList():
            try:
                links = traci.trafficlight.getControlledLinks(tls_id)
            except traci.TraCIException:
                continue
            ins, outs = set(), set()
            pts = []
            for group in links:
                if group:
                    ins.add(group[0][0].rsplit("_", 1)[0])
                    outs.add(group[0][1].rsplit("_", 1)[0])
                    try:
                        pts.append(self.net.getLane(
                            group[0][0]).getShape()[-1])
                    except Exception:
                        pass
            tls_in[tls_id], tls_out[tls_id] = ins, outs
            if pts:
                tls_pos[tls_id] = (sum(p[0] for p in pts) / len(pts),
                                   sum(p[1] for p in pts) / len(pts))

        def elen(eid):
            try:
                return self.net.getEdge(eid).getLength()
            except Exception:
                return 1e9
        parent = {t: t for t in tls_in}

        def find(t):
            while parent[t] != t:
                parent[t] = parent[parent[t]]
                t = parent[t]
            return t
        ids = list(tls_in)
        for a in ids:
            for b in ids:
                if a >= b:
                    continue
                near = False
                pa, pb = tls_pos.get(a), tls_pos.get(b)
                if pa and pb:
                    # wide interchanges: sibling stop lines can sit ~90 m
                    # apart with no short connector between them
                    near = ((pa[0] - pb[0]) ** 2
                            + (pa[1] - pb[1]) ** 2) < 90.0 ** 2
                if near or \
                   any(elen(e) < 60.0 for e in tls_out[a] & tls_in[b]) or \
                   any(elen(e) < 60.0 for e in tls_out[b] & tls_in[a]):
                    parent[find(a)] = find(b)
        groups = {}
        for t in ids:
            groups.setdefault(find(t), []).append(t)
        for members in groups.values():
            if len(members) < 2:
                continue
            all_in = set().union(*(tls_in[t] for t in members))
            all_out = set().union(*(tls_out[t] for t in members))
            internal = {e for e in all_in & all_out if elen(e) < 60.0}
            ext = {}
            for e in all_in - internal:
                lanes = self._zone_lanes(e)
                if lanes:
                    ext[e] = lanes
                    for lane in lanes:
                        traci.lane.subscribe(
                            lane, [tc.LAST_STEP_VEHICLE_NUMBER])
            for t in members:
                if t in self.tls_info:
                    self.complex_ext[t] = ext
            self._all_complex_edges.update(ext)

    def _complex_clear(self, tls_id, edge, now):
        """True when the WHOLE junction complex (all sibling nodes) has no
        other external approach that carried traffic within the quiet
        window — the doctrine's definition of "alone at the junction"."""
        ext = self.complex_ext.get(tls_id)
        if not ext:
            return True
        if edge not in ext:
            return False      # internal connector: never a lone arrival
        for e in ext:
            if e != edge and now - self._seen_any.get(e, -1e9) \
                    <= self.cfg.lone_quiet_s:
                return False
        return True

    def _zone_lanes(self, edge_id):
        """All lanes within detection_zone_m upstream of the stop line,
        walking back across edge boundaries (all predecessors)."""
        lanes = set()
        if self.net is None:
            return lanes
        try:
            start = self.net.getEdge(edge_id)
        except Exception:
            return lanes
        frontier = [(start, 0.0)]
        seen = {edge_id}
        while frontier:
            edge, covered = frontier.pop()
            for lane in edge.getLanes():
                lanes.add(lane.getID())
            covered += edge.getLength()
            if covered >= self.cfg.detection_zone_m:
                continue
            for prev in edge.getIncoming():
                pid = prev.getID()
                if pid in seen or pid.startswith(":"):
                    continue
                # stop at another signalized junction: that is its zone
                if prev.getToNode().getType().startswith("traffic_light") \
                        and prev.getID() != edge_id and covered > 0:
                    continue
                seen.add(pid)
                frontier.append((prev, covered))
        return lanes

    # -------------------------------------------------------------- queries

    def status(self):
        return {tls: SERVING if c["mode"] == SERVING else AMBERING
                for tls, c in self.claims.items()}

    def active_count(self):
        return sum(1 for c in self.claims.values() if c["mode"] == SERVING)

    # ------------------------------------------------------------------ tick

    def update(self, now, excluded):
        """excluded: junction ids the preemption controller owns right now."""
        if not self.enabled:
            self.claims.clear()
            self.pending.clear()
            self.released.clear()
            return
        lane_res = traci.lane.getAllSubscriptionResults()
        self._lane_res = lane_res
        for e, lanes in self._all_complex_edges.items():
            if any(lane_res.get(l, {}).get(tc.LAST_STEP_VEHICLE_NUMBER, 0)
                   for l in lanes):
                self._seen_any[e] = now
        fair = lone = occ_n = 0
        for tls_id, info in self.tls_info.items():
            if tls_id in excluded:
                # preemption outranks us; drop any claim without touching
                # the signal (the preemption controller owns it now)
                claim = self.claims.pop(tls_id, None)
                if claim is not None:
                    self.ops.emit(now, "actuation",
                                  f"Junction {self.ops.jn(tls_id)}: early green "
                                  f"ended — an ambulance corridor takes priority "
                                  f"at this junction", "info")
                    # rule D3: cooldown also applies when a corridor takes the box
                    self.cooldown[tls_id] = now + self.cfg.actuation_cooldown_s
                self.pending.pop(tls_id, None)
                # the preemption controller now drives the phases: our
                # tripwire could no longer attribute what it sees to us
                self.released.pop(tls_id, None)
                continue
            occ = {edge: sum(lane_res.get(lane, {})
                             .get(tc.LAST_STEP_VEHICLE_NUMBER, 0)
                             for lane in lanes)
                   for edge, lanes in info["approach"].items()}
            occupied = [e for e, n in occ.items() if n > 0]
            seen = self._last_seen.setdefault(tls_id, {})
            for e in occupied:
                seen[e] = now
            # approaches that carried traffic within the quiet window count
            # as "in use" even if momentarily empty between platoons
            in_use = [e for e, t in seen.items()
                      if now - t <= self.cfg.lone_quiet_s]
            if len(in_use) >= 2:
                fair += 1
            elif len(in_use) == 1:
                lone += 1
            if in_use:
                occ_n += 1
            if tls_id in self.released:
                self._check_released(tls_id, occupied, now)
            claim = self.claims.get(tls_id)
            if claim is not None:
                self._advance(tls_id, claim, occ, occupied, now)
            elif (len(occupied) == 1 and len(in_use) == 1
                  and now >= self.cooldown.get(tls_id, 0.0)):
                if self._complex_clear(tls_id, occupied[0], now):
                    self._consider(tls_id, info, occupied[0], now)
                else:
                    # a sibling node of the SAME physical junction has
                    # traffic: fair timers by design, no statement emitted
                    self.audit["complex_blocked"] += 1
                    self.pending.pop(tls_id, None)
            else:
                self.pending.pop(tls_id, None)
        self._modes = {"fair": fair, "lone": lone, "occupied": occ_n}

    def _check_released(self, tls_id, occupied, now):
        """Fairness tripwire, armed on the step an early green was ended.

        The release command (setPhaseDuration -> 1 s) is an instruction, not
        an outcome: the programme can override it or the command can be
        dropped.  If the junction is STILL showing the early-green phase
        more than ``_release_grace_s`` after the release while another
        approach is occupied, the hold outlived its minimum green at another
        approach's expense — that is a fairness violation, and it is counted
        once per released hold.  Any real phase change disarms the wire."""
        rec = self.released[tls_id]
        if now - rec["at"] > self._release_watch_s:
            self.released.pop(tls_id, None)
            return
        try:
            phase = traci.trafficlight.getPhase(tls_id)
        except traci.TraCIException:
            self.released.pop(tls_id, None)
            return
        if phase != rec["serve"]:
            self.released.pop(tls_id, None)     # the green really did end
            return
        if now - rec["at"] <= self._release_grace_s:
            return                              # inside the wind-down
        others = [e for e in occupied if e != rec["edge"]]
        if not others:
            return                              # nobody is being held up
        self.released.pop(tls_id, None)         # one count per released hold
        self.audit["violations"] += 1
        self.ops.emit(now, "actuation",
                      f"Junction {self.ops.jn(tls_id)}: FAIRNESS VIOLATION — "
                      f"the early green on {self.ops.rd(rec['edge'])} was "
                      f"ended {now - rec['at']:.0f} s ago but the junction "
                      f"is still showing that phase while "
                      f"{self.ops.rd(others[0])} is occupied; the release "
                      f"command did not take effect", "warn")

    def _consider(self, tls_id, info, edge, now):
        serve_idx = info["serve"].get(edge)
        if serve_idx is None:
            return
        pend = self.pending.get(tls_id)
        if pend is None or pend[0] != edge:
            self.pending[tls_id] = (edge, now)
            return
        if now - pend[1] < self.cfg.lone_confirm_s:
            return
        clear, blockers = self._junction_physically_clear(info, edge)
        if not clear:
            # another direction of this junction is physically occupied:
            # fair timers by design — and no statement is emitted, because
            # nothing happened
            self.audit["proximity_blocked"] += 1
            self.pending.pop(tls_id, None)
            return
        try:
            cur = traci.trafficlight.getPhase(tls_id)
        except traci.TraCIException:
            return
        if cur == serve_idx:
            # already being served by the normal cycle: extend it instead of
            # letting the timer cut a lone stream off mid-service
            try:
                traci.trafficlight.setPhaseDuration(
                    tls_id, self.cfg.lone_max_hold_s)
            except traci.TraCIException:
                return
            self.claims[tls_id] = {"mode": SERVING, "edge": edge,
                                   "serve": serve_idx, "until": 0.0,
                                   "since": now}
            self.pending.pop(tls_id, None)
            self.released.pop(tls_id, None)   # this hold owns the phase now
            self.granted_total += 1
            self.audit["extensions"] += 1
            self.ops.emit(now, "actuation",
                          f"Junction {self.ops.jn(tls_id)}: green EXTENDED for the only "
                          f"occupied approach ({self.ops.rd(edge)}) — every other "
                          f"approach empty, and no vehicle standing within "
                          f"{self.cfg.junction_clear_radius_m:.0f} m of the "
                          f"junction on any of them", "info")
            return
        self._grant(tls_id, info, edge, serve_idx, cur, now)

    def _grant(self, tls_id, info, edge, serve_idx, cur, now):
        phases = info["phases"]
        claim = {"mode": AMBERING, "edge": edge, "serve": serve_idx,
                 "until": now, "since": now}
        try:
            state = phases[cur].state
            if "y" in state:
                # let the running amber finish first
                claim["until"] = max(
                    now, traci.trafficlight.getNextSwitch(tls_id))
            elif any(ch in "Gg" for ch in state):
                nxt = (cur + 1) % len(phases)
                if "y" in phases[nxt].state:
                    traci.trafficlight.setPhase(tls_id, nxt)
                    claim["until"] = now + phases[nxt].duration
                # no amber phase in the programme: the approaches being cut
                # are empty (that is the trigger), a direct jump is safe
        except traci.TraCIException:
            return
        self.claims[tls_id] = claim
        self.pending.pop(tls_id, None)
        self.released.pop(tls_id, None)       # this hold owns the phase now
        self.granted_total += 1
        self.audit["grants"] += 1
        self.ops.emit(now, "actuation",
                      f"Junction {self.ops.jn(tls_id)}: EARLY GREEN granted to the only "
                      f"occupied approach ({self.ops.rd(edge)}) — every other approach "
                      f"empty for {self.cfg.lone_confirm_s:.0f} s and no vehicle "
                      f"standing within {self.cfg.junction_clear_radius_m:.0f} m of "
                      f"the junction on any of them; no reason to hold traffic "
                      f"on a timer", "info")

    def _advance(self, tls_id, claim, occ, occupied, now):
        try:
            if claim["mode"] == AMBERING:
                if now >= claim["until"]:
                    traci.trafficlight.setPhase(tls_id, claim["serve"])
                    traci.trafficlight.setPhaseDuration(
                        tls_id, self.cfg.lone_max_hold_s)
                    claim["mode"] = SERVING
                    claim["since"] = now
                return
            others = [e for e in occupied if e != claim["edge"]]
            served_min = now - claim["since"] >= self.cfg.lone_min_green_s
            lane_empty = occ.get(claim["edge"], 0) == 0
            timeout = now - claim["since"] >= self.cfg.lone_max_hold_s
            # how long another approach has been waiting for this hold to
            # end.  Bounded by lone_min_green_s by construction (the release
            # below fires on the first step where both conditions hold), so
            # this measures the guarantee rather than asserting it.
            if others:
                claim.setdefault("others_since", now)
            else:
                claim.pop("others_since", None)
            if others and served_min:
                self.audit["ended_for_other_traffic"] += 1
            if (others and served_min) or (lane_empty and served_min) or timeout:
                waited = now - claim.get("others_since", now)
                if waited > self.audit["max_other_wait_s"]:
                    self.audit["max_other_wait_s"] = round(waited, 1)
                # end the green promptly; the programme then continues its
                # normal cycle from here — fair timers for everyone
                traci.trafficlight.setPhaseDuration(tls_id, 1.0)
                reason = ("another approach is now occupied — fair timers "
                          "resume" if others
                          else "the lone traffic has passed" if lane_empty
                          else f"max early-green hold "
                               f"({self.cfg.lone_max_hold_s:.0f} s) reached")
                self.ops.emit(now, "actuation",
                              f"Junction {self.ops.jn(tls_id)}: early green ended — "
                              f"{reason}", "info")
                self.cooldown[tls_id] = now + self.cfg.actuation_cooldown_s
                self.claims.pop(tls_id, None)
                # arm the fairness tripwire: the command above must actually
                # take the junction off this phase (see _check_released)
                self.released[tls_id] = {"serve": claim["serve"],
                                         "edge": claim["edge"], "at": now}
        except traci.TraCIException:
            self.claims.pop(tls_id, None)
