"""Backfill fields added in Build 7a on existing Firestore docs. No Gemini calls.

  pr_analyses.evidence_urls        evidence id -> html_url (rebuilt from GitHub data:
                                   the on-disk cache for public repos, a live read for
                                   the demo repo)
  questions.n_questions/n_answered numeric counters for server-side sum()
  guards.matched_count             number of decisions the guard comment showed
  runs.models_used                 models that answered (from llm_calls_detail)

    uv run python scripts/backfill_fields.py           # dry run (default)
    uv run python scripts/backfill_fields.py --apply

Only adds/overwrites these fields (update(), never set()); nothing is deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from google.cloud import firestore

from app.analysis.evidence import build_evidence_index, evidence_urls
from app.config import get_settings
from app.github.fetch import fetch_pr_context
from app.models.records import QuestionSetDoc
from app.store.firestore import guard_counters, question_counters


def models_used(run: dict) -> list[str]:
    out: list[str] = []
    for c in run.get("llm_calls_detail") or []:
        if c.get("ok") and c.get("model") and c["model"] not in out:
            out.append(c["model"])
    return out


async def evidence_for(repo: str, number: int) -> dict[str, str]:
    pr = await fetch_pr_context(repo, number)
    return evidence_urls(build_evidence_index(pr))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    s = get_settings()
    db = firestore.Client(project=s.google_cloud_project, database=s.firestore_database)
    mode = "APPLY" if args.apply else "dry run"
    planned = 0

    print(f"== pr_analyses.evidence_urls ({mode})")
    for snap in db.collection("pr_analyses").stream():
        a = snap.to_dict()
        urls = await evidence_for(a["repo"], a["number"])
        cited = {e for d in a.get("decisions") or [] for e in d.get("diff_evidence") or []}
        cited |= {e for r in a.get("rationale") or [] for e in r.get("evidence_ids") or []}
        missing = sorted(cited - urls.keys())
        print(f"  {snap.id}: {len(urls)} urls, cited={len(cited)} unresolved={missing or 'none'}")
        for eid in sorted(cited)[:3]:
            print(f"      {eid} -> {urls.get(eid)}")
        planned += 1
        if args.apply:
            snap.reference.update({"evidence_urls": urls})

    print(f"\n== questions counters ({mode})")
    for snap in db.collection("questions").stream():
        counters = question_counters(QuestionSetDoc.model_validate(snap.to_dict()))
        print(f"  {snap.id}: {counters}")
        planned += 1
        if args.apply:
            snap.reference.update(counters)

    print(f"\n== guards.matched_count ({mode})")
    for snap in db.collection("guards").stream():
        counters = guard_counters(snap.to_dict())
        print(f"  {snap.id}: {counters}")
        planned += 1
        if args.apply:
            snap.reference.update(counters)

    print(f"\n== runs.models_used ({mode})")
    for snap in db.collection("runs").stream():
        used = models_used(snap.to_dict())
        print(f"  {snap.id}: {used}")
        planned += 1
        if args.apply:
            snap.reference.update({"models_used": used})

    print(f"\n{planned} docs {'updated' if args.apply else 'would be updated'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
