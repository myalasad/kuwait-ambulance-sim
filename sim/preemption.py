"""Route green-wave signal preemption for ambulances.

Control model (mirrors real emergency-vehicle-preemption deployments):

1.  Every signalized junction carries a virtual enforcement camera that can
    recognise an ambulance running its emergency lights up to
    ``camera_range_m`` along its approaches.
2.  The first camera detection "confirms" the ambulance to the traffic-
    management centre.  From then on the centre — which knows the planned
    route — activates a *green corridor* along it.
3.  A signal is switched when the ambulance's estimated arrival drops below
    ``greenwave_lead_s`` (never later than ``greenwave_min_m`` out, never
    earlier than ``greenwave_distance_m``).  ETA-based activation matters:
    a fixed distance would let an ambulance crawling through a jam hold
    junctions far ahead for minutes, starving cross streets and gridlocking
    the very grid it is trying to cross.
4.  Preempting a junction means: conflicting greens get ``yellow_time_s`` of
    amber, then an all-red clearance interval, then the controller **jumps to
    and holds the real programme phase** serving the ambulance's approach.
    Signals are never switched dark — dark signals cause crashes.
5.  A single hold is capped at ``max_hold_s``; beyond it the junction cycles
    normally for at least ``preempt_cooldown_s`` (unless the ambulance is
    already at the stop line, within camera range).
6.  Once the ambulance has passed (plus a small clearance time, and only
    after it is physically clear of the junction box) the junction is
    stepped back to its normal programme, again via an amber transition.

Conflict handling between two priority vehicles:

*  A junction already serving a corridor keeps serving it (continuity) —
   switching mid-approach would trap both streams.
*  Two fresh requests with a clear distance margin: the nearest wins, and
   the arbitration is logged as an operation.
*  Two fresh requests closer than ``arbitration_tie_m``: the controller
   declares itself unable to decide, leaves the junction on its normal
   programme (the safe state), opens a D-case, and refers the choice to the
   operator; if no decision arrives within ``operator_timeout_s`` the
   default policy (nearest) applies automatically.

Every state change is emitted as a structured operation with a case id, so
the purposely-enabled period of each junction is fully reported from
activation to restoration.
"""
import traci

NORMAL = "normal"
TO_PREEMPT = "transition"    # amber / all-red shown before the corridor
PREEMPTED = "preempted"      # corridor green active (purposely enabled)
CLEARING = "clearing"        # ambulance just passed; corridor held briefly
TO_NORMAL = "restoring"      # amber before normal programme resumes


class _TlsState:
    __slots__ = ("mode", "orig_program", "orig_phase", "target",
                 "link_indices", "until", "started_at", "did_allred",
                 "amb", "case")

    def __init__(self, orig_program, orig_phase, now):
        self.mode = NORMAL
        self.orig_program = orig_program
        self.orig_phase = orig_phase
        self.target = ""
        self.link_indices = frozenset()
        self.until = 0.0
        self.started_at = now
        self.did_allred = False
        self.amb = None
        self.case = None


