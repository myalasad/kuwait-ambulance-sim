"""KPI collection: ambulance run times and live counters."""


class Metrics:
    def __init__(self):
        self.completed = []  # dicts: id, duration_s, length_m, kmh
        self.analysis = []   # per-run with/without arrival-time analysis

    def complete(self, amb_id, duration_s, length_m):
        self.completed.append({
            "id": amb_id,
            "duration_s": round(duration_s, 1),
            "length_m": round(length_m, 1),
            "kmh": round(length_m / max(duration_s, 0.1) * 3.6, 1),
        })

    def kpi(self, n_vehicles, n_ambulances, n_preempted):
        return {
            "vehicles": n_vehicles,
            "ambulances": n_ambulances,
            "preempted_tls": n_preempted,
            "runs": self.completed[-8:],
        }
