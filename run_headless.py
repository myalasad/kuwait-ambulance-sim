#!/usr/bin/env python3
"""Run the Kuwait downtown scenario without a browser.

Examples (from the project root, venv active):

  python run_headless.py --minutes 10                 # one run, preemption on
  python run_headless.py --minutes 10 --no-preemption # baseline
  python run_headless.py --compare                    # both runs, same seed,
                                                      # side-by-side table
  python run_headless.py --compare --replay replay.html
                                                      # + self-contained replay
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from sim.config import SimConfig  # noqa: E402
from sim.runner import Simulation  # noqa: E402

# Dispatch schedule: (sim-time s, origin, destination lat/lon).  Three runs
# from Amiri Hospital across downtown (2.1 / 3.4 / 2.7 km routed);
# identical in every compared run.
SCENARIO = [
    (60.0, "Amiri Hospital", (29.3700, 47.9930)),   # central, Mubarak Al-Kabeer St side
    (240.0, "Amiri Hospital", (29.3665, 47.9765)),  # western grid
    (420.0, "Amiri Hospital", (29.3735, 48.0035)),  # east, Sharq side
]


def run(minutes, seed, preemption, record_frames=False, frame_every_s=1.0):
    cfg = SimConfig()
    sim = Simulation(ROOT, cfg, preemption=preemption, seed=seed)
    sim.start()
    total_steps = int(minutes * 60 / cfg.step_length)
    frame_every = max(1, int(round(frame_every_s / cfg.step_length)))
    pending = list(SCENARIO)
    frames = []
    network = sim.network_payload() if record_frames else None
    try:
        for step_no in range(total_steps):
            sim.step()
            while pending and sim.time >= pending[0][0]:
                _, origin, dest = pending.pop(0)
                try:
                    sim.dispatch(origin, dest)
                except ValueError as exc:
                    print(f"  dispatch failed: {exc}")
            if record_frames and step_no % frame_every == 0:
                frames.append(sim.snapshot())
            elif not record_frames:
                for ev in sim.events:
                    print(f"  [{ev['t']:7.1f}s] {ev['msg']}")
                sim.events = []
        completed = list(sim.metrics.completed)
    finally:
        sim.close()
    if record_frames:
        for fr in frames:
            for ev in fr["events"]:
                print(f"  [{ev['t']:7.1f}s] {ev['msg']}")
    return completed, frames, network


def print_comparison(with_p, without_p):
    print("\n=== Ambulance travel times (same seed, same dispatches) ===")
    print(f"{'run':<8}{'with preemption':>18}{'without':>12}{'saved':>10}{'speed-up':>10}")
    amap = {r["id"]: r for r in with_p}
    bmap = {r["id"]: r for r in without_p}
    for name in sorted(set(amap) | set(bmap),
                       key=lambda n: int(n.rsplit("_", 1)[-1])):
        a, b = amap.get(name), bmap.get(name)
        ta = f"{a['duration_s']:.0f} s" if a else "DNF"
        tb = f"{b['duration_s']:.0f} s" if b else "DNF"
        if a and b:
            saved = f"{b['duration_s'] - a['duration_s']:.0f} s"
            spd = f"{(b['duration_s'] / a['duration_s'] - 1) * 100:+.0f}%"
        else:
            saved = spd = "-"
        print(f"{name:<8}{ta:>18}{tb:>12}{saved:>10}{spd:>10}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=15.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-preemption", action="store_true")
    ap.add_argument("--compare", action="store_true",
                    help="run twice (preemption on/off) and compare")
    ap.add_argument("--replay", metavar="OUT.html",
                    help="export a self-contained replay page of the "
                         "preemption run")
    args = ap.parse_args()

    if args.replay:
        # fail fast: a bad output path must not cost two full simulation runs
        out = os.path.abspath(args.replay)
        try:
            with open(out, "w"):
                pass
        except OSError as exc:
            ap.error(f"cannot write replay file {out}: {exc}")

    record = bool(args.replay)
    if args.compare:
        print("--- Run 1/2: preemption ON ---")
        with_p, frames, network = run(args.minutes, args.seed, True,
                                      record_frames=record)
        print("--- Run 2/2: preemption OFF (baseline) ---")
        without_p, _, _ = run(args.minutes, args.seed, False)
        print_comparison(with_p, without_p)
    else:
        completed, frames, network = run(
            args.minutes, args.seed, not args.no_preemption,
            record_frames=record)
        print("\n=== Completed ambulance runs ===")
        for rec in completed:
            print(f"  {rec['id']}: {rec['duration_s']:.0f} s, "
                  f"{rec['length_m'] / 1000:.1f} km, avg {rec['kmh']:.0f} km/h")

    if args.replay:
        from web.replay_export import export_replay
        out = os.path.abspath(args.replay)
        export_replay(frames, network, out)
        print(f"\nReplay written to {out}")


if __name__ == "__main__":
    main()
