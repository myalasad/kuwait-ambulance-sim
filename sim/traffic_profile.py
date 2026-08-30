"""Kuwait traffic demand calendar and traffic-level presets.

Data provenance — stated plainly because decisions may lean on this:

* The **road network, signal locations and turn restrictions are real**
  (OpenStreetMap).
* There is **no public live traffic feed for Kuwait**.  Background demand is
  therefore *calibrated*, not measured.  Two hourly shapes capture the
  Kuwaiti week as it is actually lived:

  - **Weekday (Sunday–Thursday)**: a sharp 06:30–08:30 work/school peak,
    then a long congested stretch from 13:00 (schools and ministries let
    out) through the evening to about 21:00; streets near-empty 01:00–05:00.
  - **Weekend (Friday–Saturday)**: quiet from 01:00 until noon, then heavy
    from 13:00 right through to midnight (malls, the Gulf Road corniche,
    family traffic).

* On top of the hour-of-day shape, a **traffic level** sets the overall
  intensity: easy, medium (the calibrated baseline) or extreme.  Under
  extreme traffic most signalized approaches are occupied at once, so the
  early-green rule for ordinary drivers rarely applies and junctions run
  their fair fixed timers — exactly as they should (Protocol D4).

* When real counts are available (MOI loop detectors, Municipality
  studies, the licensed xMap Kuwait catalog), drop them in
  ``data/real_counts.csv`` as ``hour,multiplier`` rows (0-23, relative to the
  daily peak = 1.0); they override the weekday shape.
"""
import csv
import os

# Relative demand per clock hour (daily peak = 1.0).
WEEKDAY = {
    0: 0.15, 1: 0.08, 2: 0.06, 3: 0.05, 4: 0.06, 5: 0.25,
    6: 0.65, 7: 1.00, 8: 0.90, 9: 0.65, 10: 0.60, 11: 0.65,
    12: 0.80, 13: 0.95, 14: 0.95, 15: 0.90, 16: 0.90, 17: 0.95,
    18: 1.00, 19: 0.95, 20: 0.90, 21: 0.70, 22: 0.50, 23: 0.30,
}
WEEKEND = {
    0: 0.35, 1: 0.20, 2: 0.12, 3: 0.08, 4: 0.06, 5: 0.06,
    6: 0.08, 7: 0.12, 8: 0.18, 9: 0.25, 10: 0.35, 11: 0.45,
    12: 0.60, 13: 0.85, 14: 0.95, 15: 1.00, 16: 1.00, 17: 1.00,
    18: 1.00, 19: 1.00, 20: 1.00, 21: 0.95, 22: 0.80, 23: 0.60,
}
PROFILES = {"weekday": WEEKDAY, "weekend": WEEKEND}
DAY_LABEL = {"weekday": "Weekday (Sun–Thu)", "weekend": "Weekend (Fri–Sat)"}

# Traffic-level presets: multiplier on the calibrated baseline.
LEVELS = {"easy": 0.45, "medium": 1.0, "extreme": 1.8}
LEVEL_LABEL = {"easy": "Easy", "medium": "Medium", "extreme": "Extreme"}


def hourly_profile(root, day_type="weekday"):
    """The calibrated shape for the day type; data/real_counts.csv (if
    present) overrides the weekday shape with measured multipliers."""
    profile = dict(PROFILES.get(day_type, WEEKDAY))
    path = os.path.join(root, "data", "real_counts.csv")
    if day_type == "weekday" and os.path.exists(path):
        with open(path) as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[0].strip().isdigit():
                    try:
                        profile[int(row[0]) % 24] = max(0.02, float(row[1]))
                    except ValueError:
                        raise ValueError(f"{path}: bad multiplier "
                                         f"{row[1]!r} for hour {row[0]!r}")
        print(f"Demand profile overridden by {path}")
    return profile


def describe(day_type, level, hour, factor=1.0, profile=None):
    """Plain words for the UI: 'Weekday 15:00 — heavy'.

    ``factor`` is the global demand factor actually applied to the
    simulation (``SimConfig.demand_factor``).  It MUST be passed by every
    caller: the multiplier and the word printed on screen have to be the
    demand the model is really running, not the calendar figure before
    scaling.

    ``profile`` is the hourly shape ACTUALLY in use — i.e. the result of
    ``hourly_profile()``, which data/real_counts.csv may have overridden.
    It MUST be passed by every caller for the same reason: with measured
    counts dropped in, the calendar shape below is not what SUMO is
    running.  It defaults to the calendar shape only so that callers with
    no simulation in hand (docs, tests) still work."""
    shape = PROFILES.get(day_type, WEEKDAY) if profile is None else profile
    m = (shape.get(hour % 24, 0.3)
         * LEVELS.get(level, 1.0) * factor)
    word = ("near-empty" if m < 0.15 else "light" if m < 0.45
            else "moderate" if m < 0.8 else "heavy" if m < 1.3 else "saturated")
    return {"multiplier": round(m, 2), "word": word}
