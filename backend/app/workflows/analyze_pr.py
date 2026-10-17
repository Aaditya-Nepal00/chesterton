"""analyze_pr: the multi-agent ADK 2.x graph workflow for one merged PR.

Graph (route names in quotes; a tuple fans out; JoinNode waits for both):

  START -> load_context -{"skip"}-> finish_skipped -> persist
                        -{default}-> (compute_signals, build_evidence) -> join_context
  join_context -> screen_input -> screen -> gate -{"skip"}-> finish_skipped
                                               -{default}-> extract_input
  extract_input -> extract -> plan_rationale -{"none"}-> verify
                                             -{default}-> rationale (parallel worker) -> verify
  verify -{"gaps"}-> questions_input -> questions -> verify_questions -> render_comment
         -{default}-> render_comment
  render_comment -> persist

Shared data travels through session state (the PR context, signals, evidence
index, decisions); node outputs carry what the next node needs plus a trace summary.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from google.adk import Context, Event, Runner, Workflow
from google.adk.apps import App
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import DEFAULT_ROUTE, JoinNode
from google.genai import types

from app.agents import extract as extract_agent
from app.agents import questions as questions_agent
from app.agents import rationale as rationale_agent
from app.agents import screen as screen_agent
from app.analysis import signals as sig
from app.analysis import verify as ver
from app.analysis.evidence import build_evidence_index, is_diff_id
from app.config import get_settings
from app.github.fetch import fetch_pr_context
from app.models.analysis import (
    AnalysisResult,
    Decision,
    DecisionList,
    QuestionSet,
    RationaleCheck,
    RenderedQuestion,
    ScreenResult,
    Signals,
)
from app.models.pr import PRContext
from app.models.trace import TraceEvent, TracePlugin, TraceRecorder, set_recorder, trace_started
from app.render.pr_comment import render_comment as render_markdown
from app.store import firestore as store

APP_NAME = "analyze_pr"


def _pr(ctx: Context) -> PRContext:
    return PRContext.model_validate(ctx.state["pr"])


def _models() -> dict[str, str | None]:
    s = get_settings()
    return {"screen": s.model_screen, "main": s.model_main}


# --- function nodes ---------------------------------------------------------


async def load_context(ctx: Context, repo: str, number: int, refresh: bool = False):
    trace_started(ctx)
    pr = await fetch_pr_context(repo, number, refresh=refresh)
    ctx.state["pr"] = pr.model_dump(mode="json")
    reason = sig.skip_reason(sig.compute_signals(pr))
    summary = (
        f"{pr.repo}#{pr.number} '{pr.title}' by @{pr.author}: {len(pr.files)} files, "
        f"{len(pr.commits)} commits, {len(pr.comments)} comments, "
        f"{len(pr.linked_issues)} linked issues"
        + (" (diff truncated)" if pr.diff_truncated else "")
    )
    if reason:
        ctx.state["skip_reason"] = reason
        return Event(output={"summary": f"{summary}; skip: {reason}"}, route="skip")
    return {"summary": summary}


async def compute_signals(ctx: Context):
    trace_started(ctx)
    s = sig.compute_signals(_pr(ctx))
    ctx.state["signals"] = s.model_dump(mode="json")
    return {
        "summary": f"score={s.score} deps={len(s.dependency_files)} config={len(s.config_files)} "
        f"migrations={len(s.migration_files)} constants={len(s.constant_changes)} "
        f"revert={s.is_revert}"
    }


async def build_evidence(ctx: Context):
    trace_started(ctx)
    evidence = build_evidence_index(_pr(ctx))
    ctx.state["evidence"] = evidence
    n_diff = sum(1 for e in evidence if is_diff_id(e))
    return {"summary": f"{len(evidence)} evidence ids ({n_diff} diff hunks)"}


async def screen_input(ctx: Context) -> str:
    trace_started(ctx)
    return screen_agent.build_input(_pr(ctx), Signals.model_validate(ctx.state["signals"]))


async def gate(ctx: Context, node_input):
    trace_started(ctx)
    screen = ScreenResult.model_validate(node_input)
    if not screen.consequential:
        reason = "screen: not consequential — " + "; ".join(screen.reasons)
        ctx.state["skip_reason"] = reason
        return Event(output={"summary": reason}, route="skip")
    return {"summary": "consequential — continue"}


async def extract_input(ctx: Context) -> str:
    trace_started(ctx)
    return extract_agent.build_input(_pr(ctx), ctx.state["evidence"])


async def plan_rationale(ctx: Context, node_input):
    """Renumber decisions, drop ones with bad evidence, build one worker item each."""
    trace_started(ctx)
    raw = DecisionList.model_validate(node_input).decisions
    renumbered = [d.model_copy(update={"id": f"D{i}"}) for i, d in enumerate(raw, 1)]
    evidence = ctx.state["evidence"]
    kept, notes = ver.verify_decisions(renumbered, evidence)
    ctx.state["decisions"] = [d.model_dump(mode="json") for d in kept]
    ctx.state["decision_notes"] = notes
    items = [rationale_agent.build_item(d, evidence) for d in kept]
    if not items:
        return Event(output=[], route="none")
    return items


async def verify(ctx: Context, node_input):
    trace_started(ctx)
    decisions = [Decision.model_validate(d) for d in ctx.state["decisions"]]
    notes = list(ctx.state.get("decision_notes") or [])
    checks: list[RationaleCheck] = []
    for decision, raw in zip(decisions, node_input or [], strict=False):
        if raw is None:
            continue
        check = RationaleCheck.model_validate(raw)
        if check.decision_id != decision.id:  # worker results are in input order
            notes.append(f"rationale id {check.decision_id!r} corrected to {decision.id}")
            check = check.model_copy(update={"decision_id": decision.id})
        checks.append(check)
    outcome = ver.verify_analysis(decisions, checks, ctx.state["evidence"])
    ctx.state["verified"] = {
        "decisions": [d.model_dump(mode="json") for d in outcome.decisions],
        "rationale": [r.model_dump(mode="json") for r in outcome.rationale],
        "gaps": outcome.gaps,
        "notes": notes + outcome.notes,
    }
    summary = {
        "summary": f"{len(outcome.decisions)} decisions, gaps={outcome.gaps}, "
        f"{len(notes) + len(outcome.notes)} verifier notes"
    }
    if outcome.gaps:
        return Event(output=summary, route="gaps")
    return summary


async def questions_input(ctx: Context) -> str:
    trace_started(ctx)
    v = ctx.state["verified"]
    return questions_agent.build_input(
        [Decision.model_validate(d) for d in v["decisions"]],
        [RationaleCheck.model_validate(r) for r in v["rationale"]],
        v["gaps"],
        ctx.state["evidence"],
        _pr(ctx).author,
    )


async def verify_questions(ctx: Context, node_input):
    trace_started(ctx)
    v = ctx.state["verified"]
    rendered, notes = ver.verify_questions(
        QuestionSet.model_validate(node_input).questions,
        v["gaps"],
        [Decision.model_validate(d) for d in v["decisions"]],
        ctx.state["evidence"],
        _pr(ctx).url,
    )
    ctx.state["rendered_questions"] = [q.model_dump(mode="json") for q in rendered]
    ctx.state["question_notes"] = notes
    return {"summary": f"{len(rendered)} questions kept, {len(notes)} notes"}


async def render_comment(ctx: Context, run_id: str):
    trace_started(ctx)
    pr = _pr(ctx)
    v = ctx.state["verified"]
    questions = [
        RenderedQuestion.model_validate(q) for q in ctx.state.get("rendered_questions") or []
    ]
    result = AnalysisResult(
        repo=pr.repo,
        number=pr.number,
        title=pr.title,
        url=pr.url,
        author=pr.author,
        run_id=run_id,
        status="gaps_found" if v["gaps"] else "no_gaps",
        signals=Signals.model_validate(ctx.state["signals"]),
        screen=ScreenResult.model_validate(ctx.state["screen"]),
        decisions=v["decisions"],
        rationale=v["rationale"],
        verifier_notes=v["notes"] + list(ctx.state.get("question_notes") or []),
        questions=questions,
        comment_markdown=render_markdown(questions, author=pr.author, run_id=run_id),
        models=_models(),
        created_at=datetime.now(UTC),
    )
    dumped = result.model_dump(mode="json")
    ctx.state["result"] = dumped
    return dumped


async def finish_skipped(ctx: Context, run_id: str, repo: str, number: int):
    trace_started(ctx)
    pr = PRContext.model_validate(ctx.state["pr"]) if ctx.state.get("pr") else None
    signals = ctx.state.get("signals")
    if signals is None and pr is not None:
        signals = sig.compute_signals(pr).model_dump(mode="json")
    result = AnalysisResult(
        repo=repo,
        number=number,
        title=pr.title if pr else "",
        url=pr.url if pr else "",
        author=pr.author if pr else None,
        run_id=run_id,
        status="skipped",
        skip_reason=ctx.state.get("skip_reason"),
        signals=signals,
        screen=ctx.state.get("screen"),
        models=_models(),
        created_at=datetime.now(UTC),
    )
    dumped = result.model_dump(mode="json")
    ctx.state["result"] = dumped
    return dumped


async def persist(ctx: Context, node_input, persist_enabled: bool = True):
    trace_started(ctx)
    if not persist_enabled:
        return {"summary": "persist disabled (--no-persist)"}
    result = AnalysisResult.model_validate(node_input)
    paths = await store.save_analysis(
        result,
        {"models": result.models, "trigger": "cli", "started_at": ctx.state.get("started_at")},
    )
    ctx.state["persisted"] = paths
    return {"summary": "wrote " + ", ".join(paths)}


# --- graph ------------------------------------------------------------------


def build_workflow() -> Workflow:
    s = get_settings()
    if not (s.model_screen and s.model_main):
        raise RuntimeError("MODEL_SCREEN and MODEL_MAIN must be set (run scripts/smoke_gemini.py)")
    screen = screen_agent.build_agent(s.model_screen)
    extract = extract_agent.build_agent(s.model_main)
    rationale = rationale_agent.build_agent(s.model_main)
    questions = questions_agent.build_agent(s.model_main)
    join = JoinNode(name="join_context")

    return Workflow(
        name=APP_NAME,
        edges=[
            (
                "START",
                load_context,
                {"skip": finish_skipped, DEFAULT_ROUTE: (compute_signals, build_evidence)},
            ),
            ((compute_signals, build_evidence), join, screen_input, screen, gate),
            (gate, {"skip": finish_skipped, DEFAULT_ROUTE: extract_input}),
            (extract_input, extract, plan_rationale, {"none": verify, DEFAULT_ROUTE: rationale}),
            (rationale, verify),
            (verify, {"gaps": questions_input, DEFAULT_ROUTE: render_comment}),
            (questions_input, questions, verify_questions, render_comment, persist),
            (finish_skipped, persist),
        ],
    )


# --- runner -----------------------------------------------------------------


def new_run_id() -> str:
    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


async def run_analysis(
    repo: str,
    number: int,
    *,
    persist: bool = True,
    refresh: bool = False,
    on_trace: Callable[[TraceEvent], None] | None = None,
) -> tuple[AnalysisResult, dict[str, Any], TraceRecorder]:
    run_id = new_run_id()
    recorder = TraceRecorder(run_id, sink=on_trace)
    set_recorder(recorder)
    try:
        app = App(name=APP_NAME, root_agent=build_workflow(), plugins=[TracePlugin()])
        sessions = InMemorySessionService()
        runner = Runner(app=app, session_service=sessions)
        session = await sessions.create_session(
            app_name=APP_NAME,
            user_id="cli",
            state={
                "repo": repo,
                "number": number,
                "refresh": refresh,
                "persist_enabled": persist,
                "run_id": run_id,
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
        run_error: Exception | None = None
        try:
            async for event in runner.run_async(
                user_id="cli",
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=f"{repo}#{number}")]),
            ):
                recorder.on_adk_event(event)
        except Exception as exc:  # surfaced after recording what we have
            run_error = exc
        final = await sessions.get_session(app_name=APP_NAME, user_id="cli", session_id=session.id)
    finally:
        set_recorder(None)

    errors = [e for e in recorder.events if e.status == "error"]
    stats = {
        "llm_calls": len(recorder.llm_calls),
        "llm_failed_calls": sum(1 for c in recorder.llm_calls if not c.ok),
        "llm_calls_detail": [c.model_dump() for c in recorder.llm_calls],
        "node_errors": [e.model_dump(mode="json") for e in errors],
        "node_durations_ms": recorder.durations(),
        "persisted": final.state.get("persisted") if final else None,
    }
    result_data = final.state.get("result") if final else None
    if result_data is None:
        detail = run_error or (errors[-1].summary if errors else "workflow ended without a result")
        raise RuntimeError(f"analysis of {repo}#{number} failed: {detail}")
    result = AnalysisResult.model_validate(result_data)
    if persist:
        stats["events_path"] = await store.save_run_stats(
            run_id,
            {k: v for k, v in stats.items() if k != "persisted"},
            recorder.events,
        )
    return result, stats, recorder
