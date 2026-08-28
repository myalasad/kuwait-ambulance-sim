#!/usr/bin/env python3
"""Start the live dashboard: SUMO simulation + web UI on Kuwait City's map.

  python run_live.py            # http://127.0.0.1:8642
  python run_live.py --port 9000
"""
import argparse
import atexit
import os
import signal
import sys

import uvicorn

ROOT = os.path.dirname(os.path.abspath(__file__))


def _shutdown_sim(*_args):
    """Close TraCI so the child SUMO process dies with the server — an
    orphaned SUMO otherwise blocks the next startup with
    'Could not connect to TraCI server ... Connection refused'."""
    try:
        from web.server import hub
        if hub.sim is not None:
            hub.sim.close()
    except Exception:
        pass
    if _args:                       # called as a signal handler
        raise SystemExit(0)


def main():
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    atexit.register(_shutdown_sim)
    signal.signal(signal.SIGTERM, _shutdown_sim)
    signal.signal(signal.SIGINT, _shutdown_sim)
    print(f"\n  Kuwait Ambulance Green-Wave Simulation")
    print(f"  Open http://{args.host}:{args.port} in your browser\n")
    uvicorn.run("web.server:app", host=args.host, port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()
