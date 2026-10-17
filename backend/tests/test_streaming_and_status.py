"""Live trace streaming + quota_paused status. Fake models only; Firestore is faked."""

import asyncio

import pytest
from google.adk import Agent, Context, Workflow

from app.agents import model_chain as mc
from app.agents.common import is_daily_quota_stop
from app.models.analysis import ScreenResult
from app.models.trace import trace_started
from app.store import firestore as store
from app.workflows import runner as runner_mod
from tests.test_model_chain import DAILY, FAST_RETRY, NOW, _factory


@pytest.fixture
def fake_store(monkeypatch):
    log: list[tuple[str, object]] = []  # ordered record of every Firestore write

    async def start_run(run_id, meta):
        log.append(("start_run", meta))

    async def write_event(run_id, data):
        await asyncio.sleep(0)  # behave like I/O
        log.append(("event", (data["seq"], data["node"], data["status"])))

    async def finish_run(run_id, meta, events):
        log.append(("finish_run", meta))
        return f"runs/{run_id}"

    monkeypatch.setattr(store, "start_run", start_run)
    monkeypatch.setattr(store, "write_event", write_event)
    monkeypatch.setattr(store, "finish_run", finish_run)
    mc.set_quota_registry(mc.QuotaRegistry(mc.MemoryQuotaBackend(), clock=lambda: NOW))
    monkeypatch.setattr(
        mc, "_default_factory", lambda name: (_ for _ in ()).throw(AssertionError(name))
    )
    yield log
    mc.set_quota_registry(None)


def _slow_workflow(model):
    async def step_one(ctx: Context):
        trace_started(ctx)
        await asyncio.sleep(0.15)
        return {"summary": "one"}

    async def step_two(ctx: Context):
        trace_started(ctx)
        await asyncio.sleep(0.15)
        return {"summary": "two"}

    agent = Agent(
        name="screen",
        model=model,
        instruction="x",
        output_schema=ScreenResult,
        output_key="screen",
        retry_config=FAST_RETRY,
    )
    return Workflow(name="slow", edges=[("START", step_one, agent, step_two)])


def _run(wf, persist=True):
    return asyncio.run(
        runner_mod.run_workflow(
            kind="selftest",
            workflow=wf,
            repo="acme/demo",
            pr=1,
            state={},
            trigger="test",
            persist=persist,
        )
    )


def test_events_stream_before_the_run_finishes(fake_store):
    model = mc.FallbackGemini.create(["m1"], factory=_factory({}))
    out = _run(_slow_workflow(model))
    assert out.ok and out.status == "done"
    kinds = [k for k, _ in fake_store]
    assert kinds[0] == "start_run" and kinds[-1] == "finish_run"
    events = [v for k, v in fake_store if k == "event"]
    # every step was written as it happened, in order, before finish_run
    assert len(events) == len(out.recorder.events) and len(events) >= 6
    assert [s for s, _, _ in events] == sorted(s for s, _, _ in events)
    assert ("event", (1, "step_one", "started")) in fake_store
    assert kinds.index("finish_run") > max(i for i, k in enumerate(kinds) if k == "event")
    assert out.stats["events_streamed"] == len(events)
    assert out.stats["models_used"] == ["m1"]


def test_whole_chain_out_of_quota_is_paused_not_failed(fake_store):
    model = mc.FallbackGemini.create(
        ["m1", "m2"],
        factory=_factory(
            {"m1": {"error": DAILY, "fail_times": 9}, "m2": {"error": DAILY, "fail_times": 9}}
        ),
    )
    out = _run(_slow_workflow(model))
    assert out.status == "quota_paused" and out.paused and not out.ok
    assert out.summary.startswith("Paused: daily Gemini quota exhausted")
    final = [v for k, v in fake_store if k == "finish_run"][0]
    assert final["status"] == "quota_paused" and "AllModelsExhausted" in final["summary"]


def test_real_errors_stay_failed(fake_store):
    async def boom(ctx: Context):
        raise ValueError("bug in a node")

    out = _run(Workflow(name="boom", edges=[("START", boom)]))
    assert out.status == "error" and not out.paused


@pytest.mark.parametrize(
    "error,expected",
    [
        ("AllModelsExhausted: all models in chain ['a'] are out of daily Gemini quota", True),
        ("RuntimeError: Error in plugin 'chesterton_trace': Gemini quota exhausted for x", True),
        ("ServerError: 503 UNAVAILABLE. high demand", False),
        ("ValueError: bug", False),
        (None, False),
    ],
)
def test_is_daily_quota_stop(error, expected):
    assert is_daily_quota_stop(error) is expected


def test_quota_stop_found_through_exception_chain():
    try:
        try:
            raise mc.AllModelsExhausted(["a"], [])
        except Exception as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert is_daily_quota_stop(outer)
