"""Central configuration for the Kuwait ambulance-preemption simulation.

Three selectable network models (``SimConfig(scenario=...)``):

* ``downtown`` — the detailed Kuwait City core: every street, sublane model,
  rescue lanes.  Best for close-up demos of the signal mechanics.
* ``metro``    — ALL SIX GOVERNORATES (Capital, Hawalli, Farwaniya,
  Mubarak Al-Kabeer, Ahmadi, Jahra) at arterial level: motorways, trunks,
  primary and secondary roads.  Real hospitals in every governorate,
  cross-governorate missions.  Sublane model on (lateral_resolution 0.8)
  so rescue lanes form.
* ``showcase`` — the downtown network with three fixed-density districts
  (dense core, normal ring, light waterfront) baked into static demand, so
  both early-green regimes are on screen at once.

Everything tunable lives here so the control-room parameters (camera range,
green-wave reach, amber times) can be changed in one place.
"""
from dataclasses import dataclass, field

SCENARIOS = {
    "downtown": {
        "label": "Downtown Kuwait City (detailed)",
        "osm": "kuwait_downtown.osm.xml",
        "net": "kuwait_downtown.net.xml",
        "routes": "background_base.rou.xml",
        "sumocfg": "scenario.sumocfg",
        "query": "overpass_query.txt",
        "peak_period_s": 0.3,     # ~12,000 veh/h in the core at peak (medium); extreme x1.8
        "lateral_resolution": 0.8,
        "snap_radius_m": 400.0,
        # The trailing parenthesis is read by the UI as the GOVERNORATE (it
        # groups the dropdown by it) and is stripped from the option text,
        # so the modelling note has to live in the name itself or the
        # disclosure disappears and "west anchor" reads as a governorate.
        # Al-Sabah and Dar Al Shifa are modelled anchors: their real sites
        # are outside this downtown extract and do not snap to it.
        "hospitals": {
            "Amiri Hospital (Capital)": (29.3857, 47.9931),
            "Al-Sabah Hospital — modelled west anchor (Capital)":
                (29.3630, 47.9560),
            "Dar Al Shifa Hospital — modelled east anchor (Capital)":
                (29.3745, 48.0025),
        },
        "areas": {
            "Mirqab (Capital)": (29.3719, 47.9852),
            "Salhiya (Capital)": (29.3701, 47.9723),
            "Qibla — Old Souq (Capital)": (29.3762, 47.9682),
            "Souq Al-Mubarakiya (Capital)": (29.3737, 47.9767),
            "Sharq — Souq Sharq (Capital)": (29.3853, 47.9880),
            "Dasman (Capital)": (29.3868, 48.0007),
            "Kuwait Towers area (Capital)": (29.3880, 48.0040),
            "Grand Mosque / Seif (Capital)": (29.3800, 47.9740),
            "Liberation Tower (Capital)": (29.3787, 47.9902),
            "Ministries area (Capital)": (29.3625, 47.9705),
            "Jibla waterfront (Capital)": (29.3791, 47.9718),
            "Bneid Al-Qar edge (Capital)": (29.3700, 48.0080),
            "National Assembly (Capital)": (29.3764, 47.9684),
            "Seif Palace / Amiri Diwan (Capital)": (29.3823, 47.9754),
            "Al-Hamra Tower (Capital)": (29.3790, 47.9936),
            "Kuwait Stock Exchange (Capital)": (29.3765, 47.9757),
            "Al-Shaheed Park (Capital)": (29.3725, 47.9930),
            "Dasman Diabetes Institute (Capital)": (29.3858, 48.0012),
            "Al-Watiya (Capital)": (29.3705, 47.9800),
            "Souq Al-Safat (Capital)": (29.3770, 47.9795),
            "Souq Sharq waterfront (Capital)": (29.3868, 47.9905),
        },
    },
    "metro": {
        "label": "All governorates (metro arterials)",
        "osm": "kuwait_metro.osm.xml",
        "net": "kuwait_metro.net.xml",
        "routes": "background_metro.rou.xml",
        "sumocfg": "scenario_metro.sumocfg",
        "query": "overpass_query_metro.txt",
        "peak_period_s": 0.25,   # ~14,400 veh/h metro-wide at peak
        "lateral_resolution": 0.8,   # rescue lanes ON (68 ms/step measured)
        "snap_radius_m": 1500.0,     # arterials only -> snap further
        "hospitals": {
            # one MoH general hospital per governorate (real locations)
            "Amiri Hospital (Capital)": (29.3857, 47.9931),
            "Al-Sabah Hospital (Capital)": (29.3486, 47.9247),
            "Mubarak Al-Kabeer Hospital (Hawalli)": (29.3126, 48.0192),
            "Farwaniya Hospital (Farwaniya)": (29.2739, 47.9410),
            "Al-Adan Hospital (Mubarak Al-Kabeer)": (29.1707, 48.0993),
            "Al-Jahra Hospital (Jahra)": (29.3402, 47.6589),
        },
        "areas": {
            # Capital
            "Sharq (Capital)": (29.3853, 47.9880),
            "Mirqab (Capital)": (29.3719, 47.9852),
            "Shamiya (Capital)": (29.3510, 47.9550),
            "Qadsiya (Capital)": (29.3450, 47.9660),
            "Surra (Capital)": (29.3140, 47.9850),
            # Hawalli
            "Hawalli (Hawalli)": (29.3320, 48.0280),
            "Salmiya (Hawalli)": (29.3330, 48.0760),
            "Jabriya (Hawalli)": (29.3120, 48.0300),
            "Mishref (Hawalli)": (29.2780, 48.0700),
            "Bayan (Hawalli)": (29.3030, 48.0490),
            # Farwaniya
            "Farwaniya (Farwaniya)": (29.2770, 47.9580),
            "Khaitan (Farwaniya)": (29.2860, 47.9720),
            "Jleeb Al-Shuyoukh (Farwaniya)": (29.2680, 47.9300),
            "Ardiya (Farwaniya)": (29.2920, 47.8980),
            # Mubarak Al-Kabeer
            "Mubarak Al-Kabeer (Mubarak Al-Kabeer)": (29.2270, 48.0790),
            "Qurain (Mubarak Al-Kabeer)": (29.2380, 48.0800),
            "Sabah Al-Salem (Mubarak Al-Kabeer)": (29.2570, 48.0860),
            "Messila (Mubarak Al-Kabeer)": (29.2710, 48.0960),
            # Ahmadi
            "Fintas (Ahmadi)": (29.1730, 48.1200),
            "Mangaf (Ahmadi)": (29.0960, 48.1270),
            "Fahaheel (Ahmadi)": (29.0820, 48.1300),
            "Ahmadi City (Ahmadi)": (29.0830, 48.0840),
            "Kaifan (Capital)": (29.3370, 47.9510),
            "Khaldiya (Capital)": (29.3360, 47.9690),
            "Adailiya (Capital)": (29.3400, 47.9850),
            "Rawda (Capital)": (29.3360, 47.9980),
            "Abdullah Al-Salem (Capital)": (29.3550, 47.9770),
            "Mansouriya (Capital)": (29.3640, 47.9880),
            "Daiya (Capital)": (29.3580, 47.9990),
            "Nuzha (Capital)": (29.3350, 48.0030),
            "Faiha (Capital)": (29.3300, 47.9600),
            "Qortuba (Capital)": (29.3146, 47.9813),
            "Yarmouk (Capital)": (29.3100, 47.9560),
            "Shuwaikh Industrial (Capital)": (29.3450, 47.9200),
            "Doha (Capital)": (29.3640, 47.8900),
            "Sulaibikhat (Capital)": (29.3336, 47.9300),
            "Granada (Capital)": (29.3290, 47.9320),
            "Rumaithiya (Hawalli)": (29.3140, 48.0770),
            "Salwa (Hawalli)": (29.2950, 48.0830),
            "Shaab (Hawalli)": (29.3520, 48.0300),
            "Maidan Hawalli (Hawalli)": (29.3280, 48.0080),
            "Bida'a (Hawalli)": (29.3120, 48.0790),
            "Hitteen (Hawalli)": (29.2766, 48.0378),
            "Zahra (Hawalli)": (29.2700, 48.0250),
            "Siddiq (Hawalli)": (29.2860, 48.0220),
            "Shuhada (Hawalli)": (29.2700, 48.0480),
            "Rabiya (Farwaniya)": (29.2930, 47.9300),
            "Andalous (Farwaniya)": (29.3060, 47.8950),
            "Firdous (Farwaniya)": (29.2850, 47.9050),
            "Omariya (Farwaniya)": (29.3060, 47.9280),
            "Rai (Farwaniya)": (29.3200, 47.9200),
            "Abraq Khaitan (Farwaniya)": (29.2760, 47.9760),
            "Sabah Al-Nasser (Farwaniya)": (29.2530, 47.8820),
            "Riggae (Farwaniya)": (29.3100, 47.9100),
            "Adan (Mubarak Al-Kabeer)": (29.2230, 48.0830),
            "Qusour (Mubarak Al-Kabeer)": (29.2300, 48.0700),
            "Fnaitees (Mubarak Al-Kabeer)": (29.2050, 48.0700),
            "Abu Fatira (Mubarak Al-Kabeer)": (29.1940, 48.0900),
            "Al-Masayel (Mubarak Al-Kabeer)": (29.1800, 48.0700),
            "Abu Hasaniya (Mubarak Al-Kabeer)": (29.2050, 48.1200),
            "Abu Halifa (Ahmadi)": (29.1340, 48.1270),
            "Mahboula (Ahmadi)": (29.1480, 48.1270),
            "Egaila (Ahmadi)": (29.1550, 48.1100),
            "Sabahiya (Ahmadi)": (29.1100, 48.1000),
            "Hadiya (Ahmadi)": (29.1400, 48.0800),
            "Riqqa (Ahmadi)": (29.1600, 48.0900),
            "Jaber Al-Ali (Ahmadi)": (29.1200, 48.1000),
            "Dhaher (Ahmadi)": (29.1000, 48.1050),
            # Jahra
            "Jahra (Jahra)": (29.3370, 47.6750),
            "Saad Al-Abdullah (Jahra)": (29.3100, 47.7780),
            "Sulaibiya (Jahra)": (29.2700, 47.8270),
            "Qasr (Jahra)": (29.3300, 47.6900),
            "Naeem (Jahra)": (29.3350, 47.6650),
            "Oyoun (Jahra)": (29.3400, 47.6800),
            "Taima (Jahra)": (29.3200, 47.6850),
            "Nasseem (Jahra)": (29.3250, 47.7000),
            "Waha (Jahra)": (29.3300, 47.7050),
            "Amghara Industrial (Jahra)": (29.2800, 47.7800),
            "Jahra Industrial (Jahra)": (29.3100, 47.7100),
        },
    },
}

