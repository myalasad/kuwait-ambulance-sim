#!/usr/bin/env python3
"""Start the live dashboard: SUMO simulation + web UI on Kuwait City's map.

  python run_live.py            # http://127.0.0.1:8642
  python run_live.py --port 9000
"""
import argparse
import os
import sys

import uvicorn

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"\n  Kuwait Ambulance Green-Wave Simulation")
    print(f"  Open http://{args.host}:{args.port} in your browser\n")
    uvicorn.run("web.server:app", host=args.host, port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()
