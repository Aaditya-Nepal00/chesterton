"""Model fallback on daily quota. Zero real Gemini calls: every model here is fake, and
the default (real Gemini) factory is patched to raise."""

import asyncio
from datetime import UTC, datetime

import pytest
from google.adk import Agent, Workflow
from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import _ResourceExhaustedError
from google.adk.models.llm_response import LlmResponse
from google.genai import errors, types

from app.agents import model_chain as mc
from app.agents.common import LLM_RETRY
from app.config import Settings, build_chain
from app.models.analysis import ScreenResult
from app.workflows import runner as runner_mod

NOW = datetime(2026, 10, 17, 11, 5, tzinfo=UTC)
FAST_RETRY = LLM_RETRY.model_copy(update={"initial_delay": 0.01, "max_delay": 0.02})
CALLS: list[str] = []


def _quota_error(quota_id: str, delay: str) -> Exception:
    body = {
        "error": {
            "code": 429,
            "message": f"Quota exceeded ({quota_id})",
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaId": quota_id}],
                },
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
            ],
        }
    }
    return _ResourceExhaustedError(errors.ClientError(429, body))


DAILY = ("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "44485s")
MINUTE = ("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "0s")


class FakeLlm(BaseLlm):
    """Fails `fail_times` times with `error`, then answers with a ScreenResult JSON."""

    error: tuple[str, str] | None = None
    fail_times: int = 0

    async def generate_content_async(self, llm_request, stream=False):
        CALLS.append(self.model)
        if self.error and CALLS.count(self.model) <= self.fail_times:
            raise _quota_error(*self.error)
        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(text='{"consequential": true, "reasons": ["ok"]}')]
            )
        )


def _factory(spec: dict[str, dict]):
    return lambda name: FakeLlm(model=name, **spec.get(name, {}))


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    CALLS.clear()
    backend = mc.MemoryQuotaBackend()
    registry = mc.QuotaRegistry(backend, clock=lambda: NOW)
    mc.set_quota_registry(registry)

    def no_real_gemini(name):
        raise AssertionError(f"real Gemini model {name} must not be used in tests")

    monkeypatch.setattr(mc, "_default_factory", no_real_gemini)
    runs: dict[str, dict] = {}

    async def start_run(run_id, meta):
        runs[run_id] = {**meta, "status": "running"}

    async def finish_run(run_id, meta, events):
        runs[run_id].update({**meta, "events": [e.model_dump() for e in events]})
        return f"runs/{run_id}"

    monkeypatch.setattr(runner_mod.store, "start_run", start_run)
    monkeypatch.setattr(runner_mod.store, "finish_run", finish_run)
    yield registry, backend, runs
    mc.set_quota_registry(None)


def _run(model: BaseLlm, persist=True):
    agent = Agent(
        name="screen",
        model=model,
        instruction="x",
        output_schema=ScreenResult,
        output_key="screen",
        retry_config=FAST_RETRY,
    )
    wf = Workflow(name="chain_test", edges=[("START", agent)])
    return asyncio.run(
        runner_mod.run_workflow(
            kind="test",
            workflow=wf,
            repo="acme/demo",
            pr=1,
            state={},
            trigger="test",
            persist=persist,
        )
    )


# --- chains from settings --------------------------------------------------------


def test_chain_building_keeps_old_single_model_first():
    assert build_chain(None, "a, b,c", ("x",)) == ["a", "b", "c"]
    assert build_chain("b", "a,b,c", ("x",)) == ["b", "a", "c"]
    assert build_chain("m", None, ("x", "y")) == ["m", "x", "y"]
    s = Settings(
        _env_file=None,
        model_main="gemini-3.8-flash",
        model_screen_chain="gemini-3.5-flash-lite,gemini-2.5-flash-lite",
    )
    assert s.main_chain == ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash"]
    assert s.screen_chain == ["gemini-3.5-flash-lite", "gemini-2.5-flash-lite"]


def test_next_utc_midnight():
    assert mc.next_utc_midnight(NOW) == datetime(2026, 10, 18, tzinfo=UTC)


# --- fallback behaviour ----------------------------------------------------------


def test_daily_429_falls_to_next_model_and_is_cached(isolated):
    registry, backend, runs = isolated
    model = mc.FallbackGemini.create(
        ["m1", "m2", "m3"], factory=_factory({"m1": {"error": DAILY, "fail_times": 99}})
    )
    out = _run(model)
    assert out.ok and out.status == "done"
    assert CALLS == ["m1", "m2"]  # same node run, next model; m3 never needed
    assert out.state["screen"] == {"consequential": True, "reasons": ["ok"]}
    # quota cache: m1 exhausted until the next 00:00 UTC, persisted to the backend
    assert registry.exhausted_until("m1") == datetime(2026, 10, 18, tzinfo=UTC)
    assert backend.data["m1"]["exhausted_until"] == "2026-10-18T00:00:00+00:00"
    # the model actually used is recorded on the trace and in the summary
    finished = [e for e in out.recorder.events if e.status == "finished"]
    assert finished[-1].model == "m2"
    assert out.recorder.models_used() == {"screen": "m2", "main": None}
    calls = [(c.model, c.ok) for c in out.recorder.llm_calls]
    assert calls == [("m1", False), ("m2", True)]

    # a second run skips m1 without spending a request on it
    CALLS.clear()
    assert _run(model).ok and CALLS == ["m2"]


def test_quota_cache_loaded_from_backend_skips_model(isolated):
    registry, backend, _ = isolated
    backend.data["m1"] = {"exhausted_until": "2026-10-18T00:00:00+00:00"}
    model = mc.FallbackGemini.create(["m1", "m2"], factory=_factory({}))
    assert _run(model).ok and CALLS == ["m2"]


def test_per_minute_429_keeps_backoff_and_does_not_fall_back(isolated):
    model = mc.FallbackGemini.create(
        ["m1", "m2"], factory=_factory({"m1": {"error": MINUTE, "fail_times": 1}})
    )
    out = _run(model)
    assert out.ok and CALLS == ["m1", "m1"]  # retried on m1 by LLM_RETRY; m2 untouched
    assert isolated[0].exhausted_until("m1") is None


def test_all_models_exhausted_fails_cleanly_and_marks_run_failed(isolated):
    _, _, runs = isolated
    model = mc.FallbackGemini.create(
        ["m1", "m2"],
        factory=_factory(
            {"m1": {"error": DAILY, "fail_times": 99}, "m2": {"error": DAILY, "fail_times": 99}}
        ),
    )
    out = _run(model)
    assert not out.ok and out.status == "error"
    assert "AllModelsExhausted" in out.summary and "['m1', 'm2']" in out.summary
    assert CALLS == ["m1", "m2"]  # no retries: a daily quota can't be retried away
    run = runs[out.run_id]
    assert run["status"] == "error"
    assert "out of daily Gemini quota" in run["summary"]
    assert any(e["status"] == "error" for e in run["events"])
