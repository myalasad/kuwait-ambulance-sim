"""Route green-wave signal preemption for ambulances.

Control model (mirrors real emergency-vehicle-preemption deployments):

1.  Every signalized junction carries an enforcement camera whose field of
    view is the physical approach roadway up to ``camera_range_m`` from the
    stop line.  Detection is JUNCTION-SIDE sensing: a camera reports an
    ambulance only when the vehicle, with active emergency lights, is
    physically inside that field of view — never inferred from the
    vehicle's own route or position feed.
2.  The first camera detection "confirms" the ambulance to the traffic-
    management centre.  Only from then on does the centre — which knows the
    planned route, as real dispatch centres do — activate a *green
    corridor* along it.  A unit no camera has seen gets nothing.
3.  A signal is switched when the ambulance's estimated arrival drops below
    ``greenwave_lead_s`` (never later than ``greenwave_min_m`` out, never
    earlier than ``greenwave_distance_m``).  ETA-based activation matters:
    a fixed distance would let an ambulance crawling through a jam hold
    junctions far ahead for minutes, starving cross streets and gridlocking
    the very grid it is trying to cross.
4.  Preempting a junction means: conflicting greens get ``yellow_time_s`` of
    amber, then an all-red clearance interval, then the held state.  The
    held state is ALWAYS built explicitly (never inherited from a phase of
    the junction's own programme, which could leave a compatible cross flow
    green): the ambulance's approach — and every approach feeding the same
    movement, so the traffic ahead of it can drain — on protected green,
    and every approach NOT on its route on SOLID RED.  Setting
    ``flash_amber`` shows those cross approaches flashing amber ('o',
    yield) instead, hardening to solid red once the ambulance is within
    ``flash_harden_eta_s`` seconds ETA or ``flash_harden_min_m`` metres.
    Signals are never switched dark — dark signals cause crashes.
    Solid red seals the junction box, so the all-red clearance interval
    always runs before the held state goes on: the box is emptied first,
    and nothing can then enter it except the corridor itself.
5.  A single continuous hold is capped at ``max_hold_s``; beyond it the
    junction cycles normally for at least ``preempt_cooldown_s``, which is
    also what drains a junction box the hold's own solid red has sealed.
    Distance is never an exemption — a unit stalled short of the stop line
    would otherwise hold cross traffic without limit.  The one exemption is
    an ambulance PHYSICALLY INSIDE the junction box, which may not be shown
    a red it is already committed past, and it is bounded: once that unit
    has been at a standstill for ``BOX_STALL_RELEASE_S`` it is no longer
    crossing but stuck, and the cap applies.
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
import traci.constants as tc

NORMAL = "normal"
TO_PREEMPT = "transition"    # amber / all-red shown before the corridor
PREEMPTED = "preempted"      # corridor green active (purposely enabled)
CLEARING = "clearing"        # ambulance just passed; corridor held briefly
TO_NORMAL = "restoring"      # amber before normal programme resumes

# How long a unit may sit at a DEAD STOP on a corridor it already holds
# before the controller stops treating it as "crossing".  Past this it is
# not crossing, it is stuck — typically behind traffic that this very hold
# is keeping on solid red — so the box-occupancy exemption from the
# ``max_hold_s`` cap lapses and the ordinary release + cooldown path runs,
# cycling the cross streets so the box can drain.  Overridable as
# cfg.box_stall_release_s.
BOX_STALL_RELEASE_S = 10.0


class _TlsState:
    __slots__ = ("mode", "orig_program", "orig_phase", "target",
                 "link_indices", "until", "started_at", "enabled_at",
                 "did_allred", "amb", "case", "hardened")

    def __init__(self, orig_program, orig_phase, now):
        self.mode = NORMAL
        self.orig_program = orig_program
        self.orig_phase = orig_phase
        self.target = ""
        self.link_indices = frozenset()
        self.until = 0.0
        # started_at is the CURRENT CONTINUOUS hold: it is reset on every
        # re-arm because rule A4 caps a continuous hold (max_hold_s).
        # enabled_at is the activation of this P-case and is never reset,
        # so the restore message can report the period the junction was
        # actually purposely enabled — the figure the Cases table shows.
        self.started_at = now
        self.enabled_at = now
        self.did_allred = False
        self.amb = None
        self.case = None
        self.hardened = False   # cross flash escalated to solid red


class GreenWaveController:
    def __init__(self, cfg, ops, enabled=True, net=None):
        self.cfg = cfg
        self.ops = ops
        self.net = net            # for building physical camera zones
        self._camera_zones = None  # edge id -> [(tls id, offset m to stop)]
        self._edge_len = {}
        self.enabled = enabled
        self.active = {}          # tls_id -> _TlsState
        self.cooldown = {}        # tls_id -> sim time until re-arm is allowed
        self.faulted = {}         # tls_id -> stand-off after a signal-command
        #                           error.  Separate from cooldown, which is
        #                           deliberately bypassed while a unit is in
        #                           the box: a junction whose controller just
        #                           rejected a command must NOT be hammered.
        self.confirmed = set()    # ambulance ids confirmed by a camera
        # ambulance id -> the junctions a corridor was actually OPENED at
        # for it.  A set, so "N junctions preempted for this unit" is exact
        # even if one junction is enabled for the unit more than once.  It
        # survives a mid-run disarm: a unit that did get corridors before
        # the operator disarmed keeps its true count.
        self.preempted_for = {}
        self.camera_logged = set()  # (tls_id, amb_id) pairs already reported
        self.pending = {}         # tls_id -> referred decision awaiting operator
        self._arb_logged = {}     # tls_id -> contender set already logged
        self._node2tls = None     # junction node id -> tls id (built lazily)
        self._now = 0.0
        self.markov = None        # congestion states for queue-flush lead
        self._links_cache = {}    # tls_id -> controlled links (static)
        self._flush_sticky = set()  # (amb, tls) flush requests held until
        #                             passed — no flap if the state flickers
        self._grant_sticky = set()  # (amb, tls) ordinary requests already
        #                             granted — released only when passed
        self._inside = set()      # junctions physically occupied by a unit
        #                           this tick (the ONLY hold-cap exemption)
        self._crossing = set()    # ...of those, the ones whose occupant is
        #                           still making progress through the box
        self._box_stall = {}      # (tls, amb) -> time an in-box unit went to
        #                           a standstill; bounds the exemption

    # ------------------------------------------------------------------ API

    def set_enabled(self, on: bool, who="operator") -> None:
        if self.enabled == on:
            return
        self.enabled = on
        self.ops.emit(self._now, "system",
                      f"Preemption system {'ARMED' if on else 'DISARMED'} "
                      f"by {who}", "warn", actor=who)

    def status(self) -> dict:
        # list() first: the web thread reads these while the sim thread
        # inserts/pops junctions (atomic snapshot avoids iteration races)
        return {tls: {"m": st.mode, "case": st.case, "amb": st.amb}
                for tls, st in list(self.active.items())}

    def active_count(self) -> int:
        return sum(1 for st in self.active.values()
                   if st.mode in (TO_PREEMPT, PREEMPTED, CLEARING))

    def pending_decisions(self):
        return [{"tls": tls, "case": p["case"],
                 "candidates": [{"amb": a, "dist": round(d)}
                                for a, d in p["candidates"]],
                 "deadline": p["deadline"]}
                for tls, p in list(self.pending.items())
                if p["choice"] is None]

    def decide(self, tls_id, amb_id, who="operator"):
        """Operator/supervisor resolves a referred conflict."""
        pend = self.pending.get(tls_id)
        if pend is None or pend["choice"] is not None:
            return False
        if amb_id not in [a for a, _ in pend["candidates"]]:
            return False
        pend["choice"] = amb_id
        self.ops.emit(self._now, "decision_made",
                      f"Junction {self.ops.jn(tls_id)}: {who} granted priority to {amb_id}",
                      "decision", actor=who, case=pend["case"])
        self.ops.close_case(pend["case"], self._now,
                            f"{who} granted {amb_id}")
        return True

    # ------------------------------------------------------------------ tick

    def update(self, ambulance_ids, now: float, next_tls=None) -> None:
        """Advance the controller one simulation step.  ``next_tls`` is an
        optional per-step cache of post-reroute getNextTLS results (filled
        by the runner's delay attribution pass) checked before fetching."""
        cfg = self.cfg
        self._now = now
        # tls_id -> {amb_id: [set of link indices, min dist]}
        requests = {}
        # junctions a unit is physically inside THIS tick.  Rebuilt every
        # step: it is the single exemption from the hold cap and the
        # cooldown, and a stale entry would re-open the unbounded hold.
        self._inside = set()
        self._crossing = set()
        inside_units = {}       # tls -> {amb ids inside its box this tick}
        stall_release_s = getattr(cfg, "box_stall_release_s",
                                  BOX_STALL_RELEASE_S)
        amb_speed = {}
        # (amb, tls) pairs whose request window came from the flush lead on
        # THIS tick — per-pair, so the "congested approach" justification is
        # only ever attached to the unit that actually earned it
        flush_pairs = set()
        seen_pairs = set()
        if self.enabled:
            # 1. PHYSICAL cameras: detection happens only when a unit with
            #    active lights is inside a junction camera's actual field
            #    of view — the sole source of confirmation.
            self._sense_cameras(ambulance_ids)
            # 2. The centre plans the corridor for CONFIRMED units only:
            #    it knows their planned route, as a real dispatch centre
            #    does — but it never acts on a unit no camera has seen.
            results = traci.vehicle.getAllSubscriptionResults()
            for amb_id in ambulance_ids:
                if amb_id not in self.confirmed:
                    continue
                try:
                    upcoming = (None if next_tls is None
                                else next_tls.get(amb_id))
                    if upcoming is None:
                        upcoming = traci.vehicle.getNextTLS(amb_id)
                    speed = results.get(amb_id, {}).get(tc.VAR_SPEED)
                    if speed is None:
                        speed = traci.vehicle.getSpeed(amb_id)
                except traci.TraCIException:
                    continue
                amb_speed[amb_id] = speed
                reach = min(cfg.greenwave_distance_m,
                            max(cfg.greenwave_min_m,
                                speed * cfg.greenwave_lead_s))
                flush_reach = min(cfg.greenwave_distance_m,
                                  reach * cfg.flush_lead_factor)
                for tls_id, link_index, dist, _state in upcoming:
                    within = dist <= reach
                    # Queue flush: a congested approach ahead on the route
                    # is enabled with EXTRA lead so its standing queue
                    # drains before the ambulance arrives — the corridor
                    # looks after signals ahead, not just the next one.
                    # Once flush-enabled, the request is STICKY for this
                    # unit until it passes: a flickering congestion state
                    # must not flap the junction.
                    # A granted flush keeps its reach even when the unit
                    # crawls: the speed-scaled window would collapse to
                    # 320 m exactly in the gridlock the drain lead was
                    # built for.  Bounded by greenwave_distance_m, and the
                    # hold cap and cooldown still protect cross traffic.
                    sticky = (amb_id, tls_id) in self._flush_sticky
                    if (not within
                            and ((sticky and dist <= cfg.greenwave_distance_m)
                                 or (dist <= flush_reach
                                     and self._approach_congested(
                                         tls_id, link_index)))):
                        within = True
                        flush_pairs.add((amb_id, tls_id))
                        self._flush_sticky.add((amb_id, tls_id))
                    # RELEASE HYSTERESIS: the speed-scaled window is an
                    # ACTIVATION test.  Re-testing it every tick to decide
                    # RELEASE makes a stop-and-go approach chatter across
                    # dist == reach, cycling the junction — and the unit's
                    # own approach — through amber every few seconds.  A
                    # granted corridor is held while the unit is still
                    # closing; max_hold_s + preempt_cooldown_s stay the
                    # protection for cross traffic.
                    if (not within
                            and (amb_id, tls_id) in self._grant_sticky
                            and dist <= cfg.greenwave_distance_m):
                        within = True
                    if within:
                        seen_pairs.add((amb_id, tls_id))
                        self._grant_sticky.add((amb_id, tls_id))
                        rec = requests.setdefault(tls_id, {}).setdefault(
                            amb_id, [set(), dist])
                        rec[0].add(link_index)
                        rec[1] = min(rec[1], dist)
                # Past the stop line but still physically inside the junction
                # (on an internal lane): keep the hold, or the cross street
                # goes green and boxes the ambulance in mid-junction.
                tls_inside = self._tls_containing(amb_id)
                if tls_inside is not None:
                    # the ONE exemption from the hold cap and the cooldown:
                    # a vehicle in the box may not be sealed in by a red
                    self._inside.add(tls_inside)
                    inside_units.setdefault(tls_inside, set()).add(amb_id)
                    # ...but only while it is actually CROSSING.  A unit at
                    # a standstill in the box is not crossing, it is stuck —
                    # typically behind traffic this very hold is keeping on
                    # solid red — so its exemption lapses (see the cap in
                    # the advance loop) and the release + cooldown cycles
                    # the cross streets, which is what drains the box.
                    if speed < 0.1:
                        self._box_stall.setdefault((tls_inside, amb_id), now)
                    else:
                        self._box_stall.pop((tls_inside, amb_id), None)
                    st = self.active.get(tls_inside)
                    if st is not None:
                        requests.setdefault(tls_inside, {}).setdefault(
                            amb_id, [set(st.link_indices), 0.0])

        self._flush_sticky &= seen_pairs   # passed / diverged: released
        self._grant_sticky &= seen_pairs   # passed / diverged: released
        # left the box: release the standstill clock for that pair
        self._box_stall = {
            k: v for k, v in self._box_stall.items()
            if k[1] in inside_units.get(k[0], ())}
        self._crossing = {
            tls for tls, ambs in inside_units.items()
            if any((tls, a) not in self._box_stall
                   or now - self._box_stall[(tls, a)] < stall_release_s
                   for a in ambs)}
        wanted = self._arbitrate(requests, now)

        # Start or refresh preemption on wanted junctions.
        for tls_id, (idxs, dist, amb) in wanted.items():
            # The cooldown is served in full, with ONE exemption: a unit
            # physically inside the junction box may not be sealed in.
            # Distance is NOT an exemption — a unit stalled a few metres
            # short of the stop line would otherwise hold cross traffic
            # indefinitely (rule A4 is a cap, not a guideline).
            in_cooldown = (self.cooldown.get(tls_id, 0.0) > now
                           and tls_id not in self._inside)
            st = self.active.get(tls_id)
            # Flashing amber for cross traffic hardens to solid red once
            # the unit is close in TIME (clearance ahead of an ambulance is
            # a time quantity: at speed this fires ~180 m out, in a crawl
            # the flash persists), with an absolute distance floor and a
            # hysteresis band so an arbitration change cannot flicker the
            # junction between the two states.
            eta = dist / max(amb_speed.get(amb, 0.0), 0.5)
            harden = (cfg.flash_amber
                      and (dist <= cfg.flash_harden_min_m
                           or eta <= cfg.flash_harden_eta_s
                           or (st is not None and st.hardened
                               and (dist <= cfg.flash_harden_min_m * 1.3
                                    or eta <= cfg.flash_harden_eta_s * 1.3))))
            if st is None:
                # a junction whose controller just rejected a command is
                # stood off, not re-armed every few seconds
                if not in_cooldown and self.faulted.get(tls_id, 0.0) <= now:
                    # the justification is the WINNER's own: was this unit's
                    # window a flush window, and is its approach congested
                    # right now (read fresh, at the moment of the emit)?
                    is_flush = (amb, tls_id) in flush_pairs
                    congested_now = is_flush and any(
                        self._approach_congested(tls_id, i) for i in idxs)
                    st = self._begin(tls_id, frozenset(idxs), now, amb,
                                     harden, flush=is_flush,
                                     congested=congested_now)
                    if st is not None:
                        self.active[tls_id] = st
            else:
                st.amb = amb
                if st.mode == CLEARING and not in_cooldown:
                    # Re-armed (same or another ambulance).  CLEARING may
                    # have been entered from TO_PREEMPT, in which case the
                    # amber/all-red transition never completed and the
                    # target was never applied — without this the junction
                    # freezes.  _arm_target re-runs the clearance interval
                    # unless the corridor state is already on display.
                    _yellow, target = self._build_states(tls_id, idxs,
                                                         harden)
                    if target:
                        st.link_indices = frozenset(idxs)
                        st.hardened = harden
                        st.started_at = now
                        self._arm_target(tls_id, st, target, now)
                elif st.mode == TO_NORMAL and not in_cooldown:
                    # Re-armed while ambering down: conflicts are already
                    # amber, so no fresh amber is needed — but the box is
                    # NOT empty, and the corridor state seals it, so the
                    # all-red clearance still runs first (_arm_target).
                    _yellow, target = self._build_states(tls_id, idxs,
                                                         harden)
                    if target:
                        st.link_indices = frozenset(idxs)
                        st.hardened = harden
                        st.started_at = now
                        self._arm_target(tls_id, st, target, now)
                elif st.mode == TO_PREEMPT and (
                        frozenset(idxs) != st.link_indices
                        or harden != st.hardened):
                    # The unit crossed the hardening boundary (or changed
                    # lanes) DURING the amber/all-red transition: refresh
                    # the pending target so the state applied at expiry is
                    # not stale.  The amber display is identical either way
                    # ('o' and 'r' both satisfy the transition condition),
                    # so the timer and the display are left untouched.
                    _yellow, target = self._build_states(tls_id, idxs,
                                                         harden)
                    if target:
                        st.link_indices = frozenset(idxs)
                        st.target = target
                        st.hardened = harden
                if st.mode == PREEMPTED and (frozenset(idxs) != st.link_indices
                                             or harden != st.hardened):
                    yellow, target = self._build_states(tls_id, idxs, harden)
                    if target and target != st.target:
                        if harden != st.hardened and cfg.flash_amber:
                            jn = self.ops.jn(tls_id)
                            if harden:
                                self.ops.emit(now, "preempt_phase",
                                              f"Junction {jn}: {amb} "
                                              f"{dist:.0f} m / "
                                              f"{min(eta, 999):.0f} s out — "
                                              f"cross flash hardened to RED "
                                              f"for final clearance", "info",
                                              actor=amb, case=st.case)
                            else:
                                # state only what the condition tested: the
                                # unit is outside the final-clearance
                                # window.  It may have slowed, or
                                # arbitration may have handed the junction
                                # to a farther unit — do not assert which.
                                self.ops.emit(now, "preempt_phase",
                                              f"Junction {jn}: {amb} "
                                              f"{dist:.0f} m / "
                                              f"{min(eta, 999):.0f} s out — "
                                              f"outside the final-clearance "
                                              f"window, cross approaches "
                                              f"back to FLASHING AMBER "
                                              f"(yield)", "info",
                                              actor=amb, case=st.case)
                        st.link_indices = frozenset(idxs)
                        st.target = target
                        st.hardened = harden
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
                            # nothing green is being taken away (a flash
                            # tightened mid-hold): no clearance interval —
                            # it would put the unit's own corridor green
                            # back to red for no gain
                            self._set_state(tls_id, st, target)
                    elif target:
                        st.link_indices = frozenset(idxs)
                        st.hardened = harden

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
                    # do not narrate a phase whose command failed into a
                    # case _fail_safe has just closed
                    if not self._set_state(tls_id, st, "r" * n):
                        continue
                    st.until = now + cfg.allred_time_s
                    self.ops.emit(now, "preempt_phase",
                                  f"Junction {self.ops.jn(tls_id)}: all-red clearance "
                                  f"({cfg.allred_time_s:.0f} s)", "info",
                                  case=st.case)
                else:
                    if not self._set_state(tls_id, st, st.target):
                        continue
                    st.mode = PREEMPTED
                    self.ops.emit(now, "preempt_phase",
                                  f"Junction {self.ops.jn(tls_id)}: corridor green ACTIVE "
                                  f"for {st.amb}", "info", case=st.case)
            if st.mode == TO_NORMAL:
                if now >= st.until:
                    self._finish_restore(tls_id, st, now)
                continue
            if tls_id in wanted:
                # Rule A4 is a HARD cap on one continuous hold.  Distance is
                # NOT an exemption: a unit stalled short of the stop line
                # stays inside camera range indefinitely, and the cap has to
                # bite.  The one exemption is a unit physically inside the
                # junction box — releasing then would put a red in front of
                # a vehicle already committed to the crossing — and it is
                # BOUNDED: a unit that has been at a standstill in there for
                # BOX_STALL_RELEASE_S is not crossing, it is stuck behind
                # traffic this very hold is holding on solid red.  Then the
                # cap applies, and the release + cooldown cycles the cross
                # streets, which is what actually drains the box.
                if (st.mode == PREEMPTED
                        and now - st.started_at > cfg.max_hold_s
                        and tls_id not in self._crossing):
                    held = now - st.started_at
                    why = (" — the unit in its junction box has been at a "
                           "standstill, which the corridor's own solid red "
                           "cannot drain"
                           if tls_id in self._inside else "")
                    self.cooldown[tls_id] = now + cfg.preempt_cooldown_s
                    self.ops.emit(now, "hold_limit",
                                  f"Junction {self.ops.jn(tls_id)} held "
                                  f"{held:.0f} s, over the "
                                  f"{cfg.max_hold_s:.0f} s cap — releasing "
                                  f"{st.amb}'s corridor{why} and cycling "
                                  f"cross traffic for "
                                  f"{cfg.preempt_cooldown_s:.0f} s before "
                                  f"re-arming", "warn", case=st.case)
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

        # Dissolve referred conflicts whose contention has passed, and drop
        # a decision whose SUBJECT has passed or diverged: the entry only
        # ever grants its own choice, so leaving it in place would block
        # every later ruling at this junction while the others still
        # contend — no corridor, no new referral, and the demand-responsive
        # controller locked out too.  This loop runs BEFORE the main
        # arbitration loop, so dropping the entry here lets the ordinary
        # tie / clear-margin logic re-run on the SAME tick: nothing is lost.
        for tls_id in list(self.pending):
            by_amb = requests.get(tls_id, {})
            pend = self.pending[tls_id]
            stale = (pend["choice"] is not None
                     and pend["choice"] not in by_amb)
            if len(by_amb) < 2 or stale:
                self.pending.pop(tls_id)
                if pend["choice"] is None:
                    self.ops.emit(now, "decision_moot",
                                  f"Junction {self.ops.jn(tls_id)}: conflict dissolved "
                                  f"(a corridor passed or diverged)", "info",
                                  case=pend["case"])
                    self.ops.close_case(pend["case"], now,
                                        "conflict dissolved before decision")
                elif stale and len(by_amb) >= 2:
                    self.ops.emit(now, "decision_moot",
                                  f"Junction {self.ops.jn(tls_id)}: "
                                  f"{pend['choice']} has passed or diverged — "
                                  f"the remaining conflict is re-arbitrated",
                                  "info", case=pend["case"])

        for tls_id, by_amb in requests.items():
            st = self.active.get(tls_id)
            # continuity: a junction already serving a corridor keeps serving
            # its ambulance — switching mid-approach traps both streams
            if (st is not None and st.amb in by_amb
                    and st.mode in (TO_PREEMPT, PREEMPTED, CLEARING)):
                idxs, dist = by_amb[st.amb]
                others = sorted(a for a in by_amb if a != st.amb)
                if others:
                    # B1 continuity is an applied rule, so it is logged like
                    # every other ruling.  De-duplicated per contender set
                    # (as the clear-margin ruling below is), and tagged
                    # "continuity" so a later clear-margin ruling at this
                    # junction is not suppressed by this entry.
                    key = ("continuity",) + tuple(sorted(by_amb))
                    if self._arb_logged.get(tls_id) != key:
                        self._arb_logged[tls_id] = key
                        follower = min(others, key=lambda a: by_amb[a][1])
                        self.ops.emit(now, "arbitration",
                                      f"Junction {self.ops.jn(tls_id)}: "
                                      f"CONTINUITY — continues serving "
                                      f"{st.amb} ({dist:.0f} m, already on "
                                      f"approach); {follower} queued behind "
                                      f"it ({by_amb[follower][1]:.0f} m)",
                                      "warn", actor=st.amb, case=st.case)
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
                                  f"Junction {self.ops.jn(tls_id)}: no operator decision in "
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
                    f"Priority conflict at {self.ops.jn(tls_id)}: "
                    + " vs ".join(a for a, _ in ranked))
                self.pending[tls_id] = {
                    "case": case, "choice": None,
                    "deadline": now + cfg.operator_timeout_s,
                    "candidates": [(a, r[1]) for a, r in ranked],
                }
                self.ops.emit(now, "decision_referred",
                              f"Junction {self.ops.jn(tls_id)}: UNABLE TO DECIDE — "
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
                              f"Junction {self.ops.jn(tls_id)}: {len(by_amb)} corridors "
                              f"requested — {amb1} granted (nearest: "
                              f"{d1:.0f} m vs {d2:.0f} m); "
                              f"{amb2} queued behind it", "warn", actor=amb1)
            wanted[tls_id] = [i1, d1, amb1]
        return wanted

    # ------------------------------------------------------------- internals

    def _set_state(self, tls_id, st, state):
        """Apply a signal state.  Returns False if the command FAILED — the
        junction has then been failed safe and dropped from self.active,
        and the caller must not go on to narrate or record the phase."""
        try:
            traci.trafficlight.setRedYellowGreenState(tls_id, state)
        except traci.TraCIException as exc:
            self._fail_safe(tls_id, st, str(exc), self._now)
            return False
        return True

    def _arm_target(self, tls_id, st, target, now):
        """Put ``target`` on the junction as the held corridor state, always
        behind the all-red clearance interval (rule A2).

        The held state puts every approach that is not on the ambulance's
        route on SOLID RED, so once it is applied nothing can drain the
        junction box.  The box must therefore be emptied first: any
        conflicting green gets its amber, then every link is held red for
        ``allred_time_s``, and only then does the corridor green go on.
        The one case with nothing to clear is a junction already showing
        exactly this state.  Returns False if a signal command failed."""
        try:
            current = traci.trafficlight.getRedYellowGreenState(tls_id)
        except traci.TraCIException:
            self._fail_safe(tls_id, st, "state read failed", now)
            return False
        st.target = target
        if current == target:
            # The junction already SHOWS this state — but showing is not
            # holding: until a state is commanded, the junction is still
            # running its own programme and will cycle straight out of it
            # (measured: a corridor "held" this way went green -> amber ->
            # red under the approaching ambulance).  Nothing is being newly
            # restricted, so no clearance interval is needed, but the state
            # must still be commanded to seize control of the signal.
            if not self._set_state(tls_id, st, target):
                return False
            st.mode = PREEMPTED
            st.did_allred = True
            return True
        st.mode = TO_PREEMPT
        st.did_allred = False
        yellow = "".join(
            "y" if (current[i] in "Gg" and i < len(target)
                    and target[i] in "ro") else current[i]
            for i in range(len(current)))
        if yellow != current:
            if not self._set_state(tls_id, st, yellow):
                return False
            st.until = now + self.cfg.yellow_time_s
            self.ops.emit(now, "preempt_phase",
                          f"Junction {self.ops.jn(tls_id)}: amber to "
                          f"conflicting traffic "
                          f"({self.cfg.yellow_time_s:.0f} s)", "info",
                          case=st.case)
        else:
            # no conflicting green left to drop — straight to the all-red
            # clearance, which the advance loop applies on this same tick
            st.until = now
        return True

    def _fail_safe(self, tls_id, st, reason, now):
        """On any signal-command error: revert the junction to its normal
        programme immediately and report the case as errored."""
        self.ops.emit(now, "error",
                      f"Junction {self.ops.jn(tls_id)}: signal command FAILED ({reason}) — "
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
        # Stand off this junction.  NOT self.cooldown: that is deliberately
        # bypassed while a unit is in the box, so a faulty controller would
        # be re-armed every few seconds for ever, filling the log and the
        # Cases table with errored P-cases for one junction.
        self.faulted[tls_id] = now + self.cfg.preempt_cooldown_s

    def _tls_containing(self, veh_id):
        """The tls id whose junction the vehicle is currently inside, if any
        (vehicles on internal lanes have lane ids like ':<node>_<i>_<j>')."""
        try:
            lane = traci.vehicle.getAllSubscriptionResults().get(
                veh_id, {}).get(tc.VAR_LANE_ID)
            if lane is None:
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

    # ------------------------------------------------------ physical cameras

    def _build_camera_zones(self):
        """Each junction camera's real field of view: every edge within
        camera_range_m upstream of its stop lines, with the offset from
        that edge's end to the junction.  Detection then means the
        ambulance is PHYSICALLY inside a camera's view — never inferred
        from the vehicle's own route."""
        zones = {}
        if self.net is None:
            self._camera_zones = zones
            return
        try:
            tls_ids = traci.trafficlight.getIDList()
        except traci.TraCIException:
            self._camera_zones = zones
            return
        for tls_id in tls_ids:
            try:
                links = traci.trafficlight.getControlledLinks(tls_id)
            except traci.TraCIException:
                continue
            approach = {grp[0][0].rsplit("_", 1)[0]
                        for grp in links if grp}
            for eid in approach:
                if eid.startswith(":"):
                    continue
                try:
                    start = self.net.getEdge(eid)
                except Exception:
                    continue
                frontier = [(start, 0.0)]
                seen = {eid}
                while frontier:
                    edge, off = frontier.pop()
                    cur = edge.getID()
                    self._edge_len[cur] = edge.getLength()
                    zones.setdefault(cur, []).append((tls_id, off))
                    off2 = off + edge.getLength()
                    if off2 >= self.cfg.camera_range_m:
                        continue
                    for prev in edge.getIncoming():
                        pid = prev.getID()
                        if pid in seen or pid.startswith(":"):
                            continue
                        seen.add(pid)
                        frontier.append((prev, off2))
        self._camera_zones = zones

    def _sense_cameras(self, ambulance_ids):
        """Junction-side detection: an ambulance with active lights inside
        a camera's field of view is reported and confirmed — the only way
        the control centre ever learns a unit exists."""
        if self._camera_zones is None:
            self._build_camera_zones()
        results = traci.vehicle.getAllSubscriptionResults()
        for amb_id in ambulance_ids:
            try:
                eid = results.get(amb_id, {}).get(tc.VAR_ROAD_ID)
                if eid is None:
                    eid = traci.vehicle.getRoadID(amb_id)
            except traci.TraCIException:
                continue
            hits = self._camera_zones.get(eid)
            if not hits:
                continue
            try:
                pos = results.get(amb_id, {}).get(tc.VAR_LANEPOSITION)
                if pos is None:
                    pos = traci.vehicle.getLanePosition(amb_id)
            except traci.TraCIException:
                continue
            remain = max(0.0, self._edge_len.get(eid, 0.0) - pos)
            for tls_id, off in hits:
                dist = off + remain
                if dist <= self.cfg.camera_range_m:
                    self._camera_report(tls_id, amb_id, dist)

    def _camera_report(self, tls_id, amb_id, dist):
        if (tls_id, amb_id) in self.camera_logged:
            return
        self.camera_logged.add((tls_id, amb_id))
        self.ops.emit(self._now, "camera",
                      f"Camera at {self.ops.jn(tls_id)} detected {amb_id} "
                      f"with lights on, {dist:.0f} m out", "info",
                      actor=amb_id)
        # The same camera is an enforcement camera: over the limit with
        # lights active means the emergency exemption applies — no citation.
        try:
            speed = traci.vehicle.getSpeed(amb_id)
            limit = traci.lane.getMaxSpeed(traci.vehicle.getLaneID(amb_id))
        except traci.TraCIException:
            return
        if speed > limit + 0.5:
            self.ops.emit(self._now, "enforcement",
                          f"Enforcement camera at {self.ops.jn(tls_id)}: {amb_id} at "
                          f"{speed * 3.6:.0f} km/h in a {limit * 3.6:.0f} "
                          f"km/h zone — emergency lights active, exemption "
                          f"applies, NO CITATION issued", "info",
                          actor=amb_id)
        if amb_id not in self.confirmed:
            self.confirmed.add(amb_id)
            self.ops.emit(self._now, "corridor",
                          f"Control centre confirmed {amb_id} — green "
                          f"corridor activated along its planned route",
                          "info", actor=amb_id)

    def _build_states(self, tls_id, link_indices, harden=False):
        """Return (amber_transition_state, preempt_target_state).

        The held state is ALWAYS built explicitly here — never inherited
        from a phase of the junction's own programme.  It is: the corridor
        approach and every other approach feeding the same movement on
        PROTECTED GREEN (so the traffic in front of the ambulance can
        drain), and every approach that is not on the ambulance's route on
        SOLID RED — the stop indication for traffic that must not enter.
        With ``flash_amber`` set, those cross approaches show FLASHING
        AMBER ('o', yield) instead, hardening to solid red once the unit is
        within ``flash_harden_eta_s`` seconds ETA or ``flash_harden_min_m``
        metres.

        Solid red seals the junction box: nothing on a red link can leave
        it.  Two things keep that from trapping the corridor itself — the
        all-red clearance interval that always precedes the hold (see
        ``_arm_target``), which empties the box before the corridor green
        goes on; and the ``max_hold_s`` cap, whose box-occupancy exemption
        lapses once the unit has been at a standstill for
        ``BOX_STALL_RELEASE_S``, so a hold that has stopped serving anyone
        is released and the cross streets cycle and drain the box.
        """
        try:
            current = traci.trafficlight.getRedYellowGreenState(tls_id)
        except traci.TraCIException:
            return "", ""
        n = len(current)
        if n == 0:
            return "", ""

        # The held state is built explicitly, never inherited from a
        # programme phase: a real phase can leave a compatible cross flow
        # GREEN, and while an ambulance is coming through, everything that
        # is not on its route must read STOP.  Corridor approach protected
        # green, every other approach solid red — flashing amber only if
        # cfg.flash_amber is turned on.
        target = self._custom_target(tls_id, link_indices, n,
                                     flash=self.cfg.flash_amber and not harden)
        if not target:
            return "", ""

        yellow = "".join(
            "y" if (current[i] in "Gg" and target[i] in "ro")
            else current[i]
            for i in range(n)
        )
        return yellow, target

    def _custom_target(self, tls_id, link_indices, n, flash=False):
        """Corridor approach protected green; everything else flashing
        amber ('o', yield — flash=True) or solid red (flash=False)."""
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
                target.append("o" if flash else "r")
        return "".join(target)

    def _approach_congested(self, tls_id, link_index):
        """Is the ambulance's approach into this junction CONGESTED or
        JAMMED per the live Markov state?  Drives the queue-flush lead."""
        mk = self.markov
        if mk is None:
            return False
        links = self._links_cache.get(tls_id)
        if links is None:
            try:
                links = traci.trafficlight.getControlledLinks(tls_id)
            except traci.TraCIException:
                links = []
            self._links_cache[tls_id] = links
        if link_index >= len(links) or not links[link_index]:
            return False
        edge = links[link_index][0][0].rsplit("_", 1)[0]
        return mk.state_now.get(edge, 0) >= 2

    def _begin(self, tls_id, link_indices, now, amb, harden=False,
               flush=False, congested=False):
        try:
            orig_program = traci.trafficlight.getProgram(tls_id)
            orig_phase = traci.trafficlight.getPhase(tls_id)
            _yellow, target = self._build_states(tls_id, link_indices,
                                                 harden)
        except traci.TraCIException:
            return None
        if not target:
            return None
        st = _TlsState(orig_program, orig_phase, now)
        st.link_indices = link_indices
        st.target = target
        st.amb = amb
        st.hardened = harden
        cross_txt = ("cross approaches to FLASHING AMBER (yield) until the "
                     "unit closes in" if self.cfg.flash_amber and not harden
                     else "every approach not on its route to SOLID RED")
        if not flush:
            flush_txt = ""
        elif congested:
            flush_txt = (" — enabled EARLY (queue flush): this approach is "
                         "congested, so the extra lead drains the standing "
                         "queue before the unit arrives")
        else:
            # the window is being held from an earlier reading (sticky
            # flush): do not assert congestion in the present tense
            flush_txt = (" — enabled EARLY (queue flush): extra lead "
                         "retained from an earlier congested reading on "
                         "this approach")
        st.case = self.ops.open_case("P", tls_id, now,
                                     f"Junction {self.ops.jn(tls_id)} corridor for {amb}")
        self.ops.emit(now, "preempt_start",
                      f"Junction {self.ops.jn(tls_id)} PURPOSELY ENABLED for {amb}: "
                      f"approach green, {cross_txt}{flush_txt}", "warn",
                      actor=amb, case=st.case)
        # Amber to any conflicting green, then the all-red clearance, then
        # the corridor green — never the corridor green straight away: the
        # held state seals the box, so the box has to be empty first.
        if not self._arm_target(tls_id, st, target, now):
            # the signal command FAILED; _fail_safe has already reverted
            # the junction and closed the case as errored.  Returning None
            # keeps it out of self.active, so nothing is re-inserted and
            # nothing is credited for a corridor that never opened.
            return None
        # a corridor was genuinely opened here for this unit: the arrival
        # analysis reads this to know whether its run had a green wave
        self.preempted_for.setdefault(amb, set()).add(tls_id)
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
        # the whole period this P-case had the junction purposely enabled,
        # not just the segment since the last re-arm
        held = now - st.enabled_at
        seg = now - st.started_at
        extra = "" if seg >= held - 0.5 else f", {seg:.0f} s since the last re-arm"
        self.ops.emit(now, "restore",
                      f"Junction {self.ops.jn(tls_id)} BACK TO NORMAL programme — "
                      f"purposely-enabled period over (held {held:.0f} s"
                      f"{extra} for {st.amb})", "info", case=st.case)
        if st.case:
            self.ops.close_case(st.case, now,
                                f"restored to normal after {held:.0f} s")
        self.active.pop(tls_id, None)
        self._arb_logged.pop(tls_id, None)
