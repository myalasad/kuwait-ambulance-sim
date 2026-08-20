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

DATA = os.path.join(ROOT, "data")
OSM = os.path.join(DATA, "kuwait_downtown.osm.xml")
NET = os.path.join(DATA, "kuwait_downtown.net.xml")
VTYPES = os.path.join(DATA, "vtypes.add.xml")
SUMOCFG = os.path.join(DATA, "scenario.sumocfg")

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
        <net-file value="kuwait_downtown.net.xml"/>
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

    # Background demand: one slice per clock hour, insertion rate from the
    # calibrated Kuwaiti profile (see sim/traffic_profile.py for provenance).
    cfg = SimConfig()
    profile = hourly_profile(ROOT)
    route_files = []
    for i in range(int(cfg.demand_hours)):
        hour = (cfg.start_hour + i) % 24
        period = period_for_hour(profile, hour)
        routes = os.path.join(DATA, f"background_h{hour:02d}.rou.xml")
        print(f"  hour {hour:02d}:00  multiplier {profile[hour]:.2f}  "
              f"period {period:.2f} s/veh")
        run([
            sys.executable, os.path.join(tools_dir(), "randomTrips.py"),
            "-n", NET,
            "-o", os.path.join(DATA, f"background_h{hour:02d}.trips.xml"),
            "-r", routes,
            "-b", str(i * 3600), "-e", str((i + 1) * 3600),
            "--period", f"{period:.3f}",
            "--fringe-factor", "5",
            "--seed", str(42 + i),
            "--validate",
            "--vehicle-class", "passenger",
            "--prefix", f"bg{hour:02d}_",
            "--trip-attributes", 'departLane="best" departSpeed="max"',
        ])
        route_files.append(os.path.basename(routes))

    with open(VTYPES, "w") as f:
        f.write(VTYPES_XML)
    with open(SUMOCFG, "w") as f:
        f.write(SUMOCFG_XML.format(routes=",".join(route_files)))
    print(f"\nScenario ready: {SUMOCFG}")


if __name__ == "__main__":
    main()
