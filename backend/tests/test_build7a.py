"""Rerender, cleanup selection, counters, evidence URLs, quota migration (pure functions)."""

import importlib.util
from pathlib import Path

from app.analysis.evidence import build_evidence_index, evidence_urls
from app.analysis.rerender import rerender_question_set
from app.models.analysis import Decision
from app.models.records import DecisionRecord, QuestionItem, QuestionSetDoc
from app.store.cleanup import select_runs_to_delete
from app.store.firestore import guard_counters, question_counters
from tests.fixtures import make_pr
from tests.test_question_baselines import NEW_FILE_PATCH, _pr


def _load_script(name: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- evidence URLs -------------------------------------------------------------


def test_every_evidence_id_gets_an_html_url():
    pr = make_pr()
    pr.comments[0].url = "https://github.com/acme/payments/pull/42#discussion_r9001"
    pr.comments[1].url = "https://github.com/acme/payments/pull/42#issuecomment-9002"
    pr.linked_issues[0].url = "https://github.com/acme/payments/issues/7"
    urls = evidence_urls(build_evidence_index(pr))
    assert urls["pr:body"] == pr.url
    assert urls["commit:abcdef1"] == f"{pr.url}/commits/abcdef1234567"
    assert urls["comment:9001"].endswith("#discussion_r9001")  # review comment
    assert urls["comment:9002"].endswith("#issuecomment-9002")  # issue comment
    assert urls["issue:7"] == "https://github.com/acme/payments/issues/7"
    assert "/files#diff-" in urls["diff:payments/webhook.py#0"] and urls[
        "diff:payments/webhook.py#0"
    ].endswith("R10")


# --- counters ---------------------------------------------------------------


def _qs(statuses=("open", "answered", "open")) -> QuestionSetDoc:
    eid = "diff:app/payments/webhook.py#0"
    snippets = [
        "WEBHOOK_MAX_RETRIES = 3",
        "WEBHOOK_TIMEOUT_SECONDS = 15",
        "WEBHOOK_BACKOFF_SECONDS = 2",
    ]
    texts = [
        "Why `WEBHOOK_MAX_RETRIES = 3` in `app/payments/webhook.py` instead of a default of 5? When would it be safe to change?",
        "Why `WEBHOOK_TIMEOUT_SECONDS = 15` in `app/payments/webhook.py` instead of the library default of 30s? When would it be safe to change?",
        "Why `WEBHOOK_BACKOFF_SECONDS = 2` in `app/payments/webhook.py` instead of a default of 5s? When would it be safe to change?",
    ]
    return QuestionSetDoc(
        repo="acme/demo",
        pr=1,
        pr_url="https://github.com/acme/demo/pull/1",
        author="dana",
        run_id="r1",
        questions=[
            QuestionItem(
                qid=f"Q{i + 1}",
                decision_id=f"D{i + 1}",
                text=texts[i],
                evidence_id=eid,
                snippet=snippets[i],
                status=statuses[i],
                path="app/payments/webhook.py",
                line=1 + i,
            )
            for i in range(3)
        ],
    )


def test_counters():
    assert question_counters(_qs()) == {"n_questions": 3, "n_answered": 1}
    assert guard_counters({"items": [{"decision": "0001"}]}) == {"matched_count": 1}
    assert guard_counters({}) == {"matched_count": 0}


# --- rerender ---------------------------------------------------------------


def _record() -> DecisionRecord:
    return DecisionRecord(
        number=1,
        id="0001",
        slug="s",
        file_path="decisions/0001-s.md",
        title="Retry cap",
        repo="acme/demo",
        owner="dana",
        paths=[],
        symbols=[],
        source_pr=1,
        source_pr_url="https://github.com/acme/demo/pull/1",
        answer_url="u",
        answer_comment_id=99,
        recorded="2026-10-17",
        qid="Q1",
        question="old",
        decision="d",
        reason="r",
        reason_quote="q",
        record_pr_number=2,
    )


def test_rerender_strips_invented_baselines_and_rebuilds_both_comments():
    ev = build_evidence_index(_pr("app/payments/webhook.py", NEW_FILE_PATCH))
    decisions = [
        Decision(
            id=f"D{i}",
            summary="s",
            category="config_constant",
            diff_evidence=["diff:app/payments/webhook.py#0"],
            why_it_matters="x",
            confidence=0.9,
        )
        for i in (1, 2)
    ]  # D3 missing from the analysis
    out = rerender_question_set(_qs(), decisions, ev, [_record()], answer_comment_id=99)
    assert set(out.text_changes) == {"Q1", "Q2", "Q3"}
    for q in out.question_set.questions:
        assert "instead of" not in q.text and "default" not in q.text
    assert out.question_set.questions[1].status == "answered"  # statuses untouched
    assert "default" not in out.questions_comment and "```" not in out.questions_comment
    assert out.questions_comment.startswith("<!-- chesterton:questions pr=1 run_id=r1 -->")
    assert out.reply_comment.startswith("<!-- chesterton:recorded pr=1 comment=99 -->")
    assert "Recorded as `decisions/0001-s.md` in #2. Thanks!" in out.reply_comment
    assert "Q2:" not in out.reply_comment  # Q2 is answered in this fixture
    assert "- Q3: Why `WEBHOOK_BACKOFF_SECONDS = 2`" in out.reply_comment


# --- cleanup selection -------------------------------------------------------


def test_cleanup_selection_keeps_referenced_and_latest_successful():
    runs = [
        {"run_id": "20261017T080000Z-a", "repo": "o/r", "number": 5, "status": "no_gaps"},  # (a)
        {
            "run_id": "20261017T080100Z-b",
            "repo": "o/r",
            "number": 5,
            "status": "no_gaps",
        },  # (a) referenced
        {
            "run_id": "20261017T090000Z-c",
            "kind": "analyze",
            "repo": "o/d",
            "pr": 1,
            "status": "error",
        },
        {
            "run_id": "20261017T090100Z-d",
            "kind": "analyze",
            "repo": "o/d",
            "pr": 1,
            "status": "questions_posted",
        },
        {
            "run_id": "20261017T090200Z-e",
            "kind": "analyze",
            "repo": "o/d",
            "pr": 1,
            "status": "quota_paused",
        },
        {
            "run_id": "20261017T090300Z-f",
            "kind": "guard",
            "repo": "o/d",
            "pr": 3,
            "status": "matched",
        },
        {
            "run_id": "20261017T090400Z-g",
            "kind": "guard",
            "repo": "o/d",
            "pr": 3,
            "status": "matched",
        },
        {
            "run_id": "20261017T090500Z-h",
            "kind": "record",
            "repo": "o/d",
            "pr": 1,
            "status": "recorded",
        },
        {
            "run_id": "20261017T090600Z-i",
            "kind": "selftest",
            "repo": "chesterton/selftest",
            "pr": 0,
            "status": "done",
        },
    ]
    referenced = {"20261017T080100Z-b", "20261017T090400Z-g"}
    picked, kept = select_runs_to_delete(runs, referenced, ["20261017T999999Z-orphan"])
    got = {c.run_id: c.category for c in picked}
    assert (
        got
        == {
            "20261017T080000Z-a": "a",
            "20261017T090600Z-i": "b",
            "20261017T090000Z-c": "c",  # failed duplicate; the successful d is kept even though e is newer
            "20261017T090200Z-e": "c",
            "20261017T090300Z-f": "c",
            "20261017T999999Z-orphan": "d",
        }
    )
    assert "20261017T090100Z-d" not in got and "20261017T090500Z-h" not in got
    assert [rid for rid, _ in kept] == ["20261017T080100Z-b"]


# --- quota migration classification -----------------------------------------


def test_quota_migration_classifies_only_daily_quota_stops():
    mig = _load_script("migrate_quota_status")
    assert mig.classify({"summary": "AllModelsExhausted: all models in chain ['a']"})[0]
    assert mig.classify(
        {"llm_calls_detail": [{"error": "429: limit: 20 ... Please retry in 12h54m"}]}
    )[0]
    assert mig.classify(
        {"node_errors": [{"summary": "quotaId GenerateRequestsPerDayPerProjectPerModel"}]}
    )[0]
    assert not mig.classify({"summary": "ServerError: 503 UNAVAILABLE"})[0]
    assert not mig.classify({"llm_calls_detail": [{"error": "429 ... Please retry in 44.7s"}]})[0]
