"""Application settings.

Environment variables always work on their own (GitHub Actions has no .env).
Locally, the repo-root .env is read too if it exists; real env vars win over it.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[2] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

DEFAULT_MAIN_CHAIN = ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash")
DEFAULT_SCREEN_CHAIN = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite")


def parse_chain(raw: str | None) -> list[str]:
    return [m.strip() for m in (raw or "").split(",") if m.strip()]


def build_chain(single: str | None, chain: str | None, default: tuple[str, ...]) -> list[str]:
    """Ordered, de-duplicated model chain.

    The single-model setting (MODEL_MAIN / the old `model-main` input) goes first, then
    MODEL_*_CHAIN, or the default chain when no chain is given.
    """
    out: dict[str, None] = {}
    for m in ([single.strip()] if single and single.strip() else []) + (
        parse_chain(chain) or list(default)
    ):
        out.setdefault(m, None)
    return list(out)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,  # optional: silently skipped when the file doesn't exist
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
    google_application_credentials: str | None = None  # SA key file for Firestore (Actions)
    firestore_database: str = "(default)"
    model_screen: str | None = None  # single model (old setting); goes first in the chain
    model_main: str | None = None
    model_screen_chain: str | None = None  # comma-separated fallback chain
    model_main_chain: str | None = None

    # GitHub
    github_token: str | None = None  # read-only PAT for public repos (local only)
    github_app_id: str | None = None
    github_app_private_key: str | None = None  # key contents (Actions)
    github_app_private_key_path: str | None = None  # key file (local)
    github_webhook_secret: str | None = None
    github_demo_repo: str | None = None
    smee_url: str | None = None

    # Set by the GitHub Actions runner
    github_actions: bool = False
    github_repository: str | None = None
    github_server_url: str = "https://github.com"
    github_run_id: str | None = None

    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    @property
    def gemini_backend(self) -> str:
        return "vertex" if self.google_genai_use_vertexai else "gemini-api"

    @property
    def main_chain(self) -> list[str]:
        return build_chain(self.model_main, self.model_main_chain, DEFAULT_MAIN_CHAIN)

    @property
    def screen_chain(self) -> list[str]:
        return build_chain(self.model_screen, self.model_screen_chain, DEFAULT_SCREEN_CHAIN)

    @property
    def github_app_configured(self) -> bool:
        return bool(
            self.github_app_id and (self.github_app_private_key or self.github_app_private_key_path)
        )

    @property
    def write_repos(self) -> set[str]:
        """The only repos Chesterton may write to (lower-cased).

        In GitHub Actions: the repository running the workflow. Locally: the demo repo.
        """
        repo = self.github_repository if self.github_actions else self.github_demo_repo
        return {repo.lower()} if repo else set()

    def can_write(self, repo: str) -> bool:
        return repo.lower() in self.write_repos

    @property
    def actions_run_url(self) -> str | None:
        if self.github_actions and self.github_repository and self.github_run_id:
            repo_url = f"{self.github_server_url}/{self.github_repository}"
            return f"{repo_url}/actions/runs/{self.github_run_id}"
        return None


def export_genai_env(settings: Settings) -> None:
    """Export the variables that ADK, google-genai and google-auth read from os.environ."""
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = (
        "TRUE" if settings.google_genai_use_vertexai else "FALSE"
    )
    os.environ["GOOGLE_CLOUD_LOCATION"] = settings.google_cloud_location
    if settings.google_cloud_project:
        os.environ["GOOGLE_CLOUD_PROJECT"] = settings.google_cloud_project
    if settings.google_api_key:
        os.environ["GOOGLE_API_KEY"] = settings.google_api_key
    if settings.google_application_credentials:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_application_credentials


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    export_genai_env(settings)
    return settings
