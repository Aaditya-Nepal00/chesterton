"""GitHub App auth (JWT -> installation token) and per-repo token selection."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import jwt

from app.config import REPO_ROOT, Settings

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
_REFRESH_MARGIN_S = 60


class GitHubAuthError(RuntimeError):
    pass


def make_app_jwt(app_id: str, private_key: str, now: float | None = None) -> str:
    """RS256 JWT for the App: iat backdated 60s, exp 9 minutes out (GitHub max is 10)."""
    now_i = int(now if now is not None else time.time())
    return jwt.encode(
        {"iat": now_i - 60, "exp": now_i + 9 * 60, "iss": str(app_id)},
        private_key,
        algorithm="RS256",
    )


def normalize_pem(key: str) -> str:
    """Accept a PEM pasted with literal "\\n" escapes (common for single-line secrets)."""
    key = key.strip()
    if "\\n" in key and "\n" not in key:
        key = key.replace("\\n", "\n")
    return key + "\n"


def resolve_key_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


@dataclass
class _CachedToken:
    token: str
    expires_at: float  # epoch seconds
    permissions: dict


class AppAuth:
    """Mints installation tokens and caches them until ~1 minute before expiry."""

    def __init__(self, app_id: str, private_key: str, *, clock=time.time):
        self.app_id = app_id
        self._private_key = private_key
        self._clock = clock
        self._tokens: dict[str, _CachedToken] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> AppAuth | None:
        if not settings.github_app_configured:
            return None
        if settings.github_app_private_key:
            return cls(settings.github_app_id, normalize_pem(settings.github_app_private_key))
        path = resolve_key_path(settings.github_app_private_key_path)
        if not path.is_file():
            raise GitHubAuthError(f"GitHub App private key file not found at {path}")
        return cls(settings.github_app_id, path.read_text())

    def app_jwt(self) -> str:
        return make_app_jwt(self.app_id, self._private_key, now=self._clock())

    def cached(self, repo: str) -> _CachedToken | None:
        tok = self._tokens.get(repo.lower())
        if tok and self._clock() < tok.expires_at - _REFRESH_MARGIN_S:
            return tok
        return None

    async def installation_token(self, repo: str, http: httpx.AsyncClient | None = None) -> str:
        tok = self.cached(repo)
        if tok:
            return tok.token
        own = http is None
        http = http or httpx.AsyncClient(base_url=API, headers=HEADERS, timeout=30)
        try:
            auth = {"Authorization": f"Bearer {self.app_jwt()}"}
            r = await http.get(f"/repos/{repo}/installation", headers=auth)
            if r.status_code != 200:
                raise GitHubAuthError(f"App not installed on {repo}: HTTP {r.status_code}")
            inst_id = r.json()["id"]
            r = await http.post(f"/app/installations/{inst_id}/access_tokens", headers=auth)
            if r.status_code != 201:
                raise GitHubAuthError(f"Installation token request failed: HTTP {r.status_code}")
            body = r.json()
            expires = _parse_github_time(body["expires_at"])
            self._tokens[repo.lower()] = _CachedToken(
                body["token"], expires, body.get("permissions", {})
            )
            return body["token"]
        finally:
            if own:
                await http.aclose()


def _parse_github_time(value: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class TokenSelector:
    """App installation token for the writable repo (demo repo locally, the workflow's repo
    in Actions); GITHUB_TOKEN (read-only PAT) for everything else."""

    def __init__(self, settings: Settings, app_auth: AppAuth | None = None):
        self.settings = settings
        self.app_auth = app_auth

    def uses_app(self, repo: str) -> bool:
        return bool(self.app_auth and self.settings.can_write(repo))

    async def token_for(self, repo: str) -> str | None:
        if self.uses_app(repo):
            return await self.app_auth.installation_token(repo)
        return self.settings.github_token
