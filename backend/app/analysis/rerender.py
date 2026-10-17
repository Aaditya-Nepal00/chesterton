"""Re-apply the current question verifier/renderer to an already-posted question set.

Pure functions (no I/O) so the rerender script can show a diff before changing
Firestore or editing the bot's GitHub comments.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.analysis.verify import verify_questions
from app.models.analysis import Decision, Question
from app.models.records import DecisionRecord, QuestionSetDoc
from app.render import decision_record as dr
from app.render.pr_comment import render_comment


@dataclass
class Rerendered:
    question_set: QuestionSetDoc
    text_changes: dict[str, tuple[str, str]]  # qid -> (old, new)
    questions_comment: str | None
    reply_comment: str | None
    notes: list[str] = field(default_factory=list)


def rerender_question_set(
    qs: QuestionSetDoc,
    decisions: list[Decision],
    evidence: dict[str, dict],
    records: list[DecisionRecord],
    answer_comment_id: int | None,
) -> Rerendered:
    by_decision = {d.id: d for d in decisions}
    # Decisions the analysis doc no longer has still get a minimal stand-in, so a
    # question is never dropped just because the analysis was re-run.
    stand_ins = [
        Decision(
            id=q.decision_id,
            summary=q.decision_summary or "",
            category="other",
            diff_evidence=[q.evidence_id],
            why_it_matters="",
            confidence=1.0,
        )
        for q in qs.questions
        if q.decision_id not in by_decision
    ]
    rendered, notes = verify_questions(
        [
            Question(
                decision_id=q.decision_id,
                text=q.text,
                evidence_id=q.evidence_id,
                snippet=q.snippet or "",
            )
            for q in qs.questions
        ],
        [q.decision_id for q in qs.questions],
        decisions + stand_ins,
        evidence,
        qs.pr_url,
    )
    new_text = {r.decision_id: r.text for r in rendered}
    updated = qs.model_copy(deep=True)
    changes: dict[str, tuple[str, str]] = {}
    for q in updated.questions:
        nt = new_text.get(q.decision_id)
        if nt and nt != q.text:
            changes[q.qid] = (q.text, nt)
            q.text = nt
    questions_comment = render_comment(rendered, pr=qs.pr, run_id=qs.run_id)
    reply = None
    if records and answer_comment_id is not None:
        recs = sorted(records, key=lambda r: r.number)
        answered = {r.qid for r in recs}
        still_open = [q for q in updated.questions if q.status == "open" and q.qid not in answered]
        reply = dr.reply_text(
            recs,
            recs[0].record_pr_number,
            still_open,
            pr=qs.pr,
            answer_comment_id=answer_comment_id,
        )
    return Rerendered(updated, changes, questions_comment, reply, notes)
