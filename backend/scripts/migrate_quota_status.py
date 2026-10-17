"""One-off: runs that "failed" only because the daily Gemini quota ran out become
status "quota_paused". Real errors (503 overload, per-minute limits, bugs) stay "error".

    uv run python scripts/migrate_quota_status.py          # dry run (default)
    uv run python scripts/migrate_quota_status.py --apply

The original status is kept in `previous_status`; nothing is deleted.
"""

from __future__ import annotations

import argparse
import re
import sys

from google.cloud import firestore
from google.cloud.firestore import FieldFilter

from app.config import get_settings

DAILY_PATTERNS = [
    r"PerDay",
    r"AllModelsExhausted",
    r"out of daily Gemini quota",
    r"Gemini quota exhausted",
    r"retry in \d+h",  # Gemini's message for a daily quota: "Please retry in 12h54m"
]


def run_texts(run: dict) -> list[str]:
    texts = [run.get("summary") or ""]
    texts += [e.get("summary") or "" for e in run.get("node_errors") or []]
    texts += [c.get("error") or "" for c in run.get("llm_calls_detail") or []]
    return texts


def classify(run: dict) -> tuple[bool, str]:
    """(is a daily-quota stop, evidence snippet)."""
    for text in run_texts(run):
        for pat in DAILY_PATTERNS:
            m = re.search(pat, text)
            if m:
                start = max(0, m.start() - 60)
                return True, text[start : m.end() + 40].replace("\n", " ")
    return False, (run.get("summary") or "")[:120].replace("\n", " ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--also",
        nargs="*",
        default=[],
        help="run ids known to be daily-quota stops whose stored error text was truncated",
    )
    args = ap.parse_args()
    s = get_settings()
    db = firestore.Client(project=s.google_cloud_project, database=s.firestore_database)
    query = db.collection("runs").where(filter=FieldFilter("status", "==", "error"))
    runs = list(query.stream())
    print(f"{len(runs)} runs with status=error ({'APPLY' if args.apply else 'dry run'})\n")
    changed = 0
    for snap in runs:
        run = snap.to_dict()
        daily, why = classify(run)
        if not daily and snap.id in args.also:
            daily, why = True, "listed with --also (stored error text truncated; see build log)"
        verdict = "-> quota_paused" if daily else "   stays error "
        print(f"{verdict}  {snap.id}  {run.get('repo')}#{run.get('pr') or run.get('number')}")
        print(f"                 {why}")
        if daily:
            changed += 1
            if args.apply:
                snap.reference.update(
                    {
                        "status": "quota_paused",
                        "previous_status": "error",
                        "status_reason": why[:200],
                        "summary": "Paused: daily Gemini quota exhausted. "
                        + (run.get("summary") or "")[:300],
                    }
                )
    print(f"\n{changed} to change{' (applied)' if args.apply else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
