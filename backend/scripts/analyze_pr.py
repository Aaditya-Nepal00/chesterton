"""Analyze one merged PR with the ADK workflow. Read-only on GitHub; posts nothing.

uv run python scripts/analyze_pr.py encode/httpx 3000 [--no-persist] [--refresh]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from app.models.trace import TraceEvent
from app.workflows.analyze_pr import run_analysis

_T0 = time.perf_counter()


def print_trace(ev: TraceEvent) -> None:
    dur = f" {ev.duration_ms / 1000:5.1f}s" if ev.duration_ms is not None else "       "
    mark = {"started": "▶", "finished": "✓", "error": "✗"}[ev.status]
    print(
        f"[{time.perf_counter() - _T0:6.1f}s] {mark} {ev.status:8}{dur}  {ev.path}"
        + (f"\n{'':20}{ev.summary}" if ev.summary else ""),
        flush=True,
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("number", type=int)
    ap.add_argument("--no-persist", action="store_true", help="don't write to Firestore")
    ap.add_argument("--refresh", action="store_true", help="bypass the GitHub response cache")
    args = ap.parse_args()

    print(f"== Trace: {args.repo}#{args.number} ==")
    result, stats, recorder = await run_analysis(
        args.repo,
        args.number,
        persist=not args.no_persist,
        refresh=args.refresh,
        on_trace=print_trace,
    )

    print(
        f"\n== Result: {result.status}" + (f" ({result.skip_reason})" if result.skip_reason else "")
    )
    print(f"{result.url}\nrun_id={result.run_id} models={result.models}")
    if result.signals:
        s = result.signals
        print(
            f"signals: score={s.score} deps={s.dependency_files} config={s.config_files} "
            f"migrations={s.migration_files} constants="
            f"{[f'{c.path}:{c.line} {c.name} {c.old}->{c.new}' for c in s.constant_changes]}"
        )
    if result.screen:
        print(
            f"screen: consequential={result.screen.consequential} reasons={result.screen.reasons}"
        )

    rationale = {r.decision_id: r for r in result.rationale}
    if result.decisions:
        print("\n== Decisions ==")
    for d in result.decisions:
        r = rationale.get(d.id)
        print(f"{d.id} [{d.category}] conf={d.confidence:.2f} {d.summary}")
        print(f"    evidence: {d.diff_evidence}")
        if r:
            quote = f' — "{r.reason_quote}" {r.evidence_ids}' if r.reason_quote else ""
            print(f"    rationale: {r.status}{quote}" + (f"\n    note: {r.note}" if r.note else ""))

    if result.verifier_notes:
        print("\n== Verifier notes ==")
        for n in result.verifier_notes:
            print(f"- {n}")
    if result.questions:
        print("\n== Questions ==")
        for q in result.questions:
            print(f"{q.decision_id} ({q.path}:{q.line}, {q.evidence_id}): {q.text}")
    print("\n== Rendered comment ==")
    print(result.comment_markdown or "(none — nothing to post)")

    print("\n== Stats ==")
    print(f"LLM calls: {stats['llm_calls']} (failed: {stats['llm_failed_calls']})")
    for c in stats["llm_calls_detail"]:
        status = "ok" if c["ok"] else f"ERROR {c['error']}"
        print(f"  {c['node']:10} {c['model'] or '':24} {c['latency_ms'] / 1000:6.2f}s  {status}")
    print(f"node errors (incl. retried attempts): {len(stats['node_errors'])}")
    if stats.get("persisted"):
        print(
            f"Firestore: {stats['persisted']} + {stats.get('events_path')} "
            f"({len(recorder.events)} trace events)"
        )
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(asyncio.run(main()))
