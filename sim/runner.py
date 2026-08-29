"""Core simulation wrapper: owns the TraCI connection, the preemption
controller, the dispatcher, the operations log and per-step snapshots."""
import json
import math
import os

import traci
import traci.constants as tc
import sumolib

from .config import SimConfig, SCENARIOS
from .sumo_env import ensure_sumo_home, sumo_binary
from .preemption import GreenWaveController
from .actuation import DemandResponsiveController
from .ambulance import Dispatcher
from .metrics import Metrics
from .operations import OperationsLog
from .router import Router
from .traffic_profile import hourly_profile, LEVELS, DAY_LABEL, LEVEL_LABEL, describe, PROFILES
from .markov import TrafficMarkov
from .places import Places

VEH_VARS = [tc.VAR_POSITION, tc.VAR_ANGLE, tc.VAR_SPEED,
            tc.VAR_ROAD_ID, tc.VAR_LANE_ID, tc.VAR_LANEPOSITION,
            tc.VAR_DISTANCE]
# background cars never need speed in the frame — position + heading only
CAR_VARS = [tc.VAR_POSITION, tc.VAR_ANGLE]


class Simulation:
    def __init__(self, root, cfg=None, gui=False, preemption=True, seed=None,
                 extra_args=None):
        self.root = root
        self.cfg = cfg or SimConfig()
        self.gui = gui
        self.extra_args = list(extra_args or [])
        self.seed = self.cfg.seed if seed is None else seed
        self._preemption_wanted = preemption
        self.net = None
        self.ops = None
        self.router = None
        self.controller = None
        self.dispatcher = None
        self.metrics = None
        self.markov = None
        self.time = 0.0
        self.teleports = 0
        self._limit_cache = {}   # amb id -> (lane, posted limit km/h)
        self._last_seq = 0
        self._tls_static = []  # per-junction geometry for the map

    # -------------------------------------------------------------- lifecycle

    def start(self):
        ensure_sumo_home()
        self.net = sumolib.net.readNet(os.path.join(self.root, self.cfg.net_file))
        cmd = [
            sumo_binary(self.gui),
            "-c", os.path.join(self.root, self.cfg.sumocfg),
            "--seed", str(self.seed),
            "--step-length", str(self.cfg.step_length),
            "--no-step-log", "--duration-log.disable",
        ]
        if self.cfg.lateral_resolution > 0:
            cmd += ["--lateral-resolution", str(self.cfg.lateral_resolution)]
        # time-of-day realism: the flat peak-rate demand base is scaled to
        # the chosen clock hour (01:00-05:00 -> near-empty Kuwaiti streets)
        self._profile = hourly_profile(self.root, self.cfg.day_type)
        self._level = LEVELS.get(self.cfg.traffic_level, 1.0)
        self._scale_hour = self.cfg.start_hour % 24
        # showcase-style scenarios bake their densities into the route file:
        # the clock does not scale them, and the state always caches
        self._static_demand = bool(
            SCENARIOS[self.cfg.scenario].get("static_demand"))
        if self._static_demand:
            cmd += ["--scale", "1.000"]
        else:
            cmd += ["--scale", f"{self._level * self.cfg.demand_factor * self._profile.get(self._scale_hour, 0.3):.3f}"]
        cmd += self.extra_args
        traci.start(cmd)
        self.ops = OperationsLog(self.root)
        self.places = Places(self.net, self.cfg, self.root)
        self.ops.places = self.places
        self.router = Router(self.net)
        self.router.places = self.places
        self.controller = GreenWaveController(
            self.cfg, self.ops, enabled=self._preemption_wanted,
            net=self.net)
        self.actuation = DemandResponsiveController(
            self.cfg, self.ops, enabled=self.cfg.actuation_enabled,
            net=self.net)
        self.markov = TrafficMarkov(self.net, self.cfg, self.root, self.ops)
        self._markov_next_save = self.cfg.markov_save_every_s
        self._fair_next_report = 300.0
        if self.cfg.markov_routing:
            self.router.predictor = self.markov
        self.controller.markov = self.markov   # queue-flush congestion feed
        if self.markov._loaded_obs:
            self.ops.emit(0.0, "system",
                          f"Markov traffic predictor loaded "
                          f"{self.markov._loaded_obs} observations from "
                          f"previous sessions ({len(self.markov.monitored)} "
                          f"monitored corridors) — the model keeps feeding "
                          f"itself every {self.cfg.markov_sample_s:.0f} s",
                          "info")
        self.dispatcher = Dispatcher(self.net, self.cfg, self.ops, self.router)
        self.metrics = Metrics()
        for tls_id in traci.trafficlight.getIDList():
            traci.trafficlight.subscribe(tls_id, [tc.TL_RED_YELLOW_GREEN_STATE])
        self._tls_static = self._locate_tls()
        # node id -> tls id (joined signals have prefixed ids like GS_<node>)
        tls_links, tls_pos = {}, {}
        for t in self._tls_static:
            tls_pos[t["id"]] = (t["lat"], t["lon"])
        for tls_id in traci.trafficlight.getIDList():
            ins = []
            for group in traci.trafficlight.getControlledLinks(tls_id):
                for in_lane, _out, via in group:
                    ins.append(in_lane.rsplit("_", 1)[0])
                    if via.startswith(":"):
                        node = via[1:].rsplit("_", 2)[0]
                        self.router.tls_map[node] = tls_id
            tls_links[tls_id] = sorted(set(ins))
        self.places.attach_tls(tls_links, tls_pos)
        for t in self._tls_static:
            d = self.places.describe(t["id"])
            t["code"], t["name"] = d["code"], d["name"]
            t["category"], t["area"] = d["category"], d["area"]
        if self._static_demand:
            self.ops.emit(0.0, "system",
                          f"Simulation started: {self.cfg.label()}, "
                          f"{len(self._tls_static)} signalized junctions, "
                          f"{self.clock()}, fixed 3-district demand "
                          f"(showcase — the clock never scales it), "
                          f"preemption "
                          f"{'ARMED' if self._preemption_wanted else 'DISARMED'}",
                          "info")
        else:
            d0 = describe(self.cfg.day_type, self.cfg.traffic_level,
                          self.cfg.start_hour)
            m0 = (self._level * self.cfg.demand_factor
                  * self._profile.get(self._scale_hour, 0.3))
            self.ops.emit(0.0, "system",
                          f"Simulation started: {self.cfg.label()}, "
                          f"{len(self._tls_static)} signalized junctions, "
                          f"{DAY_LABEL[self.cfg.day_type]} "
                          f"{self.clock()}, {LEVEL_LABEL[self.cfg.traffic_level]} "
                          f"traffic (demand {d0['word']}, {m0:.2f} x "
                          f"peak), preemption "
                          f"{'ARMED' if self._preemption_wanted else 'DISARMED'}",
                          "info")

    # ------------------------------------------------- warm-state caching

    WARM_STATE_VERSION = 1

    def _warm_state_path(self):
        key = (f"{self.cfg.scenario}_{self.cfg.day_type}_"
               f"{self.cfg.traffic_level}_{self.cfg.start_hour:02d}")
        return os.path.join(self.root, "data", f"warmstate_{key}.xml.gz")

    def _warm_state_stamp(self):
        mtimes = {}
        sc = SCENARIOS.get(self.cfg.scenario, {})
        for label, rel in (("routes", os.path.join("data",
                                                   sc.get("routes", ""))),
                           ("net", self.cfg.net_file),
                           ("vtypes", os.path.join("data",
                                                   "vtypes.add.xml"))):
            try:
                mtimes[label] = round(os.path.getmtime(
                    os.path.join(self.root, rel)))
            except OSError:
                mtimes[label] = 0
        return {"v": self.WARM_STATE_VERSION, "mtimes": mtimes,
                "warmup_s": self.cfg.warmup_s, "seed": self.seed,
                "step": self.cfg.step_length,
                "demand": self.cfg.demand_factor,
                "latres": self.cfg.lateral_resolution}

    def _drop_warm_state(self):
        for p in (self._warm_state_path(), self._warm_state_path() + ".json"):
            try:
                os.remove(p)
            except OSError:
                pass

    def try_load_warm_state(self):
        """Load a cached post-warm-up SUMO state, if a valid one exists:
        the city is instantly flowing instead of fast-forwarding minutes
        of traffic build-up on every start and mode switch.  A failed
        load leaves SUMO in an undefined state, so the cache is dropped
        and the whole simulation is relaunched cleanly."""
        path = self._warm_state_path()
        meta_path = path + ".json"
        try:
            with open(meta_path) as f:
                if json.load(f) != self._warm_state_stamp():
                    return False
        except (OSError, ValueError):
            return False
        try:
            traci.simulation.loadState(path)
        except Exception as exc:      # incl. FatalTraCIError
            self._drop_warm_state()
            try:
                self.close()
            except Exception:
                pass
            self.start()              # clean relaunch; cold warm-up follows
            self.ops.emit(0.0, "system",
                          f"cached city state could not be loaded ({exc}) "
                          f"— cache dropped, warming up cold", "warn")
            return False
        # the loaded state wipes ALL subscriptions: vehicles AND signals
        for veh_id in traci.vehicle.getIDList():
            traci.vehicle.subscribe(
                veh_id,
                VEH_VARS if veh_id.startswith("AMB_") else CAR_VARS)
        for tls_id in traci.trafficlight.getIDList():
            traci.trafficlight.subscribe(tls_id,
                                         [tc.TL_RED_YELLOW_GREEN_STATE])
        self.time = traci.simulation.getTime()
        if self.markov is not None:
            # restart the sampling grid at the loaded clock — otherwise
            # the catch-up loop fires every slice in one frame
            self.markov._next_sample = self.time
        self.ops.emit(self.time, "system",
                      f"WARM START: cached city state loaded "
                      f"(t={self.time:.0f} s, "
                      f"{len(traci.vehicle.getIDList())} vehicles already "
                      f"flowing) — no warm-up needed", "info")
        return True

    def save_warm_state(self):
        """Persist the post-warm-up SUMO state so the next start or mode
        switch at this scenario/day/level/hour begins instantly."""
        # SUMO 1.27 cannot serialise scale-clone vehicles (ids with '.'):
        # a state saved with them fails to load.  Demand scales above 1.0
        # produce clones, so those combinations stay on the cold warm-up.
        try:
            if any("." in v for v in traci.vehicle.getIDList()):
                self.ops.emit(self.time, "system",
                              "city state not cached at this demand level "
                              "(SUMO cannot serialise scale-cloned "
                              "vehicles) — cold warm-up remains", "info")
                return
        except traci.TraCIException:
            return
        path = self._warm_state_path()
        try:
            traci.simulation.saveState(path)
            with open(path + ".json", "w") as f:
                json.dump(self._warm_state_stamp(), f)
            self.ops.emit(self.time, "system",
                          f"City state cached — future starts at this "
                          f"scenario/hour skip the warm-up", "info")
        except (traci.TraCIException, OSError):
            pass

    def close(self):
        try:
            if self.markov is not None:
                self.markov.save()
        except Exception:
            pass
        try:
            traci.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ step

    def step(self):
        traci.simulationStep()
        self.time = traci.simulation.getTime()
        hour = int(self.cfg.start_hour + self.time / 3600) % 24
        if hour != self._scale_hour and self._static_demand:
            self._scale_hour = hour          # clock ticks; demand is baked
        elif hour != self._scale_hour:
            self._scale_hour = hour
            mult = (self._level * self.cfg.demand_factor
                    * self._profile.get(hour, 0.3))
            d = describe(self.cfg.day_type, self.cfg.traffic_level, hour)
            try:
                traci.simulation.setScale(mult)
                self.ops.emit(self.time, "system",
                              f"Clock reached {hour:02d}:00 — "
                              f"{DAY_LABEL[self.cfg.day_type]}, "
                              f"{LEVEL_LABEL[self.cfg.traffic_level]} traffic: "
                              f"demand now {d['word']} ({mult:.2f} x peak)",
                              "info")
            except traci.TraCIException:
                pass
        if self.time >= self._fair_next_report:
            self._fair_next_report = self.time + 300.0
            fm = self.actuation.mode_counts()
            # statement discipline: this check SPEAKS only when the audit
            # numbers actually moved — no narration without an action
            key = (fm["audit"]["grants"],
                   fm["audit"]["ended_for_other_traffic"],
                   fm["audit"]["violations"])
            if fm["occupied"] >= 5 and key != getattr(
                    self, "_last_fair_key", None):
                self._last_fair_key = key
                pct = round(100 * fm["fair"] / max(fm["occupied"], 1))
                self.ops.emit(self.time, "actuation",
                              f"Traffic check: {fm['fair']} of {fm['occupied']} "
                              f"occupied junctions have several approaches "
                              f"occupied — early green cannot apply there, fair "
                              f"timers by design ({pct}%); {fm['lone']} junctions "
                              f"have a lone approach; {fm['audit']['grants']} early "
                              f"greens granted so far, {fm['audit']['ended_for_other_traffic']} "
                              f"ended the moment other traffic arrived, fairness "
                              f"violations {fm['audit']['violations']}", "info")
        for veh_id in traci.simulation.getDepartedIDList():
            traci.vehicle.subscribe(
                veh_id,
                VEH_VARS if veh_id.startswith("AMB_") else CAR_VARS)
            self.dispatcher.on_depart(veh_id, self.time)
        for veh_id in traci.simulation.getStartingTeleportIDList():
            self.teleports += 1
            self.dispatcher.on_teleport(veh_id, self.time)
        for veh_id in traci.simulation.getArrivedIDList():
            self.dispatcher.on_arrive(veh_id, self.time, self.metrics)
        active = self.dispatcher.active_ambulances(lights_only=False)
        if active:
            self.dispatcher.check_vanished(set(traci.vehicle.getIDList()),
                                           self.time)
            if self.cfg.reroute_to_hospital:
                self.dispatcher.check_scene_reached(self.time)
            if self.cfg.adaptive_reroute:
                self.dispatcher.check_stuck(self.time)
        self.dispatcher.process_returns(self.time)
        # shared post-reroute getNextTLS cache: _attribute_delay fills it,
        # the preemption controller reads it before fetching itself
        next_tls = {}
        if self.dispatcher.info:
            self.dispatcher.check_pending_insertions(self.time)
            self._attribute_delay(active, next_tls)
        self.controller.update(
            self.dispatcher.active_ambulances(lights_only=True), self.time,
            next_tls)
        self.actuation.update(
            self.time,
            excluded=set(self.controller.active) | set(self.controller.pending))
        self.markov.update(self.time)
        if self.time >= self._markov_next_save:
            self._markov_next_save = self.time + self.cfg.markov_save_every_s
            self.markov.save()

    def _attribute_delay(self, active, next_tls):
        """Split each ambulance's lost time between 'waiting at a red signal'
        and 'stuck in traffic' — the measured side of the with/without
        arrival-time comparison."""
        results = traci.vehicle.getAllSubscriptionResults()
        for amb_id in active:
            rec = self.dispatcher.info.get(amb_id)
            if rec is None or rec.get("mission") == "loading":
                continue     # the loading stop is neither traffic nor signal
            try:
                speed = results.get(amb_id, {}).get(tc.VAR_SPEED)
                if speed is None:
                    speed = traci.vehicle.getSpeed(amb_id)
                if speed < 0.5:
                    nxt = traci.vehicle.getNextTLS(amb_id)
                    next_tls[amb_id] = nxt
                    if nxt and nxt[0][2] < 60 and nxt[0][3] in "ru":
                        rec["signal_wait_s"] += self.cfg.step_length
                    else:
                        rec["traffic_wait_s"] += self.cfg.step_length
                else:
                    lane = results.get(amb_id, {}).get(tc.VAR_LANE_ID)
                    if lane is None:
                        lane = traci.vehicle.getLaneID(amb_id)
                    limit = traci.lane.getMaxSpeed(lane)
                    if speed < 0.3 * limit:
                        rec["traffic_wait_s"] += self.cfg.step_length
            except traci.TraCIException:
                continue

    # ------------------------------------------------------------- snapshots

    def clock(self):
        total = int(self.cfg.start_hour * 3600 + self.time)
        return f"{(total // 3600) % 24:02d}:{(total % 3600) // 60:02d}:" \
               f"{total % 60:02d}"

    def _batch_lonlat(self, xys):
        """One vectorised inverse projection for the whole vehicle list —
        the per-vehicle convertXY2LonLat loop was the dominant snapshot
        cost at 5000 vehicles."""
        proj = getattr(self.net, "_proj", None)
        if proj is None or not xys:
            return [self.net.convertXY2LonLat(x, y) for x, y in xys]
        ox, oy = self.net.getLocationOffset()
        xs = [x - ox for x, _ in xys]
        ys = [y - oy for _, y in xys]
        lons, lats = proj(xs, ys, inverse=True)
        return list(zip(lons, lats))

    def snapshot(self):
        results = traci.vehicle.getAllSubscriptionResults()
        ids = list(results)
        lonlats = self._batch_lonlat(
            [results[v][tc.VAR_POSITION] for v in ids])
        cars, ambs = [], []
        for veh_id, (lon, lat) in zip(ids, lonlats):
            # a vehicle mid-teleport has no valid position: SUMO reports an
            # invalid coordinate that projects to inf/nan — ONE such value
            # makes the whole frame unparseable JSON in the browser (the
            # hidden cause of multi-second freezes during teleport storms)
            if not (math.isfinite(lon) and math.isfinite(lat)):
                continue
            vals = results[veh_id]
            angle = round(vals.get(tc.VAR_ANGLE, 0.0), 1)
            if veh_id.startswith("AMB_"):
                rec = self.dispatcher.info.get(veh_id, {})
                # the posted limit only changes on a lane change — cached
                lane = vals.get(tc.VAR_LANE_ID)
                if lane is None:
                    try:
                        lane = traci.vehicle.getLaneID(veh_id)
                    except traci.TraCIException:
                        lane = None
                cached = self._limit_cache.get(veh_id)
                if cached is not None and cached[0] == lane:
                    limit = cached[1]
                else:
                    try:
                        limit = (round(traci.lane.getMaxSpeed(lane) * 3.6)
                                 if lane and not lane.startswith(":")
                                 else None)
                    except traci.TraCIException:
                        limit = None
                    self._limit_cache[veh_id] = (lane, limit)
                ambs.append({
                    "id": veh_id,
                    "lon": round(lon, 6), "lat": round(lat, 6),
                    "angle": angle,
                    "kmh": round(vals.get(tc.VAR_SPEED, 0.0) * 3.6),
                    "limit": limit,
                    "lights": rec.get("lights", True),
                    "case": rec.get("case"),
                    "mission": rec.get("mission", "to_scene"),
                })
            else:
                cars.append([veh_id, round(lon, 6), round(lat, 6), angle])

        status = self.controller.status()
        demand = self.actuation.status()
        tls = {}
        for tls_id, vals in traci.trafficlight.getAllSubscriptionResults().items():
            entry = {"s": vals.get(tc.TL_RED_YELLOW_GREEN_STATE, ""),
                     "m": "normal", "case": None, "amb": None}
            if tls_id in status:
                entry.update(status[tls_id])
            elif tls_id in demand:
                entry["m"] = "demand"
            tls[tls_id] = entry

        events = [{"t": e["t"], "msg": e["msg"], "sev": e["sev"],
                   "type": e["type"], "case": e["case"]}
                  for e in self.ops.since(self._last_seq)]
        if events:
            self._last_seq = self.ops.ring[-1]["seq"]

        # route overlays straight from the mission records — the full
        # navigation() payload (rows etc.) is only built for /api/navigation
        routes = {}
        for amb_id, rec in self.dispatcher.info.items():
            if rec["departed"] is not None and rec["arrived"] is None:
                routes[amb_id] = {"pts": rec["geometry"][::2],
                                  "lights": rec["lights"]}

        kpi = self.metrics.kpi(len(results), len(ambs),
                               self.controller.active_count())
        kpi["teleports"] = self.teleports
        kpi["clock"] = self.clock()
        kpi["open_cases"] = self.ops.open_count
        kpi["early_greens"] = self.actuation.granted_total
        kpi["queued_calls"] = len(self.dispatcher.call_queue)
        kpi["queue_oldest_s"] = (round(self.time
                                       - self.dispatcher.call_queue[0]["t"])
                                 if self.dispatcher.call_queue else 0)
        kpi["response"] = self.dispatcher.response_summary()
        kpi["demand_serving"] = self.actuation.active_count()
        fm = self.actuation.mode_counts()
        kpi["fair_timer_junctions"] = fm["fair"]
        kpi["lone_junctions"] = fm["lone"]
        kpi["occupied_junctions"] = fm["occupied"]
        kpi["early_audit"] = fm["audit"]
        if self._static_demand:
            kpi["traffic"] = {"day": "showcase", "level": "showcase",
                              "word": "fixed 3-district demand",
                              "multiplier": 1.0}
        else:
            kpi["traffic"] = {"day": self.cfg.day_type,
                              "level": self.cfg.traffic_level,
                              **describe(self.cfg.day_type,
                                         self.cfg.traffic_level,
                                         self._scale_hour)}

        return {
            "t": self.time,
            "cars": cars,
            "ambs": ambs,
            "tls": tls,
            "kpi": kpi,
            "events": events,
            "routes": routes,
            "fleet": self.dispatcher.fleet_status(),
            "pending": self.controller.pending_decisions(),
            "preemption": self.controller.enabled,
        }

    # ----------------------------------------------------- static map layers

    def network_payload(self):
        """Road polylines, signal-head positions, hospitals — sent once."""
        edges = []
        for edge in self.net.getEdges():
            shape = [(round(lat, 6), round(lon, 6))
                     for lon, lat in (self.net.convertXY2LonLat(x, y)
                                      for x, y in edge.getShape())]
            edges.append({"pts": shape, "lanes": edge.getLaneNumber(),
                          "prio": edge.getPriority()})
        (x0, y0), (x1, y1) = self.net.getBoundary()[:2], self.net.getBoundary()[2:]
        lon0, lat0 = self.net.convertXY2LonLat(x0, y0)
        lon1, lat1 = self.net.convertXY2LonLat(x1, y1)
        return {
            "edges": edges,
            "tls": self._tls_static,
            "districts": SCENARIOS[self.cfg.scenario].get("districts"),
            "static_demand": self._static_demand,
            "hospitals": [{"name": name, "lat": lat, "lon": lon}
                          for name, (lat, lon) in
                          self.dispatcher.hospitals.items()],
            "areas": [{"name": name, "lat": lat, "lon": lon}
                      for name, (lat, lon) in self.dispatcher.areas.items()],
            "start_hour": self.cfg.start_hour,
            "day_type": self.cfg.day_type,
            "traffic_level": self.cfg.traffic_level,
            "profiles": {k: [round(LEVELS["medium"] * v[h], 2) for h in range(24)]
                         for k, v in PROFILES.items()},
            "levels": LEVELS,
            "scenario": self.cfg.scenario,
            "scenarios": {k: v["label"] for k, v in SCENARIOS.items()},
            "bounds": [[lat0, lon0], [lat1, lon1]],
        }

    def _locate_tls(self):
        """Per traffic light: junction centre + ONE signal head per APPROACH
        (all lanes of an approach grouped), each with its travel bearing and
        the state-string indices it aggregates — a 4-way junction shows 4
        heads, which is what a non-engineer expects to see."""
        out = []
        for tls_id in traci.trafficlight.getIDList():
            links = traci.trafficlight.getControlledLinks(tls_id)
            by_edge = {}   # in-edge -> {"xs", "ys", "bearings", "idx"}
            xs_all, ys_all = [], []
            for i, group in enumerate(links):
                if not group:
                    continue
                in_lane = group[0][0]
                try:
                    shape = traci.lane.getShape(in_lane)
                    x, y = shape[-1]
                except traci.TraCIException:
                    continue
                if len(shape) >= 2:
                    px, py = shape[-2]
                    bearing = math.degrees(math.atan2(x - px, y - py)) % 360
                else:
                    bearing = 0.0
                edge = in_lane.rsplit("_", 1)[0]
                rec = by_edge.setdefault(
                    edge, {"xs": [], "ys": [], "b": [], "idx": []})
                rec["xs"].append(x)
                rec["ys"].append(y)
                rec["b"].append(bearing)
                rec["idx"].append(i)
                xs_all.append(x)
                ys_all.append(y)
            approaches = []
            for rec in by_edge.values():
                x = sum(rec["xs"]) / len(rec["xs"])
                y = sum(rec["ys"]) / len(rec["ys"])
                # circular mean of the lane bearings
                sx = sum(math.sin(math.radians(b)) for b in rec["b"])
                cy = sum(math.cos(math.radians(b)) for b in rec["b"])
                bearing = round(math.degrees(math.atan2(sx, cy)) % 360)
                lon, lat = self.net.convertXY2LonLat(x, y)
                approaches.append({"lat": round(lat, 6), "lon": round(lon, 6),
                                   "dir": bearing,
                                   "idx": sorted(set(rec["idx"]))})
            if xs_all:
                lon, lat = self.net.convertXY2LonLat(
                    sum(xs_all) / len(xs_all), sum(ys_all) / len(ys_all))
            else:
                lat = lon = 0.0
            out.append({"id": tls_id, "lat": round(lat, 6),
                        "lon": round(lon, 6), "appr": approaches})
        return out

    # ---------------------------------------------------------------- helpers

    def log_event(self, msg, sev="warn"):
        self.ops.emit(self.time, "system", msg, sev)

    def set_preemption(self, on: bool, who="operator"):
        self.controller.set_enabled(on, who)
        if not on:
            self.controller.release_all(self.time)

    def dispatch(self, origin=None, destination=None):
        return self.dispatcher.dispatch(origin, destination, self.time)
