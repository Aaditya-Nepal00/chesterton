"""Firestore persistence for analyses, runs and run trace events."""

from __future__ import annotations

from typing import Any

from google.cloud import firestore

from app.config import get_settings
from app.models.analysis import AnalysisResult
from app.models.trace import TraceEvent

ANALYSES = "pr_analyses"
RUNS = "runs"
EVENTS = "events"
_BATCH_LIMIT = 450  # Firestore allows 500 writes per batch


def analysis_doc_id(repo: str, number: int) -> str:
    owner, _, name = repo.partition("/")
    return f"{owner}__{name}__{number}"


def _client() -> firestore.AsyncClient:
    s = get_settings()
    return firestore.AsyncClient(project=s.google_cloud_project, database=s.firestore_database)


async def save_analysis(result: AnalysisResult, run_meta: dict[str, Any]) -> list[str]:
    """Write pr_analyses/<doc> (full result) and runs/<run_id> (metadata)."""
    db = _client()
    doc = result.model_dump(mode="json")
    doc["client_created_at"] = doc.pop("created_at")
    doc["created_at"] = firestore.SERVER_TIMESTAMP
    doc["updated_at"] = firestore.SERVER_TIMESTAMP
    analysis_ref = db.collection(ANALYSES).document(analysis_doc_id(result.repo, result.number))
    run_ref = db.collection(RUNS).document(result.run_id)
    batch = db.batch()
    batch.set(analysis_ref, doc)
    batch.set(
        run_ref,
        {
            **run_meta,
            "run_id": result.run_id,
            "repo": result.repo,
            "number": result.number,
            "status": result.status,
            "analysis_doc": analysis_ref.path,
            "finished_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    await batch.commit()
    return [analysis_ref.path, run_ref.path]


async def save_run_stats(run_id: str, stats: dict[str, Any], events: list[TraceEvent]) -> str:
    """Merge run stats into runs/<run_id> and write runs/<run_id>/events/<seq>."""
    db = _client()
    run_ref = db.collection(RUNS).document(run_id)
    await run_ref.set({**stats, "stats_written_at": firestore.SERVER_TIMESTAMP}, merge=True)
    for start in range(0, len(events), _BATCH_LIMIT):
        batch = db.batch()
        for ev in events[start : start + _BATCH_LIMIT]:
            data = ev.model_dump(mode="json")
            data["written_at"] = firestore.SERVER_TIMESTAMP
            batch.set(run_ref.collection(EVENTS).document(f"{ev.seq:04d}"), data)
        await batch.commit()
    return f"{run_ref.path}/{EVENTS}"
