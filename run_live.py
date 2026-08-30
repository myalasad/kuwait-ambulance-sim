#!/usr/bin/env python3
"""Start the live dashboard: SUMO simulation + web UI on Kuwait City's map.

  python run_live.py                 # http://127.0.0.1:8642
  python run_live.py --port 9000
  python run_live.py --no-reload     # never restart itself (pitch mode)

The server WATCHES ITS OWN SOURCE.  Editing any file it runs restarts it
automatically, so what the browser shows and what the simulation runs can
never drift apart — the pages are read from disk on every request, but a
python process keeps whatever it imported at boot, and that gap silently
cost real debugging time before this existed.  A restart costs about three
seconds because the city state is cached; the page reconnects on its own.
"""
import argparse
import atexit
import os
import signal
import sys
import threading
import time

import uvicorn

ROOT = os.path.dirname(os.path.abspath(__file__))

# every tree whose code the running server actually executes
WATCH_DIRS = ("sim", "web", "rag")
WATCH_FILES = ("run_live.py",)
SETTLE_S = 1.5          # wait for a burst of edits to finish before acting
POLL_S = 1.0


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


def _source_fingerprint():
    """Newest modification time across the code this process runs."""
    newest, newest_f = 0.0, ""
    for name in WATCH_FILES:
        try:
            m = os.path.getmtime(os.path.join(ROOT, name))
        except OSError:
            continue
        if m > newest:
            newest, newest_f = m, name
    for sub in WATCH_DIRS:
        for dirpath, dirs, files in os.walk(os.path.join(ROOT, sub)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(dirpath, f)
                try:
                    m = os.path.getmtime(path)
                except OSError:
                    continue
                if m > newest:
                    newest, newest_f = m, os.path.relpath(path, ROOT)
    return newest, newest_f


def _restart(reason):
    """Replace this process with a fresh one running the current code.

    os.execv does NOT run atexit handlers, so TraCI is closed explicitly
    first — otherwise the orphaned SUMO child holds its port and the new
    process cannot connect."""
    print(f"\n  Source changed ({reason}) — restarting to run it.\n",
          flush=True)
    _shutdown_sim()
    time.sleep(0.4)                 # let SUMO actually exit before rebinding
    os.execv(sys.executable, [sys.executable] + sys.argv)


def _watch_sources():
    baseline, _ = _source_fingerprint()
    while True:
        time.sleep(POLL_S)
        newest, newest_f = _source_fingerprint()
        if newest <= baseline:
            continue
        # an edit landed: wait for the file set to stop moving, so a burst
        # of saves restarts once instead of once per file
        while True:
            time.sleep(SETTLE_S)
            again, again_f = _source_fingerprint()
            if again <= newest:
                break
            newest, newest_f = again, again_f
        _restart(newest_f)


def main():
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8642)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-reload", action="store_true",
                    help="do not restart when the source changes")
    args = ap.parse_args()
    atexit.register(_shutdown_sim)
    signal.signal(signal.SIGTERM, _shutdown_sim)
    signal.signal(signal.SIGINT, _shutdown_sim)
    print("\n  Kuwait Ambulance Green-Wave Simulation")
    print(f"  Open http://{args.host}:{args.port} in your browser")
    if args.no_reload:
        print("  Source watching OFF — restart by hand after any edit\n")
    else:
        print("  Watching its own source: an edit restarts it "
              "automatically (~3 s)\n")
        threading.Thread(target=_watch_sources, daemon=True).start()
    uvicorn.run("web.server:app", host=args.host, port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()
