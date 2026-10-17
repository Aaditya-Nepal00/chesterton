"""Backfill: prefilter, summary math, the budgeted loop, and the analyze_pr flags it uses.

Zero real Gemini calls: the full analyze_pr graph runs with a fake model that answers
per agent (by its output schema). Firestore is faked.
"""

import asyncio
import re

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from app import backfill as bf
from app.agents import model_chain as mc
from app.models.pr import ChangedFile
from app.store import firestore as store
from app.workflows import analyze_pr
from app.workflows.runner import RunOutcome
from tests.fixtures import make_pr
from tests.test_model_chain import DAILY, NOW, _quota_error

# --- a fake model that plays every analyze_pr agent ----------------------------

SENT: list[str] = []  # schema name of every request the fake received


def _answer(schema: str, text: str) -> str:
    if schema == "ScreenResult":
        return '{"consequential": true, "reasons": ["retry cap and dependency swap"]}'
    if schema == "DecisionList":
        return (
            '{"decisions": ['
            '{"id": "D1", "summary": "MAX_RETRIES lowered from 5 to 3", '
            '"category": "config_constant", "diff_evidence": ["diff:payments/webhook.py#0"], '
            '"why_it_matters": "retry storms", "confidence": 0.9},'
            '{"id": "D2", "summary": "requests replaced by httpx", "category": "dependency", '
            '"diff_evidence": ["diff:pyproject.toml#0"], "why_it_matters": "async client", '
            '"confidence": 0.8}]}'
        )
    if schema == "RationaleCheck":
        if "httpx" in text and "requests" in text and "MAX_RETRIES = 3" not in text:
            return (
                '{"decision_id": "D2", "status": "found", "reason_quote": "We moved to httpx '
                'because requests has no async client and we need one.", '
                '"evidence_ids": ["comment:9001"], "note": ""}'
            )
        return '{"decision_id": "D1", "status": "missing", "evidence_ids": [], "note": ""}'
    if schema == "QuestionSet":
        return '{"questions": []}'
    raise AssertionError(f"unexpected agent call with schema {schema}")


class AgentFake(BaseLlm):
    fail_daily: bool = False

    async def generate_content_async(self, llm_request, stream=False):
        schema = getattr(llm_request.config.response_schema, "__name__", "?")
        SENT.append(schema)
        if self.fail_daily and schema != "ScreenResult":
            raise _quota_error(*DAILY)
        text = " ".join(
            p.text or "" for c in llm_request.contents or [] for p in c.parts or [] if p
        )
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=_answer(schema, text))])
        )


@pytest.fixture
def fakes(monkeypatch):
    SENT.clear()
    saved: dict[str, object] = {}

    async def fetch(repo, number, refresh=False):
        return make_pr(repo=repo, number=number)

    async def save_analysis(result):
        saved["analysis"] = result
        return f"pr_analyses/{store.pr_doc_id(result.repo, result.number)}"

    async def noop(*a, **k):
        return "runs/x"

    monkeypatch.setattr(analyze_pr, "fetch_pr_context", fetch)
    monkeypatch.setattr(store, "save_analysis", save_analysis)
    for name in ("start_run", "write_event", "finish_run"):
        monkeypatch.setattr(store, name, noop)
    mc.set_quota_registry(mc.QuotaRegistry(mc.MemoryQuotaBackend(), clock=lambda: NOW))
    monkeypatch.setattr(mc, "_default_factory", lambda name: AgentFake(model=name))
    yield saved
    mc.set_quota_registry(None)
    mc.set_call_budget(None)


def _run(**kw) -> RunOutcome:
    return asyncio.run(analyze_pr.run("acme/payments", 42, persist=True, trigger="test", **kw))


# --- analyze_pr flags ------------------------------------------------------------


def test_skip_questions_measures_without_the_questions_agent(fakes):
    out = _run(skip_questions=True)
    assert out.status == "gaps_found", out.summary
    assert SENT == ["ScreenResult", "DecisionList", "RationaleCheck", "RationaleCheck"]
    result = fakes["analysis"]
    assert result.questions_skipped and result.questions == [] and not result.comment_markdown
    assert sorted(r.status for r in result.rationale) == ["found", "missing"]


