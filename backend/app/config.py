"""Application settings, loaded from the repo-root .env file."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[2] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # blank values fall back to the default (None)
        extra="ignore",
    )

    app_name: str = "Chesterton"

    # Gemini / Google Cloud
    google_cloud_project: str | None = None
    google_genai_use_vertexai: bool = False
    google_api_key: str | None = None  # required when not using Vertex AI
    google_cloud_location: str = "global"
    firestore_database: str = "(default)"
    model_screen: str | None = None
    model_main: str | None = None

    # GitHub
    github_token: str | None = None
    github_app_id: str | None = None
    github_app_private_key_path: str | None = None
    github_webhook_secret: str | None = None
    github_demo_repo: str | None = None
    smee_url: str | None = None

    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    @property
    def gemini_backend(self) -> str:
        return "vertex" if self.google_genai_use_vertexai else "gemini-api"

    @property
    def github_app_configured(self) -> bool:
        return bool(self.github_app_id and self.github_app_private_key_path)


def export_genai_env(settings: Settings) -> None:
    """Export the variables that ADK and google-genai read from os.environ."""
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = (
        "TRUE" if settings.google_genai_use_vertexai else "FALSE"
    )
    os.environ["GOOGLE_CLOUD_LOCATION"] = settings.google_cloud_location
    if settings.google_cloud_project:
        os.environ["GOOGLE_CLOUD_PROJECT"] = settings.google_cloud_project
    if settings.google_api_key:
        os.environ["GOOGLE_API_KEY"] = settings.google_api_key


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    export_genai_env(settings)
    return settings