class GreenWaveController:
    def __init__(self, cfg, ops, enabled=True):
        self.cfg = cfg
        self.ops = ops
        self.enabled = enabled
        self.active = {}          # tls_id -> _TlsState
        self.cooldown = {}        # tls_id -> sim time until re-arm is allowed
        self.confirmed = set()    # ambulance ids confirmed by a camera
        self.camera_logged = set()  # (tls_id, amb_id) pairs already reported
        self.pending = {}         # tls_id -> referred decision awaiting operator
        self._arb_logged = {}     # tls_id -> contender set already logged
        self._node2tls = None     # junction node id -> tls id (built lazily)
        self._now = 0.0

    # ------------------------------------------------------------------ API

    def set_enabled(self, on: bool, who="operator") -> None:
        if self.enabled == on:
            return
        self.enabled = on
        self.ops.emit(self._now, "system",
                      f"Preemption system {'ARMED' if on else 'DISARMED'} "
                      f"by {who}", "warn", actor=who)

    def status(self) -> dict:
        return {tls: {"m": st.mode, "case": st.case, "amb": st.amb}
                for tls, st in self.active.items()}

    def modes(self) -> dict:
        return {tls: st.mode for tls, st in self.active.items()}

    def active_count(self) -> int:
        return sum(1 for st in self.active.values()
                   if st.mode in (TO_PREEMPT, PREEMPTED, CLEARING))

    def pending_decisions(self):
        return [{"tls": tls, "case": p["case"],
                 "candidates": [{"amb": a, "dist": round(d)}
                                for a, d in p["candidates"]],
                 "deadline": p["deadline"]}
                for tls, p in self.pending.items() if p["choice"] is None]

    def decide(self, tls_id, amb_id, who="operator"):
        """Operator/supervisor resolves a referred conflict."""
        pend = self.pending.get(tls_id)
        if pend is None or pend["choice"] is not None:
            return False
        if amb_id not in [a for a, _ in pend["candidates"]]:
            return False
        pend["choice"] = amb_id
        self.ops.emit(self._now, "decision_made",
                      f"Junction {tls_id}: {who} granted priority to {amb_id}",
                      "decision", actor=who, case=pend["case"])
        self.ops.close_case(pend["case"], self._now,
                            f"{who} granted {amb_id}")
        return True

    # ------------------------------------------------------------------ tick

    def update(self, ambulance_ids, now: float) -> None:
        """Advance the controller one simulation step."""
        cfg = self.cfg
        self._now = now
        # tls_id -> {amb_id: [set of link indices, min dist]}
        requests = {}
        if self.enabled:
            for amb_id in ambulance_ids:
                try:
                    upcoming = traci.vehicle.getNextTLS(amb_id)
                    speed = traci.vehicle.getSpeed(amb_id)
                except traci.TraCIException:
                    continue
                if amb_id in self.confirmed:
                    reach = min(cfg.greenwave_distance_m,
                                max(cfg.greenwave_min_m,
                                    speed * cfg.greenwave_lead_s))
                else:
                    reach = cfg.camera_range_m
                for tls_id, link_index, dist, _state in upcoming:
                    if dist <= cfg.camera_range_m:
                        self._camera_report(tls_id, amb_id, dist)
                    if dist <= reach:
                        rec = requests.setdefault(tls_id, {}).setdefault(
                            amb_id, [set(), dist])
                        rec[0].add(link_index)
                        rec[1] = min(rec[1], dist)
                # Past the stop line but still physically inside the junction
                # (on an internal lane): keep the hold, or the cross street
                # goes green and boxes the ambulance in mid-junction.
                tls_inside = self._tls_containing(amb_id)
                if tls_inside is not None:
                    st = self.active.get(tls_inside)
                    if st is not None:
                        requests.setdefault(tls_inside, {}).setdefault(
                            amb_id, [set(st.link_indices), 0.0])

        wanted = self._arbitrate(requests, now)

        # Start or refresh preemption on wanted junctions.
        for tls_id, (idxs, dist, amb) in wanted.items():
            in_cooldown = (self.cooldown.get(tls_id, 0.0) > now
                           and dist > cfg.camera_range_m)
            st = self.active.get(tls_id)
            if st is None:
                if not in_cooldown:
                    st = self._begin(tls_id, frozenset(idxs), now, amb)
                    if st is not None:
                        self.active[tls_id] = st
            else:
                st.amb = amb
                if st.mode == CLEARING and not in_cooldown:
                    # Re-armed (same or another ambulance).  Re-issue the
                    # corridor state: CLEARING may have been entered from
                    # TO_PREEMPT, in which case the display still shows the
                    # amber/all-red transition and the target was never
                    # applied — without this the junction freezes.
                    st.mode = PREEMPTED
                    st.started_at = now
                    self._set_state(tls_id, st, st.target)
                elif st.mode == TO_NORMAL and not in_cooldown:
                    # Re-armed while ambering down: conflicts are already amber
                    # or red, so the corridor green can be applied directly.
                    _yellow, target = self._build_states(tls_id, idxs)
                    if target:
                        st.link_indices = frozenset(idxs)
                        st.target = target
                        self._set_state(tls_id, st, target)
                        st.mode = PREEMPTED
                        st.started_at = now
                if st.mode == PREEMPTED and frozenset(idxs) != st.link_indices:
                    yellow, target = self._build_states(tls_id, idxs)
                    if target and target != st.target:
                        st.link_indices = frozenset(idxs)
                        st.target = target
                        try:
                            current = traci.trafficlight.getRedYellowGreenState(tls_id)
                        except traci.TraCIException:
                            continue
                        if yellow != current:
                            # some greens must drop to red: full amber +
                            # all-red transition before the new corridor
                            self._set_state(tls_id, st, yellow)
                            st.mode = TO_PREEMPT
                            st.did_allred = False
                            st.until = now + cfg.yellow_time_s
                        else:
                            self._set_state(tls_id, st, target)
                    elif target:
                        st.link_indices = frozenset(idxs)

        # Advance state machines; release passed or over-held junctions.
        for tls_id, st in list(self.active.items()):
            if st.mode == TO_PREEMPT and now >= st.until:
                if not st.did_allred and cfg.allred_time_s > 0:
                    # all-red clearance: let vehicles trapped in the box leave
                    # before the corridor green charges through it
                    st.did_allred = True
                    try:
                        n = len(traci.trafficlight.getRedYellowGreenState(tls_id))
                    except traci.TraCIException:
                        self._fail_safe(tls_id, st, "state read failed", now)
                        continue
                    self._set_state(tls_id, st, "r" * n)
                    st.until = now + cfg.allred_time_s
                    self.ops.emit(now, "preempt_phase",
                                  f"Junction {tls_id}: all-red clearance "
                                  f"({cfg.allred_time_s:.0f} s)", "info",
                                  case=st.case)
                else:
                    self._set_state(tls_id, st, st.target)
                    st.mode = PREEMPTED
                    self.ops.emit(now, "preempt_phase",
                                  f"Junction {tls_id}: corridor green ACTIVE "
                                  f"for {st.amb}", "info", case=st.case)
            if st.mode == TO_NORMAL:
                if now >= st.until:
                    self._finish_restore(tls_id, st, now)
                continue
            if tls_id in wanted:
                if (st.mode == PREEMPTED
                        and now - st.started_at > cfg.max_hold_s
                        and wanted[tls_id][1] > cfg.camera_range_m):
                    self.cooldown[tls_id] = now + cfg.preempt_cooldown_s
                    self.ops.emit(now, "hold_limit",
                                  f"Junction {tls_id} held {cfg.max_hold_s:.0f} s"
                                  f" — cycling cross traffic before re-arming",
                                  "warn", case=st.case)
                    self._begin_restore(tls_id, st, now)
                continue
            if st.mode in (TO_PREEMPT, PREEMPTED):
                st.mode = CLEARING
                st.until = now + cfg.clearance_after_pass_s
            elif st.mode == CLEARING and now >= st.until:
                self._begin_restore(tls_id, st, now)

    def release_all(self, now: float) -> None:
        """Restore every preempted junction (used when disarming/resetting)."""
        for tls_id, st in list(self.active.items()):
            if st.mode in (TO_PREEMPT, PREEMPTED, CLEARING):
                self._begin_restore(tls_id, st, now)

    # --------------------------------------------------------- arbitration

    def _arbitrate(self, requests, now):
        cfg = self.cfg
        wanted = {}

        # dissolve referred conflicts whose contention has passed
        for tls_id in list(self.pending):
            by_amb = requests.get(tls_id, {})
            if len(by_amb) < 2:
                pend = self.pending.pop(tls_id)
                if pend["choice"] is None:
                    self.ops.emit(now, "decision_moot",
                                  f"Junction {tls_id}: conflict dissolved "
                                  f"(a corridor passed or diverged)", "info",
                                  case=pend["case"])
                    self.ops.close_case(pend["case"], now,
                                        "conflict dissolved before decision")

        for tls_id, by_amb in requests.items():
            st = self.active.get(tls_id)
            # continuity: a junction already serving a corridor keeps serving
            # its ambulance — switching mid-approach traps both streams
            if (st is not None and st.amb in by_amb
                    and st.mode in (TO_PREEMPT, PREEMPTED, CLEARING)):
                idxs, dist = by_amb[st.amb]
                wanted[tls_id] = [idxs, dist, st.amb]
                continue
            ranked = sorted(by_amb.items(), key=lambda kv: kv[1][1])
            if len(ranked) == 1:
                amb, (idxs, dist) = ranked[0]
                wanted[tls_id] = [idxs, dist, amb]
                self._arb_logged.pop(tls_id, None)
                continue

            (amb1, (i1, d1)), (amb2, (i2, d2)) = ranked[0], ranked[1]
            pend = self.pending.get(tls_id)
            if pend is not None:
                if pend["choice"] is not None and pend["choice"] in by_amb:
                    idxs, dist = by_amb[pend["choice"]]
                    wanted[tls_id] = [idxs, dist, pend["choice"]]
                elif pend["choice"] is None and now >= pend["deadline"]:
                    self.ops.emit(now, "decision_made",
                                  f"Junction {tls_id}: no operator decision in "
                                  f"{cfg.operator_timeout_s:.0f} s — default "
                                  f"policy applied, {amb1} granted (nearest)",
                                  "decision", actor="policy",
                                  case=pend["case"])
                    self.ops.close_case(pend["case"], now,
                                        f"timeout — policy granted {amb1}")
                    pend["choice"] = amb1
                    wanted[tls_id] = [i1, d1, amb1]
                # else: still awaiting the operator; junction stays on its
                # normal programme — the safe state
                continue

            if abs(d2 - d1) <= cfg.arbitration_tie_m:
                case = self.ops.open_case(
                    "D", tls_id, now,
                    f"Priority conflict at {tls_id}: "
                    + " vs ".join(a for a, _ in ranked))
                self.pending[tls_id] = {
                    "case": case, "choice": None,
                    "deadline": now + cfg.operator_timeout_s,
                    "candidates": [(a, r[1]) for a, r in ranked],
                }
                self.ops.emit(now, "decision_referred",
                              f"Junction {tls_id}: UNABLE TO DECIDE — "
                              f"{amb1} at {d1:.0f} m and {amb2} at {d2:.0f} m "
                              f"(margin under {cfg.arbitration_tie_m:.0f} m). "
                              f"Referred to operator; junction held on normal "
                              f"programme; default policy in "
                              f"{cfg.operator_timeout_s:.0f} s", "decision",
                              case=case)
                continue

            contenders = tuple(sorted(by_amb))
            if self._arb_logged.get(tls_id) != contenders:
                self._arb_logged[tls_id] = contenders
                self.ops.emit(now, "arbitration",
                              f"Junction {tls_id}: {len(by_amb)} corridors "
                              f"requested — {amb1} granted (nearest: "
                              f"{d1:.0f} m vs {d2:.0f} m); "
                              f"{amb2} queued behind it", "warn", actor=amb1)
            wanted[tls_id] = [i1, d1, amb1]
        return wanted

    # ------------------------------------------------------------- internals

    def _set_state(self, tls_id, st, state):
        try:
            traci.trafficlight.setRedYellowGreenState(tls_id, state)
        except traci.TraCIException as exc:
            self._fail_safe(tls_id, st, str(exc), self._now)

    def _fail_safe(self, tls_id, st, reason, now):
        """On any signal-command error: revert the junction to its normal
        programme immediately and report the case as errored."""
        self.ops.emit(now, "error",
                      f"Junction {tls_id}: signal command FAILED ({reason}) — "
                      f"fail-safe: reverting to normal programme", "error",
                      case=st.case)
        try:
            traci.trafficlight.setProgram(tls_id, st.orig_program)
            traci.trafficlight.setPhase(tls_id, st.orig_phase)
        except traci.TraCIException:
            pass
        if st.case:
            self.ops.close_case(st.case, now, f"error: {reason}",
                                status="error")
        self.active.pop(tls_id, None)

    def _tls_containing(self, veh_id):
        """The tls id whose junction the vehicle is currently inside, if any
        (vehicles on internal lanes have lane ids like ':<node>_<i>_<j>')."""
        try:
            lane = traci.vehicle.getLaneID(veh_id)
        except traci.TraCIException:
            return None
        if not lane.startswith(":"):
            return None
        if self._node2tls is None:
            self._node2tls = {}
            for tls_id in traci.trafficlight.getIDList():
                for group in traci.trafficlight.getControlledLinks(tls_id):
                    for _in, _out, via in group:
                        if via.startswith(":"):
                            node = via[1:].rsplit("_", 2)[0]
                            self._node2tls[node] = tls_id
        node = lane[1:].rsplit("_", 2)[0]
        return self._node2tls.get(node)

    def _camera_report(self, tls_id, amb_id, dist):
        if (tls_id, amb_id) in self.camera_logged:
            return
        self.camera_logged.add((tls_id, amb_id))
        self.ops.emit(self._now, "camera",
                      f"Camera at junction {tls_id} detected {amb_id} "
                      f"with lights on, {dist:.0f} m out", "info",
                      actor=amb_id)
        if amb_id not in self.confirmed:
            self.confirmed.add(amb_id)
            self.ops.emit(self._now, "corridor",
                          f"Control centre confirmed {amb_id} — green "
                          f"corridor activated along its planned route",
                          "info", actor=amb_id)

    def _build_states(self, tls_id, link_indices):
        """Return (amber_transition_state, preempt_target_state).

        The target is a *real phase* of the junction's own signal programme
        that serves every ambulance link green — what real preemption
        controllers do (jump to and hold a phase).  Real phases are
        internally consistent: compatible cross flows and drain paths keep
        moving, which matters enormously at multi-node junctions where a
        hand-crafted "corridor green, everything else red" state can seal
        vehicles inside the box and deadlock the corridor itself.
        """
        try:
            current = traci.trafficlight.getRedYellowGreenState(tls_id)
        except traci.TraCIException:
            return "", ""
        n = len(current)
        if n == 0:
            return "", ""

        target = self._phase_target(tls_id, link_indices, n)
        if target is None:
            target = self._custom_target(tls_id, link_indices, n)
        if not target:
            return "", ""

        yellow = "".join(
            "y" if (current[i] in "Gg" and target[i] == "r") else current[i]
            for i in range(n)
        )
        return yellow, target

    def _phase_target(self, tls_id, link_indices, n):
        """Best programme phase serving all ambulance links green, if any."""
        try:
            logics = traci.trafficlight.getAllProgramLogics(tls_id)
        except traci.TraCIException:
            return None
        if not logics:
            return None
        st = self.active.get(tls_id)
        try:
            prog = st.orig_program if st else traci.trafficlight.getProgram(tls_id)
        except traci.TraCIException:
            prog = None
        logic = next((lg for lg in logics if lg.programID == prog), logics[0])

        best, best_score = None, -1
        for phase in logic.phases:
            state = phase.state
            if len(state) != n or "y" in state:   # skip transition phases
                continue
            score = 0
            for idx in link_indices:
                if idx >= n:
                    score = -1
                    break
                if state[idx] == "G":
                    score += 2
                elif state[idx] in "gs":
                    score += 1
                else:
                    score = -1
                    break
            if score > best_score:
                best, best_score = state, score
        return best

    def _custom_target(self, tls_id, link_indices, n):
        """Fallback when no programme phase serves the ambulance: corridor
        approach protected green, everything else red."""
        try:
            links = traci.trafficlight.getControlledLinks(tls_id)
        except traci.TraCIException:
            return ""
        if len(links) != n:
            return ""
        approach_edges = set()
        for idx in link_indices:
            if idx < n and links[idx]:
                approach_edges.add(links[idx][0][0].rsplit("_", 1)[0])
        target = []
        for i, group in enumerate(links):
            if i in link_indices:
                target.append("G")
            elif group and group[0][0].rsplit("_", 1)[0] in approach_edges:
                # protected green: a yielding 'g' here would defer to the
                # ambulance's own priority stream behind it and deadlock
                target.append("G")
            else:
                target.append("r")
        return "".join(target)

    def _begin(self, tls_id, link_indices, now, amb):
        try:
            orig_program = traci.trafficlight.getProgram(tls_id)
            orig_phase = traci.trafficlight.getPhase(tls_id)
            yellow, target = self._build_states(tls_id, link_indices)
            current = traci.trafficlight.getRedYellowGreenState(tls_id)
        except traci.TraCIException:
            return None
        if not target:
            return None
        st = _TlsState(orig_program, orig_phase, now)
        st.link_indices = link_indices
        st.target = target
        st.amb = amb
        st.case = self.ops.open_case("P", tls_id, now,
                                     f"Junction {tls_id} corridor for {amb}")
        self.ops.emit(now, "preempt_start",
                      f"Junction {tls_id} PURPOSELY ENABLED for {amb}: "
                      f"approach green, conflicts to red", "warn",
                      actor=amb, case=st.case)
        if yellow != current:
            self._set_state(tls_id, st, yellow)
            st.mode = TO_PREEMPT
            st.until = now + self.cfg.yellow_time_s
            self.ops.emit(now, "preempt_phase",
                          f"Junction {tls_id}: amber to conflicting traffic "
                          f"({self.cfg.yellow_time_s:.0f} s)", "info",
                          case=st.case)
        else:
            self._set_state(tls_id, st, target)
            st.mode = PREEMPTED
            self.ops.emit(now, "preempt_phase",
                          f"Junction {tls_id}: corridor green ACTIVE for "
                          f"{amb}", "info", case=st.case)
        return st

    def _begin_restore(self, tls_id, st, now):
        try:
            current = traci.trafficlight.getRedYellowGreenState(tls_id)
        except traci.TraCIException:
            self._fail_safe(tls_id, st, "state read failed on restore", now)
            return
        yellow = "".join("y" if c in "Gg" else c for c in current)
        if yellow != current:
            self._set_state(tls_id, st, yellow)
            st.mode = TO_NORMAL
            st.until = now + self.cfg.yellow_time_s
        else:
            self._finish_restore(tls_id, st, now)

    def _finish_restore(self, tls_id, st, now):
        try:
            traci.trafficlight.setProgram(tls_id, st.orig_program)
            traci.trafficlight.setPhase(tls_id, st.orig_phase)
        except traci.TraCIException:
            pass
        held = now - st.started_at
        self.ops.emit(now, "restore",
                      f"Junction {tls_id} BACK TO NORMAL programme — "
                      f"purposely-enabled period over (held {held:.0f} s "
                      f"for {st.amb})", "info", case=st.case)
        if st.case:
            self.ops.close_case(st.case, now,
                                f"restored to normal after {held:.0f} s")
        self.active.pop(tls_id, None)
        self._arb_logged.pop(tls_id, None)
