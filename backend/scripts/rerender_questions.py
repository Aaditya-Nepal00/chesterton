"""Re-apply the current verifier/renderer to a PR's posted questions and fix the bot's text.

    uv run python scripts/rerender_questions.py --repo owner/demo --pr 1          # dry run
    uv run python scripts/rerender_questions.py --repo owner/demo --pr 1 --apply

Dry run prints a before/after diff of every stored question text and of the two bot
comments (the questions comment and the "Recorded … Still open" reply). --apply:
  - updates questions/<id>.questions[].text, pr_analyses/<id>.questions[].text and
    .comment_markdown, and decisions/<id>.question for answered questions
  - EDITS the bot's own comments on GitHub (App token, writable repo only)
No Gemini calls. Decision record files already merged in the repo are not changed.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import sys

from google.cloud import firestore

from app.analysis.evidence import build_evidence_index
from app.analysis.rerender import rerender_question_set
from app.config import get_settings
from app.github.fetch import fetch_pr_context
from app.github.markers import parse_markers
from app.github.write import GitHubWriter
from app.models.analysis import Decision
from app.models.records import DecisionRecord, QuestionSetDoc
from app.store.firestore import pr_doc_id, question_counters


def show_diff(label: str, old: str, new: str) -> None:
    print(f"\n--- {label}")
    if old == new:
        print("    (unchanged)")
        return
    for line in difflib.unified_diff(
        old.splitlines(), new.splitlines(), "before", "after", lineterm="", n=1
    ):
        print(f"    {line}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    s = get_settings()
    db = firestore.Client(project=s.google_cloud_project, database=s.firestore_database)
    doc_id = pr_doc_id(args.repo, args.pr)

    qs_snap = db.collection("questions").document(doc_id).get()
    if not qs_snap.exists:
        print(f"no questions doc for {args.repo}#{args.pr}")
        return 1
    qs = QuestionSetDoc.model_validate(qs_snap.to_dict())
    a_snap = db.collection("pr_analyses").document(doc_id).get()
    analysis = a_snap.to_dict() if a_snap.exists else {}
    decisions = [Decision.model_validate(d) for d in analysis.get("decisions") or []]
    rec_snaps = [
        d
        for d in db.collection("decisions").where("repo", "==", args.repo).stream()
        if d.to_dict().get("source_pr") == args.pr
    ]
    records = [DecisionRecord.model_validate(d.to_dict()) for d in rec_snaps]

    pr = await fetch_pr_context(args.repo, args.pr)
    evidence = build_evidence_index(pr)

    async with await GitHubWriter.for_repo(args.repo) as gh:
        q_comment = await gh.find_comment_with_marker(args.pr, "questions")
        r_comment = await gh.find_comment_with_marker(args.pr, "recorded")
        answer_id = None
        if r_comment:
            m = next((m for m in parse_markers(r_comment["body"]) if m["kind"] == "recorded"), {})
            answer_id = int(m["comment"]) if m.get("comment") else None
        out = rerender_question_set(qs, decisions, evidence, records, answer_id)

        print(f"== {args.repo}#{args.pr}  ({'APPLY' if args.apply else 'dry run'})")
        print(f"questions doc: {doc_id}; verifier notes: {out.notes or 'none'}")
        for qid, (old, new) in out.text_changes.items():
            show_diff(f"question {qid} text", old, new)
        if q_comment:
            show_diff(
                f"questions comment {q_comment['html_url']}",
                q_comment["body"],
                out.questions_comment or "",
            )
        if r_comment and out.reply_comment:
            show_diff(
                f"reply comment {r_comment['html_url']}", r_comment["body"], out.reply_comment
            )
        for r in records:
            new_q = next((q.text for q in out.question_set.questions if q.qid == r.qid), None)
            if new_q:
                show_diff(f"decisions/{r.id} stored question (Firestore only)", r.question, new_q)

        if not args.apply:
            print("\n(dry run: nothing changed)")
            return 0

        qref = db.collection("questions").document(doc_id)
        qref.update(
            {
                "questions": [q.model_dump(mode="json") for q in out.question_set.questions],
                **question_counters(out.question_set),
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
        )
        if a_snap.exists:
            new_by_decision = {q.decision_id: q.text for q in out.question_set.questions}
            aq = analysis.get("questions") or []
            for q in aq:
                q["text"] = new_by_decision.get(q.get("decision_id"), q.get("text"))
            a_snap.reference.update({"questions": aq, "comment_markdown": out.questions_comment})
        for snap, r in zip(rec_snaps, records, strict=True):
            new_q = next((q.text for q in out.question_set.questions if q.qid == r.qid), None)
            if new_q and new_q != r.question:
                snap.reference.update({"question": new_q})
        if q_comment and out.questions_comment and q_comment["body"] != out.questions_comment:
            await gh.update_issue_comment(q_comment["id"], out.questions_comment)
            print(f"edited {q_comment['html_url']}")
        if r_comment and out.reply_comment and r_comment["body"] != out.reply_comment:
            await gh.update_issue_comment(r_comment["id"], out.reply_comment)
            print(f"edited {r_comment['html_url']}")
        print("applied")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
