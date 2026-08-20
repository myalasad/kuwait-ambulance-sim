"""Kuwait traffic demand calibration.

Data provenance — stated plainly because decisions may lean on this:

* The **road network, signal locations and turn restrictions are real**
  (OpenStreetMap, downtown Kuwait City).
* There is **no public live traffic feed for Kuwait** (no open MOI/
  Municipality API).  Background demand is therefore *calibrated*, not
  measured: the hourly shape below reflects the published pattern of
  Kuwaiti weekday traffic — a sharp 06:30–08:30 work/school peak, a
  13:00–15:00 afternoon peak when schools and ministries let out, and a
  long evening peak 17:00–21:00 — scaled so the downtown cutout carries
  realistic volumes without gridlocking the simulation.
* When real counts are available (MOI loop detectors, Municipality
  studies), drop them in ``data/real_counts.csv`` as ``hour,multiplier``
  rows (0-23, relative to the daily peak = 1.0) and rebuild; the file
  overrides the calibrated shape below.
"""
import csv
import os

# Relative demand per starting hour, typical Kuwaiti working day (Sun-Thu).
HOURLY = {
    0: 0.15, 1: 0.10, 2: 0.08, 3: 0.08, 4: 0.12, 5: 0.30,
    6: 0.65, 7: 1.00, 8: 0.90, 9: 0.70, 10: 0.60, 11: 0.65,
    12: 0.80, 13: 0.95, 14: 0.85, 15: 0.75, 16: 0.80, 17: 0.95,
    18: 1.00, 19: 0.95, 20: 0.85, 21: 0.70, 22: 0.50, 23: 0.30,
}

# randomTrips insertion period (seconds/vehicle) at the daily peak for this
# downtown cutout: ~2770 veh/h inserted network-wide, which reproduces the
# queue lengths the corridor work is calibrated against.
PEAK_PERIOD_S = 1.3


def hourly_profile(root):
    """The calibrated profile, overridden by data/real_counts.csv if given."""
    profile = dict(HOURLY)
    path = os.path.join(root, "data", "real_counts.csv")
    if os.path.exists(path):
        with open(path) as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[0].strip().isdigit():
                    profile[int(row[0]) % 24] = max(0.02, float(row[1]))
        print(f"Demand profile overridden by {path}")
    return profile


def period_for_hour(profile, hour):
    """randomTrips --period for the given clock hour."""
    return PEAK_PERIOD_S / max(profile.get(hour % 24, 0.3), 0.02)
