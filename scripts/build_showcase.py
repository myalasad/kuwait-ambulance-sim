#!/usr/bin/env python3
"""Build the Showcase scenario: three fixed-density districts on the
downtown network.

Filters the already-routed downtown demand (background_base.rou.xml) by
the district each vehicle's route STARTS in (nearest district anchor):
dense keeps everything, normal keeps ~45%, light keeps ~10% — the exact
probabilities live in SCENARIOS["showcase"]["districts"].  Selection is a
deterministic hash of the vehicle id, so the build is reproducible.
Writes background_showcase.rou.xml and scenario_showcase.sumocfg.
"""
import hashlib
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import sumolib  # noqa: E402

from sim.config import SCENARIOS  # noqa: E402
from _templates import SUMOCFG_XML  # noqa: E402

DATA = os.path.join(ROOT, "data")
SC = SCENARIOS["showcase"]


def main():
    net = sumolib.net.readNet(os.path.join(DATA, SC["net"]))
    anchors = []
    for d in SC["districts"]:
        x, y = net.convertLonLat2XY(d["lon"], d["lat"])
        anchors.append((x, y, d["keep"], d["kind"]))

    def keep_prob(edge_id):
        try:
            x, y = net.getEdge(edge_id).getFromNode().getCoord()
        except KeyError:
            return 0.3, "?"
        best = min(anchors, key=lambda a: math.hypot(a[0] - x, a[1] - y))
        return best[2], best[3]

    src = os.path.join(DATA, "background_base.rou.xml")
    dst = os.path.join(DATA, SC["routes"])
    kept = {"dense": 0, "normal": 0, "light": 0, "?": 0}
    dropped = 0
    with open(src) as f, open(dst, "w") as out:
        vehicle = []
        vid = first_edge = None

        def flush():
            nonlocal dropped
            p, kind = keep_prob(first_edge)
            h = int(hashlib.md5(vid.encode()).hexdigest()[:8], 16)
            if (h % 10000) < p * 10000:
                out.writelines(vehicle)
                kept[kind] += 1
            else:
                dropped += 1

        for line in f:
            if vehicle:
                vehicle.append(line)
                m = re.search(r'edges="([^" ]+)', line)
                if m:
                    first_edge = m.group(1)
                if "</vehicle>" in line:
                    flush()
                    vehicle = []
            elif "<vehicle " in line:
                vid = re.search(r'id="([^"]+)"', line).group(1)
                m = re.search(r'edges="([^" ]+)', line)
                first_edge = m.group(1) if m else None
                vehicle = [line]
                if "</vehicle>" in line:
                    flush()
                    vehicle = []
            else:
                out.write(line)
    with open(os.path.join(DATA, SC["sumocfg"]), "w") as f:
        f.write(SUMOCFG_XML.format(net=SC["net"],
                                   routes=os.path.basename(dst)))
    total = sum(kept.values())
    print(f"showcase demand: kept {total} vehicles "
          f"(dense {kept['dense']}, normal {kept['normal']}, "
          f"light {kept['light']}), dropped {dropped}")
    print(f"wrote {dst} and {SC['sumocfg']}")


if __name__ == "__main__":
    main()
