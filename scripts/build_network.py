#!/usr/bin/env python3
"""Build the SUMO scenario for downtown Kuwait City.

Steps:
  1. netconvert  : OSM extract -> SUMO network (signalized junctions kept,
                   simple ones joined, static signal programs so the
                   preemption controller can override and restore them)
  2. randomTrips : two hours of background passenger traffic
  3. write vtypes.add.xml (ambulance with blue-light device) and scenario.sumocfg
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sim.sumo_env import ensure_sumo_home, tool_binary, tools_dir  # noqa: E402
from sim.traffic_profile import hourly_profile, period_for_hour  # noqa: E402
from sim.config import SimConfig  # noqa: E402

from sim.config import SCENARIOS  # noqa: E402

import argparse
_ap = argparse.ArgumentParser()
_ap.add_argument("--scenario", default="downtown", choices=sorted(SCENARIOS))
_SC = SCENARIOS[_ap.parse_args().scenario]

DATA = os.path.join(ROOT, "data")
OSM = os.path.join(DATA, _SC["osm"])
NET = os.path.join(DATA, _SC["net"])
VTYPES = os.path.join(DATA, "vtypes.add.xml")
SUMOCFG = os.path.join(DATA, _SC["sumocfg"])

VTYPES_XML = """<additional>
    <!-- Kuwait MoH ambulance: emergency class + blue-light device.  Other
         vehicles react to the device (yielding / rescue-lane behaviour when
         the sublane model is enabled). -->
    <vType id="ambulance" vClass="emergency" guiShape="emergency"
           length="6.5" width="2.2" accel="3.0" decel="5.0" emergencyDecel="9.0"
           maxSpeed="33.3" speedFactor="1.35" minGap="1.5" tau="0.8"
           lcStrategic="1.0" lcSpeedGain="2.0" lcAssertive="1.5" color="1,0,0">
        <param key="has.bluelight.device" value="true"/>
    </vType>
</additional>
"""

SUMOCFG_XML = """<configuration>
    <input>
        <net-file value="{net}"/>
        <route-files value="{routes}"/>
        <additional-files value="vtypes.add.xml"/>
    </input>
    <time>
        <step-length value="0.5"/>
    </time>
    <processing>
        <ignore-route-errors value="true"/>
        <time-to-teleport value="180"/>
    </processing>
    <report>
        <no-step-log value="true"/>
        <verbose value="false"/>
    </report>
</configuration>
"""


def run(cmd: list) -> None:
    print("+", " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
    subprocess.run(cmd, check=True)


def main() -> None:
    ensure_sumo_home()
    if not os.path.exists(OSM):
        sys.exit(f"OSM extract missing: {OSM}\nRun scripts/download_map.py first.")

    run([
        tool_binary("netconvert"),
        "--osm-files", OSM,
        "-o", NET,
        "--geometry.remove",
        "--ramps.guess",
        "--junctions.join",
        "--junctions.join-dist", "30",
        "--tls.guess-signals",
        "--tls.discard-simple",
        "--tls.default-type", "static",
        "--tls.cycle.time", "90",
        "--keep-edges.by-vclass", "passenger",
        "--remove-edges.isolated",
        "--osm.turn-lanes",
        "--output.street-names",
        "--no-turnarounds",
        "--no-warnings",
    ])

    # Background demand: ONE flat base generated at the daily-peak insertion
    # rate.  The running simulation scales it each hour with the calibrated
    # Kuwaiti profile (sumo --scale + traci.simulation.setScale), so any
    # start hour 0-23 works without rebuilding — 03:00 gives the near-empty
    # night grid, 07:00 the full morning peak.
    cfg = SimConfig()
    peak_period = _SC["peak_period_s"]
    routes = os.path.join(DATA, _SC["routes"])
    print(f"  flat peak-rate base: period {peak_period:.2f} s/veh for "
          f"{cfg.demand_hours:.0f} h (scaled live by the hourly profile)")
    run([
        sys.executable, os.path.join(tools_dir(), "randomTrips.py"),
        "-n", NET,
        "-o", os.path.join(DATA, _SC["routes"].replace(".rou.", ".trips.")),
        "-r", routes,
        "-b", "0", "-e", str(int(cfg.demand_hours * 3600)),
        "--period", f"{peak_period:.3f}",
        "--fringe-factor", "5",
        "--seed", "42",
        "--validate",
        "--vehicle-class", "passenger",
        "--prefix", "bg",
        "--trip-attributes", 'departLane="best" departSpeed="max"',
    ])

    with open(VTYPES, "w") as f:
        f.write(VTYPES_XML)
    with open(SUMOCFG, "w") as f:
        f.write(SUMOCFG_XML.format(routes=os.path.basename(routes),
                                   net=_SC["net"]))
    print(f"\nScenario ready: {SUMOCFG}")


if __name__ == "__main__":
    main()
