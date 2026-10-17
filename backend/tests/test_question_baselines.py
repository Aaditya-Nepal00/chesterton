"""Questions must not invent comparison baselines (library/industry defaults)."""

import re
from datetime import UTC, datetime

from app.analysis.evidence import build_evidence_index
from app.analysis.verify import enforce_baselines, verify_questions
from app.models.analysis import Decision, Question
from app.models.pr import ChangedFile, PRContext
from app.render.pr_comment import render_comment

NEW_FILE_PATCH = """\
@@ -0,0 +1,4 @@
+WEBHOOK_MAX_RETRIES = 3
+WEBHOOK_TIMEOUT_SECONDS = 15
+WEBHOOK_BACKOFF_SECONDS = 2
+
"""
CHANGED_PATCH = """\
@@ -8,3 +8,3 @@
 log = logging.getLogger(__name__)
-WEBHOOK_MAX_RETRIES = 5
+WEBHOOK_MAX_RETRIES = 3
"""


def _pr(path: str, patch: str) -> PRContext:
    return PRContext(
        repo="acme/demo",
        number=1,
        title="t",
        url="https://github.com/acme/demo/pull/1",
        files=[ChangedFile(path=path, status="added", patch=patch)],
        fetched_at=datetime(2026, 10, 17, tzinfo=UTC),
    )


def _d(i: int, eid: str) -> Decision:
    return Decision(
        id=f"D{i}",
        summary="s",
        category="config_constant",
        diff_evidence=[eid],
        why_it_matters="x",
        confidence=0.9,
    )


BANNED = re.compile(r"instead of|rather than|default|usual|typical", re.IGNORECASE)


def test_new_file_questions_have_no_invented_baselines():
    ev = build_evidence_index(_pr("app/payments/webhook.py", NEW_FILE_PATCH))
    eid = "diff:app/payments/webhook.py#0"
    llm_questions = [
        Question(
            decision_id="D1",
            evidence_id=eid,
            snippet="WEBHOOK_MAX_RETRIES = 3",
            text="Why `WEBHOOK_MAX_RETRIES = 3` in `app/payments/webhook.py` instead of a "
            "default of 5? When would it be safe to change?",
        ),
        Question(
            decision_id="D2",
            evidence_id=eid,
            snippet="WEBHOOK_TIMEOUT_SECONDS = 15",
            text="Why `WEBHOOK_TIMEOUT_SECONDS = 15` in `app/payments/webhook.py` instead "
            "of the library default of 30s? When would it be safe to change?",
        ),
        Question(
            decision_id="D3",
            evidence_id=eid,
            snippet="WEBHOOK_BACKOFF_SECONDS = 2",
            text="Why keep the usual default backoff `WEBHOOK_BACKOFF_SECONDS = 2`? "
            "When would it be safe to change?",
        ),
    ]
    decisions = [_d(1, eid), _d(2, eid), _d(3, eid)]
    rendered, notes = verify_questions(
        llm_questions, ["D1", "D2", "D3"], decisions, ev, "https://github.com/acme/demo/pull/1"
    )
    assert len(rendered) == 3 and len(notes) == 3
    for q in rendered:
        assert not BANNED.search(q.text), q.text
        assert q.text.endswith("When would it be safe to change?")
    assert rendered[0].text == (
        "Why `WEBHOOK_MAX_RETRIES = 3` in `app/payments/webhook.py`? "
        "When would it be safe to change?"
    )
    md = render_comment(rendered, pr=1, run_id="r")
    assert not BANNED.search(md)
    assert "```" not in md  # every question quotes its whole line: no code blocks


def test_previous_value_from_a_removed_line_is_kept():
    ev = build_evidence_index(_pr("app/payments/webhook.py", CHANGED_PATCH))
    entry = ev["diff:app/payments/webhook.py#0"]
    text = "Why `WEBHOOK_MAX_RETRIES = 3` instead of 5? When would it be safe to raise it?"
    assert enforce_baselines(text, entry, "WEBHOOK_MAX_RETRIES = 3") == (text, False)
    invented = "Why `WEBHOOK_MAX_RETRIES = 3` instead of 10? When would it be safe to raise it?"
    out, changed = enforce_baselines(invented, entry, "WEBHOOK_MAX_RETRIES = 3")
    assert changed and out == "Why `WEBHOOK_MAX_RETRIES = 3`? When would it be safe to raise it?"


def test_code_block_kept_when_question_does_not_quote_the_line():
    ev = build_evidence_index(_pr("app/payments/webhook.py", NEW_FILE_PATCH))
    eid = "diff:app/payments/webhook.py#0"
    q = Question(
        decision_id="D1",
        evidence_id=eid,
        snippet="WEBHOOK_MAX_RETRIES = 3",
        text="Why 3 retries? When would it be safe to change?",
    )
    rendered, _ = verify_questions([q], ["D1"], [_d(1, eid)], ev, "https://x/pull/1")
    md = render_comment(rendered, pr=1, run_id="r")
    assert "```python\n   WEBHOOK_MAX_RETRIES = 3\n   ```" in md
