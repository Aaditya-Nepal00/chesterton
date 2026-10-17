"""Run the Action's code path locally against a real PR.

Builds the same event payload GitHub Actions would deliver (from the GitHub API),
routes it with app.action.route_event and runs app.action.dispatch.

    uv run python scripts/run_event.py --kind analyze --repo owner/repo --pr 1 [--dry-run]
    uv run python scripts/run_event.py --kind record  --repo owner/repo --pr 1 --comment-id 123
    uv run python scripts/run_event.py --kind guard   --repo owner/repo --pr 2

--dry-run prints what WOULD be posted and writes nothing (no GitHub, no Firestore).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time

from app.action import dispatch, route_event
from app.github.fetch import client_for
from app.models.trace import TraceEvent

_T0 = time.perf_counter()


def print_trace(ev: TraceEvent) -> None:
    dur = f" {ev.duration_ms / 1000:5.1f}s" if ev.duration_ms is not None else "       "
    mark = {"started": "▶", "finished": "✓", "error": "✗"}[ev.status]
    print(
        f"[{time.perf_counter() - _T0:6.1f}s] {mark} {ev.status:8}{dur}  {ev.path}"
        + (f"\n{'':20}{ev.summary}" if ev.summary and ev.status != "started" else ""),
        flush=True,
    )


async def build_event(kind: str, repo: str, pr: int, comment_id: int | None) -> tuple[str, dict]:
    async with await client_for(repo, refresh=True) as gh:
        if kind in ("analyze", "guard"):
            pull = await gh.rest_get(f"/repos/{repo}/pulls/{pr}")
            action = "closed" if kind == "analyze" else "synchronize"
            return "pull_request", {
                "action": action,
                "number": pr,
                "pull_request": pull,
                "repository": {"full_name": repo},
            }
        if comment_id is None:
            raise SystemExit("--comment-id is required for --kind record")
        issue = await gh.rest_get(f"/repos/{repo}/issues/{pr}")
        comment = await gh.rest_get(f"/repos/{repo}/issues/comments/{comment_id}")
        return "issue_comment", {
            "action": "created",
            "issue": issue,
            "comment": comment,
            "repository": {"full_name": repo},
        }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["analyze", "guard", "record"], required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--comment-id", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    event_name, payload = await build_event(args.kind, args.repo, args.pr, args.comment_id)
    route = route_event(event_name, payload, args.repo)
    print(f"== route: {route.kind or 'ignored'} ({route.reason}) dry_run={args.dry_run} ==")
    if route.kind is None:
        return 0
    if route.kind != args.kind:
        print(f"note: event routed to {route.kind}, not {args.kind}")
    outcome = await dispatch(route, dry_run=args.dry_run, on_trace=print_trace)

    print(f"\n== {outcome.kind} {outcome.repo}#{outcome.pr}: {outcome.status} ==")
    print(outcome.summary)
    for key in ("questions_comment_url", "record_pr", "reply_comment_url", "guard_comment_url"):
        if outcome.state.get(key):
            print(f"{key}: {outcome.state[key]}")
    for key in ("answer_notes", "judgements"):
        if outcome.state.get(key):
            print(f"{key}: {json.dumps(outcome.state[key], default=str)}")
    if args.dry_run:
        for w in outcome.state.get("planned_writes") or []:
            print(f"\n--- WOULD {w['action']} ---")
            for k, v in w.items():
                if k != "action":
                    print(v if k in ("body", "content") else f"{k}: {v}")
        if outcome.kind == "analyze" and not outcome.state.get("planned_writes"):
            result = outcome.state.get("result") or {}
            print("\n(nothing would be posted)", result.get("status"))
    print(f"\nLLM calls: {outcome.stats['llm_calls']} (failed {outcome.stats['llm_failed_calls']})")
    for c in outcome.stats["llm_calls_detail"]:
        print(
            f"  {c['node']:12} {c['model'] or '':24} {c['latency_ms'] / 1000:6.2f}s "
            f"{'ok' if c['ok'] else 'ERROR ' + str(c['error'])}"
        )
    if outcome.run_doc:
        print(f"Firestore: {outcome.run_doc} (+events), {outcome.state.get('persisted')}")
    if outcome.error:
        print(f"ERROR: {outcome.error}")
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(asyncio.run(main()))
