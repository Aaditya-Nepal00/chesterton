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
  render_comment -> persist -> post_questions   (posts only when state.post is True)

Shared data travels through session state (the PR context, signals, evidence
index, decisions); node outputs carry what the next node needs plus a trace summary.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from google.adk import Context, Event, Workflow
from google.adk.workflow import DEFAULT_ROUTE, JoinNode

from app.agents import extract as extract_agent
from app.agents import questions as questions_agent
from app.agents import rationale as rationale_agent
from app.agents import screen as screen_agent
from app.agents.model_chain import make_model
from app.analysis import signals as sig
from app.analysis import verify as ver
from app.analysis.evidence import build_evidence_index, evidence_urls, is_diff_id
from app.analysis.symbols import paths_and_symbols, symbols_for_snippet
from app.config import get_settings
from app.github.fetch import fetch_pr_context
from app.github.write import GitHubWriter
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
from app.models.records import QuestionItem, QuestionSetDoc
from app.models.trace import TraceEvent, TraceRecorder, get_recorder, trace_started
from app.render.pr_comment import render_comment as render_markdown
from app.store import firestore as store
from app.workflows.runner import RunOutcome, run_workflow

APP_NAME = "analyze_pr"
MAX_RATIONALE_DECISIONS = 3  # free-tier budget: extract + 3 rationale + questions = 5 calls


def _pr(ctx: Context) -> PRContext:
    return PRContext.model_validate(ctx.state["pr"])


def _models() -> dict[str, str | None]:
    """Models that actually answered in this run (after any fallback), plus the chains."""
    s = get_settings()
    recorder = get_recorder()
    used = recorder.models_used() if recorder else {"screen": None, "main": None}
    return {**used, "screen_chain": ",".join(s.screen_chain), "main_chain": ",".join(s.main_chain)}


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
    if len(kept) > MAX_RATIONALE_DECISIONS:  # at most 3 questions are asked anyway
        dropped = [d.id for d in kept[MAX_RATIONALE_DECISIONS:]]
        notes.append(f"rationale checked for top {MAX_RATIONALE_DECISIONS} only; skipped {dropped}")
        kept = kept[:MAX_RATIONALE_DECISIONS]
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
        comment_markdown=render_markdown(questions, pr=pr.number, run_id=run_id),
        models=_models(),
        evidence_urls=evidence_urls(ctx.state.get("evidence") or {}),
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
        evidence_urls=evidence_urls(ctx.state.get("evidence") or {}),
        created_at=datetime.now(UTC),
    )
    dumped = result.model_dump(mode="json")
    ctx.state["result"] = dumped
    return dumped


async def persist(ctx: Context, node_input, persist_enabled: bool = True):
    trace_started(ctx)
    result = AnalysisResult.model_validate(node_input)
    ctx.state["run_status"] = result.status
    ctx.state["summary"] = (
        f"{result.status}"
        + (f" ({result.skip_reason})" if result.skip_reason else "")
        + f", {len(result.questions)} questions"
    )
    if not persist_enabled:
        return {"summary": "persist disabled (--no-persist)"}
    path = await store.save_analysis(result)
    ctx.state["persisted"] = [path]
    return {"summary": f"wrote {path}"}


def build_question_set(
    result: AnalysisResult, pr: PRContext, evidence: dict[str, dict], comment: dict | None
) -> QuestionSetDoc:
    decisions = {d.id: d for d in result.decisions}
    items = []
    for i, q in enumerate(result.questions, 1):
        d = decisions.get(q.decision_id)
        paths, hunk_symbols = paths_and_symbols(d.diff_evidence if d else [q.evidence_id], evidence)
        symbols = symbols_for_snippet(q.path, q.snippet, hunk_symbols)
        items.append(
            QuestionItem(
                qid=f"Q{i}",
                decision_id=q.decision_id,
                text=q.text,
                evidence_id=q.evidence_id,
                paths=paths,
                symbols=symbols,
                decision_summary=d.summary if d else "",
                category=d.category if d else "other",
                path=q.path,
                line=q.line,
                evidence_url=q.url,
                snippet=q.snippet,
            )
        )
    return QuestionSetDoc(
        repo=result.repo,
        pr=result.number,
        pr_url=result.url,
        pr_title=result.title,
        author=pr.author,
        comment_id=comment["id"] if comment else None,
        comment_url=comment["html_url"] if comment else None,
        run_id=result.run_id,
        questions=items,
    )


