"""Hybrid retrieval: metadata filters first, BM25 ranking second.

Half the operator's questions name exact entities (AMB_2, case P-012, a
junction id, an event kind).  Those are answered by FILTERS — a WHERE
clause beats embeddings for exact identifiers.  BM25 then ranks the
remaining candidates by the free-text part of the question.  Pure Python,
no dependencies; for this corpus size (hundreds to a few thousand
documents) it is instant, free, and — per the eval harness — accurate.
Optional dense embeddings can be layered in later without changing the
interface.
"""
import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9_#-]+")

KIND_HINTS = {
    "exempt": "enforcement", "exemption": "enforcement", "fine": "enforcement",
    "citation": "enforcement", "speed": "enforcement",
    "camera": "camera", "detected": "camera",
    "decision": "decision_referred", "referred": "decision_referred",
    "supervisor": "decision_made", "arbitration": "arbitration",
    "conflict": "decision_referred", "tie": "decision_referred",
    "reroute": "reroute", "hospital": "reroute", "loading": "reroute",
    "arrived": "arrival", "arrival": "arrival",
    "teleport": "teleport", "lights": "lights",
    "early": "actuation", "lone": "actuation", "actuation": "actuation",
    "hold": "hold_limit", "starv": "hold_limit",
    "enabled": "preempt_start", "corridor": "preempt_start",
    "restored": "restore", "normal": "restore",
    "analysis": "analysis", "timer": "analysis", "saved": "analysis",
    "protocol": "protocol", "rule": "protocol", "policy": "protocol",
    "congest": "markov", "jam": "markov", "forecast": "markov",
    "markov": "markov", "predict": "markov",
}


DOC_WORDS = {
    "what", "how", "why", "explain", "explains", "mean", "means", "does",
    "describe", "install", "setup", "set", "run", "start", "page", "tab",
    "config", "configuration", "parameter", "parameters", "setting",
    "version", "release", "history", "who", "built", "architecture",
    "module", "algorithm", "dijkstra", "markov", "dtmc", "ctmc", "dmm",
    "matrix", "stack", "technology", "real", "calibrated", "provenance",
    "limitation", "limitations", "scope", "copilot", "work", "works",
    "purpose", "programme", "program", "system", "project", "feature",
    "features", "hospital", "hospitals", "area", "areas", "governorate",
    "scenario", "model", "exemption", "fine", "early", "green", "corridor",
    "preemption", "arbitration", "pages", "driver", "navigation", "results",
    "measured", "cost", "key", "api",
}


STOPWORDS = {
    "what", "does", "do", "did", "the", "a", "an", "is", "are", "was", "were",
    "how", "why", "of", "to", "in", "and", "or", "it", "this", "that", "for",
    "with", "on", "at", "by", "be", "can", "could", "would", "should", "i",
    "me", "my", "we", "you", "your", "its", "there", "which", "who", "when",
    "where", "any", "all", "from", "into", "about", "explain", "tell", "show",
    "please", "mean", "means", "s", "t",
}


def tokenize(text):
    return _TOKEN.findall(text.lower())


def query_tokens(text):
    """Question words are RARE in a corpus of terse records, so BM25 would
    weight them absurdly; drop them and keep the content terms."""
    return [t for t in tokenize(text) if t not in STOPWORDS]


class Index:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.docs = docs
        self.k1 = k1
        self.b = b
        self.tf = []
        self.df = Counter()
        self.lens = []
        for d in docs:
            toks = tokenize(d["text"])
            counts = Counter(toks)
            for t in tokenize(d["title"]):      # titles count triple
                counts[t] += 3
                toks.append(t)
            self.tf.append(counts)
            self.lens.append(len(toks))
            for t in counts:
                self.df[t] += 1
        self.avg_len = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        self.n = len(docs)
        self._max_session = max((d["meta"].get("session", 0)
                                 for d in docs if d["type"] in ("case", "ops")),
                                default=0)

    # ---------------------------------------------------------------- query

    def parse_query(self, question):
        """Extract exact entities and kind hints from the question."""
        q = question.strip()
        want = {
            "ambs": set(re.findall(r"AMB[_ ]?(\d+)", q, re.I)),
            "cases": set(re.findall(r"\b([PAD])-?(\d{1,3})\b", q)),
            "tls": set(re.findall(r"\bJ-\d{3}\b|(?:GS_)?cluster_[\w#]+|\b\d{6,}\b",
                                  q, flags=re.I)),
            "kinds": set(),
        }
        want["ambs"] = {f"AMB_{n}" for n in want["ambs"]}
        want["cases"] = {f"{k}-{int(n):03d}" for k, n in want["cases"]}
        toks = tokenize(q)
        for tok in toks:
            for stem, kind in KIND_HINTS.items():
                if tok.startswith(stem):
                    want["kinds"].add(kind)
        # a question about the programme itself (no named entity, explanatory
        # wording) prefers the documentation corpus
        if (not want["ambs"] and not want["cases"] and not want["tls"]
                and len(set(toks) & DOC_WORDS) >= 2):
            want["kinds"].add("docs")
        return want

    def _bm25(self, q_tokens, i):
        score = 0.0
        counts = self.tf[i]
        for t in q_tokens:
            if t not in counts:
                continue
            idf = math.log(1 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5))
            f = counts[t]
            score += idf * f * (self.k1 + 1) / (
                f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg_len))
        return score

    def search(self, question, k=6):
        want = self.parse_query(question)
        q_tokens = query_tokens(question)
        # identifiers such as camera_range_m or run_live.py are exact keys
        exact = {t for t in q_tokens if "_" in t or t.endswith(".py")}
        scored = []
        for i, d in enumerate(self.docs):
            meta = d["meta"]
            # hard filters: every named entity must appear in the doc
            if want["ambs"] and not want["ambs"] & set(meta["ambs"]):
                continue
            if want["cases"] and not want["cases"] & set(meta["cases"]):
                continue
            if want["tls"] and not ({t.upper() for t in want["tls"]}
                                    & {t.upper() for t in meta["tls"]}):
                continue
            score = self._bm25(q_tokens, i)
            if exact and exact & set(self.tf[i]):
                score += 8.0                      # exact identifier match
            # soft boost when the doc carries a hinted event kind
            if want["kinds"] & set(meta.get("kinds", [])):
                score += 2.5
            # mild recency boost: current-session records outrank old runs;
            # documentation is timeless and counts as current
            sess = meta.get("session", 0)
            score += 0.6 if sess >= 10**6 else 0.6 * sess / max(self._max_session, 1)
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)
        return [dict(self.docs[i], score=round(s, 2)) for s, i in scored[:k]]
