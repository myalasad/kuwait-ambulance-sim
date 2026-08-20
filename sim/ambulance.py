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

from .config import HOSPITALS


class Dispatcher:
    def __init__(self, net, cfg, ops, router):
        self.net = net
        self.cfg = cfg
        self.ops = ops
        self.router = router
        self.count = 0
        self.info = {}  # amb_id -> lifecycle + navigation record
        self._edges = [e for e in net.getEdges()
                       if e.allows("passenger") and e.getLength() > 30]
        self._rng = random.Random(1)

    # ------------------------------------------------------------- geocoding

    def nearest_edges(self, lat, lon, k=4, radius=350):
        """The k closest passenger edges — the caller tries them in order,
        because the very nearest can be an unreachable one-way stub."""
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
        """Insert an ambulance, lights on.  Returns the new ambulance id."""
        from_edges, from_desc = self._resolve(origin, "origin")
        if destination is not None:
            to_edges, to_desc = self._resolve(destination, "destination")
        else:
            to_edges = [self.random_edge() for _ in range(12)]
            to_desc = "random destination"

        route, algorithm = None, None
        for from_edge in from_edges:
            for to_edge in to_edges:
                if to_edge.getID() == from_edge.getID():
                    continue
                route = self.router.route(from_edge.getID(), to_edge.getID(),
                                          live=self.cfg.route_live_weights)
                algorithm = "Dijkstra (live edge travel times)"
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

        self.count += 1
        amb_id = f"AMB_{self.count}"
        route_id = f"route_{amb_id}"
        traci.route.add(route_id, route)
        traci.vehicle.add(amb_id, route_id, typeID=self.cfg.ambulance_type,
                          departLane="best", departSpeed="max")
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
        }
        self.ops.emit(now, "dispatch",
                      f"{amb_id} dispatched ({from_desc} to {to_desc}): "
                      f"{algorithm}, {length_m / 1000:.1f} km, "
                      f"{self.info[amb_id]['signals_on_route']} signals on "
                      f"route, ETA {eta_s:.0f} s, lights ON", "info",
                      actor=amb_id, case=case)
        return amb_id

    def _resolve(self, spec, kind):
        if spec is None:
            return [self.random_edge()], f"random {kind}"
        if isinstance(spec, str):
            if spec not in HOSPITALS:
                raise ValueError(f"Unknown hospital: {spec}")
            lat, lon = HOSPITALS[spec]
            edges = self.nearest_edges(lat, lon)
            if not edges:
                raise ValueError(f"No road near {spec}")
            return edges, spec
        lat, lon = spec
        edges = self.nearest_edges(lat, lon)
        if not edges:
            raise ValueError(f"No road near {kind} ({lat:.4f}, {lon:.4f})")
        return edges, f"({lat:.4f}, {lon:.4f})"

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
            self.ops.emit(now, "arrival",
                          f"{veh_id} ARRIVED at its destination and was "
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
        return [amb_id for amb_id, rec in self.info.items()
                if rec["departed"] is not None and rec["arrived"] is None
                and (rec["lights"] or not lights_only)]

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
                "algorithm": rec["algorithm"],
                "length_m": rec["planned_length"],
                "eta_s": rec["eta_s"],
                "signals_on_route": rec["signals_on_route"],
                "rows": rec["nav_rows"],
                "geometry": rec["geometry"],
            })
        return out
