"""Smoke test: Firestore round trip using Application Default Credentials."""

from __future__ import annotations

import sys
import time
import uuid

from google.cloud import firestore

from app.config import get_settings


def main() -> int:
    settings = get_settings()
    if not settings.google_cloud_project:
        print("SKIPPED: GOOGLE_CLOUD_PROJECT not set")
        return 1

    db = firestore.Client(
        project=settings.google_cloud_project, database=settings.firestore_database
    )
    print(f"Project: {db.project}")
    print(f"Database id: {db._database}")

    doc_ref = db.collection("_smoke").document(f"smoke-{uuid.uuid4().hex[:8]}")
    payload = {"hello": "world", "n": 42, "at": firestore.SERVER_TIMESTAMP}

    start = time.perf_counter()
    doc_ref.set(payload)
    t_write = time.perf_counter()
    snap = doc_ref.get()
    t_read = time.perf_counter()
    doc_ref.delete()
    t_delete = time.perf_counter()

    data = snap.to_dict() if snap.exists else None
    print(f"Wrote and read back {doc_ref.path}: {data}")
    gone = not doc_ref.get().exists
    print(f"Deleted: {gone}")
    print(
        f"Latency: write={1000 * (t_write - start):.0f}ms read={1000 * (t_read - t_write):.0f}ms "
        f"delete={1000 * (t_delete - t_read):.0f}ms round-trip={1000 * (t_delete - start):.0f}ms"
    )
    ok = data is not None and data.get("n") == 42 and gone
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
