"""Measure the why-gap over a repo's last N merged PRs, within a Gemini call budget.

Read-only on GitHub, never posts. Writes backfills/{owner}__{repo} (+ items/{pr}),
pr_analyses and runs to Firestore.

uv run python scripts/backfill.py encode/httpx --limit 20 --budget-calls 40 --dry-run
uv run python scripts/backfill.py encode/httpx --limit 20 --budget-calls 40
uv run python scripts/backfill.py encode/httpx --limit 20 --budget-calls 40 --resume

--dry-run makes 0 Gemini calls: it lists the PRs, applies the prefilter, fetches (and
caches) what the real run will read from GitHub, and prints the estimated calls.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app import backfill as bf
from app.config import get_settings
from app.store import firestore as store

DEFAULT_BUDGETS = [20, 30, 40, 60]


async def _existing(repo: str) -> tuple[dict | None, dict[int, dict], str | None]:
    """(summary doc, items, error) — a dry run still works without Firestore access."""
    try:
        return await store.get_backfill(repo), await store.list_backfill_items(repo), None
    except Exception as exc:
        return None, {}, f"{type(exc).__name__}: {str(exc)[:120]}"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("repo", help="owner/name of a public repo")
    ap.add_argument("--limit", type=int, default=20, help="last N merged PRs (max 50)")
    ap.add_argument("--budget-calls", type=int, required=True, help="max Gemini requests")
    ap.add_argument("--dry-run", action="store_true", help="0 Gemini calls: estimate only")
    ap.add_argument("--resume", action="store_true", help="continue the saved sample")
    ap.add_argument("--restart", action="store_true", help="discard saved progress")
    ap.add_argument("--refresh", action="store_true", help="bypass the GitHub response cache")
    args = ap.parse_args()
    if not 1 <= args.limit <= 50:
        ap.error("--limit must be 1-50")
    if args.budget_calls < 1:
        ap.error("--budget-calls must be at least 1")
    if args.resume and args.restart:
        ap.error("--resume and --restart are mutually exclusive")

    s = get_settings()
    doc, items, fs_error = await _existing(args.repo)
    if fs_error and not args.dry_run:
        print(f"Firestore is not reachable ({fs_error}); a real run needs it.", file=sys.stderr)
        return 2

    saved_sample = (doc or {}).get("sample") or []
    if args.resume and saved_sample:
        sample = saved_sample  # the same PRs as the first run, even if new ones merged since
        listed_at = doc.get("sample_listed_at")
    else:
        sample = await bf.load_sample(args.repo, args.limit, refresh=args.refresh)
        listed_at = bf.now_iso()
    if not sample:
        print(f"{args.repo}: no merged PRs found")
        return 1

    numbers = [p["number"] for p in sample]
    epoch = (doc or {}).get("epoch") if args.resume and doc else None
    if args.restart or not epoch:
        epoch = listed_at
    if args.restart:
        items = {}
    elif not args.resume and not args.dry_run:
        done = [n for n in numbers if n in items]
        if done:
            print(
                f"{args.repo} already has backfill progress for {len(done)} of these PRs. "
                "Pass --resume to continue (PRs done at the same head SHA cost nothing) "
                "or --restart to measure again from scratch.",
                file=sys.stderr,
            )
            return 2
    items = {n: it for n, it in items.items() if it.get("epoch", epoch) == epoch}

    files = await bf.load_files(args.repo, numbers, refresh=args.refresh)

    if args.dry_run:
        budgets = sorted({*DEFAULT_BUDGETS, args.budget_calls})
        for line in bf.dry_run_report(
            args.repo,
            sample,
            files,
            items if args.resume else {},
            budgets,
            screen_chain=s.screen_chain,
            main_chain=s.main_chain,
            target=args.budget_calls,
        ):
            print(line)
        todo = [r.number for r in bf.plan(sample, files, {}) if r.kind is None]
        requests = await bf.prewarm_contexts(args.repo, todo, refresh=args.refresh)
        print(f"  prewarmed GitHub cache for {len(todo)} PRs ({requests} GitHub requests)")
        if fs_error:
            print(f"  (Firestore not read: {fs_error})")
        print("  Gemini calls made: 0")
        return 0

    started_at = (doc or {}).get("started_at") if args.resume and doc else None
    print(
        f"== Backfill {args.repo}: {len(sample)} PRs, budget {args.budget_calls} calls"
        + (f", resuming ({len(items)} PRs already have results)" if args.resume else "")
    )
    report = await bf.execute(
        args.repo,
        sample,
        files,
        budget_calls=args.budget_calls,
        items=items,
        epoch=epoch,
        started_at=started_at or bf.now_iso(),
        listed_at=listed_at,
    )
    m = report.summary
    c = m["decisions"]
    rate = m["why_gap_rate"]
    print(f"\n== {m['status']}: {report.calls} calls this run, {m['calls_used']} in total")
    print(
        f"considered {m['considered']}/{m['sample_size']}  skipped by rule "
        f"{m['skipped_deterministic']}  screened {m['screened']}  consequential "
        f"{m['consequential']}  analyzed {m['analyzed']}  pending {m['pending']}  "
        f"errors {m['errors']}"
    )
    print(
        f"decisions: found {c['found']}, partial {c['partial']}, missing {c['missing']}, "
        f"self-evident {c['self_evident']}  why-gap rate "
        + (f"{rate:.0%}" if rate is not None else "n/a")
    )
    if sentence := bf.headline(m):
        print(f"\n{sentence}")
    if m["status"] != "complete":
        print("\nContinue later with the same command plus --resume.")
    print(f"models: {', '.join(m['models_used']) or '-'}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(asyncio.run(main()))
