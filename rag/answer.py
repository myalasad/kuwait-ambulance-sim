"""Grounded answering with tiered models and an offline fallback.

Cost/accuracy design (chosen deliberately):
* **Haiku 4.5** answers retrieval-grounded Q&A — its sweet spot, ~$0.004
  per question at typical sizes;
* it ESCALATES to **Sonnet 5** when the job is genuinely harder: report
  drafting, or a first answer that came back without citations;
* with no API credentials at all, the copilot still works in
  **extractive mode** — it returns the retrieved records verbatim with
  their ids, clearly labelled.  Nothing in the product depends on a key.

Grounding rules are absolute: the model answers ONLY from the retrieved
records, cites [doc ids] inline, and says so when the record doesn't
contain the answer.  This copilot is read-only by construction — it has no
tools and no path to the signal controls.
"""
import re

HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-5"

SYSTEM = (
    "You are the Operations Copilot for the Kuwait ambulance green-wave "
    "traffic-signal system. You answer questions from operators and "
    "reviewers using ONLY the retrieved records provided in the user "
    "message. Rules, all absolute:\n"
    "1. Every factual claim must come from the provided records. Cite the "
    "source id in square brackets inline, e.g. [case:P-012] or "
    "[protocol:...], immediately after the claim it supports.\n"
    "2. If the records do not contain the answer, say exactly that — "
    "never guess, never use outside knowledge about this deployment.\n"
    "3. Times are simulation seconds unless a Kuwait clock time appears in "
    "the record. Quote numbers exactly as recorded.\n"
    "4. You are a record assistant, not a decision maker: never instruct "
    "anyone to change signals; direct control questions to the operator "
    "workflow described in the protocol records.\n"
    "5. Be concise and plain-spoken; the reader may not be an engineer. "
    "Answer in the language of the question (English or Arabic)."
)

_REPORT_HINT = re.compile(
    r"\b(report|summar|draft|after.?action|overview|review of|write.?up)\b",
    re.I)


def _context_block(docs):
    parts = []
    for d in docs:
        parts.append(f"<record id=\"{d['id']}\" title=\"{d['title']}\">\n"
                     f"{d['text'][:3500]}\n</record>")
    return "\n\n".join(parts)


def _extract_text(response):
    return "".join(b.text for b in response.content
                   if getattr(b, "type", "") == "text").strip()


def answer(question, docs, client=None):
    """Returns {answer, mode, model, sources:[ids]}."""
    sources = [d["id"] for d in docs]
    if not docs:
        return {"answer": "The record contains nothing matching that "
                          "question — try naming an ambulance (AMB_1), a "
                          "case (P-012), a junction, or an event type.",
                "mode": "no-retrieval", "model": None, "sources": []}

    if client is None:
        try:
            import anthropic
            client = anthropic.Anthropic()
        except Exception:
            client = False
    if client is False or client is None:
        return _extractive(question, docs, "no API credentials configured")

    user = (f"Retrieved records:\n\n{_context_block(docs)}\n\n"
            f"Question: {question}")
    model = SONNET if _REPORT_HINT.search(question) else HAIKU
    try:
        text, used = _ask(client, model, user)
        if used == HAIKU and "[" not in text:
            # answered without citations -> escalate once
            text, used = _ask(client, SONNET, user)
        return {"answer": text, "mode": "grounded", "model": used,
                "sources": sources}
    except Exception as exc:
        return _extractive(question, docs, f"API unavailable ({exc})")


def _ask(client, model, user):
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=[{"type": "text", "text": SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    return _extract_text(response), model


def _extractive(question, docs, reason):
    """Offline mode: the retrieved records themselves, clearly labelled."""
    lines = [f"(Extractive mode — {reason}. Showing the matching records "
             f"verbatim; add an ANTHROPIC_API_KEY for synthesized answers.)"]
    for d in docs[:4]:
        snippet = d["text"][:600]
        lines.append(f"\n--- [{d['id']}] {d['title']} ---\n{snippet}")
    return {"answer": "\n".join(lines), "mode": "extractive",
            "model": None, "sources": [d["id"] for d in docs[:4]]}
