"""guard_pr: a new PR touches code covered by a recorded decision -> one guard comment.

Graph:
  START -> load_pr_diff -{"skip"}-> finish_skipped -> persist
                        -{default}-> load_decisions
  load_decisions -> match -{"none"}-> finish_none -> upsert_comment
                          -{default}-> judge [parallel worker, MODEL_SCREEN, max 2]
  judge -> render_guard_comment -> upsert_comment -> persist
"""

from __future__ import annotations

from collections.abc import Callable

from google.adk import Context, Event, Workflow
from google.adk.workflow import DEFAULT_ROUTE

from app.agents import judge as judge_agent
from app.agents.model_chain import make_model
from app.analysis.symbols import match_decisions
from app.config import get_settings
from app.github.fetch import client_for, fetch_pr_context
from app.github.write import GitHubWriter
from app.models.pr import PRContext
from app.models.records import GuardJudgement
from app.models.trace import TraceEvent, trace_started
from app.render.guard_comment import NO_LONGER, render_guard
from app.store import firestore as store
from app.workflows.runner import RunOutcome, run_workflow

APP_NAME = "guard_pr"
KEEP = ("changes_decision", "touches_nearby")


async def load_pr_diff(ctx: Context, repo: str, number: int):
    trace_started(ctx)
    async with await client_for(repo, refresh=True) as gh:
        pr = await fetch_pr_context(repo, number, gh=gh, require_merged=False)
    ctx.state["pr"] = pr.model_dump(mode="json")
    if pr.author_is_bot or (pr.head_ref or "").startswith("chesterton/"):
        ctx.state["skip_reason"] = "bot author or chesterton/ branch"
        return Event(output={"summary": "skip: bot author or chesterton/ branch"}, route="skip")
    return {"summary": f"{repo}#{number} '{pr.title}' ({pr.state}): {len(pr.files)} files"}


async def load_decisions(ctx: Context, repo: str):
    trace_started(ctx)
    decisions = await store.list_decisions(repo)
    ctx.state["decisions"] = decisions
    return {"summary": f"{len(decisions)} recorded decisions in {repo}"}


async def match(ctx: Context):
    trace_started(ctx)
    pr = PRContext.model_validate(ctx.state["pr"])
    decisions = ctx.state["decisions"]
    matches = match_decisions(decisions, pr.files)
    by_number = {int(d["number"]): d for d in decisions}
    ctx.state["matches"] = [m.to_dict() for m in matches]
    if not matches:
        return Event(output={"summary": "no recorded decision touched"}, route="none")
    return [judge_agent.build_item(by_number[m.decision_number], m.to_dict()) for m in matches]


async def render_guard_comment(ctx: Context, repo: str, number: int, node_input):
    trace_started(ctx)
    decisions = {int(d["number"]): d for d in ctx.state["decisions"]}
    items = []
    judgements = []
    async with await client_for(repo, refresh=True) as gh:
        default = (await gh.rest_get(f"/repos/{repo}"))["default_branch"]
        for m, raw in zip(ctx.state["matches"], node_input or [], strict=False):
            if raw is None:
                continue
            j = GuardJudgement.model_validate(raw)
            judgements.append({**j.model_dump(), "decision_number": m["decision_number"]})
            if j.verdict not in KEEP:
                continue
            d = decisions[m["decision_number"]]
            link = await _decision_link(gh, repo, default, d)
            items.append(
                {"match": m, "decision": d, "verdict": j.verdict, "note": j.note, "link": link}
            )
    ctx.state["judgements"] = judgements
    ctx.state["guard_items"] = [
        {"decision": i["decision"]["id"], "verdict": i["verdict"], "link": i["link"]} for i in items
    ]
    ctx.state["guard_body"] = render_guard(items, pr=number) if items else None
    return {"summary": f"{len(items)} of {len(judgements)} matches kept: {judgements}"}


async def _decision_link(gh, repo: str, default: str, d: dict) -> str:
    """The record on the default branch if it's merged there, else the record PR."""
    resp = await gh.request(
        "GET", f"/repos/{repo}/contents/{d['file_path']}", params={"ref": default}
    )
    if resp.status_code == 200:
        return f"https://github.com/{repo}/blob/{default}/{d['file_path']}"
    return d.get("record_pr_url") or f"https://github.com/{repo}"


