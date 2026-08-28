"""Structured operations log and case tracking.

Every consequential thing that happens in the system — a camera detection, a
junction being purposely enabled, an arbitration between two ambulances, an
error, an ambulance leaving the map — is recorded as a typed *operation*, and
grouped into *cases* with an open/close lifecycle:

  P-nnn  preemption case: one junction purposely enabled for one corridor,
         closed when the junction is back on its normal programme
  A-nnn  ambulance case: dispatch to arrival (or teleport/removal), so an
         ambulance can never "just disappear" — the close reason says why
  D-nnn  decision case: a conflict the controller referred to the operator

Operations stream to the dashboards in real time (WS frames + /api/operations)
and persist to data/operations.jsonl for after-action review.
"""
import json
import os
import time
from collections import deque

SEVERITIES = ("info", "warn", "error", "decision")


class OperationsLog:
    RING = 3000

    ROTATE_BYTES = 20 * 1024 * 1024   # archive the log past ~20 MB

    def __init__(self, root):
        self.seq = 0
        self.ring = deque(maxlen=self.RING)
        self.path = os.path.join(root, "data", "operations.jsonl")
        self._rotate_if_large()
        self._fh = open(self.path, "a", encoding="utf-8")
        self.cases = {}
        self._case_counters = {"P": 0, "A": 0, "D": 0}
        self.places = None   # real-name registry, attached by the runner

    def _rotate_if_large(self):
        """Archive an oversized operations log (gzip, timestamped) so the
        JSONL never grows without bound and the copilot corpus stays fast."""
        try:
            if (os.path.exists(self.path)
                    and os.path.getsize(self.path) > self.ROTATE_BYTES):
                import gzip
                import shutil
                from datetime import date
                dst = self.path.replace(
                    ".jsonl", f".{date.today().isoformat()}.jsonl.gz")
                with open(self.path, "rb") as src, \
                        gzip.open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.remove(self.path)
        except OSError:
            pass

    def jn(self, tls_id):
        """Human junction label (code + real streets)."""
        return self.places.jn(tls_id) if self.places else f"junction {tls_id}"

    def rd(self, edge_id):
        """Human road label."""
        return self.places.road(edge_id) if self.places else edge_id

    # ---------------------------------------------------------------- events

    def emit(self, t, ev_type, msg, sev="info", actor=None, case=None,
             data=None):
        self.seq += 1
        event = {
            "seq": self.seq,
            "t": round(t, 1),
            "wall": round(time.time(), 1),
            "type": ev_type,
            "sev": sev if sev in SEVERITIES else "info",
            "actor": actor,
            "case": case,
            "msg": msg,
        }
        if data:
            event["data"] = data
        self.ring.append(event)
        if case and case in self.cases:
            self.cases[case]["events"].append(self.seq)
        self._fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._fh.flush()
        return event

    def since(self, seq):
        if seq >= self.seq:            # nothing new: skip the ring scan
            return []
        # list() first: the web thread reads this while the sim thread
        # appends (an atomic snapshot avoids "deque mutated during iteration")
        return [e for e in list(self.ring) if e["seq"] > seq]

    # ----------------------------------------------------------------- cases

    def open_case(self, kind, subject, t, summary):
        self._case_counters[kind] += 1
        case_id = f"{kind}-{self._case_counters[kind]:03d}"
        self.cases[case_id] = {
            "id": case_id, "kind": kind, "subject": subject,
            "opened_t": round(t, 1), "closed_t": None,
            "status": "open", "summary": summary, "outcome": None,
            "events": [],
        }
        return case_id

    def close_case(self, case_id, t, outcome, status="closed"):
        c = self.cases.get(case_id)
        if c is None or c["status"] != "open":
            return
        c["closed_t"] = round(t, 1)
        c["status"] = status
        c["outcome"] = outcome

    def case_list(self, limit=200):
        cases = sorted(list(self.cases.values()),
                       key=lambda c: c["opened_t"], reverse=True)
        return cases[:limit]

    def open_cases(self):
        return [c for c in self.cases.values() if c["status"] == "open"]
