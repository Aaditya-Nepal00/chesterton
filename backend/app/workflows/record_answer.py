"""record_answer: the author's reply on a PR -> cited decision record(s) -> record PR.

Graph:
  START -> load_question_set -{"none"}-> finish_none -> persist
                             -{default}-> answer_input
  answer_input -> parse_answer [agent] -> verify_answer -{"none"}-> finish_none
                                                        -{default}-> allocate_numbers
  allocate_numbers -> render_records -> open_record_pr -> reply_on_source_pr -> persist
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from google.adk import Context, Event, Workflow
from google.adk.workflow import DEFAULT_ROUTE

from app.agents import parse_answer as parse_agent
from app.analysis.answers import clean_reply, verify_answers
from app.config import get_settings
from app.github.markers import parse_markers
from app.github.write import GitHubWriter
from app.models.records import AnswerSet, DecisionRecord, ParsedAnswer, QuestionSetDoc
from app.models.trace import TraceEvent, trace_started
from app.render import decision_record as dr
from app.store import firestore as store
from app.workflows.runner import RunOutcome, run_workflow

APP_NAME = "record_answer"


def _qs(ctx: Context) -> QuestionSetDoc:
    return QuestionSetDoc.model_validate(ctx.state["question_set"])


def _none(ctx: Context, reason: str):
    ctx.state["none_reason"] = reason
    return Event(output={"summary": reason}, route="none")


async def load_question_set(ctx: Context, repo: str, number: int, comment_id: int):
    trace_started(ctx)
    qs = await store.get_question_set(repo, number)
    if qs is None:
        return _none(ctx, f"no questions doc for {repo}#{number}")
    if comment_id in qs.processed_comment_ids:
        return _none(ctx, f"comment {comment_id} already processed")
    if not qs.open_questions():
        return _none(ctx, "no open questions")
    async with await GitHubWriter.for_repo(repo) as gh:
        comment = await gh.get_issue_comment(comment_id)
    user = comment.get("user") or {}
    if not (comment.get("issue_url") or "").endswith(f"/issues/{number}"):
        return _none(ctx, f"comment {comment_id} is not on #{number}")
    if user.get("type") == "Bot":
        return _none(ctx, "comment is from a bot")
    if (user.get("login") or "").lower() != (qs.author or "").lower():
        return _none(ctx, f"comment author @{user.get('login')} is not the PR author @{qs.author}")
    if parse_markers(comment.get("body")):
        return _none(ctx, "comment carries a chesterton marker")
    body = clean_reply(comment.get("body") or "")
    if not body:
        return _none(ctx, "reply is empty after removing quoted text")
    ctx.state["eligible"] = True
    ctx.state["question_set"] = qs.model_dump(mode="json")
    ctx.state["reply"] = {
        "id": comment["id"],
        "body": body,
        "url": comment["html_url"],
        "author": user.get("login"),
    }
    return {
        "summary": f"{len(qs.open_questions())} open questions; reply by @{user.get('login')} "
        f"({len(body)} chars)"
    }


async def answer_input(ctx: Context) -> str:
    trace_started(ctx)
    reply = ctx.state["reply"]
    return parse_agent.build_input(_qs(ctx).open_questions(), reply["body"], reply["author"])


async def verify_answer(ctx: Context, node_input):
    trace_started(ctx)
    parsed = AnswerSet.model_validate(node_input).answers
    qs = _qs(ctx)
    usable, notes = verify_answers(
        parsed, [q.qid for q in qs.open_questions()], ctx.state["reply"]["body"]
    )
    ctx.state["answer_notes"] = notes
    ctx.state["usable_answers"] = [a.model_dump(mode="json") for a in usable]
    summary = f"{len(usable)} usable of {len(parsed)} parsed; notes={notes}"
    if not usable:
        return _none(ctx, "no usable answer in the reply: " + "; ".join(notes))
    return {"summary": summary}


async def allocate_numbers(ctx: Context, repo: str):
    trace_started(ctx)
    async with await GitHubWriter.for_repo(repo) as gh:
        default = await gh.get_default_branch()
        names = [e["name"] for e in await gh.list_dir(dr.DECISIONS_DIR, ref=default)]
    used = dr.numbers_in_dir(names) | {int(d["number"]) for d in await store.list_decisions(repo)}
    numbers = dr.allocate_numbers(used, len(ctx.state["usable_answers"]))
    ctx.state["numbers"] = numbers
    ctx.state["default_branch"] = default
    return {"summary": f"used={sorted(used)} -> allocated {[dr.record_id(n) for n in numbers]}"}


async def render_records(ctx: Context, repo: str):
    trace_started(ctx)
    qs = _qs(ctx)
    reply = ctx.state["reply"]
    by_qid = {q.qid: q for q in qs.questions}
    today = datetime.now(UTC).date().isoformat()
    records = []
    for n, raw in zip(ctx.state["numbers"], ctx.state["usable_answers"], strict=True):
        a = ParsedAnswer.model_validate(raw)
        q = by_qid[a.qid]
        slug = dr.slugify(a.title)
        rid = dr.record_id(n)
        records.append(
            DecisionRecord(
                number=n,
                id=rid,
                slug=slug,
                file_path=f"{dr.DECISIONS_DIR}/{rid}-{slug}.md",
                title=a.title.strip(),
                repo=repo,
                owner=qs.author,
                paths=q.paths,
                symbols=q.symbols,
                source_pr=qs.pr,
                source_pr_url=qs.pr_url,
                source_pr_title=qs.pr_title,
                answer_url=reply["url"],
                answer_comment_id=reply["id"],
                recorded=today,
                qid=q.qid,
                question=q.text,
                decision=a.decision,
                reason=a.reason,
                reason_quote=a.reason_quote,
                alternatives=a.alternatives,
                constraints=a.constraints,
                safe_to_change_when=a.safe_to_change_when,
                evidence_url=q.evidence_url,
                evidence_path=q.path,
                evidence_line=q.line,
            )
        )
    ctx.state["records"] = [r.model_dump(mode="json") for r in records]
    ctx.state["files"] = {r.file_path: dr.render_record(r) for r in records}
    return {"summary": "rendered " + ", ".join(r.file_path for r in records)}


async def open_record_pr(ctx: Context, repo: str, dry_run: bool = False):
    trace_started(ctx)
    records = [DecisionRecord.model_validate(r) for r in ctx.state["records"]]
    branch = f"chesterton/decision-{records[0].id}"
    title = dr.record_pr_title(records)
    body = dr.record_pr_body(records)
    if dry_run:
        ctx.state["planned_writes"] = ctx.state.get("planned_writes", []) + [
            {"action": "create_branch", "branch": branch},
            *[
                {"action": "put_file", "path": p, "content": c}
                for p, c in ctx.state["files"].items()
            ],
            {"action": "open_pr", "head": branch, "title": title, "body": body},
        ]
        return {"summary": f"dry run: would open PR {title!r} from {branch}"}
    async with await GitHubWriter.for_repo(repo) as gh:
        await gh.create_branch(branch)
        for path, content in ctx.state["files"].items():
            rec = next(r for r in records if r.file_path == path)
            await gh.put_file(branch, path, content, f"Add decision record {rec.id}: {rec.title}")
        pr = await gh.open_pr(branch, title, body)
    ctx.state["record_pr"] = {"number": pr["number"], "url": pr["html_url"], "branch": branch}
    return {"summary": f"opened {pr['html_url']}"}


async def reply_on_source_pr(ctx: Context, repo: str, number: int, dry_run: bool = False):
    trace_started(ctx)
    records = [DecisionRecord.model_validate(r) for r in ctx.state["records"]]
    answered = {r.qid for r in records}
    still_open = [q for q in _qs(ctx).open_questions() if q.qid not in answered]
    record_pr = ctx.state.get("record_pr") or {}
    text = dr.reply_text(
        records,
        record_pr.get("number"),
        still_open,
        pr=number,
        answer_comment_id=ctx.state["reply"]["id"],
    )
    if dry_run:
        ctx.state["planned_writes"] = ctx.state.get("planned_writes", []) + [
            {"action": "post_issue_comment", "pr": number, "body": text}
        ]
        return {"summary": "dry run: would reply on the source PR"}
    async with await GitHubWriter.for_repo(repo) as gh:
        comment = await gh.post_issue_comment(number, text, kind="recorded")
    ctx.state["reply_comment_url"] = comment["html_url"]
    return {"summary": f"replied {comment['html_url']}"}


async def finish_none(ctx: Context):
    trace_started(ctx)
    return {"summary": ctx.state.get("none_reason", "nothing to record")}


async def persist(ctx: Context, persist_enabled: bool = True, dry_run: bool = False):
    trace_started(ctx)
    records = [DecisionRecord.model_validate(r) for r in ctx.state.get("records") or []]
    record_pr = ctx.state.get("record_pr") or {}
    if records and record_pr:
        ctx.state["run_status"] = "recorded"
        ctx.state["summary"] = (
            f"recorded {', '.join(r.file_path for r in records)} in {record_pr['url']}"
        )
    elif records:
        ctx.state["run_status"] = "dry_run"
        ctx.state["summary"] = f"would record {', '.join(r.file_path for r in records)}"
    else:
        ctx.state["run_status"] = "nothing_recorded"
        ctx.state["summary"] = ctx.state.get("none_reason", "nothing recorded")
    if not persist_enabled or dry_run:
        return {"summary": "persist disabled"}
    written = []
    if records and record_pr:
        for r in records:
            r.record_pr_url = record_pr["url"]
            r.record_pr_number = record_pr["number"]
        written += await store.save_decisions(
            records, {"reply_comment_url": ctx.state.get("reply_comment_url")}
        )
    if ctx.state.get("eligible"):
        qs = _qs(ctx)
        by_qid = {r.qid: r for r in records if record_pr}
        for q in qs.questions:
            if q.qid in by_qid:
                q.status = "answered"
                q.decision_number = by_qid[q.qid].number
                q.record_pr_url = record_pr.get("url")
        qs.processed_comment_ids.append(int(ctx.state["reply"]["id"]))
        written.append(await store.update_question_set(qs))
    ctx.state["persisted"] = written
    return {"summary": "wrote " + (", ".join(written) or "nothing")}


def build_workflow() -> Workflow:
    s = get_settings()
    if not s.model_main:
        raise RuntimeError("MODEL_MAIN must be set")
    parse_answer = parse_agent.build_agent(s.model_main)
    return Workflow(
        name=APP_NAME,
        edges=[
            ("START", load_question_set, {"none": finish_none, DEFAULT_ROUTE: answer_input}),
            (answer_input, parse_answer, verify_answer),
            (verify_answer, {"none": finish_none, DEFAULT_ROUTE: allocate_numbers}),
            (allocate_numbers, render_records, open_record_pr, reply_on_source_pr, persist),
            (finish_none, persist),
        ],
    )


async def run(
    repo: str,
    number: int,
    comment_id: int,
    *,
    persist: bool = True,
    dry_run: bool = False,
    trigger: str = "cli",
    on_trace: Callable[[TraceEvent], None] | None = None,
) -> RunOutcome:
    return await run_workflow(
        kind="record",
        workflow=build_workflow(),
        repo=repo,
        pr=number,
        state={
            "comment_id": int(comment_id),
            "persist_enabled": persist and not dry_run,
            "dry_run": dry_run,
        },
        trigger=trigger,
        persist=persist and not dry_run,
        on_trace=on_trace,
    )
