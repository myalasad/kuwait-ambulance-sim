"""Ambulance dispatch: geocode lat/lon to network edges, route, insert."""
import random

import traci

from .config import HOSPITALS


class Dispatcher:
    def __init__(self, net, cfg, log):
        self.net = net
        self.cfg = cfg
        self.log = log
        self.count = 0
        self.info = {}  # amb_id -> {planned_length, departed, arrived, desc}
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

    def dispatch(self, origin=None, destination=None):
        """Insert an ambulance, lights on.

        origin: hospital name, (lat, lon) tuple, or None for a random edge.
        destination: (lat, lon) tuple, or None for a random reachable edge.
        Returns the new ambulance id; raises ValueError if unroutable.
        """
        from_edges, from_desc = self._resolve(origin, "origin")
        stage = None
        to_desc = ""
        if destination is not None:
            to_edges, to_desc = self._resolve(destination, "destination")
            for from_edge in from_edges:
                for to_edge in to_edges:
                    if to_edge.getID() == from_edge.getID():
                        continue
                    stage = traci.simulation.findRoute(
                        from_edge.getID(), to_edge.getID(),
                        vType=self.cfg.ambulance_type)
                    if stage.edges:
                        break
                if stage is not None and stage.edges:
                    break
            if stage is None or not stage.edges:
                raise ValueError(f"No route from {from_desc} to {to_desc}")
        else:
            from_edge = from_edges[0]
            for _ in range(25):
                to_edge = self.random_edge()
                if to_edge.getID() == from_edge.getID():
                    continue
                stage = traci.simulation.findRoute(
                    from_edge.getID(), to_edge.getID(),
                    vType=self.cfg.ambulance_type)
                if stage.edges:
                    to_desc = to_edge.getID()
                    break
            if stage is None or not stage.edges:
                raise ValueError("Could not find a routable destination")

        self.count += 1
        amb_id = f"AMB_{self.count}"
        route_id = f"route_{amb_id}"
        traci.route.add(route_id, stage.edges)
        traci.vehicle.add(amb_id, route_id, typeID=self.cfg.ambulance_type,
                          departLane="best", departSpeed="max")
        self.info[amb_id] = {
            "planned_length": stage.length,
            "departed": None,
            "arrived": None,
            "desc": f"{from_desc} -> {to_desc}",
        }
        self.log(f"{amb_id} dispatched ({from_desc} to {to_desc}), "
                 f"route {stage.length / 1000:.1f} km, lights ON")
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

    # ------------------------------------------------------------ lifecycle

    def on_depart(self, veh_id, now):
        if veh_id in self.info:
            self.info[veh_id]["departed"] = now

    def on_arrive(self, veh_id, now, metrics):
        rec = self.info.get(veh_id)
        if rec is None or rec["arrived"] is not None:
            return
        rec["arrived"] = now
        if rec["departed"] is not None:
            duration = now - rec["departed"]
            metrics.complete(veh_id, duration, rec["planned_length"])
            self.log(f"{veh_id} arrived: {duration:.0f} s for "
                     f"{rec['planned_length'] / 1000:.1f} km "
                     f"(avg {rec['planned_length'] / max(duration, 1) * 3.6:.0f} km/h)")

    def active_ambulances(self):
        return [amb_id for amb_id, rec in self.info.items()
                if rec["departed"] is not None and rec["arrived"] is None]
