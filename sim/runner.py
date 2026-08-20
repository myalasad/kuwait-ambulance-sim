"""Core simulation wrapper: owns the TraCI connection, the preemption
controller, the dispatcher, the operations log and per-step snapshots."""
import os

import traci
import traci.constants as tc
import sumolib

from .config import SimConfig, HOSPITALS
from .sumo_env import ensure_sumo_home, sumo_binary
from .preemption import GreenWaveController
from .ambulance import Dispatcher
from .metrics import Metrics
from .operations import OperationsLog
from .router import Router

VEH_VARS = [tc.VAR_POSITION, tc.VAR_ANGLE, tc.VAR_SPEED]


class Simulation:
    def __init__(self, root, cfg=None, gui=False, preemption=True, seed=None):
        self.root = root
        self.cfg = cfg or SimConfig()
        self.gui = gui
        self.seed = self.cfg.seed if seed is None else seed
        self._preemption_wanted = preemption
        self.net = None
        self.ops = None
        self.router = None
        self.controller = None
        self.dispatcher = None
        self.metrics = None
        self.time = 0.0
        self.teleports = 0
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
        traci.start(cmd)
        self.ops = OperationsLog(self.root)
        self.router = Router(self.net)
        self.controller = GreenWaveController(
            self.cfg, self.ops, enabled=self._preemption_wanted)
        self.dispatcher = Dispatcher(self.net, self.cfg, self.ops, self.router)
        self.metrics = Metrics()
        for tls_id in traci.trafficlight.getIDList():
            traci.trafficlight.subscribe(tls_id, [tc.TL_RED_YELLOW_GREEN_STATE])
        self._tls_static = self._locate_tls()
        self.ops.emit(0.0, "system",
                      f"Simulation started: downtown Kuwait City, "
                      f"{len(self._tls_static)} signalized junctions, "
                      f"clock {self.clock()}, preemption "
                      f"{'ARMED' if self._preemption_wanted else 'DISARMED'}",
                      "info")

    def close(self):
        try:
            traci.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ step

    def step(self):
        traci.simulationStep()
        self.time = traci.simulation.getTime()
        for veh_id in traci.simulation.getDepartedIDList():
            traci.vehicle.subscribe(veh_id, VEH_VARS)
            self.dispatcher.on_depart(veh_id, self.time)
        for veh_id in traci.simulation.getStartingTeleportIDList():
            self.teleports += 1
            self.dispatcher.on_teleport(veh_id, self.time)
        for veh_id in traci.simulation.getArrivedIDList():
            self.dispatcher.on_arrive(veh_id, self.time, self.metrics)
        if self.dispatcher.active_ambulances(lights_only=False):
            self.dispatcher.check_vanished(set(traci.vehicle.getIDList()),
                                           self.time)
        self.controller.update(
            self.dispatcher.active_ambulances(lights_only=True), self.time)

    # ------------------------------------------------------------- snapshots

    def clock(self):
        total = int(self.cfg.start_hour * 3600 + self.time)
        return f"{(total // 3600) % 24:02d}:{(total % 3600) // 60:02d}:" \
               f"{total % 60:02d}"

    def snapshot(self):
        results = traci.vehicle.getAllSubscriptionResults()
        cars, ambs = [], []
        for veh_id, vals in results.items():
            x, y = vals[tc.VAR_POSITION]
            lon, lat = self.net.convertXY2LonLat(x, y)
            angle = round(vals.get(tc.VAR_ANGLE, 0.0), 1)
            if veh_id.startswith("AMB_"):
                rec = self.dispatcher.info.get(veh_id, {})
                ambs.append({
                    "id": veh_id,
                    "lon": round(lon, 6), "lat": round(lat, 6),
                    "angle": angle,
                    "kmh": round(vals.get(tc.VAR_SPEED, 0.0) * 3.6),
                    "lights": rec.get("lights", True),
                    "case": rec.get("case"),
                })
            else:
                cars.append([veh_id, round(lon, 6), round(lat, 6), angle])

        status = self.controller.status()
        tls = {}
        for tls_id, vals in traci.trafficlight.getAllSubscriptionResults().items():
            entry = {"s": vals.get(tc.TL_RED_YELLOW_GREEN_STATE, ""),
                     "m": "normal", "case": None, "amb": None}
            if tls_id in status:
                entry.update(status[tls_id])
            tls[tls_id] = entry

        events = [{"t": e["t"], "msg": e["msg"], "sev": e["sev"],
                   "type": e["type"], "case": e["case"]}
                  for e in self.ops.since(self._last_seq)]
        if events:
            self._last_seq = self.ops.ring[-1]["seq"]

        routes = {}
        for nav in self.dispatcher.navigation():
            if nav["active"]:
                routes[nav["id"]] = {"pts": nav["geometry"][::2],
                                     "lights": nav["lights"]}

        kpi = self.metrics.kpi(len(results), len(ambs),
                               self.controller.active_count())
        kpi["teleports"] = self.teleports
        kpi["clock"] = self.clock()
        kpi["open_cases"] = len(self.ops.open_cases())

        return {
            "t": self.time,
            "cars": cars,
            "ambs": ambs,
            "tls": tls,
            "kpi": kpi,
            "events": events,
            "routes": routes,
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
            "hospitals": [{"name": name, "lat": lat, "lon": lon}
                          for name, (lat, lon) in HOSPITALS.items()],
            "bounds": [[lat0, lon0], [lat1, lon1]],
        }

    def _locate_tls(self):
        """Per traffic light: junction centre + one point per signal head
        (the end of each controlled approach lane), so the map can show the
        red/green state of every approach individually."""
        out = []
        for tls_id in traci.trafficlight.getIDList():
            links = traci.trafficlight.getControlledLinks(tls_id)
            heads, xs, ys = [], [], []
            for group in links:
                if not group:
                    heads.append(None)
                    continue
                in_lane = group[0][0]
                try:
                    x, y = traci.lane.getShape(in_lane)[-1]
                except traci.TraCIException:
                    heads.append(None)
                    continue
                lon, lat = self.net.convertXY2LonLat(x, y)
                heads.append([round(lat, 6), round(lon, 6)])
                xs.append(x)
                ys.append(y)
            if xs:
                lon, lat = self.net.convertXY2LonLat(
                    sum(xs) / len(xs), sum(ys) / len(ys))
            else:
                lat = lon = 0.0
            out.append({"id": tls_id, "lat": round(lat, 6),
                        "lon": round(lon, 6), "heads": heads})
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
