"""Firestore persistence.

Collections:
  pr_analyses/{owner}__{repo}__{pr}     latest analysis per PR
  questions/{owner}__{repo}__{pr}       the open/answered question set posted on a PR
  decisions/{owner}__{repo}__{NNNN}     recorded decisions
  guards/{owner}__{repo}__{pr}          latest guard result per PR
  runs/{run_id} (+ events/{seq})        every workflow run and its trace
  backfills/{owner}__{repo} (+ items/{pr})  why-gap measurement over past merged PRs
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.cloud import firestore
from google.cloud.firestore import FieldFilter

from app.config import get_settings
from app.models.analysis import AnalysisResult
from app.models.records import DecisionRecord, QuestionSetDoc
from app.models.trace import TraceEvent

ANALYSES = "pr_analyses"
QUESTIONS = "questions"
DECISIONS = "decisions"
GUARDS = "guards"
RUNS = "runs"
EVENTS = "events"
BACKFILLS = "backfills"
ITEMS = "items"
_BATCH_LIMIT = 450  # Firestore allows 500 writes per batch
logger = logging.getLogger(__name__)


def pr_doc_id(repo: str, number: int) -> str:
    owner, _, name = repo.partition("/")
    return f"{owner}__{name}__{number}"


analysis_doc_id = pr_doc_id  # backwards-compatible name


def repo_doc_id(repo: str) -> str:
    owner, _, name = repo.partition("/")
    return f"{owner}__{name}"


def decision_doc_id(repo: str, number: int) -> str:
    owner, _, name = repo.partition("/")
    return f"{owner}__{name}__{number:04d}"


def _client() -> firestore.AsyncClient:
    s = get_settings()
    return firestore.AsyncClient(project=s.google_cloud_project, database=s.firestore_database)


# --- analyses ---
async def save_analysis(result: AnalysisResult) -> str:
    db = _client()
    doc = result.model_dump(mode="json")
    doc["client_created_at"] = doc.pop("created_at")
    doc["created_at"] = firestore.SERVER_TIMESTAMP
    doc["updated_at"] = firestore.SERVER_TIMESTAMP
    ref = db.collection(ANALYSES).document(pr_doc_id(result.repo, result.number))
    await ref.set(doc)
    return ref.path


# --- question sets ---
def question_counters(qs: QuestionSetDoc) -> dict[str, int]:
    """Numeric counters so the dashboard can sum() them server-side."""
    return {
        "n_questions": len(qs.questions),
        "n_answered": sum(1 for q in qs.questions if q.status == "answered"),
    }


async def save_question_set(qs: QuestionSetDoc) -> str:
    db = _client()
    doc = qs.model_dump(mode="json", exclude={"created_at"})
    doc.update(question_counters(qs))
    doc["created_at"] = firestore.SERVER_TIMESTAMP
    doc["updated_at"] = firestore.SERVER_TIMESTAMP
    ref = db.collection(QUESTIONS).document(pr_doc_id(qs.repo, qs.pr))
    await ref.set(doc)
    return ref.path


async def get_question_set(repo: str, pr: int) -> QuestionSetDoc | None:
    snap = await _client().collection(QUESTIONS).document(pr_doc_id(repo, pr)).get()
    return QuestionSetDoc.model_validate(snap.to_dict()) if snap.exists else None


async def update_question_set(qs: QuestionSetDoc) -> str:
    db = _client()
    ref = db.collection(QUESTIONS).document(pr_doc_id(qs.repo, qs.pr))
    await ref.set(
        {
            "questions": [q.model_dump(mode="json") for q in qs.questions],
            "processed_comment_ids": qs.processed_comment_ids,
            **question_counters(qs),
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    return ref.path


# --- decisions ---
async def list_decisions(repo: str) -> list[dict]:
    q = _client().collection(DECISIONS).where(filter=FieldFilter("repo", "==", repo))
    docs = [d.to_dict() async for d in q.stream()]
    return sorted(docs, key=lambda d: d.get("number", 0))


async def save_decisions(records: list[DecisionRecord], extra: dict[str, Any]) -> list[str]:
    db = _client()
    batch = db.batch()
    paths = []
    for rec in records:
        ref = db.collection(DECISIONS).document(decision_doc_id(rec.repo, rec.number))
        batch.set(
            ref,
            {
                **rec.model_dump(mode="json"),
                **extra,
                "created_at": firestore.SERVER_TIMESTAMP,
            },
        )
        paths.append(ref.path)
    await batch.commit()
    return paths


# --- guards ---
def guard_counters(data: dict[str, Any]) -> dict[str, int]:
    return {"matched_count": len(data.get("items") or [])}


async def save_guard(repo: str, pr: int, data: dict[str, Any]) -> str:
    ref = _client().collection(GUARDS).document(pr_doc_id(repo, pr))
    await ref.set(
        {
            **data,
            **guard_counters(data),
            "repo": repo,
            "pr": pr,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
    )
    return ref.path


# --- runs ---
async def start_run(run_id: str, meta: dict[str, Any]) -> str:
    ref = _client().collection(RUNS).document(run_id)
    await ref.set(
        {**meta, "run_id": run_id, "status": "running", "started_at": firestore.SERVER_TIMESTAMP},
        merge=True,
    )
    return ref.path


async def write_event(run_id: str, data: dict[str, Any]) -> None:
    """One trace event, written as it happens (doc id = zero-padded seq, so the final
    batch in finish_run rewrites the same docs idempotently)."""
    ref = _client().collection(RUNS).document(run_id).collection(EVENTS)
    await ref.document(f"{data['seq']:04d}").set({**data, "written_at": firestore.SERVER_TIMESTAMP})


class EventStreamer:
    """Streams TraceEvents to runs/{id}/events in order, off the workflow's hot path.

    `submit` is synchronous (it's the TraceRecorder sink); a background task drains the
    queue. A failed write is logged and counted, never raised: finish_run rewrites
    every event at the end anyway.
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.written = 0
        self.failed = 0
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._task = asyncio.create_task(self._drain())

    def submit(self, event: TraceEvent) -> None:
        self._queue.put_nowait(event.model_dump(mode="json"))  # snapshot now

    async def _drain(self) -> None:
        while (data := await self._queue.get()) is not None:
            try:
                await write_event(self.run_id, data)
                self.written += 1
            except Exception as exc:  # the final pass in finish_run covers it
                self.failed += 1
                if self.failed == 1:
                    logger.warning("streaming trace write failed: %s", type(exc).__name__)

    async def close(self) -> None:
        self._queue.put_nowait(None)
        await self._task


async def finish_run(run_id: str, meta: dict[str, Any], events: list[TraceEvent]) -> str:
    """Final run metadata + a consistency pass over every event (same doc ids)."""
    db = _client()
    run_ref = db.collection(RUNS).document(run_id)
    await run_ref.set({**meta, "finished_at": firestore.SERVER_TIMESTAMP}, merge=True)
    for start in range(0, len(events), _BATCH_LIMIT):
        batch = db.batch()
        for ev in events[start : start + _BATCH_LIMIT]:
            data = ev.model_dump(mode="json")
            data["written_at"] = firestore.SERVER_TIMESTAMP
            batch.set(run_ref.collection(EVENTS).document(f"{ev.seq:04d}"), data)
        await batch.commit()
    return run_ref.path


async def get_run(run_id: str) -> dict | None:
    snap = await _client().collection(RUNS).document(run_id).get()
    return snap.to_dict() if snap.exists else None


# --- backfills ---
async def get_backfill(repo: str) -> dict | None:
    snap = await _client().collection(BACKFILLS).document(repo_doc_id(repo)).get()
    return snap.to_dict() if snap.exists else None


async def list_backfill_items(repo: str) -> dict[int, dict]:
    ref = _client().collection(BACKFILLS).document(repo_doc_id(repo)).collection(ITEMS)
    return {int(d.id): d.to_dict() async for d in ref.stream()}


async def save_backfill_item(repo: str, item: dict[str, Any]) -> str:
    ref = (
        _client()
        .collection(BACKFILLS)
        .document(repo_doc_id(repo))
        .collection(ITEMS)
        .document(str(item["pr"]))
    )
    await ref.set({**item, "updated_at": firestore.SERVER_TIMESTAMP})
    return ref.path


async def save_backfill(repo: str, summary: dict[str, Any]) -> str:
    ref = _client().collection(BACKFILLS).document(repo_doc_id(repo))
    await ref.set({**summary, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
    return ref.path