def test_prescreened_skips_the_screen_call(fakes):
    out = _run(skip_questions=True, prescreened={"consequential": True, "reasons": ["saved"]})
    assert out.status == "gaps_found"
    assert "ScreenResult" not in SENT and SENT[0] == "DecisionList"
    assert out.state["screen"]["reasons"] == ["saved"]


def test_max_rationale_caps_checks_and_zero_stops_before_extract(fakes):
    out = _run(skip_questions=True, max_rationale=1)
    assert SENT.count("RationaleCheck") == 1 and out.state["rationale_capped"] == ["D2"]
    SENT.clear()
    out = _run(skip_questions=True, max_rationale=0)
    assert out.status == "budget_stopped" and SENT == ["ScreenResult"]
    assert out.state["screen"]["consequential"] and "analysis" in fakes  # from the 1st run


def test_call_budget_is_a_hard_cap(fakes):
    mc.set_call_budget(mc.CallBudget(2))
    out = _run(skip_questions=True)  # screen + extract fit; the first rationale call doesn't
    assert out.status == "budget_stopped" and not out.paused
    assert SENT == ["ScreenResult", "DecisionList"]
    assert mc.get_call_budget().used == 2


def test_daily_quota_mid_run_pauses_and_keeps_the_screen(fakes, monkeypatch):
    monkeypatch.setattr(mc, "_default_factory", lambda n: AgentFake(model=n, fail_daily=True))
    out = _run(skip_questions=True)
    assert out.status == "quota_paused"
    assert out.state["screen"]["consequential"]


def test_live_runs_still_ask_questions_by_default(fakes):
    out = _run()
    assert out.ok and SENT[-1] == "QuestionSet" and not fakes["analysis"].questions_skipped


# --- prefilter -------------------------------------------------------------------


def _f(*paths):
    return [ChangedFile(path=p, status="modified", additions=1) for p in paths]


@pytest.mark.parametrize(
    "pr,files,kind",
    [
        ({"author": "dependabot[bot]", "author_is_bot": True, "title": "x"}, _f("a.py"), "bot"),
        ({"title": "Version 0.28.2"}, _f("pyproject.toml"), "release"),
        ({"title": "Fix typo"}, _f("docs/index.md", "README.md"), "docs-only"),
        ({"title": "More tests"}, _f("tests/test_x.py", "docs/a.md"), "tests-only"),
        ({"title": "Tune timeout"}, _f("httpx/_config.py", "tests/test_config.py"), None),
    ],
)
def test_prefilter(pr, files, kind):
    assert bf.prefilter(pr, files)[0] == kind


# --- summary math ------------------------------------------------------------------


def _sample(*numbers):
    return [
        {"number": n, "title": f"PR {n}", "head_sha": f"sha{n}", "merged_at": f"2026-10-{n:02d}"}
        for n in numbers
    ]


def _item(n, status, counts=None, screen=None, calls=0, sha=None, kind=None, models=()):
    return {
        "pr": n,
        "head_sha": sha or f"sha{n}",
        "status": status,
        "skip_kind": kind,
        "screen": screen,
        "counts": {**dict.fromkeys(bf.STATUSES, 0), **(counts or {})},
        "calls": calls,
        "models_used": list(models),
    }


CONS = {"consequential": True, "reasons": []}
NOT = {"consequential": False, "reasons": []}


def test_summary_counts_and_rate_exclude_self_evident():
    items = {
        5: _item(
            5, "analyzed", {"missing": 2, "found": 1, "self_evident": 1}, CONS, 4, models=["m"]
        ),
        4: _item(4, "skipped_deterministic", kind="bot"),
        3: _item(3, "not_consequential", screen=NOT, calls=1, models=["s"]),
        2: _item(2, "analyzed", {"partial": 1}, CONS, 3, models=["m", "s"]),
    }
    m = bf.summarize("acme/x", _sample(5, 4, 3, 2), items)
    assert (m["considered"], m["skipped_deterministic"], m["screened"]) == (4, 1, 3)
    assert (m["consequential"], m["analyzed"], m["pending"]) == (2, 2, 0)
    assert m["decisions"] == {"found": 1, "partial": 1, "missing": 2, "self_evident": 1}
    assert m["why_gap_rate"] == pytest.approx(3 / 4)  # self_evident left out
    assert m["calls_used"] == 8 and m["models_used"] == ["m", "s"]
    assert m["skipped_by_kind"]["bot"] == 1 and m["prs_with_gaps"] == 2
    assert bf.headline(m) == (
        "In the last 4 merged PRs of acme/x, 3 of 4 consequential decisions had no written "
        "reason (or only a partial one) in the PR, its reviews, commits or linked issues."
    )