# Showcase: the downtown network with THREE fixed-density districts baked
# into the demand — dense core, normal ring, light waterfront — so both
# early-green regimes are on screen at the same time: lone drivers earn
# early greens in the light district while the dense district's junctions
# rule "several approaches occupied — fair timers by design".  Demand is
# static (no clock scaling), so the state caches and starts instantly.
SCENARIOS["showcase"] = {
    **SCENARIOS["downtown"],
    "label": "Showcase — 3 districts (downtown)",
    "routes": "background_showcase.rou.xml",
    "sumocfg": "scenario_showcase.sumocfg",
    "static_demand": True,
    # trips are kept with probability `keep` by the district their route
    # STARTS in (nearest-anchor assignment); build_showcase.py bakes this
    "districts": [
        {"name": "Dense core", "kind": "dense", "lat": 29.3745,
         "lon": 47.9860, "radius_m": 850, "keep": 1.0},
        {"name": "Normal ring", "kind": "normal", "lat": 29.3700,
         "lon": 47.9655, "radius_m": 800, "keep": 0.45},
        {"name": "Light waterfront", "kind": "light", "lat": 29.3868,
         "lon": 48.0005, "radius_m": 750, "keep": 0.10},
    ],
}


@dataclass
class SimConfig:
    # --- network model ---
    # DEFAULT: the 3-district showcase — fixed densities, so the city is
    # already dense the moment it opens (no waiting for a clock-scaled
    # rush hour to build up) and both early-green regimes are on screen
    # at once.  "downtown" and "metro" add the Kuwaiti weekly calendar.
    scenario: str = "showcase"        # any key of SCENARIOS: "downtown" | "metro" | "showcase"

    # --- scenario files (set from SCENARIOS in __post_init__) ---
    net_file: str = ""
    sumocfg: str = ""
    vtype_file: str = "data/vtypes.add.xml"

    # --- simulation engine ---
    step_length: float = 0.5          # seconds of simulated time per step
    lateral_resolution: float = 0.8   # set per scenario in __post_init__
    seed: int = 42
    snap_radius_m: float = 400.0      # geocoding snap radius (per scenario)

    # --- background traffic realism (keeps the city from gridlocking) -----
    # Measured on the showcase, 3 seeds x 8 missions, 40 min of city time
    # each.  Without these the downtown grid collapses: 61% of vehicles
    # standing, 12.2 km/h mean, ~408 teleports per 40 min, and 2 of 8
    # ambulance missions never finished.  With them: 48% standing,
    # 21.4 km/h, 192 teleports, 8 of 8 finished.
    ignore_junction_blocker_s: float = 20.0
    #   A car that has been blocked by a vehicle STANDING IN THE JUNCTION
    #   this long drives around it.  SUMO's default (-1) is "wait for ever",
    #   which turns any blocked box into a permanent deadlock ring that only
    #   the 180 s teleport can break.  This is the single biggest teleport
    #   reduction measured (408 -> ~200 per 40 min).  0 disables.
    nav_adoption: float = 0.35
    #   Share of background drivers carrying a navigation app (SUMO's
    #   rerouting device): they re-plan around a jam instead of queueing
    #   into it, which is the negative feedback the fixed-route demand
    #   otherwise lacks.  AMBULANCES ARE EXCLUDED (vtypes.add.xml opts them
    #   out) — sim/router.py owns their routing and the corridor follows it.
    #   0.35 was chosen by measurement, not taste: 0 leaves the city at
    #   15.6 km/h, 1.0 gives the fastest city (27.9 km/h) but SLOWER
    #   ambulances (431 s vs 389 s mean mission) because uniform congestion
    #   removes the quiet corridors the ambulance router exploits, and it
    #   lost a mission on one seed.  0.35 keeps the ambulance numbers of 0
    #   with most of the city gain of 1.0.  0 disables.
    nav_reroute_period_s: float = 300.0   # how often a nav driver re-plans
    static_demand_scale: float = 0.50
    #   Insertion scale for scenarios whose demand is baked into the route
    #   file (the showcase).  It used to be an unlabelled hard-coded 1.000,
    #   and 1.000 is OVERSATURATED: the route file offers 2.39 vehicles/s
    #   while this network only discharges ~1.85/s, so the vehicle count
    #   grows without bound and the city jams solid however well the drivers
    #   behave.  That is a queueing fact, not a behaviour bug — the only
    #   fixes are less demand or more road.
    #
    #   Measured to 2 h 10 min of city time, with all 12 calls dispatched
    #   INTO the congested second half (vehicles in network over the run /
    #   missions completed / worst unit's share of time stopped):
    #       1.00   1087 -> 4246   3 of 8   88%   <- the reported bug
    #       0.70    621 -> 1194   7 of 8   73%
    #       0.60    546 ->  760   8 of 8   43%
    #       0.50    435 ->  481   8 of 8   23%   <- bounded queue
    #   0.50 is the only value whose vehicle count is FLAT, so the dashboard
    #   can be left running for hours without degrading; it holds the city
    #   at 34.5 km/h.  Raise to 0.60 for ~50% more visible traffic at the
    #   cost of slow drift (still 8 of 8).  Do not exceed 0.70.

    # --- detection & green-wave preemption ---
    preemption_enabled: bool = True
    camera_range_m: float = 200.0        # a junction camera "sees" the flashing
    #                                      lights this far up its approaches
    greenwave_lead_s: float = 25.0       # switch a signal when the ambulance's
    #                                      ETA to it drops below this
    greenwave_min_m: float = 160.0       # ...but never later than this far out
    greenwave_distance_m: float = 800.0  # and never earlier than this far out
    yellow_time_s: float = 3.0           # amber shown to conflicting traffic
    allred_time_s: float = 2.0           # all-red clearance before the corridor
    clearance_after_pass_s: float = 2.0  # corridor held briefly after passing
    max_hold_s: float = 90.0             # cap on a single continuous hold
    preempt_cooldown_s: float = 20.0     # cross traffic gets at least this
    #                                      much normal cycling after a long hold
    flash_amber: bool = False            # cross approaches show SOLID RED
    #                                      while a corridor is held: red is
    #                                      the stop indication for traffic
    #                                      that is not on the ambulance's
    #                                      route.  The EMERGENCY indicator
    #                                      is the junction itself, which
    #                                      blinks AMBER on the map for the
    #                                      whole hold (rule A6).  Set True
    #                                      to make cross approaches flash
    #                                      amber (yield) instead of stop.
    flash_harden_eta_s: float = 12.0     # the flash hardens to solid red
    #                                      when the unit is this close in
    #                                      TIME — clearance ahead of an
    #                                      ambulance is a time quantity: at
    #                                      speed this hardens ~180 m out,
    #                                      in a crawl the flash persists
    flash_harden_min_m: float = 60.0     # ...and always within this range,
    #                                      whatever the speed
    flush_lead_factor: float = 2.0       # a CONGESTED approach ahead on the
    #                                      route is enabled with this much
    #                                      extra activation lead, so its
    #                                      queue drains before the unit
    #                                      arrives (still capped by
    #                                      greenwave_distance_m + max_hold)

    # --- arbitration & operator referral ---
    arbitration_tie_m: float = 20.0      # two fresh requests closer than this
    #                                      are a tie: refer to the operator
    operator_timeout_s: float = 8.0      # no operator decision within this ->
    #                                      default policy applies (nearest)

    # --- demand calendar ---
    start_hour: int = 7                  # simulated clock at t=0; choose any
    #                                      hour 0-23 (01:00-05:00 gives the
    #                                      near-empty Kuwaiti night streets).
    day_type: str = "weekday"            # "weekday" (Sun-Thu) | "weekend"
    #                                      (Fri-Sat): different hourly shapes
    traffic_level: str = "medium"        # "easy" | "medium" | "extreme":
    #                                      intensity on top of the hourly
    #                                      shape (x0.45 / x1.0 / x1.8)
    demand_hours: float = 3.0            # hours of base demand to build
    warmup_s: float = 420.0              # seconds of city time fast-forwarded
    #                                      on start so the city is already
    #                                      flowing when the dashboard opens
    #                                      (0 disables)
    demand_factor: float = 0.6           # global multiplier on background
    #                                      demand: the calendar SHAPE is
    #                                      kept, the vehicle count is scaled
    #                                      for a fluid presentation (1.0 =
    #                                      full calibrated demand)

    # --- routing ---
    route_live_weights: bool = True      # Dijkstra uses live travel times;
    #                                      the comparison harness sets False
    #                                      so both runs route identically
    reroute_to_hospital: bool = True     # on reaching the incident scene the
    #                                      ambulance auto-reroutes to the
    #                                      nearest hospital by travel time
    hospital_ready_units: int = 4        # ready ambulances stationed per
    #                                      hospital; a dispatch commits one;
    #                                      with every crew committed a call
    #                                      QUEUES until one returns
    gate_headway_s: float = 8.0          # a hospital that just launched a
    #                                      unit yields to an equally-close
    #                                      peer — departures never stack
    unit_turnaround_s: float = 180.0     # after delivering a patient the
    #                                      crew restocks this long before
    #                                      rejoining the READY pool (at the
    #                                      hospital it delivered to)
    dispatch_rotation_tolerance: float = 0.25
    #                                      nearest-AVAILABLE-unit dispatch:
    #                                      a hospital whose response time is
    #                                      within this fraction of the
    #                                      fastest may be chosen instead
    #                                      when the fastest already has
    #                                      units out on mission
    patient_load_s: float = 40.0         # loading stop at the scene; the
    #                                      corridor is paused while loading
    adaptive_reroute: bool = True        # a stuck ambulance re-plans around
    #                                      the blockage instead of waiting
    stuck_progress_m: float = 40.0       # less than this progress...
    stuck_after_s: float = 25.0          # ...within this window -> stuck
    #                                      (progress-based: stop-and-go
    #                                      creep still counts as stuck)
    stuck_reroute_cooldown_s: float = 60.0

    # --- demand-responsive signals for ordinary traffic ---
    actuation_enabled: bool = True
    lone_confirm_s: float = 3.0       # all other approaches must be empty
    #                                   this long before an early green
    detection_zone_m: float = 120.0   # each approach is watched over this
    #                                   length upstream of the stop line
    #                                   (across edge boundaries), like an
    #                                   advance loop detector — never just
    #                                   the final connector stub
    junction_clear_radius_m: float = 75.0
    #                                    PHYSICAL clearance test for early
    #                                    green: no vehicle may be standing
    #                                    within this radius of the junction
    #                                    on any approach other than the one
    #                                    being served — ground truth, not
    #                                    just the mapped approach list
    lone_quiet_s: float = 45.0        # ...and must not have carried ANY
    #                                   traffic within this window: a junction
    #                                   in use from several directions stays
    #                                   on its fair timer even between
    #                                   platoons (Protocol D4)
    lone_min_green_s: float = 5.0     # minimum served green once granted
    lone_max_hold_s: float = 30.0     # early-green cap; then fair timers
    actuation_cooldown_s: float = 10.0  # between early greens per junction

    # --- Markov traffic predictor (DTMC + CTMC, self-feeding) ---
    markov_routing: bool = True       # Dijkstra uses CTMC-predicted edge
    #                                   speeds at the arrival horizon
    markov_sample_s: float = 30.0     # observation period (sim seconds)
    markov_max_edges: int = 160       # junction approaches + arterials
    #                                   monitored individually
    markov_min_obs: int = 40          # below this, pool by road class
    markov_save_every_s: float = 300.0  # persist the chains this often

    # --- vehicles ---
    ambulance_type: str = "ambulance"
    speed_exemption_factor: float = 1.5  # ambulances may run at up to 150%
    #                                      of the posted limit (traffic-law
    #                                      emergency exemption — enforcement
    #                                      cameras recognise the lights and
    #                                      issue no citation)
    ambulance_max_kmh: float = 140.0     # absolute cap regardless of road

    def __post_init__(self):
        sc = SCENARIOS.get(self.scenario)
        if sc is None:
            raise ValueError(f"Unknown scenario: {self.scenario!r} "
                             f"(choose from {sorted(SCENARIOS)})")
        self.net_file = f"data/{sc['net']}"
        self.sumocfg = f"data/{sc['sumocfg']}"
        self.lateral_resolution = sc["lateral_resolution"]
        self.snap_radius_m = sc["snap_radius_m"]

    def hospitals_d(self):
        return SCENARIOS[self.scenario]["hospitals"]

    def areas_d(self):
        return SCENARIOS[self.scenario]["areas"]

    def label(self):
        return SCENARIOS[self.scenario]["label"]


# Backwards-compatible module-level names (downtown model).
HOSPITALS = SCENARIOS["downtown"]["hospitals"]
AREAS = SCENARIOS["downtown"]["areas"]
