"""Trace events: one line per workflow step, for the CLI now and the dashboard later.

ADK 2.12 emits no "node started" events, so "started" comes from two places:
function nodes call ``trace_started(ctx)`` and agent nodes are covered by
``TracePlugin.before_agent_callback``. "finished"/"error" come from the ADK
event stream via ``TraceRecorder.on_adk_event``. The recorder for the current
run lives in a ContextVar, which asyncio copies into the tasks ADK creates.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Literal

from google.adk.plugins.base_plugin import BasePlugin
from pydantic import BaseModel, Field

TraceStatus = Literal["started", "finished", "error"]


class TraceEvent(BaseModel):
    run_id: str
    seq: int
    node: str
    path: str
    status: TraceStatus
    summary: str = ""
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int | None = None
    model: str | None = None  # model that served this node (agent nodes only)


class LlmCall(BaseModel):
    node: str
    path: str
    model: str | None
    latency_ms: int
    ok: bool
    error: str | None = None


def _short(text: str, n: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def summarize_output(output: Any) -> str:
    """One-line summary of a node output for the trace."""
    if isinstance(output, dict):
        if output and all(isinstance(v, dict) and "summary" in v for v in output.values()):
            return "joined: " + ", ".join(output)  # JoinNode output keyed by predecessor
        if "status" in output and "run_id" in output:  # AnalysisResult
            extra = output.get("skip_reason") or f"{len(output.get('questions') or [])} questions"
            return f"status={output['status']} ({extra})"
        if "consequential" in output:
            return f"consequential={output['consequential']} " + _short(
                "; ".join(output.get("reasons") or []), 100
            )
        if "decisions" in output:
            ds = output["decisions"] or []
            return f"{len(ds)} decisions: " + _short(
                " | ".join(f"{d.get('category')}: {d.get('summary')}" for d in ds), 120
            )
        if "questions" in output:
            return f"{len(output['questions'] or [])} questions"
        if "status" in output and "decision_id" in output:
            return f"{output['decision_id']} {output['status']}"
        if "summary" in output:
            return _short(str(output["summary"]))
    if isinstance(output, list):
        if output and all(isinstance(o, dict) and "status" in o for o in output):
            return f"{len(output)} checks: " + ", ".join(
                f"{o.get('decision_id')}={o.get('status')}" for o in output
            )
        return f"{len(output)} items"
    if isinstance(output, str):  # only the *_input prompt builders return plain strings
        return f"prompt built ({len(output):,} chars)"
    return _short(json.dumps(output, default=str))


SCREEN_NODES = {"screen", "judge"}


def node_name_from_path(path: str) -> str:
    """'analyze_pr@1/rationale@1/rationale@abc' -> 'rationale'."""
    last = path.rsplit("/", 1)[-1]
    return last.split("@", 1)[0]


class TraceRecorder:
    def __init__(self, run_id: str, sink: Callable[[TraceEvent], None] | None = None):
        self.run_id = run_id
        self.sink = sink
        self.events: list[TraceEvent] = []
        self.llm_calls: list[LlmCall] = []
        self._started: dict[str, float] = {}
        self._finished: set[str] = set()
        self.model_by_path: dict[str, str] = {}

    def _emit(self, node: str, path: str, status: TraceStatus, summary: str = "") -> TraceEvent:
        duration_ms = None
        if status != "started" and path in self._started:
            duration_ms = int(1000 * (time.perf_counter() - self._started[path]))
        ev = TraceEvent(
            run_id=self.run_id,
            seq=len(self.events) + 1,
            node=node,
            path=path,
            status=status,
            summary=summary,
            duration_ms=duration_ms,
            model=self.model_by_path.get(path) if status != "started" else None,
        )
        self.events.append(ev)
        if self.sink:
            self.sink(ev)
        return ev

    def started(self, path: str) -> None:
        if not path or path in self._started:
            return
        self._started[path] = time.perf_counter()
        self._emit(node_name_from_path(path), path, "started")

    def on_adk_event(self, event: Any) -> TraceEvent | None:
        """Convert an ADK Event into a finished/error TraceEvent (or None)."""
        path = event.node_info.path if event.node_info else ""
        if not path or "/" not in path:  # the workflow itself
            return None
        node = event.node_name or node_name_from_path(path)
        if event.error_code:
            return self._emit(
                node, path, "error", _short(f"{event.error_code}: {event.error_message or ''}", 200)
            )
        if event.output is None or path in self._finished:
            return None
        self._finished.add(path)
        return self._emit(node, path, "finished", summarize_output(event.output))

    def models_used(self) -> dict[str, str | None]:
        """Models that actually answered, by role (screen = screen/judge nodes)."""
        roles: dict[str, dict[str, None]] = {"screen": {}, "main": {}}
        for c in self.llm_calls:
            if c.ok and c.model:
                role = "screen" if c.node in SCREEN_NODES else "main"
                roles[role].setdefault(c.model, None)
        return {k: ",".join(v) or None for k, v in roles.items()}

    def durations(self) -> dict[str, int]:
        return {e.path: e.duration_ms for e in self.events if e.duration_ms is not None}


_RECORDER: ContextVar[TraceRecorder | None] = ContextVar("trace_recorder", default=None)


def set_recorder(recorder: TraceRecorder | None):
    return _RECORDER.set(recorder)


def get_recorder() -> TraceRecorder | None:
    return _RECORDER.get()


def trace_started(ctx: Any) -> None:
    """Call at the top of a function node."""
    recorder = _RECORDER.get()
    if recorder is not None:
        recorder.started(getattr(ctx, "node_path", "") or "")


class TracePlugin(BasePlugin):
    """Records agent-node starts and every model call (count, latency, errors)."""

    def __init__(self) -> None:
        super().__init__(name="chesterton_trace")
        self._model_start: dict[str, float] = {}

    async def before_agent_callback(self, *, agent, callback_context):
        recorder = _RECORDER.get()
        if recorder is not None:
            recorder.started(callback_context.node_path)
        return None

    async def before_model_callback(self, *, callback_context, llm_request):
        self._model_start[callback_context.node_path] = time.perf_counter()
        return None

    def _record(self, callback_context, llm_request_model, ok: bool, error: str | None) -> None:
        recorder = _RECORDER.get()
        path = callback_context.node_path
        start = self._model_start.pop(path, time.perf_counter())
        if recorder is not None:
            recorder.llm_calls.append(
                LlmCall(
                    node=node_name_from_path(path),
                    path=path,
                    model=llm_request_model,
                    latency_ms=int(1000 * (time.perf_counter() - start)),
                    ok=ok,
                    error=error,
                )
            )
            if ok and llm_request_model:
                recorder.model_by_path[path] = llm_request_model

    def _record_skipped(self, callback_context, skipped: list[dict]) -> None:
        """Fallback hops inside FallbackGemini (no request was spent on cached ones)."""
        recorder = _RECORDER.get()
        if recorder is None:
            return
        path = callback_context.node_path
        for s in skipped:
            recorder.llm_calls.append(
                LlmCall(
                    node=node_name_from_path(path),
                    path=path,
                    model=s.get("model"),
                    latency_ms=0,
                    ok=False,
                    error=f"{s.get('reason')} until {s.get('until')}; fell back",
                )
            )

    async def after_model_callback(self, *, callback_context, llm_response):
        if not getattr(llm_response, "partial", False):
            meta = llm_response.custom_metadata or {}
            self._record_skipped(callback_context, meta.get("chesterton_skipped") or [])
            model = meta.get("chesterton_model") or getattr(llm_response, "model_version", None)
            self._record(callback_context, model, True, None)
        return None

    async def on_model_error_callback(self, *, callback_context, llm_request, error):
        from app.agents.common import QuotaExhausted, is_long_quota, quota_retry_delay

        skipped = getattr(error, "skipped", None)
        if skipped is not None:  # AllModelsExhausted: one row per model tried, no retry
            self._model_start.pop(callback_context.node_path, None)
            self._record_skipped(callback_context, skipped)
            return None
        model = getattr(error, "chesterton_model", None) or getattr(llm_request, "model", None)
        self._record(callback_context, model, False, _error_summary(error))
        if is_long_quota(error):
            # Raising here makes ADK surface a RuntimeError, which LLM_RETRY does not
            # match: a daily quota can't be fixed by retrying in 5-20 seconds.
            delay = quota_retry_delay(error)
            wait = f"; retry in ~{delay / 3600:.1f}h" if delay else ""
            raise QuotaExhausted(f"Gemini quota exhausted for {model}{wait} (not retrying)")
        delay = quota_retry_delay(error)
        if delay:
            # Per-minute limit: wait as long as Gemini asks (bounded by is_long_quota)
            # before LLM_RETRY's own short backoff, so the retry isn't wasted.
            await asyncio.sleep(delay + 1)
        return None


def _error_summary(error: BaseException) -> str:
    code, status = getattr(error, "code", None), getattr(error, "status", None)
    message = getattr(error, "message", None)
    if code and message:
        return _short(f"{code} {status}: {message}", 160)
    return _short(str(error), 160)
