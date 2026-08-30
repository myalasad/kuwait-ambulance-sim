"""Ambulance dispatch: geocoding, Dijkstra routing, lifecycle reporting.

Routes are computed by our own Dijkstra over the network's edge graph with
live travel-time weights (sim/router.py) and assigned to the vehicle via
TraCI — so the navigation the driver follows and the corridor the signal
controller opens are the same object.  Every lifecycle transition (dispatch,
network entry, teleport, arrival, unexpected removal, lights on/off) is a
structured operation on the ambulance's A-case: an ambulance can never
leave the map without the reason being on record.
"""
import random

import traci
import traci.constants as tc



class Dispatcher:
    def __init__(self, net, cfg, ops, router):
        self.net = net
        self.cfg = cfg
        self.ops = ops
        self.router = router
        self.count = 0
        self.info = {}  # amb_id -> lifecycle + navigation record
        self._rng = random.Random(1)
        self._logics = {}  # tls id -> cached signal programme phases
        self._links = {}   # tls id -> cached controlled-link groups
        self._vtype_vmax = None   # ambulance vType's own maxSpeed (m/s)
        # set by the runner: lets the stuck-reroute message check whether a
        # corridor is actually active for a unit before saying so
        self.controller = None
        # Ready-fleet model: each hospital stations a limited number of
        # ready units; a dispatch commits one, and a unit that delivers a
        # patient rejoins the RECEIVING hospital's pool after crew
        # turnaround (capped at the stationed strength).  This is why
        # consecutive missions do not launch as a convoy from one gate.
        self._fleet = {}       # hospital -> ready units (filled below)
        self._returning = []   # (ready_at, hospital, amb_id, origin_hosp)
        self.call_queue = []   # calls waiting for a crew (all committed)
        self._last_gate_t = {}  # hospital -> sim time of last departure
        self.response_log = []  # call-to-scene times, per governorate
        self._resp_cache = (-1, None)  # (log length, summary) — log is append-only
        # offer only places that actually snap to the modelled network, so
        # the UI never lists a scene or hospital that cannot be reached
        self.hospitals = {}
        self._hospital_cand_of = {}  # candidate edge id -> hospital (first wins)
        for n, ll in cfg.hospitals_d().items():
            cands = self.nearest_edges(ll[0], ll[1], k=3)
            if cands:
                self.hospitals[n] = ll
                for c in cands:
                    self._hospital_cand_of.setdefault(c.getID(), n)
        self._fleet = {n: cfg.hospital_ready_units for n in self.hospitals}
        self.areas = {n: ll for n, ll in cfg.areas_d().items()
                      if self.nearest_edges(ll[0], ll[1], k=1)}
        dropped = [n for n in cfg.areas_d() if n not in self.areas]
        if dropped:
            ops.emit(0.0, "system",
                     f"{len(dropped)} named area(s) lie off the modelled "
                     f"network in this scenario and are not offered: "
                     f"{', '.join(dropped[:8])}"
                     f"{'…' if len(dropped) > 8 else ''}", "info")

    # ------------------------------------------------------------- geocoding

    def nearest_edges(self, lat, lon, k=4, radius=None):
        """The k closest passenger edges — the caller tries them in order,
        because the very nearest can be an unreachable one-way stub."""
        if radius is None:
            radius = self.cfg.snap_radius_m
        x, y = self.net.convertLonLat2XY(lon, lat)
        candidates = [(e, d) for e, d in
                      self.net.getNeighboringEdges(x, y, radius)
                      if e.allows("passenger")]
        candidates.sort(key=lambda ed: ed[1])
        return [e for e, _ in candidates[:k]]

    # -------------------------------------------------------------- dispatch

    GOVERNORATES = ("Capital", "Hawalli", "Farwaniya", "Mubarak Al-Kabeer",
                    "Ahmadi", "Jahra")

    def _governorate_of(self, desc):
        for g in self.GOVERNORATES:
            if f"({g})" in desc:
                return g
        return "Other"

    def _algorithm_name(self):
        return ("Dijkstra (live + Markov-predicted travel times)"
                if self.router.predictor is not None
                else "Dijkstra (live edge travel times)")

    def dispatch(self, origin=None, destination=None, now=0.0,
                 _desc_override=None, _call_t=None):
        """Insert an ambulance, lights on.  Ambulances always originate at
        a hospital: origin None/"auto" selects the nearest hospital with a
        READY crew (the call queues when every crew is committed).
        destination None picks a random named incident area.  Returns the
        new ambulance id, or None when the call queued."""
        if destination is None:
            # Incidents do not repeat on top of each other: the last few
            # scenes are excluded so calls spread across the city.  Without
            # this a uniform draw clusters, and because dispatch answers
            # from the NEAREST hospital, clustered scenes make the origins
            # look like a rota when they are really just geography.
            names = sorted(self.areas)
            recent = getattr(self, "_recent_scenes", [])
            fresh = [n for n in names if n not in recent] or names
            area = self._rng.choice(fresh)
            keep = max(1, min(8, len(names) // 2))
            self._recent_scenes = (recent + [area])[-keep:]
            lat, lon = self.areas[area]
            to_edges = self.nearest_edges(lat, lon)
            to_desc = f"{area} (random area)"
            if not to_edges:
                raise ValueError(f"No road near area {area}")
            dest_spec = (lat, lon)      # pin the scene if the call queues
        else:
            to_edges, to_desc = self._resolve(destination, "destination")
            dest_spec = destination
        if _desc_override:
            to_desc = _desc_override   # a queued call keeps its area name

        route, algorithm, from_desc = None, None, None
        rotation_note = ""
        origin_hospital = None
        live = self.cfg.route_live_weights
        if origin in (None, "auto"):
            # ONE backward Dijkstra from the scene ranks every hospital's
            # current travel time in a single pass (instead of a full
            # search per hospital); the winning unit's actual route is then
            # computed forward with the full predictive weights.
            cand_of = self._hospital_cand_of
            ranked, to_id = [], None
            for te in to_edges:
                costs = self.router.cost_from_many(
                    [c for c in cand_of if c != te.getID()],
                    te.getID(), live=live)
                if costs:
                    to_id = te.getID()
                    per_h = {}
                    for cid, cost in costs.items():
                        h = cand_of[cid]
                        # cost_from_many() excludes the SOURCE edge's own
                        # weight, but the unit is inserted at the base of
                        # that edge (departPos defaults to "base") and
                        # drives its full length.  That term differs per
                        # hospital, so leaving it out both biases the
                        # ranking and understates every quoted response
                        # time.  Same weight convention as the backward
                        # search: live, non-predictive.  The per-hospital
                        # minimum must be taken over the TOTAL, not over
                        # `cost` — otherwise a hospital is charged the
                        # access edge of a candidate it does not use.
                        total = cost + self.router._weight(
                            cid, live, horizon=0.0, predictive=False)
                        if h not in per_h or total < per_h[h][0]:
                            per_h[h] = (total, cid)
                    ranked = sorted((cost, h, cid)
                                    for h, (cost, cid) in per_h.items())
                    break
            if not ranked:
                raise ValueError(f"No hospital can reach {to_desc}")
            # NEAREST READY UNIT — the scene decides, not a quota.
            # Hospitals are ranked by travel time to THIS scene; those
            # with no ready crew are dropped outright.  Among the rest,
            # only a near-tie (dispatch_rotation_tolerance of the
            # fastest candidate) may be re-ordered, and the sole rule
            # that re-orders it is gate headway: a hospital that
            # launched a unit within gate_headway_s yields to an
            # equally-close peer, so departures never stack at one gate.
            # Ties are otherwise broken by travel time.  Mission load is
            # deliberately NOT a factor: sorting the candidate band by
            # load turns dispatch into a round-robin, so the origin
            # follows a rota rather than the incident.
            self.process_returns(now)
            cap = self.cfg.hospital_ready_units
            candidates = [r for r in ranked if self._fleet.get(r[1], 0) > 0]
            if not candidates:
                # No reachable crew: the call QUEUES — real EMS never
                # conjures an unlimited convoy out of one gate.  The next
                # crew to finish turnaround responds automatically.
                self.call_queue.append({"dest": dest_spec,
                                        "desc": to_desc, "t": now})
                if not _desc_override:
                    # (a re-queued call already has its record — the serve
                    # loop restores its position and timestamp)
                    self.ops.emit(now, "dispatch",
                                  f"CALL QUEUED (position "
                                  f"{len(self.call_queue)}): {to_desc} — "
                                  f"no crew can respond right now (on "
                                  f"mission, in turnaround, or unable to "
                                  f"reach the scene); the next READY crew "
                                  f"responds automatically", "warn")
                return None
            # only a near-tie may be re-ordered: hospitals whose response
            # times are within the tolerance of EACH OTHER, not of the
            # whole field
            tol = 1.0 + self.cfg.dispatch_rotation_tolerance
            tie = [r for r in candidates
                   if r[0] <= candidates[0][0] * tol]
            # gate headway: a hospital that just launched a unit yields to
            # an equally-close peer, so departures never stack at one gate
            gh = self.cfg.gate_headway_s
            tie.sort(key=lambda r: (
                1 if now - self._last_gate_t.get(r[1], -1e9) < gh else 0,
                r[0]))
            chosen = None
            for eta_est, name, cid in tie + [r for r in candidates
                                             if r not in tie]:
                r = self.router.route(cid, to_id, live=live)
                if r:
                    route = r
                    chosen = (name, eta_est)
                    break
            if route is None:
                raise ValueError(f"No hospital can reach {to_desc}")
            origin_hospital, eta_est = chosen
            nearest_eta, nearest_name = ranked[0][0], ranked[0][1]
            if origin_hospital == nearest_name:
                from_desc = (f"{origin_hospital} "
                             f"(nearest available hospital to the scene)")
            elif self._fleet.get(nearest_name, 0) <= 0:
                from_desc = f"{origin_hospital} (nearest READY unit)"
                rotation_note = (
                    f"; {nearest_name} is nearest ({nearest_eta:.0f} s) but "
                    f"has no ready units (0/{cap} — crews on mission or in "
                    f"turnaround); {origin_hospital} responds in "
                    f"{eta_est:.0f} s with "
                    f"{self._fleet.get(origin_hospital, 0)}/{cap} ready")
            else:
                # the nearest hospital HAD a ready crew and was still not
                # chosen — the only rule that can do that is gate headway,
                # so say so rather than blaming a load it does not have
                since = now - self._last_gate_t.get(nearest_name, -1e9)
                from_desc = f"{origin_hospital} (nearest AVAILABLE unit)"
                if since < self.cfg.gate_headway_s:
                    rotation_note = (
                        f"; {nearest_name} is nearest ({nearest_eta:.0f} s) "
                        f"but launched a unit {since:.0f} s ago — gate "
                        f"headway ({self.cfg.gate_headway_s:.0f} s) keeps "
                        f"departures from stacking at one gate, so "
                        f"{origin_hospital} responds in {eta_est:.0f} s "
                        f"(+{eta_est - nearest_eta:.0f} s, within the "
                        f"{self.cfg.dispatch_rotation_tolerance:.0%} "
                        f"tie margin)")
                else:
                    rotation_note = (
                        f"; {nearest_name} is nearest ({nearest_eta:.0f} s) "
                        f"but its route could not be built; "
                        f"{origin_hospital} responds in {eta_est:.0f} s")
            algorithm = self._algorithm_name()
        else:
            from_edges, from_desc = self._resolve(origin, "origin")
            if isinstance(origin, str) and origin in self.hospitals:
                # manual hospital dispatch counts toward that hospital's
                # load, or the availability rotation would treat it as idle
                origin_hospital = origin
            algorithm = self._algorithm_name()
            for from_edge in from_edges:
                for to_edge in to_edges:
                    if to_edge.getID() == from_edge.getID():
                        continue
                    route = self.router.route(from_edge.getID(),
                                              to_edge.getID(), live=live)
                    if route is None:
                        stage = traci.simulation.findRoute(
                            from_edge.getID(), to_edge.getID(),
                            vType=self.cfg.ambulance_type)
                        if stage.edges:
                            route = list(stage.edges)
                            algorithm = "SUMO fallback router"
                    if route:
                        break
                if route:
                    break
        if not route:
            raise ValueError(f"No route from {from_desc} to {to_desc}")

        # Observable predictive routing: compare against the live-only route.
        routing_note = ""
        pred = self.router.predictor
        if pred is not None and algorithm.startswith("Dijkstra"):
            alt = self.router.route(route[0], route[-1], live=live,
                                    predictive=False)
            if alt:
                # Score the choice under BOTH objectives and keep both
                # signed.  Scoring only under the predictor's own weights
                # (and clamping at zero) can never produce a negative
                # number, so it is not evidence — each metric favours the
                # route optimised for it, and the difference is a model
                # disagreement, not a measured saving.
                t_pred_m = self.router.route_time(route, live=live,
                                                  predictive=True)
                t_alt_m = self.router.route_time(alt, live=live,
                                                 predictive=True)
                t_pred_l = self.router.route_time(route, live=live,
                                                  predictive=False)
                t_alt_l = self.router.route_time(alt, live=live,
                                                 predictive=False)
                ev = pred.routing_evidence
                ev["compared"] += 1
                if alt != route:
                    ev["differed"] += 1
                    ev["model_delta_s"] = (ev.get("model_delta_s", 0.0)
                                           + (t_alt_m - t_pred_m))
                    ev["live_delta_s"] = (ev.get("live_delta_s", 0.0)
                                          + (t_alt_l - t_pred_l))
                    # legacy key, kept in step with the model-scored
                    # ledger so the copilot page cannot display a number
                    # that is not the one accumulated here; it is signed
                    # now, never clamped up to zero
                    ev["predicted_saving_s"] = round(ev["model_delta_s"], 1)
                    routing_note = (f"; predictive routing chose a different "
                                    f"corridor than live-only routing "
                                    f"(its own model scores that "
                                    f"{t_alt_m - t_pred_m:+.0f} s; live-only "
                                    f"weights score it "
                                    f"{t_alt_l - t_pred_l:+.0f} s — each "
                                    f"metric favours the route optimised "
                                    f"for it)")
                else:
                    routing_note = ("; predictive routing agrees with "
                                    "live-only routing for this trip")
                algorithm = self._algorithm_name()

        rows = self.router.nodal_analysis(route,
                                          live=self.cfg.route_live_weights)
        length_m = rows[-1]["dist_m"] if rows else 0
        eta_s = rows[-1]["eta_s"] if rows else 0
        geometry = self.router.route_geometry(route)
        exp_signal_wait, per_signal = self._expected_signal_wait(rows)
        free_flow_s = self._free_flow_exempt(route)

        self.count += 1
        amb_id = f"AMB_{self.count}"
        route_id = f"route_{amb_id}"
        try:
            traci.route.add(route_id, route)
            traci.vehicle.add(amb_id, route_id,
                              typeID=self.cfg.ambulance_type,
                              departLane="best", departSpeed="max")
            # emergency speed exemption: above the posted limit, capped
            traci.vehicle.setSpeedFactor(amb_id,
                                         self.cfg.speed_exemption_factor)
            traci.vehicle.setMaxSpeed(amb_id,
                                      self.cfg.ambulance_max_kmh / 3.6)
        except traci.TraCIException as exc:
            # the unit was never committed: fail without leaking a crew
            raise ValueError(f"insertion failed for {amb_id}: {exc}")
        if origin_hospital:
            # commit a ready unit from the origin hospital's pool ONLY
            # after the vehicle physically exists.  No floor: a manual
            # reserve dispatch leaves a DEBT that the crew's eventual
            # return pays back — clamping would mint units.
            self._fleet[origin_hospital] = (
                self._fleet.get(origin_hospital, 0) - 1)
            self._last_gate_t[origin_hospital] = now
        case = self.ops.open_case("A", amb_id, now,
                                  f"{amb_id}: {from_desc} -> {to_desc}")
        self.info[amb_id] = {
            "case": case,
            "desc": f"{from_desc} -> {to_desc}",
            "planned_length": length_m,
            "eta_s": eta_s,
            "departed": None,
            "arrived": None,
            "lights": True,
            "route_edges": route,
            "nav_rows": rows,
            "geometry": geometry,
            "algorithm": algorithm,
            "signals_on_route": sum(1 for r in rows if r["signal"]),
            # arrival-time analysis inputs
            "free_flow_s": free_flow_s,
            "exp_signal_wait_s": exp_signal_wait,
            "per_signal": per_signal,
            "signal_wait_s": 0.0,     # measured: stopped at a red signal
            "traffic_wait_s": 0.0,    # measured: stopped/crawling in traffic
            "mission": "to_scene",    # to_scene -> loading -> to_hospital
            "hospital": None,
            "origin_hospital": origin_hospital,
            # response-time accounting: the clock starts when the CALL was
            # made (a queued call's wait counts), ends at scene arrival
            "call_t": _call_t if _call_t is not None else now,
            "queue_wait_s": (round(now - _call_t, 1)
                             if _call_t is not None else 0.0),
            "gov": self._governorate_of(to_desc),
            "loading_started": False,
            "created": now,
            "insert_retries": 0,
        }
        self.ops.emit(now, "dispatch",
                      f"{amb_id} dispatched ({from_desc} to {to_desc}): "
                      f"{algorithm}, {length_m / 1000:.1f} km, "
                      f"{self.info[amb_id]['signals_on_route']} signals on "
                      f"route, ETA {eta_s:.0f} s, lights ON, speed-limit "
                      f"exemption active (up to "
                      f"{self.cfg.speed_exemption_factor:.0%} of posted "
                      f"limit, max {self.cfg.ambulance_max_kmh:.0f} km/h)"
                      f"{routing_note}{rotation_note}",
                      "info", actor=amb_id, case=case)
        return amb_id

    # ------------------------------------------------------------ fleet

    def units_on_mission(self):
        """Crews currently committed to a mission — the ones a hospital
        pool paid for (origin_hospital truthy) whose mission has not yet
        closed, so their crew has not yet been handed to _returning."""
        return sum(1 for rec in self.info.values()
                   if rec.get("origin_hospital") and rec["arrived"] is None)

    def fleet_conservation(self):
        """(expected, actual) crew count.  Crews are conserved: every one
        is either ready in a pool, out on a mission, or in turnaround.
        Reserve dispatches leave a negative pool (a debt) which the
        returning crew repays, so the identity holds through those too."""
        actual = (sum(self._fleet.values()) + self.units_on_mission()
                  + len(self._returning))
        return len(self.hospitals) * self.cfg.hospital_ready_units, actual

    def process_returns(self, now):
        """Crews finishing turnaround rejoin a ready pool.  Units are
        conserved: preferably the receiving hospital's pool, else the
        origin's (which may be repaying a reserve-dispatch debt), else the
        neediest pool below stationed strength.  A crew is never dropped:
        with one in hand the pools cannot all be at strength."""
        if not self._returning and not self.call_queue:
            return
        due = [r for r in self._returning if r[0] <= now]
        self._returning = [r for r in self._returning if r[0] > now]
        cap = self.cfg.hospital_ready_units
        for _t, hosp, amb_id, origin in due:
            target = hosp if hosp in self._fleet else None
            if ((target is None or self._fleet.get(target, 0) >= cap)
                    and origin in self._fleet
                    and self._fleet.get(origin, cap) < cap):
                target = origin
            if target is None or self._fleet[target] >= cap:
                # Conservation: a crew in hand means the pools cannot all
                # be at strength (sum(_fleet) <= N*cap - 1), so there is
                # always somewhere to send it.  Redeploy to the NEEDIEST
                # pool — sorted by shortfall, not by name — instead of
                # letting the unit fall out of circulation.
                short = sorted(((v, n) for n, v in self._fleet.items()
                                if v < cap))
                if not short:
                    self.ops.emit(now, "error",
                                  f"{amb_id} crew turnaround: fleet "
                                  f"accounting broken — every pool at "
                                  f"strength with a crew in hand", "error",
                                  actor=amb_id)
                    continue
                dest = short[0][1]
                self._fleet[dest] += 1
                at = (f"{target} already at stationed strength "
                      f"({self._fleet[target]}/{cap})" if target is not None
                      else f"its receiving hospital is not a stationed pool")
                self.ops.emit(now, "lifecycle",
                              f"{amb_id} crew turnaround complete — {at}; "
                              f"crew REDEPLOYED to {dest} "
                              f"({self._fleet[dest]}/{cap})", "info",
                              actor=amb_id)
            else:
                self._fleet[target] += 1
                self.ops.emit(now, "lifecycle",
                              f"{amb_id} crew turnaround complete — unit "
                              f"READY again at {target} "
                              f"({self._fleet[target]}/{cap})",
                              "info", actor=amb_id)
        if due:
            # crews are conserved: say so LOUDLY if they ever are not,
            # rather than letting the ready badges decay silently
            expected, actual = self.fleet_conservation()
            if actual != expected:
                self.ops.emit(now, "error",
                              f"FLEET ACCOUNTING: {actual} crews accounted "
                              f"for ({sum(self._fleet.values())} ready, "
                              f"{self.units_on_mission()} on mission, "
                              f"{len(self._returning)} in turnaround) but "
                              f"{expected} are stationed "
                              f"({len(self.hospitals)} hospitals x {cap}) — "
                              f"investigate: dispatch capacity is drifting",
                              "error")
        # Freed crews serve waiting calls, oldest first.  Guarded against
        # re-entry (dispatch calls process_returns), and BOUNDED to one
        # attempt per queued call per invocation: a call whose reachable
        # hospitals have no ready crew must wait a step, not livelock the
        # simulation while a ready crew sits somewhere it cannot help.
        if getattr(self, "_serving_queue", False):
            return
        self._serving_queue = True
        deferred = []
        try:
            attempts = len(self.call_queue)
            while (attempts > 0 and self.call_queue
                   and any(n > 0 for n in self._fleet.values())):
                attempts -= 1
                call = self.call_queue.pop(0)
                try:
                    amb = self.dispatch(None, call["dest"], now,
                                        _desc_override=call["desc"],
                                        _call_t=call["t"])
                except ValueError as exc:
                    # A queued call is COMMITTED: a failed attempt must not
                    # delete it.  It keeps its original timestamp and goes
                    # back to the FRONT after this pass.  Bounded, so a call
                    # nothing can serve is escalated once instead of erroring
                    # for ever.
                    call["fails"] = call.get("fails", 0) + 1
                    if call["fails"] >= 5:
                        self.ops.emit(now, "error",
                                      f"Queued call to {call['desc']} "
                                      f"ABANDONED after 5 failed dispatch "
                                      f"attempts (last: {exc}) — no unit "
                                      f"could be sent; escalate manually",
                                      "error")
                    else:
                        deferred.append(call)
                        self.ops.emit(now, "error",
                                      f"Queued call to {call['desc']} could "
                                      f"not be served: {exc} — it KEEPS its "
                                      f"place in the queue (attempt "
                                      f"{call['fails']}/5)", "error")
                    continue
                if amb:
                    self.ops.emit(now, "dispatch",
                                  f"QUEUED CALL served by {amb} after "
                                  f"{now - call['t']:.0f} s waiting: "
                                  f"{call['desc']} — a crew was ready "
                                  f"again", "info", actor=amb)
                elif (self.call_queue
                      and self.call_queue[-1]["dest"] is call["dest"]):
                    # dispatch re-queued it at the back: hold it aside with
                    # its ORIGINAL place and timestamp.  It must NOT go back
                    # to index 0 — that makes the next iteration pop the same
                    # call, so one unservable call burns the whole per-
                    # invocation budget (a full backward Dijkstra each time)
                    # and the calls behind it, which the ready crew may well
                    # be able to reach, are never attempted.
                    self.call_queue.pop()
                    deferred.append(call)
        finally:
            # oldest-first, at the front: FIFO order and true waiting times
            # survive even an unexpected exception
            if deferred:
                self.call_queue[:0] = deferred
            self._serving_queue = False

    def _schedule_return(self, rec, amb_id, now):
        """A closed mission's crew restocks, then rejoins a ready pool.
        Only units a hospital pool actually paid for return to one —
        origin_hospital is truthy exactly when dispatch committed a unit."""
        origin = rec.get("origin_hospital")
        if origin:
            hosp = rec.get("hospital") or origin
            self._returning.append(
                (now + self.cfg.unit_turnaround_s, hosp, amb_id, origin))

    def fleet_status(self):
        """hospital -> ready units, for the UI and the ops record."""
        return dict(self._fleet)

    def response_summary(self):
        """Call-to-scene response times: percentiles overall and per
        governorate, plus queue-wait statistics — the numbers an EMS
        board asks for first."""
        def pct(vals, q):
            if not vals:
                return None
            vals = sorted(vals)
            return round(vals[min(len(vals) - 1,
                                  int(q * (len(vals) - 1) + 0.5))], 1)
        n = len(self.response_log)
        if n == self._resp_cache[0]:
            return self._resp_cache[1]
        rows = self.response_log
        out = {"n": len(rows),
               "p50_s": pct([r["response_s"] for r in rows], 0.5),
               "p90_s": pct([r["response_s"] for r in rows], 0.9),
               "queue_wait_p50_s": pct([r["queue_wait_s"] for r in rows
                                        if r["queue_wait_s"] > 0], 0.5),
               "queue_wait_max_s": (max((r["queue_wait_s"] for r in rows),
                                        default=0.0)),
               "by_gov": {}}
        for g in sorted({r["gov"] for r in rows}):
            vals = [r["response_s"] for r in rows if r["gov"] == g]
            out["by_gov"][g] = {"n": len(vals),
                                "p50_s": pct(vals, 0.5),
                                "p90_s": pct(vals, 0.9)}
        self._resp_cache = (n, out)
        return out

    # ------------------------------------------------- arrival-time analysis

    def _movement_links(self, tls, in_edge, out_edge):
        """Controlled-link indices of the ambulance's own movement through
        `tls`: the links entering from `in_edge` and leaving towards
        `out_edge`.  When the outgoing edge is unknown (the last row of a
        route) every link of the approach is used — the conservative
        fallback.  Returns ([indices], total number of controlled links)."""
        groups = self._links.get(tls)
        if groups is None:
            try:
                groups = traci.trafficlight.getControlledLinks(tls)
            except traci.TraCIException:
                groups = []
            self._links[tls] = groups
        approach, movement = [], []
        for i, g in enumerate(groups):
            if not g or not g[0] or not g[0][0]:
                continue
            if g[0][0].rsplit("_", 1)[0] != in_edge:
                continue
            approach.append(i)
            if out_edge and g[0][1] \
                    and g[0][1].rsplit("_", 1)[0] == out_edge:
                movement.append(i)
        return (movement or approach), len(groups)

    def _expected_signal_wait(self, rows):
        """Expected red-light wait per signal on the route WITHOUT preemption,
        from each junction's real programme: for a vehicle arriving at a
        uniformly random point of the cycle, E[wait] = r^2 / (2C) with C the
        cycle and r the red time FOR THE AMBULANCE'S OWN MOVEMENT — the
        cycle minus the total duration of every phase that gives that
        movement green.  Quadratic in the signal timer, which is why the
        timer is the highest-weight variable in the with/without comparison.

        A movement that is green in every phase therefore contributes 0 s.
        A junction whose links cannot be resolved (no controlled link from
        the approach, or a programme whose phase states do not match the
        link count) is NOT silently folded into the total: it is returned
        with ``exp_wait_s: None`` and ``modelled: False`` and excluded from
        the modelled count, so no screen can count it behind a number it did
        not contribute to.  Returns (total, per-signal rows); the modelled
        count is ``sum(1 for p in per if p["modelled"])``, which stays in
        step with the total because both come from the same list."""
        total = 0.0
        per = []
        for i, r in enumerate(rows):
            if not r["signal"]:
                continue
            tls = r["signal"]
            row = {"tls": tls, "name": self.ops.jn(tls), "cycle": None,
                   "red": None, "exp_wait_s": None, "modelled": False}
            # programme definitions are static — fetch each signal's once
            if tls in self._logics:
                phases = self._logics[tls]
            else:
                try:
                    logics = traci.trafficlight.getAllProgramLogics(tls)
                except traci.TraCIException:
                    logics = None
                phases = logics[0].phases if logics else None
                self._logics[tls] = phases
            if phases is None:
                per.append(row)
                continue
            cycle = sum(p.duration for p in phases)
            if cycle <= 0:
                per.append(row)
                continue
            row["cycle"] = round(cycle)
            in_edge = r.get("in_edge")
            out_edge = (rows[i + 1].get("in_edge")
                        if i + 1 < len(rows) else None)
            idxs, n_links = self._movement_links(tls, in_edge, out_edge)
            if not idxs or any(len(p.state) != n_links for p in phases):
                per.append(row)          # cannot resolve: not modelled
                continue
            # SUM the movement's green over every phase that serves it (a
            # movement served by two phases is not red between them), then
            # red is what is left of the cycle
            green = sum(p.duration for p in phases
                        if all(p.state[k] in "Gg" for k in idxs))
            red = max(0.0, cycle - green)
            e_wait = red * red / (2 * cycle)
            total += e_wait
            row.update({"red": round(red), "exp_wait_s": round(e_wait, 1),
                        "modelled": True})
            per.append(row)
        return round(total, 1), per

    def _free_flow_exempt(self, route):
        """Travel time on an empty road at the exempt speed profile.

        A dispatch-time reference for what a clear exempt run would cost.
        It assumes the lights stay ON for the whole trip: since lights-off
        now genuinely withdraws the exemption (see set_lights), this is not
        an achievable floor for a run whose lights were switched off
        part-way, and must not be presented as one."""
        t = 0.0
        cap = self.cfg.ambulance_max_kmh / 3.6
        for eid in route:
            edge = self.net.getEdge(eid)
            v = min(edge.getSpeed() * self.cfg.speed_exemption_factor, cap)
            t += edge.getLength() / max(v, 1.0)
        return round(t, 1)

    def _resolve(self, spec, kind):
        if isinstance(spec, str):
            if spec in self.hospitals:
                lat, lon = self.hospitals[spec]
            elif spec in self.areas:
                lat, lon = self.areas[spec]
            else:
                raise ValueError(f"Unknown place: {spec}")
            edges = self.nearest_edges(lat, lon)
            if not edges:
                raise ValueError(f"No road near {spec}")
            return edges, spec
        lat, lon = spec
        edges = self.nearest_edges(lat, lon)
        if not edges:
            raise ValueError(f"No road near {kind} ({lat:.4f}, {lon:.4f})")
        return edges, f"({lat:.4f}, {lon:.4f})"

    # ------------------------------------------------ scene -> hospital leg

    def check_scene_reached(self, now):
        """Drive the mission state machine: when an ambulance reaches the
        incident scene it is rerouted to the NEAREST hospital by Dijkstra
        travel time, after a patient-loading stop during which the corridor
        is paused."""
        for amb_id, rec in self.info.items():
            if rec["departed"] is None or rec["arrived"] is not None:
                continue
            # the response clock stops where the unit physically reaches
            # the scene, which is later than the moment the return leg is
            # planned — so it is tested every step, in every mission state
            self._stamp_scene_arrival(amb_id, rec, now)
            mission = rec.get("mission")
            if mission == "to_scene":
                try:
                    idx = traci.vehicle.getRouteIndex(amb_id)
                    route = traci.vehicle.getRoute(amb_id)
                except traci.TraCIException:
                    continue
                # getRouteIndex counts from DEPARTURE and setRoute prepends
                # the edges already driven, so the end-of-route test must be
                # against SUMO's own route — rec["route_edges"] can hold a
                # tail-only plan.  ONE edge of lead so the loading stop can
                # still be placed: this PLANS the return leg, it is not
                # arrival, and nothing is stamped here.
                if 0 <= idx >= len(route) - 2:
                    try:
                        self._begin_return_leg(amb_id, rec, idx, now)
                    except traci.TraCIException as exc:
                        rec["mission"] = "to_hospital"
                        self.ops.emit(now, "error",
                                      f"{amb_id} hospital reroute failed "
                                      f"({exc}) — continuing to scene only",
                                      "error", actor=amb_id, case=rec["case"])
            elif mission == "loading":
                try:
                    stopped = traci.vehicle.isStopped(amb_id)
                except traci.TraCIException:
                    continue
                if stopped:
                    rec["loading_started"] = True
                    rec.pop("halt_since", None)
                elif rec.get("loading_started"):
                    rec["mission"] = "to_hospital"
                    self.ops.emit(now, "reroute",
                                  f"{amb_id} patient aboard — hot return to "
                                  f"{rec['hospital']}, lights ON, corridor "
                                  f"resumes along the new route", "warn",
                                  actor=amb_id, case=rec["case"])
                else:
                    # The loading stop was never taken.  Bound the state:
                    # a unit pinned in "loading" runs the whole hospital
                    # leg with no corridor, no adaptive reroute and no
                    # delay attribution, unlogged.  The deadline is armed
                    # only while the unit is HALTED, so a long drive to a
                    # far stop point is never mistaken for a failure.
                    try:
                        speed = traci.vehicle.getSpeed(amb_id)
                        pending = traci.vehicle.getStops(amb_id, 1)
                    except traci.TraCIException:
                        continue
                    if speed > 0.1:
                        rec.pop("halt_since", None)
                    else:
                        rec.setdefault("halt_since", now)
                    if (not pending or now - rec.get("halt_since", now)
                            > self.cfg.patient_load_s + 20.0):
                        if pending:
                            try:
                                traci.vehicle.replaceStop(amb_id, 0, "")
                            except traci.TraCIException:
                                pass
                        rec["mission"] = "to_hospital"
                        rec["load_stop_missed"] = True
                        self.ops.emit(now, "error",
                                      f"{amb_id}: the patient-loading stop at "
                                      f"the scene could not be taken (unit "
                                      f"never observed stopped) — stop "
                                      f"cancelled, hospital leg to "
                                      f"{rec['hospital']} resumed, corridor "
                                      f"back on; this run is EXCLUDED from "
                                      f"the with/without comparison", "error",
                                      actor=amb_id, case=rec["case"])

    def _stamp_scene_arrival(self, amb_id, rec, now, at_close=False):
        """Board metric: the call-to-scene clock stops when the unit is
        ACTUALLY at the patient-loading point, not when it enters the
        second-to-last edge of its route (up to 1.7 km / 63 s earlier).

        `at_close` is the last resort used when the mission ends without
        the unit ever being observed there (SUMO removed it at the end of
        the scene edge, or the hospital reroute failed): the entry is
        still made, flagged, so no mission drops out of p50/p90."""
        if rec.get("response_s") is not None or "scene_stop" not in rec:
            return
        edge, pos = rec["scene_stop"]
        at = at_close
        if not at:
            try:
                if traci.vehicle.isStopped(amb_id):
                    at = True
                elif traci.vehicle.getRoadID(amb_id) == edge:
                    at = traci.vehicle.getLanePosition(amb_id) >= pos - 2.0
                else:
                    # already past the scene edge: the stop was skipped or
                    # could not be placed, but the unit did drive through
                    route = traci.vehicle.getRoute(amb_id)
                    if edge in route:
                        at = (traci.vehicle.getRouteIndex(amb_id)
                              > route.index(edge))
            except traci.TraCIException:
                return
        if not at:
            return
        resp = now - rec.get("call_t", now)
        rec["response_s"] = round(resp, 1)
        entry = {"amb": amb_id, "gov": rec.get("gov", "Other"),
                 "response_s": rec["response_s"],
                 "queue_wait_s": rec.get("queue_wait_s", 0.0)}
        if at_close:
            entry["at_mission_close"] = True
        self.response_log.append(entry)
        if at_close:
            # do NOT claim an arrival nobody observed: say what was measured
            self.ops.emit(now, "arrival",
                          f"{amb_id}: call-to-scene time recorded at mission "
                          f"close ({resp:.0f} s) — the unit was never "
                          f"observed at its scene stop point (it left the map "
                          f"there, or no stop could be placed), so this is "
                          f"the time to the end of its run at the scene, not "
                          f"a confirmed on-scene arrival; it is kept in the "
                          f"response statistics rather than dropped", "warn",
                          actor=amb_id, case=rec["case"])
        else:
            self.ops.emit(now, "arrival",
                          f"{amb_id} reached the incident scene — "
                          f"{resp:.0f} s from the call", "warn",
                          actor=amb_id, case=rec["case"])

    def _begin_return_leg(self, amb_id, rec, idx, now):
        scene_edge = rec["route_edges"][-1]
        # The physical scene point, recorded BEFORE anything can fail: the
        # response clock is measured against it, and the "no reachable
        # hospital" path below takes no stop at all yet still owes a
        # response time.
        try:
            scene_len = self.net.getEdge(scene_edge).getLength()
            rec["scene_stop"] = (scene_edge, max(2.0, scene_len - 3.0))
        except Exception:
            rec["scene_stop"] = (scene_edge, 2.0)
        # nearest hospital by actual routed travel time (one-ways
        # respected): ONE forward Dijkstra from the scene reaches every
        # hospital's candidate edges in a single pass
        cand_of = {cid: h for cid, h in self._hospital_cand_of.items()
                   if cid != scene_edge}
        found = self.router.route_to_many(scene_edge, list(cand_of),
                                          live=self.cfg.route_live_weights)
        best = None
        for cid, (leg, t) in found.items():
            if len(leg) >= 2 and (best is None or t < best[2]):
                best = (cand_of[cid], leg, t)
        if best is None:
            rec["mission"] = "to_hospital"
            self.ops.emit(now, "error",
                          f"{amb_id}: no reachable hospital from the scene — "
                          f"mission ends at the scene", "error",
                          actor=amb_id, case=rec["case"])
            return
        name, leg, _rank_t = best

        cur = traci.vehicle.getRoadID(amb_id)
        if cur.startswith(":"):
            # `idx` is a whole-route index (setRoute prepends the driven
            # prefix), so the internal-edge lookup must use the whole route
            full = traci.vehicle.getRoute(amb_id)
            cur = full[min(idx, len(full) - 1)]
        new_route = leg if cur == leg[0] else [cur] + leg
        traci.vehicle.setRoute(amb_id, new_route)

        # Analyse the route actually assigned, and add only the part that is
        # genuinely NEW: both `cur` and `scene_edge` are edges of the
        # to-scene route, so they are already inside planned_length, eta_s,
        # free_flow_s and signals_on_route.  Counting them again is what
        # made the runs table quote kilometres nobody drove.
        rows = self.router.nodal_analysis(new_route,
                                          live=self.cfg.route_live_weights)
        k = new_route.index(scene_edge)     # 0, or 1 when cur was prepended
        tail = rows[k + 1:]                 # the only genuinely new edges
        add_m = rows[-1]["dist_m"] - rows[k]["dist_m"]
        eta2 = rows[-1]["eta_s"] - rows[k]["eta_s"]

        # patient-loading stop at the scene
        stopped = False
        try:
            length = self.net.getEdge(scene_edge).getLength()
            pos = max(2.0, length - 3.0)
            if cur == scene_edge:
                lane_pos = traci.vehicle.getLanePosition(amb_id)
                pos = min(length - 1.0, max(pos, lane_pos + 12.0))
                if pos <= lane_pos + 3.0:
                    raise traci.TraCIException("already past the stop point")
            # Stop on a lane the unit can actually REACH.  Forcing lane 0 on
            # a multi-lane scene edge strands the unit beside its own stop
            # until the congestion resolver teleports it, and the stop is
            # then skipped for the rest of the run.
            lane_i = 0
            try:
                if cur == scene_edge:
                    lane_i = traci.vehicle.getLaneIndex(amb_id)
                else:
                    for ln in self.net.getEdge(cur).getLanes():
                        hit = next((c.getToLane() for c in ln.getOutgoing()
                                    if c.getToLane() is not None
                                    and c.getToLane().getEdge().getID()
                                    == scene_edge), None)
                        if hit is not None:
                            lane_i = hit.getIndex()
                            break
                lane_i = max(0, min(
                    lane_i, len(self.net.getEdge(scene_edge).getLanes()) - 1))
            except Exception:
                lane_i = 0
            traci.vehicle.setStop(amb_id, scene_edge, pos=pos,
                                  laneIndex=lane_i,
                                  duration=self.cfg.patient_load_s)
            rec["scene_stop"] = (scene_edge, pos)
            stopped = True
        except traci.TraCIException:
            pass

        exp2, per2 = self._expected_signal_wait(tail)
        ff2 = self._free_flow_exempt(new_route[k + 1:])
        load = self.cfg.patient_load_s if stopped else 0.0
        rec.update({
            "mission": "loading" if stopped else "to_hospital",
            "loading_started": False,
            "loading_since": now,
            "hospital": name,
            "route_edges": new_route,
            # nav_rows now covers new_route, so it matches route_edges and
            # geometry — the driver page no longer drops the first turn
            "nav_rows": rows,
            "geometry": self.router.route_geometry(new_route),
            "planned_length": rec["planned_length"] + add_m,
            "eta_s": rec["eta_s"] + eta2 + load,
            "exp_signal_wait_s": round(rec["exp_signal_wait_s"] + exp2, 1),
            "free_flow_s": round(rec["free_flow_s"] + ff2 + load, 1),
            "signals_on_route": rec["signals_on_route"]
                                + sum(1 for r in tail if r["signal"]),
            "per_signal": rec["per_signal"] + per2,
            "desc": rec["desc"] + f" -> {name}",
        })
        loading_txt = (f"loading patient ({load:.0f} s, corridor paused); "
                       if stopped else "")
        # NOT "reached the scene": this fires one edge out, and the arrival
        # is stamped by _stamp_scene_arrival when the unit is really there
        self.ops.emit(now, "reroute",
                      f"{amb_id} on final approach to the incident scene — "
                      f"{loading_txt}"
                      f"REROUTED to the nearest hospital by travel time: "
                      f"{name} (Dijkstra, {add_m / 1000:.1f} km, "
                      f"ETA {eta2:.0f} s, "
                      f"{sum(1 for r in tail if r['signal'])} signals) — "
                      f"the signal corridor follows the new route", "warn",
                      actor=amb_id, case=rec["case"])

    # ------------------------------------------------- adaptive stuck-reroute

    def check_pending_insertions(self, now):
        """A dispatched ambulance that cannot enter the road (hospital gate
        jammed) is reported, and after a minute it is re-placed on the next
        usable edge — up to twice.  If it still cannot merge the mission is
        CLOSED with an error and the crew returns: never a silently missing
        vehicle, and never a promise of a re-placement that cannot happen."""
        for amb_id, rec in list(self.info.items()):
            if rec["departed"] is not None or rec["arrived"] is not None:
                continue
            waiting = now - rec.get("created", now)
            left = 2 - rec.get("insert_retries", 0)
            if waiting > 20 and left > 0 and not rec.get("insert_warned"):
                rec["insert_warned"] = True
                self.ops.emit(now, "lifecycle",
                              f"{amb_id} is waiting for space to enter the "
                              f"road (the departure edge is congested) — "
                              f"re-placement in "
                              f"{max(0, 60 - int(waiting))} s if it cannot "
                              f"merge ({left} of 2 re-placements left)",
                              "warn", actor=amb_id, case=rec["case"])
            if waiting > 20 and left <= 0:
                # re-placement is exhausted: say so once, and stop counting
                # a unit that will never enter the network
                try:
                    traci.vehicle.remove(amb_id)   # so it cannot depart later
                except traci.TraCIException:
                    pass
                rec["arrived"] = now
                self.ops.emit(now, "error",
                              f"{amb_id} could not enter the road after 2 "
                              f"re-placements — re-placement exhausted; the "
                              f"mission is CLOSED and the crew returns to "
                              f"{rec.get('origin_hospital') or 'its hospital'}",
                              "error", actor=amb_id, case=rec["case"])
                self.ops.close_case(rec["case"], now,
                                    "could not enter the road after 2 "
                                    "re-placements", "error")
                self._schedule_return(rec, amb_id, now)
                continue
            if waiting > 60 and left > 0:
                try:
                    traci.vehicle.remove(amb_id)
                except traci.TraCIException:
                    continue      # retry NOT consumed: nothing was re-placed
                rec["insert_retries"] = rec.get("insert_retries", 0) + 1
                rec["created"] = now
                rec["insert_warned"] = False
                route = rec["route_edges"]
                # re-enter one edge further along the planned route
                new_route = route[min(rec["insert_retries"],
                                      len(route) - 2):]
                rid = f"route_{amb_id}_r{rec['insert_retries']}"
                try:
                    traci.route.add(rid, new_route)
                    traci.vehicle.add(amb_id, rid,
                                      typeID=self.cfg.ambulance_type,
                                      departLane="best", departSpeed="max",
                                      departPos="free")
                    # re-placement must not silently re-arm the exemption on
                    # a unit whose lights the operator switched off
                    traci.vehicle.setSpeedFactor(
                        amb_id, self.cfg.speed_exemption_factor
                        if rec["lights"] else 1.0)
                    traci.vehicle.setMaxSpeed(
                        amb_id, self.cfg.ambulance_max_kmh / 3.6
                        if rec["lights"] else self._vtype_max_speed())
                    rec["route_edges"] = new_route
                    self.ops.emit(now, "lifecycle",
                                  f"{amb_id} re-placed one block further "
                                  f"along its route (departure gate was "
                                  f"blocked for {waiting:.0f} s)", "warn",
                                  actor=amb_id, case=rec["case"])
                except traci.TraCIException as exc:
                    # the vehicle was removed and could not be re-added:
                    # close the mission so the unit is not counted as
                    # on-the-road (or as hospital load) forever
                    rec["arrived"] = now
                    self.ops.emit(now, "error",
                                  f"{amb_id} re-placement failed ({exc}) — "
                                  f"unit lost at the departure gate, "
                                  f"mission closed", "error",
                                  actor=amb_id, case=rec["case"])
                    self.ops.close_case(rec["case"], now,
                                        "re-placement failed after a "
                                        "blocked departure gate", "error")
                    self._schedule_return(rec, amb_id, now)

    def _corridor_active(self, amb_id, rec):
        """True only when this unit really has a signal corridor: the
        preemption system armed (preemption.py's global toggle), the unit's
        lights on, and a camera confirmation on record — the controller
        plans corridors for confirmed units only.  Without the controller
        wired in, nothing is asserted."""
        ctl = self.controller
        return bool(rec["lights"] and ctl is not None and ctl.enabled
                    and amb_id in ctl.confirmed)

    def _corridor_note(self, amb_id, rec):
        return (", corridor active"
                if self._corridor_active(amb_id, rec) else "")

    def check_stuck(self, now):
        """An ambulance crawling in a jam re-plans around the blockage: if a
        faster corridor exists under live weights, the route (and with it
        the signal corridor and driver navigation) switches immediately.
        If no better path exists, it stays — rerouting for its own sake
        would be motion without progress."""
        results = traci.vehicle.getAllSubscriptionResults()
        for amb_id, rec in self.info.items():
            if rec["departed"] is None or rec["arrived"] is not None:
                continue
            if (rec.get("mission") == "loading"
                    and rec.get("loading_started")):
                continue      # genuinely halted at the scene, not stuck
            try:
                odo = results.get(amb_id, {}).get(tc.VAR_DISTANCE)
                if odo is None:
                    odo = traci.vehicle.getDistance(amb_id)
            except traci.TraCIException:
                continue
            mark = rec.get("stuck_mark")
            if mark is None:
                rec["stuck_mark"] = (now, odo)
                continue
            t0, d0 = mark
            if odo - d0 >= self.cfg.stuck_progress_m:
                rec["stuck_mark"] = (now, odo)   # made progress: reset window
                continue
            stuck_for = now - t0
            if stuck_for < self.cfg.stuck_after_s:
                continue
            if now - rec.get("last_reroute", -1e9) < \
                    self.cfg.stuck_reroute_cooldown_s:
                continue
            rec["last_reroute"] = now      # rate-limit even when no better
            try:
                cur_route = list(traci.vehicle.getRoute(amb_id))
                idx = max(0, traci.vehicle.getRouteIndex(amb_id))
                cur_edge = traci.vehicle.getRoadID(amb_id)
                if cur_edge.startswith(":") or cur_edge not in cur_route:
                    cur_edge = cur_route[min(idx, len(cur_route) - 1)]
                # cut at the ROUTE INDEX: .index() picks the first
                # occurrence and is wrong on a route that revisits an edge
                cut = idx if (idx < len(cur_route)
                              and cur_route[idx] == cur_edge) \
                    else cur_route.index(cur_edge)
                remaining = cur_route[cut:]
            except (traci.TraCIException, ValueError):
                continue
            if len(remaining) < 3:
                continue                    # essentially at the destination
            alt = self.router.route(cur_edge, remaining[-1], live=True)
            t_rem = t_alt = None
            better = False
            if alt and alt != remaining:
                t_rem = self.router.route_time(remaining, live=True)
                t_alt = self.router.route_time(alt, live=True)
                better = t_alt <= t_rem * 0.9 - 5.0
            if not better:
                # stuck, but not enough to justify switching.  State the rule
                # that was applied and the number it measured — "none exists"
                # is only true in the first of the three cases below.
                if now - rec.get("last_noalt", -1e9) > 180.0:
                    rec["last_noalt"] = now
                    if not alt:
                        why = ("no alternative route to the destination "
                               "exists from here")
                    elif alt == remaining:
                        why = ("the route it is on is already the fastest "
                               "under live weights")
                    else:
                        saved = t_rem - t_alt
                        pct = (saved / t_rem * 100.0) if t_rem > 0 else 0.0
                        why = (f"the best alternative saves only "
                               f"{saved:.0f} s ({pct:.0f}%) — below the 10% "
                               f"and 5 s switching margin, so switching is "
                               f"not worth the disruption")
                    self.ops.emit(now, "reroute",
                                  f"{amb_id} stuck {stuck_for:.0f} s — {why}; "
                                  f"holding course"
                                  f"{self._corridor_note(amb_id, rec)}",
                                  "warn", actor=amb_id, case=rec["case"])
                continue
            try:
                traci.vehicle.setRoute(amb_id, alt)
            except traci.TraCIException:
                continue
            rows = self.router.nodal_analysis(alt, live=True)
            # SUMO's setRoute KEEPS the already-driven prefix, so
            # getRouteIndex stays in the FULL route's frame.  The record
            # must hold that same list, or every later test that compares an
            # index against it (the scene-arrival test above) compares an
            # index into one list with the length of another.
            try:
                full = list(traci.vehicle.getRoute(amb_id))
            except traci.TraCIException:
                full = alt
            rec["route_edges"] = full
            # nav_rows / geometry stay on `alt`: they legitimately describe
            # the REMAINING leg the driver is shown
            rec["nav_rows"] = rows
            rec["geometry"] = self.router.route_geometry(alt)
            rec["stuck_mark"] = None
            # The plan CHANGED: the signal model and the free-flow bound
            # must follow it, or the arrival analysis credits the green wave
            # with red-light time on a route nobody drove.  Drop the
            # abandoned tail, add the new one; the already-driven leg's
            # contributions (including _begin_return_leg's) stay untouched.
            _, per_drop = self._expected_signal_wait(
                self.router.nodal_analysis(remaining, live=True))
            _, per_add = self._expected_signal_wait(rows)
            keep = max(0, len(rec["per_signal"]) - len(per_drop))
            rec["per_signal"] = rec["per_signal"][:keep] + per_add
            rec["exp_signal_wait_s"] = round(
                sum(p["exp_wait_s"] or 0.0 for p in rec["per_signal"]), 1)
            rec["signals_on_route"] = len(rec["per_signal"])
            rec["free_flow_s"] = round(rec["free_flow_s"]
                                       - self._free_flow_exempt(remaining)
                                       + self._free_flow_exempt(alt), 1)
            # The distance on record must be the plan the unit is actually
            # driving, END TO END.  The route SUMO now holds already is
            # driven prefix + remaining, so read it back rather than trying
            # to re-derive the driven part: re-slicing `alt` credited edges
            # still AHEAD of the unit and dropped every metre already driven.
            total_m = 0.0
            for eid in full:
                try:
                    total_m += self.net.getEdge(eid).getLength()
                except Exception:
                    pass
            rec["planned_length"] = round(total_m, 0)
            rec["eta_s"] = round(now - (rec.get("departed") or now)
                                 + (rows[-1]["eta_s"] if rows else 0), 0)
            rec["replans"] = rec.get("replans", 0) + 1
            via = self.ops.rd(alt[min(2, len(alt) - 1)])
            # the corridor clause is a claim about the preemption system, so
            # it is only made when that system is actually serving this unit
            corridor_txt = ("; the signal corridor follows the new route"
                            if self._corridor_active(amb_id, rec) else "")
            self.ops.emit(now, "reroute",
                          f"{amb_id} stuck {stuck_for:.0f} s in traffic — "
                          f"ADAPTIVE REROUTE around the blockage via {via} "
                          f"(predicted {t_rem - t_alt:.0f} s faster than "
                          f"pushing through){corridor_txt}", "warn",
                          actor=amb_id, case=rec["case"])

    # ---------------------------------------------------------------- lights

    def _vtype_max_speed(self):
        """The ambulance vType's own physical top speed (m/s).  That is a
        vehicle property, not an exemption, so it is what a lights-off unit
        keeps once the exemption is withdrawn."""
        if self._vtype_vmax is None:
            try:
                self._vtype_vmax = traci.vehicletype.getMaxSpeed(
                    self.cfg.ambulance_type)
            except traci.TraCIException:
                self._vtype_vmax = self.cfg.ambulance_max_kmh / 3.6
        return self._vtype_vmax

    def set_lights(self, amb_id, on, now, who="operator"):
        rec = self.info.get(amb_id)
        if rec is None or rec["arrived"] is not None:
            return False
        if rec["lights"] == on:
            return True
        rec["lights"] = on
        # The lights are not a label: switching them off actually withdraws
        # the speed-limit exemption.  speedFactor 1.0, NOT the vType's own
        # speedFactor — data/vtypes.add.xml sets that to 1.35, so restoring
        # the type default would still run the unit over the posted limit.
        if on:
            factor = self.cfg.speed_exemption_factor
            top = self.cfg.ambulance_max_kmh / 3.6
        else:
            factor, top = 1.0, self._vtype_max_speed()
        try:
            traci.vehicle.setSpeedFactor(amb_id, factor)
            traci.vehicle.setMaxSpeed(amb_id, top)
        except traci.TraCIException:
            pass      # unit gone / not yet inserted: the record still flips
        if on:
            self.ops.emit(now, "lights",
                          f"{amb_id} emergency lights switched ON by {who} — "
                          f"corridor requests resume; speed-limit exemption "
                          f"re-armed (up to "
                          f"{self.cfg.speed_exemption_factor:.0%} of the "
                          f"posted limit, max "
                          f"{self.cfg.ambulance_max_kmh:.0f} km/h)", "warn",
                          actor=who, case=rec["case"])
        else:
            self.ops.emit(now, "lights",
                          f"{amb_id} emergency lights switched OFF by {who} — "
                          f"it no longer requests priority; its junctions "
                          f"will return to normal; the speed-limit exemption "
                          f"is withdrawn — it now drives at the posted "
                          f"limit", "warn",
                          actor=who, case=rec["case"])
        return True

    # ------------------------------------------------------------ lifecycle

    def on_depart(self, veh_id, now):
        rec = self.info.get(veh_id)
        if rec is not None:
            rec["departed"] = now
            self.ops.emit(now, "lifecycle",
                          f"{veh_id} entered the network", "info",
                          actor=veh_id, case=rec["case"])

    def on_teleport(self, veh_id, now):
        rec = self.info.get(veh_id)
        if rec is not None:
            self.ops.emit(now, "teleport",
                          f"{veh_id} TELEPORTED by the congestion resolver "
                          f"(physically stuck > 180 s in a jam) — its map "
                          f"position will jump; this is a simulation artefact,"
                          f" not a comms loss", "warn",
                          actor=veh_id, case=rec["case"])

    def on_arrive(self, veh_id, now, metrics, junctions_preempted=0):
        rec = self.info.get(veh_id)
        if rec is None or rec["arrived"] is not None:
            return
        # a unit that reached the scene must not leave the map without its
        # response time: try the real test first, then the last resort
        self._stamp_scene_arrival(veh_id, rec, now)
        self._stamp_scene_arrival(veh_id, rec, now, at_close=True)
        rec["arrived"] = now
        if rec["departed"] is not None:
            duration = now - rec["departed"]
            metrics.complete(veh_id, duration, rec["planned_length"])
            # with/without-preemption arrival-time analysis
            exp = rec.get("exp_signal_wait_s", 0.0)
            msig = rec.get("signal_wait_s", 0.0)
            mtraffic = rec.get("traffic_wait_s", 0.0)
            ff = rec.get("free_flow_s", 0.0)
            per_signal = rec.get("per_signal", [])
            modelled = sum(1 for p in per_signal if p.get("modelled"))
            # A with/without comparison is only meaningful for a run that
            # HAD a green wave.  A unit whose lights were off, that no
            # camera confirmed, that lost every arbitration, or that ran
            # while preemption was disarmed got no corridor at all: its run
            # is a baseline, and claiming a saving for it would invent one.
            # A run whose scene stop was never taken spent its hospital leg
            # outside the corridor system and outside delay attribution, so
            # its measured signal wait is not comparable either.
            had_wave = (junctions_preempted > 0
                        and not rec.get("load_stop_missed"))
            recovered = max(0.0, exp - msig) if had_wave else 0.0
            est_without = duration + recovered
            metrics.analysis.append({
                "id": veh_id,
                "actual_s": round(duration, 1),
                "est_without_s": round(est_without, 1),
                "free_flow_s": ff,
                "no_traffic_without_s": round(ff + exp, 1),
                "exp_signal_wait_s": exp,
                "meas_signal_wait_s": round(msig, 1),
                "meas_traffic_wait_s": round(mtraffic, 1),
                "recovered_s": round(recovered, 1),
                "signals": rec.get("signals_on_route", 0),
                # of those signals, how many the r^2/2C model could resolve
                # the ambulance's own movement at (the rest contribute
                # nothing to exp_signal_wait_s and must not be counted
                # behind it)
                "signals_modelled": modelled,
                "preemption": had_wave,
                "junctions_preempted": junctions_preempted,
                "per_signal": per_signal,
            })
            if had_wave:
                self.ops.emit(now, "analysis",
                              f"Arrival-time analysis {veh_id}: "
                              f"{duration:.0f} s measured WITH the green "
                              f"wave ({junctions_preempted} junctions "
                              f"purposely enabled for this unit); est. "
                              f"{est_without:.0f} s WITHOUT it in the same "
                              f"traffic (+{recovered:.0f} s at signal "
                              f"timers, r²/2C over {modelled} modelled "
                              f"junctions); no-traffic bounds {ff:.0f} s / "
                              f"{ff + exp:.0f} s. Highest-weight variable: "
                              f"the signal timer", "info",
                              actor=veh_id, case=rec["case"])
            else:
                self.ops.emit(now, "analysis",
                              f"Arrival-time analysis {veh_id}: "
                              f"{duration:.0f} s measured with NO green "
                              f"corridor opened for this unit (0 junctions "
                              f"preempted); no-traffic bound {ff:.0f} s. "
                              f"This run is a baseline, not a with/without "
                              f"comparison", "info",
                              actor=veh_id, case=rec["case"])
            where = rec.get("hospital") or "its destination"
            self.ops.emit(now, "arrival",
                          f"{veh_id} ARRIVED at {where} and was "
                          f"removed from the map (run complete): "
                          f"{duration:.0f} s for "
                          f"{rec['planned_length'] / 1000:.1f} km (avg "
                          f"{rec['planned_length'] / max(duration, 1) * 3.6:.0f}"
                          f" km/h; planned ETA was {rec['eta_s']:.0f} s)",
                          "info", actor=veh_id, case=rec["case"])
            self.ops.close_case(rec["case"], now,
                                f"arrived in {duration:.0f} s")
        self._schedule_return(rec, veh_id, now)

    def check_vanished(self, current_ids, now):
        """An active ambulance missing from the vehicle list without an
        arrival is reported as an error — nothing disappears silently."""
        for amb_id, rec in self.info.items():
            if (rec["departed"] is not None and rec["arrived"] is None
                    and amb_id not in current_ids):
                # a scene it did reach still owes a response time
                self._stamp_scene_arrival(amb_id, rec, now, at_close=True)
                rec["arrived"] = now
                self.ops.emit(now, "error",
                              f"{amb_id} LEFT THE SIMULATION UNEXPECTEDLY "
                              f"(not arrived, not in vehicle list) — "
                              f"investigate: likely removed by SUMO after a "
                              f"routing failure or teleport to route end",
                              "error", actor=amb_id, case=rec["case"])
                self.ops.close_case(rec["case"], now,
                                    "removed unexpectedly", status="error")
                self._schedule_return(rec, amb_id, now)

    # -------------------------------------------------------------- queries

    def active_ambulances(self, lights_only=True):
        """lights_only=True yields the corridor consumers: lights on and not
        PAUSED at the scene loading a patient.  A unit still driving its
        final approach to the stop point is in mission "loading" but has not
        started loading — it is moving, with lights on, and must keep its
        corridor."""
        return [amb_id for amb_id, rec in self.info.items()
                if rec["departed"] is not None and rec["arrived"] is None
                and (not lights_only
                     or (rec["lights"]
                         and not (rec.get("mission") == "loading"
                                  and rec.get("loading_started"))))]

    NAV_HISTORY = 12   # completed missions kept in the navigation payload

    def navigation(self, detail=None):
        """Payload for the navigation page and route overlays.

        Every mission still in flight plus the most recent NAV_HISTORY
        closed ones, in full.  Older closed missions are dropped: the page
        draws rows/geometry for whatever it lists, so an entry is complete
        or absent — and uncapped this payload grows by ~9 kB per mission
        for ever and is re-sent every 2 s to two pollers.  `detail` keeps
        one named mission whatever its age.
        """
        out, done = [], 0
        for amb_id, rec in reversed(list(self.info.items())):
            if rec["arrived"] is not None and amb_id != detail:
                if done >= self.NAV_HISTORY:
                    continue
                done += 1
            out.append({
                "id": amb_id,
                "desc": rec["desc"],
                "case": rec["case"],
                "lights": rec["lights"],
                "active": rec["departed"] is not None and rec["arrived"] is None,
                "mission": rec.get("mission", "to_scene"),
                "hospital": rec.get("hospital"),
                "algorithm": rec["algorithm"],
                "length_m": rec["planned_length"],
                "eta_s": rec["eta_s"],
                "signals_on_route": rec["signals_on_route"],
                "rows": rec["nav_rows"],
                "geometry": rec["geometry"],
            })
        out.reverse()   # insertion order: navigation.html defaults the
                        # selector to ids[ids.length - 1], the newest unit
        return out
