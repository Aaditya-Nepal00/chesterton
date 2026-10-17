"""List (and, with --apply, delete) dev/test/duplicate runs. Dry run by default.

    uv run python scripts/cleanup_demo_data.py            # list only
    uv run python scripts/cleanup_demo_data.py --apply    # delete exactly the listed runs

Only touches runs/{id} and runs/{id}/events/*. Never deletes decisions, questions,
guards or pr_analyses, and never a run those documents reference.
"""

from __future__ import annotations

import argparse
import sys

from google.cloud import firestore

from app.config import get_settings
from app.store.cleanup import select_runs_to_delete

LABEL = {
    "a": "(a) dev runs with no kind",
    "b": "(b) smoke/dev script runs",
    "c": "(c) duplicate runs (same PR + kind)",
    "d": "(d) orphan events (no run doc)",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--keep", nargs="*", default=[], help="run ids to keep even if they match")
    args = ap.parse_args()
    s = get_settings()
    db = firestore.Client(project=s.google_cloud_project, database=s.firestore_database)

    runs, orphans = [], []
    for ref in db.collection("runs").list_documents():
        snap = ref.get()
        if snap.exists:
            runs.append({"run_id": ref.id, **snap.to_dict()})
        elif next(iter(ref.collection("events").limit(1).stream()), None) is not None:
            orphans.append(ref.id)
    referenced = {
        d.to_dict().get("run_id")
        for name in ("pr_analyses", "questions", "guards")
        for d in db.collection(name).stream()
    } - {None}

    to_delete, kept = select_runs_to_delete(runs, referenced | set(args.keep), orphans)
    print(
        f"{len(runs)} run docs, {len(orphans)} orphan event sets, "
        f"{len(referenced)} referenced runs\n"
    )
    current = None
    for c in to_delete:
        if c.category != current:
            current = c.category
            print(LABEL[c.category])
        n_events = sum(
            1 for _ in db.collection("runs").document(c.run_id).collection("events").stream()
        )
        what = f"{c.kind:8} {c.repo}#{c.pr} {c.status}" if c.repo else ""
        print(f"  {c.run_id}  {what:52} events={n_events:<3} {c.reason}")
    print("\nkept on purpose:")
    for rid, why in kept:
        print(f"  {rid}  {why}")
    survivors = sorted(
        r["run_id"] for r in runs if r["run_id"] not in {c.run_id for c in to_delete}
    )
    print(f"\n{len(to_delete)} to delete; {len(survivors)} runs remain: {survivors}")

    if not args.apply:
        print("\n(dry run: nothing deleted)")
        return 0
    for c in to_delete:
        ref = db.collection("runs").document(c.run_id)
        events = list(ref.collection("events").stream())
        for i in range(0, len(events), 400):
            batch = db.batch()
            for e in events[i : i + 400]:
                batch.delete(e.reference)
            batch.commit()
        ref.delete()
        print(f"deleted runs/{c.run_id} (+{len(events)} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
