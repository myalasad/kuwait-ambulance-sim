"""Export a recorded simulation run into a single self-contained replay page.

The page embeds the road geometry and every frame, draws the network itself
(no map-tile server needed), and works offline, double-clicked from disk, or
hosted anywhere.
"""
import json
import os

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "static", "replay_template.html")


def _compact(frames):
    """Shrink snapshots: integer car ids, positional arrays."""
    id_map = {}
    out = []
    for f in frames:
        cars = []
        for vid, lon, lat, _angle in f["cars"]:
            idx = id_map.setdefault(vid, len(id_map))
            cars.append([idx, lon, lat])
        out.append({
            "t": f["t"],
            "c": cars,
            "a": [[a["id"], a["lon"], a["lat"], a["kmh"]] for a in f["ambs"]],
            "l": {k: [v["s"], v["m"]] for k, v in f["tls"].items()},
            "e": [[e["t"], e["msg"]] for e in f["events"]],
            "k": [f["kpi"]["vehicles"], f["kpi"]["ambulances"],
                  f["kpi"]["preempted_tls"]],
            "r": f["kpi"]["runs"],
        })
    return out


def export_replay(frames, network, out_path):
    if not frames:
        raise ValueError("no frames recorded — nothing to export")
    data = {"network": network, "frames": _compact(frames)}
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    payload = json.dumps(data, separators=(",", ":"))
    html = html.replace("/*__REPLAY_DATA__*/null", payload)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    size_mb = os.path.getsize(out_path) / 1e6
    print(f"Replay page: {out_path} ({size_mb:.1f} MB, "
          f"{len(frames)} frames)")
    return out_path
