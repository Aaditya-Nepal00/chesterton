# Chesterton

Chesterton is a GitHub App that catches consequential decisions in merged PRs that have no written reason, asks the author why, and records the answer as a cited decision record.
When later PRs touch that code, it surfaces the recorded reason and owner, so the reasoning isn't lost when people move on.

## Local development (PowerShell)

Prereqs: Python 3.12 via [uv](https://docs.astral.sh/uv/), Node 20+. Copy `.env.example` to `.env` and fill it in.
Firestore uses Application Default Credentials: `gcloud auth application-default login`.

```powershell
# Backend (http://localhost:8080)
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8080

# Frontend dev server (http://localhost:5173, proxies /api to :8080)
cd frontend
npm install
npm run dev

# Production-style: build the frontend, then the backend serves it at /
cd frontend; npm run build

# Smoke tests (from backend/)
uv run python scripts/smoke_gemini.py     # also writes MODEL_MAIN / MODEL_SCREEN into .env
uv run python scripts/smoke_adk.py
uv run python scripts/smoke_firestore.py
uv run python scripts/smoke_github.py     # read-only

# Tests and lint (from backend/)
uv run pytest -q
uv run ruff check .
```
