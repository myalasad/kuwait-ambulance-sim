"""Central configuration for the Kuwait ambulance-preemption simulation.

Everything tunable lives here so the control-room parameters (camera range,
green-wave reach, amber times) can be changed in one place.
"""
from dataclasses import dataclass


@dataclass
class SimConfig:
    # --- scenario files (relative to the project root) ---
    net_file: str = "data/kuwait_downtown.net.xml"
    route_file: str = "data/background.rou.xml"
    vtype_file: str = "data/vtypes.add.xml"
    sumocfg: str = "data/scenario.sumocfg"

    # --- simulation engine ---
    step_length: float = 0.5          # seconds of simulated time per step
    lateral_resolution: float = 0.8   # sublane model: cars form rescue lanes
    #                                   for the blue-light device (set 0.0 to
    #                                   disable if simulation feels slow)
    seed: int = 42

    # --- detection & green-wave preemption ---
    preemption_enabled: bool = True
    camera_range_m: float = 200.0        # a junction camera "sees" the flashing
    #                                      lights this far up its approaches
    greenwave_distance_m: float = 800.0  # hard cap on the corridor look-ahead
    greenwave_lead_s: float = 25.0       # a signal is switched when the
    #                                      ambulance's ETA drops below this —
    #                                      so a crawling ambulance doesn't hold
    #                                      junctions far ahead for minutes
    greenwave_min_m: float = 160.0       # ...but never later than this far out
    max_hold_s: float = 90.0             # safety cap on one preemption hold;
    #                                      cross traffic is starving beyond it
    preempt_cooldown_s: float = 20.0     # normal cycling guaranteed after a
    #                                      hold-limit release, unless the
    #                                      ambulance is at the stop line
    yellow_time_s: float = 3.0           # amber shown to conflicting traffic
    #                                      before it is forced to red
    allred_time_s: float = 2.0           # all-red clearance after the amber so
    #                                      vehicles trapped mid-junction can
    #                                      leave before the corridor green
    clearance_after_pass_s: float = 2.0  # corridor held briefly after the
    #                                      ambulance clears the junction

    # --- arbitration & operator referral ---
    arbitration_tie_m: float = 20.0      # two fresh requests closer than this
    #                                      are a tie: refer to the operator
    operator_timeout_s: float = 8.0      # no operator decision within this ->
    #                                      default policy applies (nearest)

    # --- demand calendar ---
    start_hour: int = 7                  # simulated clock at t=0; choose any
    #                                      hour 0-23 (01:00-05:00 gives the
    #                                      near-empty Kuwaiti night streets).
    #                                      Demand is one flat peak-rate base
    #                                      scaled at runtime by the hourly
    #                                      profile, so no rebuild is needed.
    demand_hours: float = 3.0            # hours of base demand to build

    # --- routing ---
    route_live_weights: bool = True      # Dijkstra uses live travel times;
    #                                      the comparison harness sets False
    #                                      so both runs route identically
    reroute_to_hospital: bool = True     # on reaching the incident scene the
    #                                      ambulance auto-reroutes to the
    #                                      nearest hospital by travel time
    patient_load_s: float = 40.0         # loading stop at the scene; the
    #                                      corridor is paused while loading

    # --- demand-responsive signals for ordinary traffic ---
    actuation_enabled: bool = True
    lone_confirm_s: float = 3.0       # all other approaches must be empty
    #                                   this long before an early green
    lone_min_green_s: float = 5.0     # minimum served green once granted
    lone_max_hold_s: float = 30.0     # early-green cap; then fair timers
    actuation_cooldown_s: float = 10.0  # between early greens per junction

    # --- vehicles ---
    ambulance_type: str = "ambulance"
    speed_exemption_factor: float = 1.5  # ambulances may run at up to 150%
    #                                      of the posted limit (traffic-law
    #                                      emergency exemption — enforcement
    #                                      cameras recognise the lights and
    #                                      issue no citation)
    ambulance_max_kmh: float = 140.0     # absolute cap regardless of road


# Hospitals (lat, lon): dispatch origins AND the candidate destinations for
# the automatic return leg.  Amiri Hospital is the real MoH hospital on
# Arabian Gulf Street inside the modelled cutout; Al-Sabah and Dar Al Shifa
# lie outside it, so they are represented by in-map anchor points on the
# corridors leading toward them (stated plainly: anchors, not the buildings).
HOSPITALS = {
    "Amiri Hospital": (29.3857, 47.9931),
    "Al-Sabah Hospital (west anchor)": (29.3630, 47.9560),
    "Dar Al Shifa Hospital (east anchor)": (29.3745, 48.0025),
}

# Named incident areas inside the downtown cutout (lat, lon) — real
# localities and landmarks, snapped to the nearest drivable edge at dispatch.
# Ambulances always ORIGINATE at a hospital; these are where incidents occur.
AREAS = {
    "Mirqab": (29.3719, 47.9852),
    "Salhiya": (29.3701, 47.9723),
    "Qibla (Old Souq)": (29.3762, 47.9682),
    "Souq Al-Mubarakiya": (29.3737, 47.9767),
    "Sharq (Souq Sharq)": (29.3853, 47.9880),
    "Dasman": (29.3868, 48.0007),
    "Kuwait Towers area": (29.3880, 48.0040),
    "Grand Mosque / Seif": (29.3800, 47.9740),
    "Liberation Tower": (29.3787, 47.9902),
    "Ministries area (south)": (29.3625, 47.9705),
    "Jibla waterfront": (29.3845, 47.9660),
    "Bneid Al-Qar edge": (29.3700, 48.0080),
}
