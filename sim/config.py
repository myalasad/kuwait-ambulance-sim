"""Central configuration for the Kuwait ambulance-preemption simulation.

Two selectable network models (``SimConfig(scenario=...)``):

* ``downtown`` — the detailed Kuwait City core: every street, sublane model,
  rescue lanes.  Best for close-up demos of the signal mechanics.
* ``metro``    — ALL SIX GOVERNORATES (Capital, Hawalli, Farwaniya,
  Mubarak Al-Kabeer, Ahmadi, Jahra) at arterial level: motorways, trunks,
  primary and secondary roads.  Real hospitals in every governorate,
  cross-governorate missions.  Sublane off for speed.

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
        "peak_period_s": 1.3,     # randomTrips insertion period at peak
        "lateral_resolution": 0.8,
        "snap_radius_m": 400.0,
        "hospitals": {
            "Amiri Hospital": (29.3857, 47.9931),
            "Al-Sabah Hospital (west anchor)": (29.3630, 47.9560),
            "Dar Al Shifa Hospital (east anchor)": (29.3745, 48.0025),
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
            "Jibla waterfront (Capital)": (29.3845, 47.9660),
            "Bneid Al-Qar edge (Capital)": (29.3700, 48.0080),
            "National Assembly (Capital)": (29.3795, 47.9648),
            "Seif Palace / Amiri Diwan (Capital)": (29.3855, 47.9715),
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
        "lateral_resolution": 0.0,   # sublane off: 6-governorate network
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
            "Sulaibikhat (Capital)": (29.3380, 47.8700),
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


@dataclass
class SimConfig:
    # --- network model ---
    scenario: str = "downtown"        # "downtown" | "metro" (all governorates)

    # --- scenario files (set from SCENARIOS in __post_init__) ---
    net_file: str = ""
    sumocfg: str = ""
    vtype_file: str = "data/vtypes.add.xml"

    # --- simulation engine ---
    step_length: float = 0.5          # seconds of simulated time per step
    lateral_resolution: float = 0.8   # set per scenario in __post_init__
    seed: int = 42
    snap_radius_m: float = 400.0      # geocoding snap radius (per scenario)

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

    # --- arbitration & operator referral ---
    arbitration_tie_m: float = 20.0      # two fresh requests closer than this
    #                                      are a tie: refer to the operator
    operator_timeout_s: float = 8.0      # no operator decision within this ->
    #                                      default policy applies (nearest)

    # --- demand calendar ---
    start_hour: int = 7                  # simulated clock at t=0; choose any
    #                                      hour 0-23 (01:00-05:00 gives the
    #                                      near-empty Kuwaiti night streets).
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
