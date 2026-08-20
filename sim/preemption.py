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
    amber, then the ambulance's approach turns green (its own connection with
    priority, neighbouring lanes of the same approach as yielding green so
    the queue in front of the ambulance discharges) and everything else is
    held red.  Signals are never switched dark — dark signals cause crashes.
5.  A single hold is capped at ``max_hold_s``; beyond it the junction cycles
    normally for at least ``preempt_cooldown_s`` (unless the ambulance is
    already at the stop line, within camera range).
6.  Once the ambulance has passed (plus a small clearance time) the junction
    is stepped back to its normal programme, again via an amber transition.
"""
import traci

NORMAL = "normal"
TO_PREEMPT = "transition"    # amber shown to conflicting traffic
PREEMPTED = "preempted"      # corridor green active
CLEARING = "clearing"        # ambulance just passed; corridor held briefly
TO_NORMAL = "restoring"      # amber before normal programme resumes


class _TlsState:
    __slots__ = ("mode", "orig_program", "orig_phase", "target",
                 "link_indices", "until", "started_at", "did_allred")

    def __init__(self, orig_program, orig_phase, now):
        self.mode = NORMAL
        self.orig_program = orig_program
        self.orig_phase = orig_phase
        self.target = ""
        self.link_indices = frozenset()
        self.until = 0.0
        self.started_at = now
        self.did_allred = False


class GreenWaveController:
    def __init__(self, cfg, log, enabled=True):
        self.cfg = cfg
        self.log = log
        self.enabled = enabled
        self.active = {}          # tls_id -> _TlsState
        self.cooldown = {}        # tls_id -> sim time until re-arm is allowed
        self.confirmed = set()    # ambulance ids confirmed by a camera
        self.camera_logged = set()  # (tls_id, amb_id) pairs already reported
        self._node2tls = None     # junction node id -> tls id (built lazily)

    # ------------------------------------------------------------------ API

    def set_enabled(self, on: bool) -> None:
        if self.enabled == on:
            return
        self.enabled = on
        self.log("Preemption system %s" % ("ARMED" if on else "DISARMED"))

    def modes(self) -> dict:
        return {tls: st.mode for tls, st in self.active.items()}

    def active_count(self) -> int:
        return sum(1 for st in self.active.values()
                   if st.mode in (TO_PREEMPT, PREEMPTED, CLEARING))

    def update(self, ambulance_ids, now: float) -> None:
        """Advance the controller one simulation step."""
        cfg = self.cfg
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

        # Arbitrate: when several ambulances converge on one junction, the
        # nearest one wins — serving both simultaneously would need protected
        # green for two crossing streams, which no safe signal state provides.
        wanted = {}  # tls_id -> [set of link indices, winning ambulance dist]
        for tls_id, by_amb in requests.items():
            idxs, dist = min(by_amb.values(), key=lambda rec: rec[1])
            wanted[tls_id] = [idxs, dist]

        # Start or refresh preemption on wanted junctions.
        for tls_id, (idxs, dist) in wanted.items():
            in_cooldown = (self.cooldown.get(tls_id, 0.0) > now
                           and dist > cfg.camera_range_m)
            st = self.active.get(tls_id)
            if st is None:
                if not in_cooldown:
                    st = self._begin(tls_id, frozenset(idxs), now)
                    if st is not None:
                        self.active[tls_id] = st
            else:
                if st.mode == CLEARING and not in_cooldown:
                    # Re-armed (same or another ambulance).  Re-issue the
                    # corridor state: CLEARING may have been entered from
                    # TO_PREEMPT, in which case the display still shows the
                    # amber/all-red transition and the target was never
                    # applied — without this the junction freezes.
                    st.mode = PREEMPTED
                    st.started_at = now
                    traci.trafficlight.setRedYellowGreenState(tls_id, st.target)
                elif st.mode == TO_NORMAL and not in_cooldown:
                    # Re-armed while ambering down: conflicts are already amber
                    # or red, so the corridor green can be applied directly.
                    _yellow, target = self._build_states(tls_id, idxs)
                    if target:
                        st.link_indices = frozenset(idxs)
                        st.target = target
                        traci.trafficlight.setRedYellowGreenState(tls_id, target)
                        st.mode = PREEMPTED
                        st.started_at = now
                if st.mode == PREEMPTED and frozenset(idxs) != st.link_indices:
                    yellow, target = self._build_states(tls_id, idxs)
                    if target and target != st.target:
                        st.link_indices = frozenset(idxs)
                        st.target = target
                        current = traci.trafficlight.getRedYellowGreenState(tls_id)
                        if yellow != current:
                            # some greens must drop to red: full amber +
                            # all-red transition before the new corridor
                            traci.trafficlight.setRedYellowGreenState(tls_id, yellow)
                            st.mode = TO_PREEMPT
                            st.did_allred = False
                            st.until = now + cfg.yellow_time_s
                        else:
                            traci.trafficlight.setRedYellowGreenState(tls_id, target)
                    elif target:
                        st.link_indices = frozenset(idxs)

        # Advance state machines; release passed or over-held junctions.
        for tls_id, st in list(self.active.items()):
            if st.mode == TO_PREEMPT and now >= st.until:
                if not st.did_allred and cfg.allred_time_s > 0:
                    # all-red clearance: let vehicles trapped in the box leave
                    # before the corridor green charges through it
                    st.did_allred = True
                    n = len(traci.trafficlight.getRedYellowGreenState(tls_id))
                    traci.trafficlight.setRedYellowGreenState(tls_id, "r" * n)
                    st.until = now + cfg.allred_time_s
                else:
                    traci.trafficlight.setRedYellowGreenState(tls_id, st.target)
                    st.mode = PREEMPTED
            if st.mode == TO_NORMAL:
                if now >= st.until:
                    self._finish_restore(tls_id, st)
                continue
            if tls_id in wanted:
                if (st.mode == PREEMPTED
                        and now - st.started_at > cfg.max_hold_s
                        and wanted[tls_id][1] > cfg.camera_range_m):
                    self.cooldown[tls_id] = now + cfg.preempt_cooldown_s
                    self.log(f"Junction {tls_id} held {cfg.max_hold_s:.0f} s — "
                             f"cycling cross traffic before re-arming")
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

    # ------------------------------------------------------------- internals

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
        self.log(f"Camera at junction {tls_id} detected {amb_id} "
                 f"with lights on, {dist:.0f} m out")
        if amb_id not in self.confirmed:
            self.confirmed.add(amb_id)
            self.log(f"Control centre confirmed {amb_id} — green corridor "
                     f"activated along its route")

    def _build_states(self, tls_id, link_indices):
        """Return (amber_transition_state, preempt_target_state).

        The target is a *real phase* of the junction's own signal programme
        that serves every ambulance link green — what real preemption
        controllers do (jump to and hold a phase).  Real phases are
        internally consistent: compatible cross flows and drain paths keep
        moving, which matters enormously at joined multi-node clusters where
        a hand-crafted "corridor green, everything else red" state can seal
        vehicles inside the box and deadlock the corridor itself.
        """
        current = traci.trafficlight.getRedYellowGreenState(tls_id)
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
        prog = st.orig_program if st else traci.trafficlight.getProgram(tls_id)
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
        links = traci.trafficlight.getControlledLinks(tls_id)
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

    def _begin(self, tls_id, link_indices, now):
        try:
            orig_program = traci.trafficlight.getProgram(tls_id)
            orig_phase = traci.trafficlight.getPhase(tls_id)
            yellow, target = self._build_states(tls_id, link_indices)
        except traci.TraCIException:
            return None
        if not target:
            return None
        st = _TlsState(orig_program, orig_phase, now)
        st.link_indices = link_indices
        st.target = target
        current = traci.trafficlight.getRedYellowGreenState(tls_id)
        if yellow != current:
            traci.trafficlight.setRedYellowGreenState(tls_id, yellow)
            st.mode = TO_PREEMPT
            st.until = now + self.cfg.yellow_time_s
        else:
            traci.trafficlight.setRedYellowGreenState(tls_id, target)
            st.mode = PREEMPTED
        self.log(f"Junction {tls_id} preempted: ambulance approach green, "
                 f"conflicts to red")
        return st

    def _begin_restore(self, tls_id, st, now):
        try:
            current = traci.trafficlight.getRedYellowGreenState(tls_id)
        except traci.TraCIException:
            self.active.pop(tls_id, None)
            return
        yellow = "".join("y" if c in "Gg" else c for c in current)
        if yellow != current:
            traci.trafficlight.setRedYellowGreenState(tls_id, yellow)
            st.mode = TO_NORMAL
            st.until = now + self.cfg.yellow_time_s
        else:
            self._finish_restore(tls_id, st)

    def _finish_restore(self, tls_id, st):
        try:
            traci.trafficlight.setProgram(tls_id, st.orig_program)
            traci.trafficlight.setPhase(tls_id, st.orig_phase)
        except traci.TraCIException:
            pass
        self.active.pop(tls_id, None)
        self.log(f"Junction {tls_id} back to normal signal programme")
