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



class Dispatcher:
    def __init__(self, net, cfg, ops, router):
        self.net = net
        self.cfg = cfg
        self.ops = ops
        self.router = router
        self.count = 0
        self.hospitals = cfg.hospitals_d()
        self.areas = cfg.areas_d()
        self.info = {}  # amb_id -> lifecycle + navigation record
        self._edges = [e for e in net.getEdges()
                       if e.allows("passenger") and e.getLength() > 30]
        self._rng = random.Random(1)

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

    def random_edge(self):
        return self._rng.choice(self._edges)

    # -------------------------------------------------------------- dispatch

    def dispatch(self, origin=None, destination=None, now=0.0):
        """Insert an ambulance, lights on.  Ambulances always originate at a
        hospital: origin None/"auto" selects the hospital NEAREST to the
        incident scene by Dijkstra travel time.  destination None picks a
        random named incident area.  Returns the new ambulance id."""
        if destination is None:
            area = self._rng.choice(sorted(self.areas))
            lat, lon = self.areas[area]
            to_edges = self.nearest_edges(lat, lon)
            to_desc = f"{area} (random area)"
            if not to_edges:
                raise ValueError(f"No road near area {area}")
        else:
            to_edges, to_desc = self._resolve(destination, "destination")

        route, algorithm, from_desc = None, None, None
        live = self.cfg.route_live_weights
        if origin in (None, "auto"):
            best = None
            for name, (lat, lon) in self.hospitals.items():
                for cand in self.nearest_edges(lat, lon, k=3):
                    r = None
                    for to_edge in to_edges:
                        if to_edge.getID() == cand.getID():
                            continue
                        r = self.router.route(cand.getID(), to_edge.getID(),
                                              live=live)
                        if r:
                            eta = self.router.nodal_analysis(
                                r, live=live)[-1]["eta_s"]
                            if best is None or eta < best[2]:
                                best = (name, r, eta)
                            break
                    if r:
                        break
            if best is None:
                raise ValueError(f"No hospital can reach {to_desc}")
            from_desc = f"{best[0]} (nearest hospital to the scene)"
            route = best[1]
            algorithm = ("Dijkstra (live + Markov-predicted travel times)"
                             if self.router.predictor is not None
                             else "Dijkstra (live edge travel times)")
        else:
            from_edges, from_desc = self._resolve(origin, "origin")
            for from_edge in from_edges:
                for to_edge in to_edges:
                    if to_edge.getID() == from_edge.getID():
                        continue
                    route = self.router.route(from_edge.getID(),
                                              to_edge.getID(), live=live)
                    algorithm = ("Dijkstra (live + Markov-predicted travel times)"
                             if self.router.predictor is not None
                             else "Dijkstra (live edge travel times)")
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
        traci.route.add(route_id, route)
        traci.vehicle.add(amb_id, route_id, typeID=self.cfg.ambulance_type,
                          departLane="best", departSpeed="max")
        # emergency speed exemption: above the posted limit, capped absolutely
        traci.vehicle.setSpeedFactor(amb_id, self.cfg.speed_exemption_factor)
        traci.vehicle.setMaxSpeed(amb_id, self.cfg.ambulance_max_kmh / 3.6)
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
            "loading_started": False,
        }
        self.ops.emit(now, "dispatch",
                      f"{amb_id} dispatched ({from_desc} to {to_desc}): "
                      f"{algorithm}, {length_m / 1000:.1f} km, "
                      f"{self.info[amb_id]['signals_on_route']} signals on "
                      f"route, ETA {eta_s:.0f} s, lights ON, speed-limit "
                      f"exemption active (up to "
                      f"{self.cfg.speed_exemption_factor:.0%} of posted "
                      f"limit, max {self.cfg.ambulance_max_kmh:.0f} km/h)",
                      "info", actor=amb_id, case=case)
        return amb_id

    # ------------------------------------------------- arrival-time analysis

    def _expected_signal_wait(self, rows):
        """Expected red-light wait per signal on the route WITHOUT preemption,
        from each junction's real programme: for a vehicle arriving at a
        uniformly random point of the cycle, E[wait] = r^2 / (2C) with r the
        red time for its approach and C the cycle — quadratic in the signal
        timer, which is why the timer is the highest-weight variable in the
        with/without comparison."""
        total = 0.0
        per = []
        for r in rows:
            if not r["signal"]:
                continue
            try:
                logics = traci.trafficlight.getAllProgramLogics(r["signal"])
            except traci.TraCIException:
                continue
            if not logics:
                continue
            phases = logics[0].phases
            cycle = sum(p.duration for p in phases)
            if cycle <= 0:
                continue
            green = max((p.duration for p in phases
                         if "G" in p.state or "g" in p.state), default=0.0)
            red = max(0.0, cycle - green)
            e_wait = red * red / (2 * cycle)
            total += e_wait
            per.append({"tls": r["signal"], "cycle": round(cycle),
                        "red": round(red), "exp_wait_s": round(e_wait, 1)})
        return round(total, 1), per

    def _free_flow_exempt(self, route):
        """Travel time on an empty road at the exempt speed profile."""
        t = 0.0
        cap = self.cfg.ambulance_max_kmh / 3.6
        for eid in route:
            edge = self.net.getEdge(eid)
            v = min(edge.getSpeed() * self.cfg.speed_exemption_factor, cap)
            t += edge.getLength() / max(v, 1.0)
        return round(t, 1)

    def _resolve(self, spec, kind):
        if spec is None:
            return [self.random_edge()], f"random {kind}"
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
            mission = rec.get("mission")
            if mission == "to_scene":
                try:
                    idx = traci.vehicle.getRouteIndex(amb_id)
                except traci.TraCIException:
                    continue
                if 0 <= idx >= len(rec["route_edges"]) - 2:
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
                elif rec.get("loading_started"):
                    rec["mission"] = "to_hospital"
                    self.ops.emit(now, "reroute",
                                  f"{amb_id} patient aboard — hot return to "
                                  f"{rec['hospital']}, lights ON, corridor "
                                  f"resumes along the new route", "warn",
                                  actor=amb_id, case=rec["case"])

    def _begin_return_leg(self, amb_id, rec, idx, now):
        scene_edge = rec["route_edges"][-1]
        # nearest hospital by actual routed travel time (one-ways respected)
        best = None
        for name, (lat, lon) in self.hospitals.items():
            for cand in self.nearest_edges(lat, lon, k=3):
                if cand.getID() == scene_edge:
                    break                      # scene is at this hospital
                leg = self.router.route(scene_edge, cand.getID(),
                                        live=self.cfg.route_live_weights)
                if leg and len(leg) >= 2:
                    rows = self.router.nodal_analysis(
                        leg, live=self.cfg.route_live_weights)
                    if best is None or rows[-1]["eta_s"] < best[3]:
                        best = (name, leg, rows, rows[-1]["eta_s"])
                    break
        if best is None:
            rec["mission"] = "to_hospital"
            self.ops.emit(now, "error",
                          f"{amb_id}: no reachable hospital from the scene — "
                          f"mission ends at the scene", "error",
                          actor=amb_id, case=rec["case"])
            return
        name, leg, rows, eta2 = best

        cur = traci.vehicle.getRoadID(amb_id)
        if cur.startswith(":"):
            cur = rec["route_edges"][idx]
        new_route = leg if cur == leg[0] else [cur] + leg
        traci.vehicle.setRoute(amb_id, new_route)

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
            traci.vehicle.setStop(amb_id, scene_edge, pos=pos, laneIndex=0,
                                  duration=self.cfg.patient_load_s)
            stopped = True
        except traci.TraCIException:
            pass

        exp2, per2 = self._expected_signal_wait(rows)
        ff2 = self._free_flow_exempt(new_route)
        load = self.cfg.patient_load_s if stopped else 0.0
        rec.update({
            "mission": "loading" if stopped else "to_hospital",
            "loading_started": False,
            "hospital": name,
            "route_edges": new_route,
            "nav_rows": rows,
            "geometry": self.router.route_geometry(new_route),
            "planned_length": rec["planned_length"] + rows[-1]["dist_m"],
            "eta_s": rec["eta_s"] + eta2 + load,
            "exp_signal_wait_s": round(rec["exp_signal_wait_s"] + exp2, 1),
            "free_flow_s": round(rec["free_flow_s"] + ff2 + load, 1),
            "signals_on_route": rec["signals_on_route"]
                                + sum(1 for r in rows if r["signal"]),
            "per_signal": rec["per_signal"] + per2,
            "desc": rec["desc"] + f" -> {name}",
        })
        loading_txt = (f"loading patient ({load:.0f} s, corridor paused); "
                       if stopped else "")
        self.ops.emit(now, "reroute",
                      f"{amb_id} reached the incident scene — {loading_txt}"
                      f"REROUTED to the nearest hospital by travel time: "
                      f"{name} (Dijkstra, {rows[-1]['dist_m'] / 1000:.1f} km, "
                      f"ETA {eta2:.0f} s, "
                      f"{sum(1 for r in rows if r['signal'])} signals) — "
                      f"the signal corridor follows the new route", "warn",
                      actor=amb_id, case=rec["case"])

    # ---------------------------------------------------------------- lights

    def set_lights(self, amb_id, on, now, who="operator"):
        rec = self.info.get(amb_id)
        if rec is None or rec["arrived"] is not None:
            return False
        if rec["lights"] == on:
            return True
        rec["lights"] = on
        if on:
            self.ops.emit(now, "lights",
                          f"{amb_id} emergency lights switched ON by {who} — "
                          f"corridor requests resume", "warn",
                          actor=who, case=rec["case"])
        else:
            self.ops.emit(now, "lights",
                          f"{amb_id} emergency lights switched OFF by {who} — "
                          f"it no longer requests priority; its junctions "
                          f"will return to normal", "warn",
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

    def on_arrive(self, veh_id, now, metrics):
        rec = self.info.get(veh_id)
        if rec is None or rec["arrived"] is not None:
            return
        rec["arrived"] = now
        if rec["departed"] is not None:
            duration = now - rec["departed"]
            metrics.complete(veh_id, duration, rec["planned_length"])
            # with/without-preemption arrival-time analysis
            exp = rec.get("exp_signal_wait_s", 0.0)
            msig = rec.get("signal_wait_s", 0.0)
            mtraffic = rec.get("traffic_wait_s", 0.0)
            ff = rec.get("free_flow_s", 0.0)
            recovered = max(0.0, exp - msig)   # timer delay the wave removed
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
                "per_signal": rec.get("per_signal", []),
            })
            self.ops.emit(now, "analysis",
                          f"Arrival-time analysis {veh_id}: {duration:.0f} s "
                          f"measured WITH the green wave; est. "
                          f"{est_without:.0f} s WITHOUT it in the same "
                          f"traffic (+{recovered:.0f} s at signal timers, "
                          f"r²/2C per junction); no-traffic bounds "
                          f"{ff:.0f} s / {ff + exp:.0f} s. Highest-weight "
                          f"variable: the signal timer", "info",
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

    def check_vanished(self, current_ids, now):
        """An active ambulance missing from the vehicle list without an
        arrival is reported as an error — nothing disappears silently."""
        for amb_id, rec in self.info.items():
            if (rec["departed"] is not None and rec["arrived"] is None
                    and amb_id not in current_ids):
                rec["arrived"] = now
                self.ops.emit(now, "error",
                              f"{amb_id} LEFT THE SIMULATION UNEXPECTEDLY "
                              f"(not arrived, not in vehicle list) — "
                              f"investigate: likely removed by SUMO after a "
                              f"routing failure or teleport to route end",
                              "error", actor=amb_id, case=rec["case"])
                self.ops.close_case(rec["case"], now,
                                    "removed unexpectedly", status="error")

    # -------------------------------------------------------------- queries

    def active_ambulances(self, lights_only=True):
        """lights_only=True yields the corridor consumers: lights on and not
        paused at the scene loading a patient."""
        return [amb_id for amb_id, rec in self.info.items()
                if rec["departed"] is not None and rec["arrived"] is None
                and (not lights_only
                     or (rec["lights"] and rec.get("mission") != "loading"))]

    def navigation(self):
        """Payload for the navigation page and route overlays."""
        out = []
        for amb_id, rec in self.info.items():
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
        return out
