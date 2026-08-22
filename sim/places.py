"""Real-name registry: every junction, road and corridor gets a human label.

Raw SUMO/OSM identifiers (``cluster_1233282612_2113803599_...``,
``1138500808#2``) never reach a screen or an operations message.  Instead:

* **Roads** are labelled with their real OpenStreetMap name — ``name:en``
  when present, otherwise the Arabic ``name`` — with a road-class fallback
  ("Expressway link near Mirqab") for unnamed connectors.
* **Junctions** get a short stable code (``J-014``) plus a name built from
  the real streets that meet there ("Fahad Al-Salem St × Al-Soor St"), and
  a category (Motorway interchange / Arterial junction / Street junction)
  and the nearest named area.

The registry is built once per scenario from the network + OSM extract and
cached to ``data/places_<scenario>.json``.
"""
import json
import math
import os
import re
import xml.etree.ElementTree as ET

CLASS_LABEL = {
    "motorway": "Motorway", "motorway_link": "Motorway link",
    "trunk": "Expressway", "trunk_link": "Expressway link",
    "primary": "Main road", "primary_link": "Main road link",
    "secondary": "Road", "secondary_link": "Road link",
    "tertiary": "Street", "unclassified": "Street",
    "residential": "Street", "living_street": "Lane", "service": "Access road",
}
CLASS_RANK = {"motorway": 0, "trunk": 1, "primary": 2, "secondary": 3,
              "tertiary": 4, "unclassified": 5, "residential": 5,
              "living_street": 6, "service": 7}


def _way_id(edge_id):
    m = re.match(r"^-?(\d+)", edge_id)
    return m.group(1) if m else None


