"""Async GitHub client: GraphQL + REST, on-disk response cache, rate-limit handling."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import REPO_ROOT

logger = logging.getLogger(__name__)

API = "https://api.github.com"
CACHE_DIR = REPO_ROOT / "backend" / ".cache" / "github"
_MAX_RATE_WAIT_S = 60


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        *,
        refresh: bool = False,
        cache_dir: Path | None = CACHE_DIR,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(
            base_url=API, headers=headers, timeout=60, transport=transport
        )
        self.refresh = refresh
        self.cache_dir = cache_dir
        self.rate_remaining: int | None = None
        self.rate_limit: int | None = None
        self.requests_made = 0
        self.cache_hits = 0

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- cache ---
    def _cache_path(self, key: dict) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _cache_get(self, key: dict) -> Any | None:
        path = self._cache_path(key)
        if self.refresh or path is None or not path.is_file():
            return None
        self.cache_hits += 1
        return json.loads(path.read_text(encoding="utf-8"))["response"]

    def _cache_put(self, key: dict, response: Any) -> None:
        path = self._cache_path(key)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"key": key, "response": response}), encoding="utf-8")

    # --- transport ---
    def _note_rate_limit(self, resp: httpx.Response) -> None:
        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining is not None:
            self.rate_remaining = int(remaining)
            self.rate_limit = int(resp.headers.get("x-ratelimit-limit", 0)) or None
            level = logging.WARNING if self.rate_remaining < 100 else logging.DEBUG
            logger.log(
                level,
                "GitHub %s quota remaining: %s/%s",
                resp.headers.get("x-ratelimit-resource", "?"),
                self.rate_remaining,
                self.rate_limit,
            )

    def _rate_wait(self, resp: httpx.Response) -> float | None:
        """Seconds to wait if this response is a rate limit, else None."""
        if resp.status_code not in (403, 429):
            return None
        if "retry-after" in resp.headers:
            return float(resp.headers["retry-after"])
        if resp.headers.get("x-ratelimit-remaining") == "0":
            reset = float(resp.headers.get("x-ratelimit-reset", time.time()))
            return max(0.0, reset - time.time()) + 1
        return None

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        for attempt in (1, 2):
            self.requests_made += 1
            resp = await self._http.request(method, path, **kwargs)
            self._note_rate_limit(resp)
            wait = self._rate_wait(resp)
            if wait is None:
                return resp
            if attempt == 2 or wait > _MAX_RATE_WAIT_S:
                raise GitHubError(
                    f"GitHub rate limited on {path} (HTTP {resp.status_code}); "
                    f"retry in {int(wait)}s"
                )
            logger.warning("GitHub rate limited; waiting %.0fs then retrying once", wait)
            await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    # --- public API ---
    async def graphql(self, query: str, variables: dict | None = None) -> dict:
        key = {"kind": "graphql", "query": " ".join(query.split()), "variables": variables or {}}
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        resp = await self._request(
            "POST", "/graphql", json={"query": query, "variables": variables}
        )
        if resp.status_code != 200:
            raise GitHubError(f"GraphQL HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        if body.get("errors"):
            raise GitHubError(f"GraphQL errors: {json.dumps(body['errors'])[:500]}")
        self._cache_put(key, body["data"])
        return body["data"]

    async def rest_get(self, path: str, params: dict | None = None) -> Any:
        key = {"kind": "rest", "path": path, "params": params or {}}
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        resp = await self._request("GET", path, params=params)
        if resp.status_code != 200:
            raise GitHubError(f"GET {path} HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        self._cache_put(key, data)
        return data

    async def rest_get_paginated(
        self, path: str, params: dict | None = None, *, per_page: int = 100, max_pages: int = 30
    ) -> list:
        items: list = []
        for page in range(1, max_pages + 1):
            batch = await self.rest_get(
                path, {**(params or {}), "per_page": per_page, "page": page}
            )
            items.extend(batch)
            if len(batch) < per_page:
                break
        return items