async def finish_none(ctx: Context):
    trace_started(ctx)
    ctx.state["guard_body"] = None
    ctx.state["guard_items"] = []
    return {"summary": "no matches"}


async def finish_skipped(ctx: Context):
    trace_started(ctx)
    return {"summary": ctx.state.get("skip_reason", "skipped")}


async def upsert_comment(ctx: Context, repo: str, number: int, dry_run: bool = False):
    """One guard comment per PR: create, edit in place, or mark as no longer relevant."""
    trace_started(ctx)
    body = ctx.state.get("guard_body")
    if dry_run and not get_settings().can_write(repo):  # dry run on a read-only repo
        ctx.state["guard_action"] = "dry_run_create" if body else "none"
        if body:
            ctx.state["planned_writes"] = [
                {"action": "create_guard_comment", "pr": number, "body": body}
            ]
        return {"summary": "dry run on a read-only repo: no comment lookup"}
    async with await GitHubWriter.for_repo(repo) as gh:
        existing = await gh.find_comment_with_marker(number, "guard")
        if body is None:
            if existing is None:
                ctx.state["guard_action"] = "none"
                return {"summary": "no matches and no existing guard comment"}
            body = render_guard([], pr=number)
            if NO_LONGER in (existing.get("body") or ""):
                ctx.state["guard_action"] = "unchanged"
                ctx.state["guard_comment_url"] = existing["html_url"]
                return {"summary": "existing guard comment already says no matches"}
        if dry_run:
            action = "update" if existing else "create"
            ctx.state["guard_action"] = f"dry_run_{action}"
            ctx.state["planned_writes"] = [
                {"action": f"{action}_guard_comment", "pr": number, "body": body}
            ]
            return {"summary": f"dry run: would {action} the guard comment"}
        if existing:
            if (existing.get("body") or "").strip() == body.strip():
                c, action = existing, "unchanged"
            else:
                c, action = await gh.update_issue_comment(existing["id"], body), "updated"
        else:
            c, action = await gh.post_issue_comment(number, body, kind="guard"), "created"
    ctx.state["guard_action"] = action
    ctx.state["guard_comment_url"] = c["html_url"]
    return {"summary": f"{action} {c['html_url']}"}


async def persist(ctx: Context, repo: str, number: int, persist_enabled: bool = True):
    trace_started(ctx)
    items = ctx.state.get("guard_items") or []
    action = ctx.state.get("guard_action", "skipped")
    ctx.state["run_status"] = (
        "skipped" if ctx.state.get("skip_reason") else ("matched" if items else "no_matches")
    )
    ctx.state["summary"] = ctx.state.get(
        "skip_reason"
    ) or f"{len(items)} decisions touched; comment {action}" + (
        f" {ctx.state['guard_comment_url']}" if ctx.state.get("guard_comment_url") else ""
    )
    if not persist_enabled or ctx.state.get("skip_reason"):
        return {"summary": "nothing persisted"}
    path = await store.save_guard(
        repo,
        number,
        {
            "run_id": ctx.state["run_id"],
            "matches": ctx.state.get("matches") or [],
            "judgements": ctx.state.get("judgements") or [],
            "items": items,
            "comment_action": action,
            "comment_url": ctx.state.get("guard_comment_url"),
        },
    )
    return {"summary": f"wrote {path}"}


def build_workflow() -> Workflow:
    s = get_settings()
    judge = judge_agent.build_agent(make_model(s.screen_chain, "screen"))
    return Workflow(
        name=APP_NAME,
        edges=[
            ("START", load_pr_diff, {"skip": finish_skipped, DEFAULT_ROUTE: load_decisions}),
            (load_decisions, match, {"none": finish_none, DEFAULT_ROUTE: judge}),
            (judge, render_guard_comment, upsert_comment, persist),
            (finish_none, upsert_comment),
            (finish_skipped, persist),
        ],
    )


async def run(
    repo: str,
    number: int,
    *,
    persist: bool = True,
    dry_run: bool = False,
    trigger: str = "cli",
    on_trace: Callable[[TraceEvent], None] | None = None,
) -> RunOutcome:
    return await run_workflow(
        kind="guard",
        workflow=build_workflow(),
        repo=repo,
        pr=number,
        state={"persist_enabled": persist and not dry_run, "dry_run": dry_run},
        trigger=trigger,
        persist=persist and not dry_run,
        on_trace=on_trace,
    )
