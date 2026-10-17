"""Deterministic verification of LLM output against the evidence index.

Nothing the models claim survives unless the cited evidence exists and, for
rationale, actually contains the quoted text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.analysis.evidence import (
    default_snippet,
    diff_url,
    is_diff_id,
    locate_snippet,
    normalize,
    split_hunks,
    strip_diff_markers,
)
from app.models.analysis import Decision, Question, RationaleCheck, RenderedQuestion

_BASELINE_CLAUSE = re.compile(
    r"\s*,?\s*\b(?:instead of|rather than|compared to|compared with|versus|vs\.?)\b"
    r"(?P<what>[^?;]*?)(?=\?|;|$)",
    re.IGNORECASE,
)
_CLAIM_WORDS = re.compile(
    r"\b(default|defaults|typical|typically|usual|usually|standard|industry|"
    r"best practice|recommended|common)\b",
    re.IGNORECASE,
)
_FILLER = {"the", "a", "an", "of", "previous", "old", "prior", "value", "values", "s", "to"}


def _changed_old_text(entry: dict) -> str:
    hunks = split_hunks(entry.get("text"))
    return "\n".join(t for h in hunks for k, _, t in h.lines if k == "-")


def _clause_supported(what: str, old_text: str, evidence_text: str) -> bool:
    """An "instead of X" clause survives only if X is visible on a removed line, and
    any default/typical-style claim word in it appears verbatim in the evidence."""
    if not old_text.strip():
        return False
    for word in _CLAIM_WORDS.findall(what):
        if word.lower() not in evidence_text.lower():
            return False
    tokens = [t for t in re.findall(r"[\w.]+", what.replace("`", "")) if t.lower() not in _FILLER]
    return bool(tokens) and all(
        re.search(rf"(?<![\w.]){re.escape(t)}(?![\w])", old_text) for t in tokens
    )


def enforce_baselines(text: str, entry: dict, snippet: str) -> tuple[str, bool]:
    """Remove invented comparison baselines ("instead of the library default of 30s").

    Returns (text, changed). If a default/typical claim remains that the evidence
    doesn't contain, the question is replaced by a plain one built from the snippet.
    """
    old_text = _changed_old_text(entry)
    evidence_text = entry.get("text") or ""
    changed = False

    def repl(m: re.Match) -> str:
        nonlocal changed
        if _clause_supported(m.group("what"), old_text, evidence_text):
            return m.group(0)
        changed = True
        return ""

    out = _BASELINE_CLAUSE.sub(repl, text)
    leftover = [w for w in _CLAIM_WORDS.findall(out) if w.lower() not in evidence_text.lower()]
    if leftover:
        first = next((ln.strip() for ln in snippet.splitlines() if ln.strip()), "")
        name = (entry.get("path") or "").rsplit("/", 1)[-1]
        subject = f"`{first}`" + (f" in `{name}`" if name else "")
        return f"Why {subject}? When would it be safe to change?", True
    out = re.sub(r"\s+\?", "?", re.sub(r"\s{2,}", " ", out)).strip()
    return out, changed


MIN_QUOTE_WORDS = 4  # a "reason" shorter than this ("fix", "tweak retries") can't be one
MIN_SELF_EVIDENT_NOTE_WORDS = 3
MAX_QUESTIONS = 3


@dataclass
class VerifyOutcome:
    decisions: list[Decision]
    rationale: list[RationaleCheck]
    gaps: list[str]  # decision ids, missing first, then partial
    notes: list[str] = field(default_factory=list)


def verify_decisions(
    decisions: list[Decision], evidence: dict[str, dict]
) -> tuple[list[Decision], list[str]]:
    kept: list[Decision] = []
    notes: list[str] = []
    for d in decisions:
        valid = [e for e in d.diff_evidence if is_diff_id(e) and e in evidence]
        bad = [e for e in d.diff_evidence if e not in valid]
        if not valid:
            notes.append(f"{d.id} dropped: cited diff evidence {bad or '[]'} does not exist")
            continue
        if bad:
            notes.append(f"{d.id}: removed unknown evidence ids {bad}")
        kept.append(d.model_copy(update={"diff_evidence": valid}))
    return kept, notes


def _quote_supported(check: RationaleCheck, evidence: dict[str, dict]) -> bool:
    quote = normalize(check.reason_quote or "")
    if len(quote.split()) < MIN_QUOTE_WORDS:
        return False
    return any(
        eid in evidence and quote in normalize(evidence[eid]["text"]) for eid in check.evidence_ids
    )


def verify_analysis(
    decisions: list[Decision], checks: list[RationaleCheck], evidence: dict[str, dict]
) -> VerifyOutcome:
    decisions, notes = verify_decisions(decisions, evidence)
    by_id = {d.id: d for d in decisions}

    rationale: dict[str, RationaleCheck] = {}
    for c in checks:
        if c.decision_id not in by_id:
            notes.append(f"rationale for unknown decision {c.decision_id} dropped")
            continue
        if c.decision_id in rationale:
            notes.append(f"duplicate rationale for {c.decision_id} dropped")
            continue
        if c.status in ("found", "partial"):
            if _quote_supported(c, evidence):
                c = c.model_copy(
                    update={"evidence_ids": [e for e in c.evidence_ids if e in evidence]}
                )
            else:
                notes.append(
                    f"rationale for {c.decision_id} not found in cited evidence "
                    f"{c.evidence_ids}; downgraded {c.status} -> missing"
                )
                c = c.model_copy(
                    update={
                        "status": "missing",
                        "reason_quote": None,
                        "evidence_ids": [],
                        "note": (c.note + " [verifier: quote not found in cited evidence]").strip(),
                    }
                )
        elif c.status == "self_evident":
            # No quote needed, but the model must say why it's common knowledge.
            if len(c.note.split()) < MIN_SELF_EVIDENT_NOTE_WORDS:
                notes.append(
                    f"self_evident for {c.decision_id} has no explanatory note; "
                    "downgraded -> missing"
                )
                c = c.model_copy(
                    update={"status": "missing", "reason_quote": None, "evidence_ids": []}
                )
            elif c.reason_quote and not _quote_supported(c, evidence):
                c = c.model_copy(update={"reason_quote": None, "evidence_ids": []})
        else:
            c = c.model_copy(update={"reason_quote": None, "evidence_ids": []})
        rationale[c.decision_id] = c

    for d in decisions:
        if d.id not in rationale:
            notes.append(f"no rationale check returned for {d.id}; treated as missing")
            rationale[d.id] = RationaleCheck(
                decision_id=d.id, status="missing", note="no rationale check returned"
            )

    ordered = [rationale[d.id] for d in decisions]
    gaps = [r.decision_id for r in ordered if r.status == "missing"] + [
        r.decision_id for r in ordered if r.status == "partial"
    ]
    return VerifyOutcome(decisions=decisions, rationale=ordered, gaps=gaps, notes=notes)


def verify_questions(
    questions: list[Question],
    gap_ids: list[str],
    decisions: list[Decision],
    evidence: dict[str, dict],
    pr_url: str,
) -> tuple[list[RenderedQuestion], list[str]]:
    notes: list[str] = []
    by_id = {d.id: d for d in decisions}
    out: list[RenderedQuestion] = []
    seen: set[str] = set()
    for q in questions:
        if q.decision_id not in gap_ids or q.decision_id not in by_id:
            notes.append(f"question for {q.decision_id} dropped: not a verified gap")
            continue
        if q.decision_id in seen:
            notes.append(f"extra question for {q.decision_id} dropped")
            continue
        if len(out) >= MAX_QUESTIONS:
            notes.append(f"question for {q.decision_id} dropped: over {MAX_QUESTIONS} questions")
            continue
        decision = by_id[q.decision_id]
        eid = q.evidence_id
        if not (is_diff_id(eid) and eid in evidence):
            fallback = decision.diff_evidence[0]
            notes.append(f"question for {q.decision_id}: evidence {eid!r} unknown; used {fallback}")
            eid = fallback
        entry = evidence[eid]
        snippet = strip_diff_markers(q.snippet or "").strip("\n")
        line = locate_snippet(entry, snippet) if snippet.strip() else None
        if line is None:
            snippet, line = default_snippet(entry)
            notes.append(
                f"question for {q.decision_id}: snippet not found in {eid}; "
                "used the hunk's changed lines"
            )
        text, rewritten = enforce_baselines(q.text.strip(), entry, snippet)
        if rewritten:
            notes.append(
                f"question for {q.decision_id}: removed a comparison not visible in the diff"
            )
        out.append(
            RenderedQuestion(
                decision_id=q.decision_id,
                text=text,
                evidence_id=eid,
                snippet=snippet,
                path=entry.get("path"),
                line=line,
                url=diff_url(pr_url, entry["path"], line)
                if entry.get("path")
                else entry.get("url"),
            )
        )
        seen.add(q.decision_id)
    return out, notes
