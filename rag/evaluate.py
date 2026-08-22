#!/usr/bin/env python3
"""Eval harness: proves the cheap tier answers accurately BEFORE trusting it.

Builds question/expected pairs deterministically from the real operations
log (arrivals, reroutes, exemptions, referred decisions, early greens),
then scores each mode on two checks per question:
* GROUNDING — the answer cites the expected source document;
* CONTENT — the expected key phrase appears in the answer.

Run: .venv/bin/python rag/evaluate.py [--modes extractive haiku sonnet]
Without API credentials only `extractive` runs (retrieval-quality check).
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rag.ingest import build_corpus  # noqa: E402
from rag.index import Index  # noqa: E402
from rag import answer as answer_mod  # noqa: E402


def build_evalset(docs, limit=24):
    """Derive Q/A pairs from case documents — ground truth by construction."""
    pairs = []
    for d in docs:
        # current-session A-cases only: old sessions share case numbers, so
        # "case A-001" is ambiguous across sessions and the retriever's
        # recency preference (correctly) picks the newest
        if (d["type"] != "case" or not d["id"].startswith("case:A-")
                or "#s" in d["id"]):
            continue
        case = d["id"].split(":")[1]
        amb = d["meta"]["ambs"][0] if d["meta"]["ambs"] else None
        if not amb:
            continue
        m = re.search(r"ARRIVED at ([^\n]+?) and was removed", d["text"])
        if m:
            pairs.append({
                "q": f"Where did {amb} (case {case}) arrive, and how long "
                     f"did the run take?",
                "expect_source": d["id"],
                "expect_text": m.group(1).split(" and")[0].strip()[:25],
            })
        m = re.search(r"REROUTED to the nearest hospital by travel time: "
                      r"([^(]+)", d["text"])
        if m:
            pairs.append({
                "q": f"Which hospital was {amb} rerouted to after the scene?",
                "expect_source": d["id"],
                "expect_text": m.group(1).strip()[:20],
            })
        m = re.search(r"at (\d+) km/h in a (\d+) km/h zone", d["text"])
        if m:
            pairs.append({
                "q": f"Was {amb} fined for speeding? What speed was recorded?",
                "expect_source": d["id"],
                "expect_text": m.group(1),
            })
        if len(pairs) >= limit:
            break
    for d in docs:
        if d["type"] == "protocol" and "unable to decide" in d["text"].lower():
            pairs.append({
                "q": "What happens when two ambulances tie for the same "
                     "junction and the system cannot decide?",
                "expect_source": d["id"],
                "expect_text": "operator",
            })
            break
    return pairs[:limit]


def run_mode(mode, pairs, index):
    import anthropic
    client = None
    if mode == "extractive":
        client = False
    elif mode == "haiku":
        answer_mod_orig = answer_mod.SONNET
        answer_mod.SONNET = answer_mod.HAIKU     # forbid escalation
        client = anthropic.Anthropic()
    elif mode == "sonnet":
        answer_mod.HAIKU = answer_mod.SONNET     # force sonnet
        client = anthropic.Anthropic()

    grounded = content = 0
    for p in pairs:
        docs = index.search(p["q"], k=6)
        res = answer_mod.answer(p["q"], docs, client=client)
        text = res["answer"]
        srcs = res["sources"] or []
        hit = [sid for sid in srcs if sid.startswith(p["expect_source"])]
        if hit and (mode == "extractive" or any(h in text for h in hit)):
            grounded += 1
        wants = p["expect_text"]
        if isinstance(wants, str):
            wants = [wants]
        if all(w.lower() in text.lower() for w in wants):
            content += 1
    return grounded, content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", nargs="+", default=["extractive"],
                    choices=["extractive", "haiku", "sonnet"])
    ap.add_argument("--set", default="records",
                    choices=["records", "knowledge"],
                    help="records: Q/A derived from the operations log; "
                         "knowledge: curated questions about the programme")
    args = ap.parse_args()

    docs = build_corpus(ROOT)
    index = Index(docs)
    if args.set == "knowledge":
        kpath = os.path.join(ROOT, "rag", "knowledge_eval.json")
        pairs = [{"q": p["q"], "expect_source": p["source"],
                  "expect_text": p["expect"]}
                 for p in json.load(open(kpath, encoding="utf-8"))]
    else:
        pairs = build_evalset(docs)
    if not pairs:
        sys.exit("No eval pairs — run the simulation first to produce "
                 "operations data.")
    print(f"corpus: {len(docs)} documents | eval set: {len(pairs)} questions")
    print(f"{'mode':<12} {'grounding':>10} {'content':>10}")
    for mode in args.modes:
        # re-import to reset any model overrides between modes
        import importlib
        importlib.reload(answer_mod)
        g, c = run_mode(mode, pairs, index)
        n = len(pairs)
        print(f"{mode:<12} {g:>7}/{n:<3} {c:>7}/{n:<3}")


if __name__ == "__main__":
    main()