async def post_questions(
    ctx: Context, post: bool = False, persist_enabled: bool = True, dry_run: bool = False
):
    """Post the questions comment as the App (only when state.post is True)."""
    trace_started(ctx)
    result = AnalysisResult.model_validate(ctx.state["result"])
    pr = _pr(ctx) if ctx.state.get("pr") else None
    if not result.comment_markdown or pr is None:
        return {"summary": "nothing to post"}
    if pr.author_is_bot or (pr.head_ref or "").startswith("chesterton/"):
        return {"summary": "not posting: bot author or chesterton/ branch"}
    if not post:
        ctx.state["planned_writes"] = [
            {"action": "post_issue_comment", "pr": pr.number, "body": result.comment_markdown}
        ]
        return {"summary": "posting disabled" + (" (dry run)" if dry_run else "")}

    async with await GitHubWriter.for_repo(pr.repo) as gh:
        existing = await gh.find_comment_with_marker(pr.number, "questions")
        if existing:
            ctx.state["questions_comment_url"] = existing["html_url"]
            ctx.state["summary"] += f"; questions comment already present {existing['html_url']}"
            return {"summary": f"already posted: {existing['html_url']}"}
        comment = await gh.post_issue_comment(pr.number, result.comment_markdown, kind="questions")
    ctx.state["questions_comment_url"] = comment["html_url"]
    ctx.state["run_status"] = "questions_posted"
    ctx.state["summary"] += f"; posted {comment['html_url']}"
    qs = build_question_set(result, pr, ctx.state["evidence"], comment)
    if persist_enabled:
        await store.save_question_set(qs)
    return {"summary": f"posted {comment['html_url']}"}


# --- graph ------------------------------------------------------------------


def build_workflow() -> Workflow:
    s = get_settings()
    main = make_model(s.main_chain, "main")
    screen = screen_agent.build_agent(make_model(s.screen_chain, "screen"))
    extract = extract_agent.build_agent(main)
    rationale = rationale_agent.build_agent(main)
    questions = questions_agent.build_agent(main)
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
            (persist, post_questions),
        ],
    )


# --- runner -----------------------------------------------------------------


async def run(
    repo: str,
    number: int,
    *,
    post: bool = False,
    persist: bool = True,
    refresh: bool = False,
    dry_run: bool = False,
    trigger: str = "cli",
    on_trace: Callable[[TraceEvent], None] | None = None,
) -> RunOutcome:
    return await run_workflow(
        kind="analyze",
        workflow=build_workflow(),
        repo=repo,
        pr=number,
        state={
            "refresh": refresh,
            "persist_enabled": persist and not dry_run,
            "post": post and not dry_run,
            "dry_run": dry_run,
            "started_at": datetime.now(UTC).isoformat(),
        },
        trigger=trigger,
        persist=persist and not dry_run,
        on_trace=on_trace,
    )


async def run_analysis(
    repo: str,
    number: int,
    *,
    persist: bool = True,
    refresh: bool = False,
    on_trace: Callable[[TraceEvent], None] | None = None,
) -> tuple[AnalysisResult, dict[str, Any], TraceRecorder]:
    """CLI/backfill entry point: analyze only, never posts."""
    outcome = await run(repo, number, persist=persist, refresh=refresh, on_trace=on_trace)
    data = outcome.state.get("result")
    if data is None:
        raise RuntimeError(f"analysis of {repo}#{number} failed: {outcome.summary}")
    stats = {**outcome.stats, "persisted": outcome.state.get("persisted")}
    if outcome.run_doc:
        stats["events_path"] = f"{outcome.run_doc}/events"
    return AnalysisResult.model_validate(data), stats, outcome.recorder
