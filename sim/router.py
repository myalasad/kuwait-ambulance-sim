"""Route optimisation: Dijkstra over the road network's edge graph.

The graph is edge-based (nodes of the search are road edges, arcs are the
legal edge-to-edge connections), so one-way streets and turn restrictions
from OpenStreetMap are respected exactly as netconvert encoded them.

Edge weights are travel times.  With a live TraCI connection the current
estimated travel time of each edge is used (SUMO derives it from the mean
speed actually being driven), so the route adapts to congestion; without one
it falls back to free-flow times (length / speed limit).

The same route object drives both the ambulance (assigned via TraCI) and the
signal controller (which preempts the junctions along it) — the corridor and
the navigation are one and the same, by construction.
"""
import heapq

import traci


class Router:
    def __init__(self, net, vclass="passenger"):
        self.net = net
        self.vclass = vclass
        # edge id -> list of (successor edge id, successor static cost)
        self.succ = {}
        self.static_cost = {}
        for edge in net.getEdges():
            if not edge.allows(vclass):
                continue
            eid = edge.getID()
            speed = max(edge.getSpeed(), 1.0)
            self.static_cost[eid] = edge.getLength() / speed
            outs = []
            for nxt in edge.getOutgoing():
                if nxt.allows(vclass):
                    outs.append(nxt.getID())
            self.succ[eid] = outs

    # ------------------------------------------------------------- weights

    def _weight(self, eid, live):
        if live:
            try:
                t = traci.edge.getTraveltime(eid)
                # SUMO reports absurd values for empty/blocked edges; clamp
                # to no better than free flow and no worse than 20 minutes
                return min(max(t, self.static_cost[eid]), 1200.0)
            except traci.TraCIException:
                pass
        return self.static_cost[eid]

    # ------------------------------------------------------------ dijkstra

    def route(self, from_edge, to_edge, live=True):
        """Shortest-time edge sequence from from_edge to to_edge, or None."""
        if from_edge not in self.succ or to_edge not in self.succ:
            return None
        if from_edge == to_edge:
            return [from_edge]
        dist = {from_edge: 0.0}
        prev = {}
        heap = [(0.0, from_edge)]
        visited = set()
        while heap:
            d, eid = heapq.heappop(heap)
            if eid in visited:
                continue
            visited.add(eid)
            if eid == to_edge:
                path = [eid]
                while path[-1] != from_edge:
                    path.append(prev[path[-1]])
                return list(reversed(path))
            for nxt in self.succ.get(eid, ()):
                if nxt in visited:
                    continue
                nd = d + self._weight(nxt, live)
                if nd < dist.get(nxt, float("inf")):
                    dist[nxt] = nd
                    prev[nxt] = eid
                    heapq.heappush(heap, (nd, nxt))
        return None

    # ------------------------------------------------------ nodal analysis

    def nodal_analysis(self, edges, live=True):
        """Node-by-node breakdown of a route: cumulative distance, ETA,
        street name, and whether the node ahead is signalized."""
        rows = []
        cum_d = 0.0
        cum_t = 0.0
        for eid in edges:
            edge = self.net.getEdge(eid)
            node = edge.getToNode()
            cum_d += edge.getLength()
            cum_t += self._weight(eid, live)
            lon, lat = self.net.convertXY2LonLat(*node.getCoord())
            rows.append({
                "node": node.getID(),
                "lat": round(lat, 6), "lon": round(lon, 6),
                "street": edge.getName() or eid,
                "dist_m": round(cum_d, 0),
                "eta_s": round(cum_t, 0),
                "signal": node.getID()
                          if node.getType().startswith("traffic_light") else None,
            })
        return rows

    def route_geometry(self, edges, every=1):
        """Lat/lon polyline of a route for map display."""
        pts = []
        for eid in edges[::every] if every > 1 else edges:
            edge = self.net.getEdge(eid)
            for x, y in edge.getShape():
                lon, lat = self.net.convertXY2LonLat(x, y)
                pts.append([round(lat, 6), round(lon, 6)])
        return pts
