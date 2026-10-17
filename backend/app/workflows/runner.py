"""Shared runner for every Chesterton workflow: ADK Runner + TraceRecorder + runs/{run_id}.

Each workflow puts `run_status` (str) and `summary` (one line) into session state;
the runner turns them into the runs doc and the Actions log line.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from google.adk import Runner, Workflow
from google.adk.apps import App
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.config import get_settings
from app.models.trace import TraceEvent, TracePlugin, TraceRecorder, set_recorder
from app.store import firestore as store


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


@dataclass
class RunOutcome:
    kind: str
    run_id: str
    repo: str
    pr: int
    status: str
    summary: str
    state: dict[str, Any]
    stats: dict[str, Any]
    recorder: TraceRecorder
    error: str | None = None
    run_doc: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


async def run_workflow(
    *,
    kind: str,
    workflow: Workflow,
    repo: str,
    pr: int,
    state: dict[str, Any],
    trigger: str,
    persist: bool,
    on_trace: Callable[[TraceEvent], None] | None = None,
) -> RunOutcome:
    settings = get_settings()
    run_id = state.get("run_id") or new_run_id()
    state = {**state, "run_id": run_id, "repo": repo, "number": pr}
    recorder = TraceRecorder(run_id, sink=on_trace)
    meta = {
        "kind": kind,
        "repo": repo,
        "pr": pr,
        "trigger": trigger,
        "actions_run_url": settings.actions_run_url,
        "model_chains": {"screen": settings.screen_chain, "main": settings.main_chain},
    }
    if persist:
        await store.start_run(run_id, meta)

    set_recorder(recorder)
    error: str | None = None
    final_state: dict[str, Any] = {}
    try:
        app = App(name=workflow.name, root_agent=workflow, plugins=[TracePlugin()])
        sessions = InMemorySessionService()
        runner = Runner(app=app, session_service=sessions)
        session = await sessions.create_session(
            app_name=workflow.name, user_id="chesterton", state=state
        )
        try:
            async for event in runner.run_async(
                user_id="chesterton",
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=f"{repo}#{pr}")]),
            ):
                recorder.on_adk_event(event)
        except Exception as exc:  # recorded below; the trace keeps what happened
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
        final = await sessions.get_session(
            app_name=workflow.name, user_id="chesterton", session_id=session.id
        )
        final_state = dict(final.state) if final else {}
    finally:
        set_recorder(None)

    errors = [e for e in recorder.events if e.status == "error"]
    stats = {
        "llm_calls": len(recorder.llm_calls),
        "llm_failed_calls": sum(1 for c in recorder.llm_calls if not c.ok),
        "llm_calls_detail": [c.model_dump() for c in recorder.llm_calls],
        "node_errors": [e.model_dump(mode="json") for e in errors],
        "node_durations_ms": recorder.durations(),
    }
    status = "error" if error else str(final_state.get("run_status") or "done")
    summary = error or str(final_state.get("summary") or status)
    outcome = RunOutcome(
        kind=kind,
        run_id=run_id,
        repo=repo,
        pr=pr,
        status=status,
        summary=summary,
        state=final_state,
        stats=stats,
        recorder=recorder,
        error=error,
    )
    if persist:
        outcome.run_doc = await store.finish_run(
            run_id, {**meta, "status": status, "summary": summary, **stats}, recorder.events
        )
    return outcome
