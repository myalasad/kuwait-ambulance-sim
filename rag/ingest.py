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
            r"\bJ-\d{3}\b|(?:GS_|joinedS_)?cluster_[\w#]+|\b\d{6,}\b", text)))[:40],
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
            text = _strip_html("<h2>" + sec)[:7000]
            title = text[:80].strip()
            docs.append({"id": f"protocol:{title[:40]}", "type": "protocol",
                         "title": f"Protocol — {title[:60]}",
                         "text": "Operating protocol section: " + text,
                         "meta": {"ambs": [], "cases": [], "tls": [],
                                  "kinds": ["protocol", "docs"],
                                  "session": 10**6}})
    # --- the programme's own documentation: handbook, README, config,
    #     module docstrings, version history ---
    docs += programme_docs(root)
    return docs


# ------------------------------------------------------------ programme docs

def _md_sections(path, prefix):
    """Split a markdown file into one document per heading."""
    docs = []
    if not os.path.exists(path):
        return docs
    text = open(path, encoding="utf-8").read()
    parts = re.split(r"^(#{1,3} .+)$", text, flags=re.M)
    title = os.path.basename(path)
    for i in range(1, len(parts), 2):
        head = parts[i].lstrip("# ").strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if len(body) < 40:
            continue
        docs.append({
            "id": f"{prefix}:{head[:48]}", "type": "doc",
            "title": f"{prefix.capitalize()} — {head}",
            "text": f"{head}\n{body}"[:5000],
            "meta": {"ambs": [], "cases": [], "tls": [],
                     "kinds": ["docs", prefix], "session": 10**6},
        })
    return docs


def _config_reference(root):
    """Every SimConfig field with its inline comment, as one document per
    parameter group, so 'what does camera_range_m mean?' is answerable."""
    path = os.path.join(root, "sim", "config.py")
    if not os.path.exists(path):
        return []
    src = open(path, encoding="utf-8").read()
    body = src.split("class SimConfig", 1)[-1].split("def __post_init__", 1)[0]
    groups, current, lines = [], "General", []
    for raw in body.splitlines():
        line = raw.strip()
        m = re.match(r"#\s*---\s*(.+?)\s*---", line)
        if m:
            if lines:
                groups.append((current, lines))
            current, lines = m.group(1), []
            continue
        m = re.match(r"(\w+):\s*[\w\[\], ]+=\s*([^#]+?)\s*(?:#\s*(.*))?$", line)
        if m:
            lines.append(f"{m.group(1)} = {m.group(2).strip()}"
                         + (f" — {m.group(3).strip()}" if m.group(3) else ""))
        elif line.startswith("#") and lines:
            lines[-1] += " " + line.lstrip("# ").strip()
    if lines:
        groups.append((current, lines))
    docs = []
    for name, items in groups:
        docs.append({
            "id": f"config:{name[:40]}", "type": "doc",
            "title": f"Configuration — {name}",
            "text": f"Configuration parameters ({name}), file sim/config.py:\n"
                    + "\n".join(items),
            "meta": {"ambs": [], "cases": [], "tls": [],
                     "kinds": ["docs", "config"], "session": 10**6},
        })
    return docs


def _module_docs(root):
    """Module docstrings: what each part of the code does."""
    docs = []
    for rel in ("sim", "rag", "web", "scripts"):
        folder = os.path.join(root, rel)
        if not os.path.isdir(folder):
            continue
        for fn in sorted(os.listdir(folder)):
            if not fn.endswith(".py"):
                continue
            src = open(os.path.join(folder, fn), encoding="utf-8").read()
            m = re.match(r'\s*(?:#![^\n]*\n)?\s*"""(.*?)"""', src, flags=re.S)
            if not m or len(m.group(1)) < 60:
                continue
            docs.append({
                "id": f"module:{rel}/{fn}", "type": "doc",
                "title": f"Module {rel}/{fn}",
                "text": f"Source module {rel}/{fn} — purpose and design:\n"
                        + m.group(1).strip()[:4000],
                "meta": {"ambs": [], "cases": [], "tls": [],
                         "kinds": ["docs", "module"], "session": 10**6},
            })
    return docs


def _version_history(root):
    """Release notes from git tags, if git is available."""
    try:
        import subprocess
        out = subprocess.run(["git", "tag", "-n40", "--sort=v:refname"],
                             cwd=root, capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return []
    if not out.strip():
        return []
    return [{
        "id": "docs:version-history", "type": "doc",
        "title": "Version history (git releases)",
        "text": "Version history of the programme, newest last:\n" + out[:6000],
        "meta": {"ambs": [], "cases": [], "tls": [],
                 "kinds": ["docs", "version"], "session": 10**6},
    }]


def programme_docs(root):
    docs = []
    docs += _md_sections(os.path.join(root, "docs", "knowledge.md"), "handbook")
    docs += _md_sections(os.path.join(root, "docs", "algorithms.md"),
                         "algorithms")
    docs += _md_sections(os.path.join(root, "README.md"), "readme")
    docs += _config_reference(root)
    docs += _module_docs(root)
    docs += _version_history(root)
    return docs