def test_summary_counts_only_the_newest_first_prefix():
    items = {
        3: _item(3, "analyzed", {"missing": 1}, CONS, 3),
        2: _item(2, "error", calls=1),  # no verdict: the prefix stops here
        1: _item(1, "analyzed", {"found": 5}, CONS, 3),
    }
    m = bf.summarize("acme/x", _sample(3, 2, 1), items)
    assert m["considered"] == 1 and m["decisions"]["found"] == 0 and m["why_gap_rate"] == 1.0
    assert m["errors"] == 1 and m["calls_used"] == 7  # calls are counted wherever spent


def test_pending_with_screen_is_a_verdict_but_not_analyzed():
    items = {2: _item(2, "pending", screen=CONS, calls=1), 1: _item(1, "skipped_deterministic")}
    m = bf.summarize("acme/x", _sample(2, 1), items)
    assert (m["considered"], m["consequential"], m["analyzed"], m["pending"]) == (2, 1, 0, 1)
    assert m["why_gap_rate"] is None and bf.headline(m) is None


def test_items_at_another_head_sha_do_not_count():
    items = {1: _item(1, "analyzed", {"missing": 1}, CONS, 3, sha="old")}
    assert bf.summarize("acme/x", _sample(1), items)["considered"] == 0


# --- the budgeted loop ---------------------------------------------------------------


class FakeAnalyze:
    """Stands in for analyze_pr.run: spends calls from the real CallBudget."""

    def __init__(self, script: dict[int, tuple[str, int]]):
        self.script = script  # pr -> (outcome kind, rationale count)
        self.calls: list[dict] = []

    async def __call__(self, repo, number, **kw):
        self.calls.append({"pr": number, **kw})
        budget = mc.get_call_budget()
        kind, k = self.script[number]
        state: dict = {}
        status = "done"

        def spend(n):
            for _ in range(n):
                budget.take("fake-model")

        try:
            if not kw.get("prescreened"):
                spend(1)
                state["screen"] = CONS if kind != "not" else NOT
            if kind == "not":
                status = "skipped"
                state["result"] = {"skip_reason": "screen: not consequential"}
            elif kw["max_rationale"] < 1:
                status = "budget_stopped"
            else:
                spend(1 + min(k, kw["max_rationale"]))
                status = "gaps_found"
                rows = [{"id": f"D{i}", "summary": "s"} for i in range(1, k + 1)]
                state["result"] = {
                    "decisions": rows[: kw["max_rationale"]],
                    "rationale": [{"decision_id": r["id"], "status": "missing"} for r in rows],
                }
        except mc.CallBudgetExhausted as exc:
            return RunOutcome(
                "analyze",
                "r",
                repo,
                number,
                "budget_stopped",
                str(exc),
                state,
                {"models_used": []},
                None,
                error=str(exc),
            )
        return RunOutcome(
            "analyze",
            f"run-{number}",
            repo,
            number,
            status,
            status,
            state,
            {"models_used": ["fake-model"]},
            None,
        )


@pytest.fixture
def fake_backfill_store(monkeypatch):
    db: dict[str, dict] = {"items": {}, "summary": {}}

    async def save_item(repo, item):
        db["items"][item["pr"]] = dict(item)
        return "x"

    async def save_summary(repo, summary):
        db["summary"] = dict(summary)
        return "x"

    monkeypatch.setattr(store, "save_backfill_item", save_item)
    monkeypatch.setattr(store, "save_backfill", save_summary)
    yield db
    mc.set_call_budget(None)


