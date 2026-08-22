#!/usr/bin/env python3
"""Download the downtown Kuwait City road network from the Overpass API.

The bounding box covers the signalized core of Kuwait City: roughly the area
inside the First Ring Road up to the Gulf shoreline (Al-Soor St, Fahad
Al-Salem St, Abdullah Al-Ahmad St, Arabian Gulf St).
"""
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, ROOT)
from sim.config import SCENARIOS

import argparse
_ap = argparse.ArgumentParser()
_ap.add_argument("--scenario", default="downtown", choices=sorted(SCENARIOS))
_SC = SCENARIOS[_ap.parse_args().scenario]
QUERY_FILE = os.path.join(ROOT, "scripts", _SC["query"])
OUT_FILE = os.path.join(ROOT, "data", _SC["osm"])
OVERPASS = "https://overpass-api.de/api/interpreter"


def main() -> None:
    with open(QUERY_FILE, "rb") as f:
        query = f.read()
    print(f"Requesting downtown Kuwait City extract from {OVERPASS} ...")
    req = urllib.request.Request(OVERPASS, data=query, method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = resp.read()
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "wb") as f:
        f.write(data)
    print(f"Saved {len(data) / 1e6:.1f} MB to {OUT_FILE}")


if __name__ == "__main__":
    main()
