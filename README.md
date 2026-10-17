# Chesterton

Chesterton catches consequential decisions in merged PRs that have no written reason, asks the author why, and records the answer as a cited decision record in `decisions/`.
When a later PR touches that code, it posts the recorded reason, when it's safe to change, and the owner, so the reasoning isn't lost when people move on.

## How it works

Chesterton runs as a GitHub Action in your repo and talks to GitHub as its own GitHub App.

1. **PR merged** → a multi-agent [Google ADK](https://google.github.io/adk-docs/) graph workflow reads the diff, description, commits, review threads and linked issues; Gemini finds decisions (limits, dependencies, config, migrations, API changes) and checks whether the reason is written anywhere. Every claim is verified against the evidence in plain Python. If reasons are missing, it posts one short comment with 1–3 questions.
2. **Author replies** → Gemini turns the answer into a decision record (the reason is quoted verbatim from the reply), and Chesterton opens a PR adding `decisions/NNNN-slug.md`.
3. **Later PR touches that code** → a deterministic path/symbol match plus a Gemini judge posts one calm, non-blocking comment with the recorded reason and owner.

**Architecture:** GitHub Actions is the runtime (no server). Each event runs `python -m app.action`, which dispatches to one of three ADK 2.x graph workflows (`analyze_pr`, `record_answer`, `guard_pr`) using Gemini Flash models through the Gemini API. Firestore stores question sets, decisions, guard results and a step-by-step trace of every run (`runs/{run_id}/events`). The FastAPI app in `backend/app/main.py` is for local development only.

## Install in your repo

1. **Create a GitHub App** (Settings → Developer settings → GitHub Apps → New):
   - Webhook: off (the Action receives events, not the App).
   - Repository permissions: Contents **read & write**, Issues **read & write**, Pull requests **read & write**, Metadata read.
   - Generate a private key (`.pem`), note the App ID, and **install the App** on your repo.
2. **Firestore:** a Google Cloud project with Firestore (Native mode, free tier is enough) and a service account with `roles/datastore.user`. Create a JSON key for it.
3. **Add secrets and a variable** to your repo (Settings → Secrets and variables → Actions):
   - Secrets: `GOOGLE_API_KEY` (Gemini API key from Google AI Studio), `GCP_SA_KEY` (the service-account JSON), `CHESTERTON_APP_ID`, `CHESTERTON_APP_PRIVATE_KEY` (the full `.pem` contents).
   - Variable: `GCP_PROJECT` (your Google Cloud project id).
4. **Copy [`examples/chesterton.yml`](examples/chesterton.yml)** to `.github/workflows/chesterton.yml`.

That's it: merge a PR with an unexplained limit or dependency change and Chesterton will ask about it. You can also run it by hand from the Actions tab (`workflow_dispatch` with `mode` and `pr`).

## Local development (PowerShell)

Prereqs: Python 3.12 via [uv](https://docs.astral.sh/uv/), Node 20+. Copy `.env.example` to `.env` and fill it in (or set the same variables in the environment; `.env` is optional).
Firestore uses Application Default Credentials: `gcloud auth application-default login`, or set `GOOGLE_APPLICATION_CREDENTIALS`.

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

# Analyze any public PR (read-only, never posts)
uv run python scripts/analyze_pr.py encode/httpx 3592 --no-persist
uv run python scripts/list_prs.py pallets/flask --limit 30

# Run the Action's code path locally (writes only to GITHUB_DEMO_REPO, as the App)
uv run python scripts/run_event.py --kind analyze --repo owner/demo --pr 1 --dry-run
uv run python scripts/run_event.py --kind record  --repo owner/demo --pr 1 --comment-id 123
uv run python scripts/run_event.py --kind guard   --repo owner/demo --pr 2

# Smoke tests (from backend/)
uv run python scripts/smoke_gemini.py     # also writes MODEL_MAIN / MODEL_SCREEN into .env
uv run python scripts/smoke_adk.py
uv run python scripts/smoke_firestore.py
uv run python scripts/smoke_github.py     # read-only

# Tests and lint (from backend/)
uv run pytest -q
uv run ruff check .
```
