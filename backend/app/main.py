"""FastAPI entrypoint."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from app.config import get_settings
from app.github.webhooks import verify_signature

logger = logging.getLogger("app")
logging.basicConfig(level=logging.INFO)

settings = get_settings()
app = FastAPI(title=settings.app_name)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "app_name": settings.app_name,
        "gemini_backend": settings.gemini_backend,
        "model_screen": settings.screen_chain[0],
        "model_main": settings.main_chain[0],
        "model_screen_chain": settings.screen_chain,
        "model_main_chain": settings.main_chain,
        "github_app_configured": settings.github_app_configured,
    }


@app.post("/api/github/webhook", status_code=202)
async def github_webhook(request: Request) -> JSONResponse:
    if not settings.github_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")
    body = await request.body()
    if not verify_signature(
        settings.github_webhook_secret, body, request.headers.get("X-Hub-Signature-256")
    ):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event", "unknown")
    action = None
    try:
        payload = await request.json()
        if isinstance(payload, dict):
            action = payload.get("action")
    except ValueError:
        pass
    logger.info("GitHub webhook received: event=%s action=%s", event, action)
    return JSONResponse({"accepted": True}, status_code=202)


# Serve the built frontend (SPA) when present. Registered last so /api routes win.
_dist = settings.frontend_dist.resolve()
if _dist.is_dir():

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (_dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(_dist):
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