class Places:
    def __init__(self, net, cfg, root):
        self.net = net
        self.cfg = cfg
        self.root = root
        self.path = os.path.join(root, "data", f"places_{cfg.scenario}.json")
        self.areas = cfg.areas_d()
        self.edges = {}      # edge id -> label
        self.edge_class = {}  # edge id -> class key
        self.tls = {}        # tls id -> {code, name, category, area, streets}
        self.code_to_tls = {}
        if not self._load():
            self._build_edges()
        # junctions need TraCI link data; filled by attach_tls()

    # ---------------------------------------------------------------- cache

    def _load(self):
        if not os.path.exists(self.path):
            return False
        try:
            with open(self.path) as f:
                d = json.load(f)
            self.edges = d["edges"]
            self.edge_class = d["edge_class"]
            self.tls = d.get("tls", {})
            self.code_to_tls = {v["code"]: k for k, v in self.tls.items()}
            return bool(self.edges)
        except (OSError, ValueError, KeyError):
            return False

    def save(self):
        try:
            with open(self.path, "w") as f:
                json.dump({"edges": self.edges, "edge_class": self.edge_class,
                           "tls": self.tls}, f, ensure_ascii=False)
        except OSError:
            pass

    # ---------------------------------------------------------------- roads

    def _osm_names(self):
        """way id -> preferred display name from the OSM extract."""
        from .config import SCENARIOS
        osm = os.path.join(self.root, "data",
                           SCENARIOS[self.cfg.scenario]["osm"])
        names = {}
        if not os.path.exists(osm):
            return names
        for _, el in ET.iterparse(osm, events=("end",)):
            if el.tag != "way":
                continue
            tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
            nm = tags.get("name:en") or tags.get("name") or tags.get("ref")
            if nm:
                names[el.get("id")] = nm.strip()
            el.clear()
        return names

    def _build_edges(self):
        osm_names = self._osm_names()
        for e in self.net.getEdges():
            eid = e.getID()
            etype = (e.getType() or "").replace("highway.", "")
            self.edge_class[eid] = etype
            name = e.getName() or ""
            way = _way_id(eid)
            if way and way in osm_names:
                name = osm_names[way]
            if name:
                self.edges[eid] = name
            else:
                cls = CLASS_LABEL.get(etype, "Road")
                x, y = e.getShape()[0]
                lon, lat = self.net.convertXY2LonLat(x, y)
                self.edges[eid] = f"{cls} near {self.area_of(lat, lon)}"

    def road(self, edge_id):
        if edge_id.startswith(":"):
            return "junction box"
        return self.edges.get(edge_id) or "unnamed road"

    def corridor(self, edge_id):
        """Self-explanatory corridor descriptor for analytics tables:
        road name, nearest area, and compass direction of travel."""
        try:
            e = self.net.getEdge(edge_id)
        except Exception:
            return {"road": self.road(edge_id), "area": "", "dir": ""}
        shape = e.getShape()
        (x0, y0), (x1, y1) = shape[0], shape[-1]
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        lon, lat = self.net.convertXY2LonLat(xm, ym)
        bearing = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360
        compass = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][
            int((bearing + 22.5) // 45) % 8]
        dirs = {"N": "northbound", "NE": "north-east", "E": "eastbound",
                "SE": "south-east", "S": "southbound", "SW": "south-west",
                "W": "westbound", "NW": "north-west"}
        return {"road": self.road(edge_id), "area": self.area_of(lat, lon),
                "dir": dirs[compass], "class": self.road_class(edge_id)}

    def road_class(self, edge_id):
        return CLASS_LABEL.get(self.edge_class.get(edge_id, ""), "Road")

    # ---------------------------------------------------------------- areas

    def area_of(self, lat, lon):
        best, best_d = "downtown", 1e18
        for name, (alat, alon) in self.areas.items():
            d = (alat - lat) ** 2 + ((alon - lon) * 0.87) ** 2
            if d < best_d:
                best, best_d = name, d
        return re.sub(r"\s*\([^)]*\)\s*$", "", best)   # strip governorate

    # ------------------------------------------------------------ junctions

    def attach_tls(self, tls_links, tls_pos):
        """tls_links: tls id -> list of incoming edge ids;
        tls_pos: tls id -> (lat, lon).  Builds codes + names once."""
        if self.tls:
            return
        order = sorted(tls_links)
        for n, tls_id in enumerate(order, 1):
            in_edges = tls_links[tls_id]
            # rank streets by road importance, dedupe by name
            ranked = sorted(in_edges, key=lambda e: CLASS_RANK.get(
                self.edge_class.get(e, ""), 9))
            streets = []
            for e in ranked:
                nm = self.edges.get(e)
                if nm and not nm.startswith(tuple(CLASS_LABEL.values())) \
                        and nm not in streets:
                    streets.append(nm)
            lat, lon = tls_pos.get(tls_id, (0.0, 0.0))
            area = self.area_of(lat, lon)
            if len(streets) >= 2:
                name = f"{streets[0]} × {streets[1]}"
            elif streets:
                name = f"{streets[0]} junction"
            else:
                name = f"{area} junction"
            top = min((CLASS_RANK.get(self.edge_class.get(e, ""), 9)
                       for e in in_edges), default=9)
            category = ("Motorway interchange" if top <= 1
                        else "Arterial junction" if top <= 3
                        else "Street junction")
            code = f"J-{n:03d}"
            self.tls[tls_id] = {"code": code, "name": name,
                                "category": category, "area": area,
                                "streets": streets[:4]}
            self.code_to_tls[code] = tls_id
        self.save()

    def jn(self, tls_id):
        """Short professional label: 'J-014 · Fahad Al-Salem St × Al-Soor St'."""
        d = self.tls.get(tls_id)
        if not d:
            return "junction"
        return f"{d['code']} · {d['name']}"

    def code(self, tls_id):
        d = self.tls.get(tls_id)
        return d["code"] if d else tls_id

    def describe(self, tls_id):
        return self.tls.get(tls_id, {"code": tls_id, "name": "junction",
                                     "category": "", "area": "",
                                     "streets": []})

    def resolve_code(self, code):
        return self.code_to_tls.get(code.upper())
