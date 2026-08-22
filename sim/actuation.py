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
    def __init__(self, cfg, ops, enabled=True):
        self.cfg = cfg
        self.ops = ops
        self.enabled = enabled
        self.tls_info = {}      # tls -> approach lanes, serving phase map
        self.claims = {}        # tls -> {"mode","edge","serve","until","since"}
        self.cooldown = {}      # tls -> sim time until next grant allowed
        self.pending = {}       # tls -> (edge, first seen lone) confirmation
        self.granted_total = 0
        self._modes = {"fair": 0, "lone": 0, "occupied": 0}
        self._last_seen = {}    # tls -> {edge: last sim time it had traffic}
        # permanent self-audit: a held early green that persists while
        # another approach has traffic (beyond the minimum green) is a
        # FAIRNESS VIOLATION; the count is shown on the dashboard
        self.audit = {"grants": 0, "extensions": 0, "violations": 0,
                      "ended_for_other_traffic": 0}
        self.skipped_nonconflict = 0
        self._build()

    def mode_counts(self):
        """How many junctions currently have several approaches occupied
        (fair timers by design), a single occupied approach (early-green
        candidates), and any traffic at all — plus the self-audit."""
        return {**self._modes, "early": self.granted_total,
                "audit": dict(self.audit),
                "arbitrated_junctions": len(self.tls_info),
                "nonconflict_excluded": self.skipped_nonconflict}

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
            approach = {}   # in-edge -> set of in-lanes
            for group in links:
                if group:
                    in_lane = group[0][0]
                    approach.setdefault(in_lane.rsplit("_", 1)[0],
                                        set()).add(in_lane)
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
                                     "phases": phases}

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
            return
        lane_res = traci.lane.getAllSubscriptionResults()
        fair = lone = occ_n = 0
        for tls_id, info in self.tls_info.items():
            if tls_id in excluded:
                # preemption outranks us; drop any claim without touching
                # the signal (the preemption controller owns it now)
                self.claims.pop(tls_id, None)
                self.pending.pop(tls_id, None)
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
            claim = self.claims.get(tls_id)
            if claim is not None:
                self._advance(tls_id, claim, occ, occupied, now)
            elif (len(occupied) == 1 and len(in_use) == 1
                  and now >= self.cooldown.get(tls_id, 0.0)):
                self._consider(tls_id, info, occupied[0], now)
            else:
                self.pending.pop(tls_id, None)
        self._modes = {"fair": fair, "lone": lone, "occupied": occ_n}

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
            self.granted_total += 1
            self.audit["extensions"] += 1
            self.ops.emit(now, "actuation",
                          f"Junction {self.ops.jn(tls_id)}: green EXTENDED for the only "
                          f"occupied approach ({self.ops.rd(edge)}) — all other "
                          f"approaches empty", "info")
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
        self.granted_total += 1
        self.audit["grants"] += 1
        self.ops.emit(now, "actuation",
                      f"Junction {self.ops.jn(tls_id)}: EARLY GREEN granted to the only "
                      f"occupied approach ({self.ops.rd(edge)}) — every other approach "
                      f"empty for {self.cfg.lone_confirm_s:.0f} s; no reason "
                      f"to hold traffic on a timer", "info")

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
            # self-audit: a hold that CONTINUES after other traffic has been
            # waiting longer than the minimum green plus one second of grace
            # is a fairness violation (releases on this step are not)
            if others:
                claim.setdefault("others_since", now)
                waited = now - claim["others_since"]
                if (waited > 1.0 and now - claim["since"]
                        > self.cfg.lone_min_green_s + 1.0):
                    self.audit["violations"] += 1
            else:
                claim.pop("others_since", None)
            if others and served_min:
                self.audit["ended_for_other_traffic"] += 1
            if (others and served_min) or (lane_empty and served_min) or timeout:
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
        except traci.TraCIException:
            self.claims.pop(tls_id, None)
