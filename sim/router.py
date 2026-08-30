"""Route optimisation: Dijkstra over the road network's edge graph.

The graph is edge-based (nodes of the search are road edges, arcs are the
legal edge-to-edge connections), so one-way streets and turn restrictions
from OpenStreetMap are respected exactly as netconvert encoded them.

Edge weights are travel times.  With a live TraCI connection the current
estimated travel time of each edge is used (SUMO derives it from the mean
speed actually being driven), so the route adapts to congestion; without one
it falls back to free-flow times (length / speed limit).

With a Markov predictor attached the weights are time-dependent: an edge is
priced by its CTMC-predicted EXPECTED TRAVEL TIME at the horizon the search
would reach it.  Time-dependent weights only make Dijkstra correct if they
satisfy FIFO (leaving later can never mean arriving earlier), so the
predicted family is explicitly closed under that property — see _weight.

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
        # junction node id -> actual TLS id (joined signals get prefixed ids
        # like "GS_<node>"); filled in by the runner once TraCI is up
        self.tls_map = {}
        # optional TrafficMarkov instance for anticipatory weights
        self.predictor = None
        self.places = None   # real-name registry (set by the runner)
        # memoised anticipatory weights, keyed (edge, state, horizon
        # bucket) exactly as the predictor's own forecast cache is, and
        # dropped only when the predictor says its forecasts changed
        # (see _sync_predictor)
        self._wcache = {}    # (edge, state, bucket) -> weight or None
        self._fcache = {}    # (edge, state, bucket) -> FIFO arrival floor
        self._pred_gen = None
        self._lengths = {e.getID(): e.getLength() for e in net.getEdges()
                         if e.allows(vclass)}
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
        # reversed adjacency: lets one backward Dijkstra from a scene rank
        # every hospital's travel time in a single pass
        self.pred = {eid: [] for eid in self.succ}
        for eid, outs in self.succ.items():
            for nxt in outs:
                self.pred[nxt].append(eid)

    # ------------------------------------------------------------- weights

    # Anticipatory weights are quantised into horizon buckets.  This MUST
    # match TrafficMarkov.forecast()'s quantisation: the FIFO closure below
    # reasons about the steps between buckets, so a finer grid here would
    # smooth over jumps the predictor still reports.
    _BUCKET = 15.0

    def _raw_weight(self, eid, live):
        """Non-anticipatory weight of `eid`: SUMO's live travel-time
        estimate, else free flow.  Independent of the horizon, so it is
        trivially FIFO-consistent."""
        if live:
            try:
                t = traci.edge.getTraveltime(eid)
                # SUMO reports absurd values for empty/blocked edges; clamp
                # to no better than free flow and no worse than 20 minutes
                return min(max(t, self.static_cost[eid]), 1200.0)
            except traci.TraCIException:
                pass
        return self.static_cost[eid]

    def _sync_predictor(self):
        """Drop the memoised anticipatory weights when — and only when —
        the predictor invalidated its own forecasts (once per sampling
        rotation).  Clearing them at the top of every route() call instead
        measured 141 ms per route against 30 ms."""
        gen = (id(self.predictor), getattr(self.predictor, "generation", 0))
        if gen != self._pred_gen:
            self._pred_gen = gen
            self._wcache.clear()
            self._fcache.clear()

    def _bucket_weight(self, eid, state, k):
        """Anticipatory weight of entering `eid` in horizon bucket `k`, or
        None when the predictor has no usable forecast for that edge."""
        key = (eid, state, k)
        if key in self._wcache:
            return self._wcache[key]
        t = self.predictor.predicted_traveltime(
            eid, k * self._BUCKET, self._lengths.get(eid, 0.0))
        w = (None if t is None
             else min(max(t, self.static_cost[eid]), 1200.0))
        self._wcache[key] = w
        return w

    def _fifo_floor(self, eid, state, k):
        """Earliest arrival at the far end of `eid` already reachable by
        entering it at an EARLIER horizon: max over j < k of the top of
        bucket j plus that bucket's weight.  A prefix maximum, memoised
        and filled forward, so it costs O(1) amortised per lookup."""
        if k <= 0:
            return 0.0
        v = self._fcache.get((eid, state, k))
        if v is not None:
            return v
        j = k                       # walk back to the deepest cached prefix
        while j > 0 and (eid, state, j) not in self._fcache:
            j -= 1
        v = 0.0 if j <= 0 else self._fcache[(eid, state, j)]
        for m in range(j + 1, k + 1):
            w = self._bucket_weight(eid, state, m - 1)
            if w is not None:
                v = max(v, m * self._BUCKET + w)
            self._fcache[(eid, state, m)] = v
        return v

    def _weight(self, eid, live, horizon=0.0, predictive=True):
        """Travel-time weight of entering `eid` `horizon` seconds from now.

        With a Markov predictor attached and enough history, the weight is
        the CTMC-predicted EXPECTED TRAVEL TIME at the arrival horizon —
        E[L/v], not L/E[v], which Jensen makes optimistic by up to 2.7x on
        the bimodal free/jammed forecasts that matter most.  Anticipatory
        routing: the route avoids where congestion WILL be, not just where
        it is.  Falls back to live TraCI travel time, then to free flow.

        The predicted family is closed under FIFO — the property Dijkstra
        needs for "the first pop is final" to hold: h -> h + w(h) is forced
        non-decreasing, so entering the edge later can never be reported as
        arriving earlier.  A jam may still be predicted to clear at up to
        1 s of weight per 1 s of horizon, so anticipation keeps working;
        what is clipped is only the physically impossible claim that
        dawdling 20 s gets the ambulance through 40 s sooner."""
        if not predictive or self.predictor is None:
            return self._raw_weight(eid, live)
        self._sync_predictor()
        # same key the predictor's own forecast cache uses
        state = getattr(self.predictor, "state_now", {}).get(eid, 0)
        k = int(max(0.0, horizon) // self._BUCKET)
        w = self._bucket_weight(eid, state, k)
        if w is None:                       # not enough history for this edge
            return self._raw_weight(eid, live)
        return max(w, self._fifo_floor(eid, state, k) - horizon)

    # ------------------------------------------------------------ dijkstra

    def route(self, from_edge, to_edge, live=True, predictive=True):
        """Shortest-time edge sequence from from_edge to to_edge, or None.
        predictive=False ignores the Markov predictor (live weights only) —
        used to measure whether the prediction changed the decision."""
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
                # time-dependent: the weight of the next edge is evaluated
                # at the horizon we would reach it (cumulative time so far)
                nd = d + self._weight(nxt, live, horizon=d,
                                      predictive=predictive)
                if nd < dist.get(nxt, float("inf")):
                    dist[nxt] = nd
                    prev[nxt] = eid
                    heapq.heappush(heap, (nd, nxt))
        return None

    def route_to_many(self, from_edge, to_edges, live=True, predictive=True):
        """Shortest-time routes from one edge to EVERY reachable edge in
        `to_edges`, in one Dijkstra — the cost of a single route() call
        instead of one search per destination.  Returns
        {to_edge: (edge_list, weighted_time)}."""
        remaining = {t for t in to_edges if t in self.succ}
        found = {}
        if from_edge in remaining:
            found[from_edge] = ([from_edge], 0.0)
            remaining.discard(from_edge)
        if from_edge not in self.succ or not remaining:
            return found
        dist = {from_edge: 0.0}
        prev = {}
        heap = [(0.0, from_edge)]
        visited = set()
        while heap and remaining:
            d, eid = heapq.heappop(heap)
            if eid in visited:
                continue
            visited.add(eid)
            if eid in remaining:
                path = [eid]
                while path[-1] != from_edge:
                    path.append(prev[path[-1]])
                found[eid] = (list(reversed(path)), d)
                remaining.discard(eid)
                if not remaining:
                    break
            for nxt in self.succ.get(eid, ()):
                if nxt in visited:
                    continue
                nd = d + self._weight(nxt, live, horizon=d,
                                      predictive=predictive)
                if nd < dist.get(nxt, float("inf")):
                    dist[nxt] = nd
                    prev[nxt] = eid
                    heapq.heappush(heap, (nd, nxt))
        return found

    def cost_from_many(self, from_edges, to_edge, live=True):
        """Travel-time estimate from EACH edge in `from_edges` to
        `to_edge`, in one backward Dijkstra over the reversed graph.
        Time-dependent prediction is undefined expanding backwards (the
        arrival time at each edge is unknown until the search completes),
        so weights are live/static — a RANKING estimate; compute the final
        route forward with full predictive weights.  Returns
        {from_edge: cost}; unreachable sources are absent."""
        remaining = {s for s in from_edges if s in self.succ}
        found = {}
        if to_edge in remaining:
            found[to_edge] = 0.0
            remaining.discard(to_edge)
        if to_edge not in self.succ or not remaining:
            return found
        dist = {to_edge: 0.0}
        heap = [(0.0, to_edge)]
        visited = set()
        while heap and remaining:
            d, eid = heapq.heappop(heap)
            if eid in visited:
                continue
            visited.add(eid)
            if eid in remaining:
                found[eid] = d
                remaining.discard(eid)
                if not remaining:
                    break
            # stepping back to a predecessor p costs the weight of ENTERING
            # this edge (route costs count every edge after the origin)
            step = self._weight(eid, live, horizon=0.0, predictive=False)
            for p in self.pred.get(eid, ()):
                if p in visited:
                    continue
                nd = d + step
                if nd < dist.get(p, float("inf")):
                    dist[p] = nd
                    heapq.heappush(heap, (nd, p))
        return found

    # ------------------------------------------------------ nodal analysis

    def route_time(self, edges, live=True, predictive=True):
        """Total weighted travel time of an edge sequence."""
        t = 0.0
        for eid in edges:
            t += self._weight(eid, live, horizon=t, predictive=predictive)
        return t

    def nodal_analysis(self, edges, live=True):
        """Node-by-node breakdown of a route: cumulative distance, ETA,
        street name, and whether the node ahead is signalized.

        Each row also carries ``in_edge``, the edge id the route uses to
        REACH that node.  Consecutive rows therefore give the (in, out)
        pair of the movement through the node, which is what lets the
        signal-wait model charge the ambulance's own movement rather than
        the junction as a whole ("street" is a display name and cannot be
        reversed into an edge id)."""
        rows = []
        cum_d = 0.0
        cum_t = 0.0
        for eid in edges:
            edge = self.net.getEdge(eid)
            node = edge.getToNode()
            cum_d += edge.getLength()
            cum_t += self._weight(eid, live, horizon=cum_t)
            lon, lat = self.net.convertXY2LonLat(*node.getCoord())
            sig = self.tls_map.get(node.getID(), node.getID())
            rows.append({
                "node": node.getID(),
                "in_edge": eid,
                "junction": (self.places.jn(sig) if self.places
                             and node.getType().startswith("traffic_light")
                             else ""),
                "lat": round(lat, 6), "lon": round(lon, 6),
                "street": (self.places.road(eid) if self.places
                           else (edge.getName() or eid)),
                "dist_m": round(cum_d, 0),
                "eta_s": round(cum_t, 0),
                "signal": self.tls_map.get(node.getID(), node.getID())
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
