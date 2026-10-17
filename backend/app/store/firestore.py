"""Firestore persistence.

Collections:
  pr_analyses/{owner}__{repo}__{pr}     latest analysis per PR
  questions/{owner}__{repo}__{pr}       the open/answered question set posted on a PR
  decisions/{owner}__{repo}__{NNNN}     recorded decisions
  guards/{owner}__{repo}__{pr}          latest guard result per PR
  runs/{run_id} (+ events/{seq})        every workflow run and its trace
"""

from __future__ import annotations

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
_BATCH_LIMIT = 450  # Firestore allows 500 writes per batch


def pr_doc_id(repo: str, number: int) -> str:
    owner, _, name = repo.partition("/")
    return f"{owner}__{name}__{number}"


analysis_doc_id = pr_doc_id  # backwards-compatible name


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
async def save_question_set(qs: QuestionSetDoc) -> str:
    db = _client()
    doc = qs.model_dump(mode="json", exclude={"created_at"})
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
async def save_guard(repo: str, pr: int, data: dict[str, Any]) -> str:
    ref = _client().collection(GUARDS).document(pr_doc_id(repo, pr))
    await ref.set({**data, "repo": repo, "pr": pr, "updated_at": firestore.SERVER_TIMESTAMP})
    return ref.path


# --- runs ---
async def start_run(run_id: str, meta: dict[str, Any]) -> str:
    ref = _client().collection(RUNS).document(run_id)
    await ref.set(
        {**meta, "run_id": run_id, "status": "running", "started_at": firestore.SERVER_TIMESTAMP},
        merge=True,
    )
    return ref.path


async def finish_run(run_id: str, meta: dict[str, Any], events: list[TraceEvent]) -> str:
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