def _execute(sample, files, budget, items, analyze):
    return asyncio.run(
        bf.execute(
            "acme/x",
            sample,
            files,
            budget_calls=budget,
            items=items,
            epoch="e1",
            started_at="t0",
            listed_at="2026-10-17T00:00:00+00:00",
            analyze=analyze,
            log=lambda line: None,
        )
    )


def test_budget_stop_then_resume_without_rescreening(fake_backfill_store):
    sample = _sample(6, 5, 4, 3)
    files = {6: _f("docs/a.md"), 5: _f("app.py"), 4: _f("app.py"), 3: _f("app.py")}
    analyze = FakeAnalyze({5: ("cons", 3), 4: ("not", 0), 3: ("cons", 2)})
    items: dict = {}

    rep = _execute(sample, files, 7, items, analyze)  # 5: 1+1+3=5, 4: 1, 3: screen only
    assert rep.stop == "stopped_budget" and rep.calls == 7
    assert [c["pr"] for c in analyze.calls] == [5, 4, 3]  # 6 was prefiltered at 0 calls
    assert items[6]["status"] == "skipped_deterministic" and items[6]["calls"] == 0
    assert items[3]["status"] == "pending" and items[3]["screen"]["consequential"]
    s = fake_backfill_store["summary"]
    assert s["status"] == "stopped_budget" and s["considered"] == 4 and s["pending"] == 1
    assert s["decisions"]["missing"] == 3 and s["calls_used"] == 7 and s["finished_at"]

    rep = _execute(sample, files, 10, items, analyze)  # resume: only #3, from its screen
    assert [c["pr"] for c in analyze.calls[3:]] == [3]
    assert analyze.calls[-1]["prescreened"]["consequential"] and analyze.calls[-1]["skip_questions"]
    assert rep.calls == 3 and rep.stop == "complete"
    s = fake_backfill_store["summary"]
    assert s["analyzed"] == 2 and s["pending"] == 0 and s["decisions"]["missing"] == 5
    assert s["calls_used"] == 10 and s["why_gap_rate"] == 1.0
    assert s["method"].startswith("Sample: the 4 most recently merged") and s["sample_prs"]


def test_tight_budget_caps_rationale_checks(fake_backfill_store):
    analyze = FakeAnalyze({1: ("cons", 3)})
    items: dict = {}
    _execute(_sample(1), {1: _f("a.py")}, 3, items, analyze)  # screen + extract + 1
    assert analyze.calls[0]["max_rationale"] == 1
    assert items[1]["status"] == "analyzed" and len(items[1]["decisions"]) == 1


def test_never_exceeds_the_budget(fake_backfill_store):
    analyze = FakeAnalyze({n: ("cons", 3) for n in range(1, 9)})
    rep = _execute(_sample(*range(8, 0, -1)), {n: _f("a.py") for n in range(1, 9)}, 11, {}, analyze)
    assert rep.calls <= 11 and fake_backfill_store["summary"]["calls_used"] <= 11


# --- dry run ---------------------------------------------------------------------------


def test_dry_run_plan_and_coverage():
    retry = ChangedFile(
        path="app/client.py",
        status="modified",
        patch="@@ -1,2 +1,2 @@\n-MAX_RETRIES = 5\n+MAX_RETRIES = 3\n-TIMEOUT = 30\n+TIMEOUT = 5\n",
    )
    sample = _sample(3, 2, 1)
    files = {3: [retry], 2: _f("docs/a.md"), 1: _f("app/x.py")}
    rows = bf.plan(sample, files, {})
    assert [r.kind for r in rows] == [None, "docs-only", None]
    assert rows[0].flagged and rows[0].k == 2 and rows[0].p == bf.P_CONSEQUENTIAL_FLAGGED
    assert rows[2].p == bf.P_CONSEQUENTIAL_OTHER and rows[2].k == 1
    covered, used, _ = bf.coverage(rows, 4)
    assert covered == 2 and used == pytest.approx(1 + 0.9 * 3)  # 3rd PR doesn't fit
    lines = bf.dry_run_report(
        "acme/x", sample, files, {}, [4], screen_chain=["s"], main_chain=["m"]
    )
    assert any("prefilter skips (0 calls): 1" in ln for ln in lines)
    assert any(re.search(r"need a model: 2", ln) for ln in lines)
