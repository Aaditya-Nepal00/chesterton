"""List recent merged PRs with deterministic signal scores (no LLM calls).

uv run python scripts/list_prs.py encode/httpx --limit 30 [--refresh]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.analysis.signals import compute_signals_for
from app.github.fetch import client_for, fetch_pr_files, list_merged_prs


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--refresh", action="store_true", help="bypass the GitHub response cache")
    args = ap.parse_args()

    async with await client_for(args.repo, refresh=args.refresh) as gh:
        prs = await list_merged_prs(args.repo, args.limit, gh=gh)
        sem = asyncio.Semaphore(4)

        async def score(pr: dict) -> tuple[dict, object]:
            async with sem:
                files, _ = await fetch_pr_files(gh, args.repo, pr["number"])
            return pr, compute_signals_for(pr["title"], pr["author_is_bot"], files)

        rows = await asyncio.gather(*(score(p) for p in prs))
        quota = f"{gh.rate_remaining}/{gh.rate_limit}" if gh.rate_remaining is not None else "n/a"
        print(
            f"{args.repo}: {len(rows)} merged PRs | GitHub requests={gh.requests_made} "
            f"cache_hits={gh.cache_hits} quota_remaining={quota}\n"
        )

    rows.sort(key=lambda r: (r[1].score, r[0]["merged_at"]), reverse=True)
    print(f"{'score':>5}  {'#':>6}  {'merged':10}  {'files':>5}  {'cmts':>4}  flags / title")
    for pr, s in rows:
        flags = []
        if s.dependency_files:
            flags.append("deps")
        if s.config_files:
            flags.append("config")
        if s.migration_files:
            flags.append("migration")
        if s.constant_changes:
            flags.append("const:" + ",".join(sorted({c.name for c in s.constant_changes}))[:40])
        if s.is_revert:
            flags.append("revert")
        if s.is_docs_only:
            flags.append("docs-only")
        if s.is_release_or_bot:
            flags.append("release/bot")
        print(
            f"{s.score:>5}  {pr['number']:>6}  {pr['merged_at'][:10]}  {pr['changed_files']:>5}  "
            f"{pr['comment_count']:>4}  [{' '.join(flags)}] {pr['title'][:70]}"
        )
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
