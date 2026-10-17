"""Deterministic checks on parsed answers before anything is recorded."""

from __future__ import annotations

import re

from app.analysis.evidence import normalize
from app.models.records import ParsedAnswer

MIN_ANSWER_QUOTE_WORDS = 3
_MARKER = re.compile(r"<!--.*?-->", re.DOTALL)


def clean_reply(body: str) -> str:
    """The author's own words: drop quoted lines ("> ...") and HTML comments, so a
    "quote" can't come from our question being quoted back."""
    body = _MARKER.sub("", body or "")
    return "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith(">")).strip()


def verify_answers(
    parsed: list[ParsedAnswer], open_qids: list[str], reply: str
) -> tuple[list[ParsedAnswer], list[str]]:
    usable: list[ParsedAnswer] = []
    notes: list[str] = []
    seen: set[str] = set()
    reply_norm = normalize(clean_reply(reply))
    for a in parsed:
        if a.qid not in open_qids:
            notes.append(f"answer for {a.qid} dropped: not an open question")
            continue
        if a.qid in seen:
            notes.append(f"duplicate answer for {a.qid} dropped")
            continue
        seen.add(a.qid)
        if not a.answered:
            notes.append(f"{a.qid} not answered; stays open")
            continue
        quote = normalize(a.reason_quote)
        if len(quote.split()) < MIN_ANSWER_QUOTE_WORDS or quote not in reply_norm:
            notes.append(f"reason_quote for {a.qid} not found verbatim in the reply; not recorded")
            continue
        if not a.decision.strip() or not a.title.strip():
            notes.append(f"answer for {a.qid} has no decision/title; not recorded")
            continue
        usable.append(a)
    return usable, notes
