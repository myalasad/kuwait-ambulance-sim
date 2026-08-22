"""Build the copilot's corpus from the system's own records.

Sources:
* ``data/operations.jsonl`` — every structured operation, grouped ONE
  DOCUMENT PER CASE (all of P-012's events together retrieve far better
  than event-by-event), plus session-level system events in batches;
* the Protocol rulebook — sections extracted from the served HTML;
* the live Markov traffic analytics — injected as a document at query time
  by the server (see web/server.py), so congestion questions are answerable.

Each document: {id, type, title, text, meta:{ambs, cases, tls, kinds}}.
"""
import json
import os
import re


def _strip_html(html):
    text = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _entities(text):
    return {
        "ambs": sorted(set(re.findall(r"AMB_\d+", text))),
        "cases": sorted(set(re.findall(r"\b[PAD]-\d{3}\b", text))),
        "tls": sorted(set(re.findall(
            r"(?:GS_|joinedS_)?cluster_[\w#]+|\b\d{6,}\b", text)))[:40],
    }


def load_operations(root):
    path = os.path.join(root, "data", "operations.jsonl")
    events = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    return events


def build_corpus(root):
    docs = []

    # --- operations, grouped per case PER SESSION ---
    # Case ids restart every simulation session while the log appends
    # forever; the sequence counter resetting marks a session boundary.
    events = load_operations(root)
    session = 0
    last_seq = None
    by_case = {}
    loose = []
    for ev in events:
        seq = ev.get("seq", 0)
        if last_seq is not None and seq < last_seq:
            session += 1
        last_seq = seq
        ev["_session"] = session
        if ev.get("case"):
            by_case.setdefault((session, ev["case"]), []).append(ev)
        else:
            loose.append(ev)
    n_sessions = session + 1
    for (sess, case_id), evs in by_case.items():
        lines = [f"[t={e['t']:.0f}s] ({e['type']}/{e['sev']}) {e['msg']}"
                 for e in evs]
        tag = "" if sess == session else f" (session {sess + 1} of {n_sessions})"
        text = f"Case {case_id}{tag} record ({len(evs)} operations):\n" + \
               "\n".join(lines)
        meta = _entities(text)
        meta["kinds"] = sorted({e["type"] for e in evs})
        meta["session"] = sess
        suffix = "" if sess == session else f"#s{sess + 1}"
        docs.append({"id": f"case:{case_id}{suffix}", "type": "case",
                     "title": f"Case {case_id}{tag}", "text": text,
                     "meta": meta})
    for i in range(0, len(loose), 60):
        chunk = loose[i:i + 60]
        text = "System/uncased operations:\n" + "\n".join(
            f"[t={e['t']:.0f}s] ({e['type']}) {e['msg']}" for e in chunk)
        meta = _entities(text)
        meta["kinds"] = sorted({e["type"] for e in chunk})
        meta["session"] = chunk[-1].get("_session", 0)
        docs.append({"id": f"ops:{i}", "type": "ops",
                     "title": f"Operations batch {i // 60 + 1}",
                     "text": text, "meta": meta})

    # --- protocol rulebook, one doc per numbered section ---
    proto_path = os.path.join(root, "web", "static", "protocol.html")
    if os.path.exists(proto_path):
        html = open(proto_path, encoding="utf-8").read()
        body = html.split("<main>", 1)[-1]
        sections = re.split(r"<h2>", body)
        for sec in sections[1:]:
            text = _strip_html("<h2>" + sec)[:4000]
            title = text[:80].strip()
            docs.append({"id": f"protocol:{title[:40]}", "type": "protocol",
                         "title": f"Protocol — {title[:60]}",
                         "text": "Operating protocol section: " + text,
                         "meta": {"ambs": [], "cases": [], "tls": [],
                                  "kinds": ["protocol"]}})
    return docs
