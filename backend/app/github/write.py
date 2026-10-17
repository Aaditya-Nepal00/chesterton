"""GitHub write client. App installation token only, allow-listed repos only.

Every method talks to one repo; the constructor refuses any repo outside
Settings.write_repos (the demo repo locally, the workflow's own repo in Actions).
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from app.config import Settings, get_settings
from app.github.auth import AppAuth
from app.github.client import GitHubClient, GitHubError
from app.github.markers import has_marker

logger = logging.getLogger(__name__)


class WriteNotAllowed(RuntimeError):
    pass


class GitHubWriter:
    def __init__(self, repo: str, token: str, *, settings: Settings | None = None):
        settings = settings or get_settings()
        if not settings.can_write(repo):
            raise WriteNotAllowed(
                f"writes to {repo} are not allowed (allowed: {settings.write_repos})"
            )
        self.repo = repo
        self.gh = GitHubClient(token, cache_dir=None)
        self._default_branch: str | None = None

    @classmethod
    async def for_repo(cls, repo: str, settings: Settings | None = None) -> GitHubWriter:
        settings = settings or get_settings()
        if not settings.can_write(repo):
            raise WriteNotAllowed(f"writes to {repo} are not allowed")
        auth = AppAuth.from_settings(settings)
        if auth is None:
            raise WriteNotAllowed("GitHub App is not configured (GITHUB_APP_ID + private key)")
        return cls(repo, await auth.installation_token(repo), settings=settings)

    async def aclose(self) -> None:
        await self.gh.aclose()

    async def __aenter__(self) -> GitHubWriter:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # --- helpers ---
    async def _call(self, method: str, path: str, *, ok: tuple[int, ...] = (200,), **kwargs) -> Any:
        resp = await self.gh.request(method, f"/repos/{self.repo}{path}", **kwargs)
        if resp.status_code not in ok:
            raise GitHubError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else None

    # --- reads ---
    async def get_pr(self, number: int) -> dict:
        return await self._call("GET", f"/pulls/{number}")

    async def get_issue_comment(self, comment_id: int) -> dict:
        return await self._call("GET", f"/issues/comments/{comment_id}")

    async def get_default_branch(self) -> str:
        if self._default_branch is None:
            self._default_branch = (await self._call("GET", ""))["default_branch"]
        return self._default_branch

    async def get_file(self, path: str, ref: str | None = None) -> dict | None:
        """{"content": str, "sha": str} or None if missing."""
        resp = await self.gh.request(
            "GET", f"/repos/{self.repo}/contents/{path}", params={"ref": ref} if ref else None
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise GitHubError(f"GET contents/{path} -> HTTP {resp.status_code}")
        data = resp.json()
        content = base64.b64decode(data.get("content") or "").decode("utf-8")
        return {"content": content, "sha": data["sha"], "path": data["path"]}

    async def list_dir(self, path: str, ref: str | None = None) -> list[dict]:
        resp = await self.gh.request(
            "GET", f"/repos/{self.repo}/contents/{path}", params={"ref": ref} if ref else None
        )
        if resp.status_code == 404:
            return []
        if resp.status_code != 200:
            raise GitHubError(f"GET contents/{path} -> HTTP {resp.status_code}")
        data = resp.json()
        return data if isinstance(data, list) else [data]

    async def list_issue_comments(self, number: int) -> list[dict]:
        out: list[dict] = []
        for page in range(1, 31):
            batch = await self._call(
                "GET", f"/issues/{number}/comments", params={"per_page": 100, "page": page}
            )
            out.extend(batch)
            if len(batch) < 100:
                break
        return out

    async def find_comment_with_marker(self, pr: int, kind: str) -> dict | None:
        """The first Bot-authored comment on `pr` carrying a chesterton:<kind> pr=<pr> marker.

        Only Bot comments count, so a human pasting a marker can't hijack the thread.
        """
        for c in await self.list_issue_comments(pr):
            if (c.get("user") or {}).get("type") == "Bot" and has_marker(c.get("body"), kind, pr):
                return c
        return None

    # --- writes ---
    async def create_branch(self, name: str) -> str:
        """Create refs/heads/<name> at the default branch head; reuse it if it exists."""
        default = await self.get_default_branch()
        head = await self._call("GET", f"/git/ref/heads/{default}")
        sha = head["object"]["sha"]
        resp = await self.gh.request(
            "POST", f"/repos/{self.repo}/git/refs", json={"ref": f"refs/heads/{name}", "sha": sha}
        )
        if resp.status_code == 201:
            return sha
        if resp.status_code == 422 and "already exists" in resp.text:
            existing = await self._call("GET", f"/git/ref/heads/{name}")
            return existing["object"]["sha"]
        raise GitHubError(f"create branch {name} -> HTTP {resp.status_code}: {resp.text[:300]}")

    async def put_file(self, branch: str, path: str, content: str, message: str) -> dict:
        existing = await self.get_file(path, ref=branch)
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if existing:
            if existing["content"] == content:
                return {"path": path, "unchanged": True}
            body["sha"] = existing["sha"]
        data = await self._call("PUT", f"/contents/{path}", ok=(200, 201), json=body)
        return {"path": path, "commit": data["commit"]["sha"]}

    async def open_pr(self, head: str, title: str, body: str, base: str | None = None) -> dict:
        """Open a PR, or return the already-open one for this head branch."""
        base = base or await self.get_default_branch()
        resp = await self.gh.request(
            "POST",
            f"/repos/{self.repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
            retry_5xx=False,
        )
        if resp.status_code == 201:
            return resp.json()
        owner = self.repo.split("/")[0]
        existing = await self._call(
            "GET", "/pulls", params={"head": f"{owner}:{head}", "state": "open"}
        )
        if existing:
            return existing[0]
        raise GitHubError(f"open PR {head} -> HTTP {resp.status_code}: {resp.text[:300]}")

    async def post_issue_comment(self, number: int, body: str, *, kind: str | None = None) -> dict:
        """Post a comment. Not blindly retried on 5xx (it may have been created anyway):
        if `kind` is given, a 5xx is followed by a marker lookup before one retry."""
        resp = await self.gh.request(
            "POST",
            f"/repos/{self.repo}/issues/{number}/comments",
            json={"body": body},
            retry_5xx=False,
        )
        if resp.status_code == 201:
            return resp.json()
        if resp.status_code >= 500 and kind:
            found = await self.find_comment_with_marker(number, kind)
            if found:
                return found
            return await self._call(
                "POST", f"/issues/{number}/comments", ok=(201,), json={"body": body}
            )
        raise GitHubError(
            f"post comment on #{number} -> HTTP {resp.status_code}: {resp.text[:300]}"
        )

    async def update_issue_comment(self, comment_id: int, body: str) -> dict:
        return await self._call("PATCH", f"/issues/comments/{comment_id}", json={"body": body})
