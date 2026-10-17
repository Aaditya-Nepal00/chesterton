"""GitHub Actions entry point: `python -m app.action`.

Reads GITHUB_EVENT_NAME / GITHUB_EVENT_PATH, decides what to run (route_event is a
pure function, unit-tested with event fixtures), runs it, and prints one summary line.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Literal

from app.github.markers import parse_markers

logger = logging.getLogger("app.action")

Kind = Literal["analyze", "guard", "record"]


@dataclass
class Route:
    kind: Kind | None
    repo: str | None = None
    pr: int | None = None
    comment_id: int | None = None
    reason: str = ""

    @property
    def trigger(self) -> str:
        return self.reason


def _is_bot(user: dict | None) -> bool:
    user = user or {}
    return user.get("type") == "Bot" or (user.get("login") or "").endswith("[bot]")


def route_event(event_name: str, payload: dict[str, Any], repo: str | None = None) -> Route:
    repo = repo or (payload.get("repository") or {}).get("full_name")
    action = payload.get("action")

    if event_name == "pull_request":
        pr = payload.get("pull_request") or {}
        number = pr.get("number") or payload.get("number")
        trigger = f"pull_request.{action}"
        if _is_bot(pr.get("user")):
            return Route(None, repo, number, reason=f"{trigger}: bot author")
        if (pr.get("head", {}).get("ref") or "").startswith("chesterton/"):
            return Route(None, repo, number, reason=f"{trigger}: chesterton/ branch")
        if action == "closed":
            if pr.get("merged"):
                return Route("analyze", repo, number, reason=trigger)
            return Route(None, repo, number, reason=f"{trigger}: closed without merge")
        if action in ("opened", "synchronize", "reopened"):
            return Route("guard", repo, number, reason=trigger)
        return Route(None, repo, number, reason=f"{trigger}: ignored action")

    if event_name == "issue_comment":
        issue = payload.get("issue") or {}
        comment = payload.get("comment") or {}
        number = issue.get("number")
        trigger = f"issue_comment.{action}"
        if action != "created":
            return Route(None, repo, number, reason=f"{trigger}: ignored action")
        if not issue.get("pull_request"):
            return Route(None, repo, number, reason=f"{trigger}: not a pull request")
        if _is_bot(comment.get("user")):
            return Route(None, repo, number, reason=f"{trigger}: bot comment")
        if parse_markers(comment.get("body")):
            return Route(None, repo, number, reason=f"{trigger}: chesterton marker in comment")
        if (comment.get("user") or {}).get("login", "").lower() != (
            (issue.get("user") or {}).get("login", "").lower()
        ):
            return Route(None, repo, number, reason=f"{trigger}: commenter is not the PR author")
        return Route("record", repo, number, comment.get("id"), reason=trigger)

    if event_name == "workflow_dispatch":
        inputs = payload.get("inputs") or {}
        mode = inputs.get("mode")
        try:
            number = int(inputs.get("pr"))
        except (TypeError, ValueError):
            return Route(None, repo, None, reason="workflow_dispatch: pr input is not a number")
        if mode in ("analyze", "guard"):
            return Route(mode, repo, number, reason=f"workflow_dispatch.{mode}")
        return Route(None, repo, number, reason=f"workflow_dispatch: unknown mode {mode!r}")

    return Route(None, repo, None, reason=f"{event_name}: not handled")


async def dispatch(route: Route, *, dry_run: bool = False, persist: bool = True, on_trace=None):
    """Run the workflow a route points at. Returns the RunOutcome (or None if ignored)."""
    if route.kind is None:
        return None
    if route.kind == "analyze":
        from app.workflows import analyze_pr

        return await analyze_pr.run(
            route.repo,
            route.pr,
            post=True,
            persist=persist,
            dry_run=dry_run,
            trigger=route.trigger,
            on_trace=on_trace,
        )
    if route.kind == "guard":
        from app.workflows import guard_pr

        return await guard_pr.run(
            route.repo,
            route.pr,
            persist=persist,
            dry_run=dry_run,
            trigger=route.trigger,
            on_trace=on_trace,
        )
    from app.workflows import record_answer

    return await record_answer.run(
        route.repo,
        route.pr,
        route.comment_id,
        persist=persist,
        dry_run=dry_run,
        trigger=route.trigger,
        on_trace=on_trace,
    )


def _write_step_summary(line: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


async def amain() -> int:
    from app.config import get_settings

    settings = get_settings()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not os.path.exists(event_path):
        print("chesterton: no GITHUB_EVENT_PATH; nothing to do")
        return 1
    with open(event_path, encoding="utf-8") as f:
        payload = json.load(f)
    route = route_event(event_name, payload, settings.github_repository)
    if route.kind is None:
        line = f"chesterton: ignored {route.repo}#{route.pr} ({route.reason})"
        print(line)
        _write_step_summary(line)
        return 0
    outcome = await dispatch(route)
    line = (
        f"chesterton: {route.kind} {route.repo}#{route.pr} -> {outcome.status}: "
        f"{outcome.summary} | run {outcome.run_id}, {outcome.stats['llm_calls']} LLM calls"
    )
    print(line)
    _write_step_summary(line)
    return 0 if outcome.ok else 1


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
