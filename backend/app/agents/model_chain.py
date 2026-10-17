"""Ordered model chains with automatic fallback on the Gemini free-tier daily quota.

`FallbackGemini` is an ADK BaseLlm that wraps a chain such as
gemini-3.8-flash -> gemini-3.6-flash -> gemini-2.5-flash:

- daily-quota 429 (QuotaFailure ...PerDay..., or RetryInfo > 2 min): the model is marked
  exhausted until the next 00:00 UTC (cached in Firestore meta/model_quota so later runs
  skip it without spending a request), and the SAME request is retried on the next
  model inside the same node run.
- per-minute 429 and 5xx: re-raised unchanged, so the TracePlugin wait + LLM_RETRY
  backoff apply as before.
- every model exhausted: AllModelsExhausted, which LLM_RETRY does not retry, so the
  node (and the run) fails with a clear reason.

The model actually used is written into LlmResponse.custom_metadata["chesterton_model"];
skipped models into ["chesterton_skipped"]. TracePlugin turns those into LlmCall rows.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from pydantic import Field, PrivateAttr

from app.agents.common import is_long_quota
from app.config import DEFAULT_MAIN_CHAIN, DEFAULT_SCREEN_CHAIN  # noqa: F401  (re-export)

logger = logging.getLogger(__name__)

META_COLLECTION = "meta"
QUOTA_DOC = "model_quota"


def next_utc_midnight(now: datetime) -> datetime:
    return datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)


class AllModelsExhausted(Exception):
    """Every model in the chain is out of daily quota. Not retried by LLM_RETRY."""

    def __init__(self, chain: list[str], skipped: list[dict]):
        self.chain = chain
        self.skipped = skipped
        until = min((s.get("until") or "" for s in skipped), default="") or "00:00 UTC"
        super().__init__(
            f"all models in chain {chain} are out of daily Gemini quota "
            f"(earliest reset {until}); run stopped without retrying"
        )


# --- quota cache ---------------------------------------------------------------


class QuotaBackend(Protocol):
    async def load(self) -> dict[str, dict]: ...
    async def save(self, model: str, entry: dict) -> None: ...


class MemoryQuotaBackend:
    def __init__(self, data: dict[str, dict] | None = None):
        self.data = dict(data or {})

    async def load(self) -> dict[str, dict]:
        return dict(self.data)

    async def save(self, model: str, entry: dict) -> None:
        self.data[model] = entry


class FirestoreQuotaBackend:
    """meta/model_quota: {"models": {"gemini-3_8-flash": {model, exhausted_until, ...}}}"""

    @staticmethod
    def _key(model: str) -> str:
        return model.replace(".", "_").replace("/", "_")

    async def load(self) -> dict[str, dict]:
        from app.store.firestore import _client

        snap = await _client().collection(META_COLLECTION).document(QUOTA_DOC).get()
        models = (snap.to_dict() or {}).get("models", {}) if snap.exists else {}
        return {v["model"]: v for v in models.values() if isinstance(v, dict) and "model" in v}

    async def save(self, model: str, entry: dict) -> None:
        from google.cloud import firestore

        from app.store.firestore import _client

        ref = _client().collection(META_COLLECTION).document(QUOTA_DOC)
        await ref.set(
            {
                "models": {self._key(model): {**entry, "model": model}},
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )


class QuotaRegistry:
    """Which models are out of daily quota, shared by every node in the process."""

    def __init__(
        self,
        backend: QuotaBackend | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.backend = backend
        self.clock = clock
        self._until: dict[str, datetime] = {}
        self._loaded = False

    async def ensure_loaded(self) -> None:
        if self._loaded or self.backend is None:
            self._loaded = True
            return
        self._loaded = True
        try:
            for model, entry in (await self.backend.load()).items():
                until = datetime.fromisoformat(entry["exhausted_until"])
                self._until[model] = until
        except Exception as exc:  # a cache problem must never stop a run
            logger.warning("could not load model quota cache: %s", type(exc).__name__)

    def exhausted_until(self, model: str) -> datetime | None:
        until = self._until.get(model)
        return until if until and self.clock() < until else None

    async def mark_exhausted(self, model: str, reason: str) -> datetime:
        now = self.clock()
        until = next_utc_midnight(now)
        self._until[model] = until
        if self.backend is not None:
            try:
                await self.backend.save(
                    model,
                    {
                        "exhausted_until": until.isoformat(),
                        "marked_at": now.isoformat(),
                        "reason": reason[:300],
                    },
                )
            except Exception as exc:
                logger.warning("could not save model quota cache: %s", type(exc).__name__)
        return until


_REGISTRY: QuotaRegistry | None = None


def get_quota_registry() -> QuotaRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = QuotaRegistry(FirestoreQuotaBackend())
    return _REGISTRY


def set_quota_registry(registry: QuotaRegistry | None) -> None:
    """Tests (and offline tools) swap in an in-memory registry."""
    global _REGISTRY
    _REGISTRY = registry


# --- the fallback model ----------------------------------------------------------


def _default_factory(name: str) -> BaseLlm:
    from google.adk.models.google_llm import Gemini

    return Gemini(model=name)


class FallbackGemini(BaseLlm):
    chain: list[str] = Field(default_factory=list)
    role: str = "main"  # "main" or "screen", for traces
    # A regular field (not a PrivateAttr) so it survives ADK's agent clones; tests pass a
    # fake factory here and must never fall back to real Gemini.
    factory: Callable[[str], BaseLlm] | None = Field(default=None, exclude=True)
    _llms: dict[str, BaseLlm] = PrivateAttr(default_factory=dict)

    @classmethod
    def create(
        cls,
        chain: list[str],
        *,
        role: str = "main",
        factory: Callable[[str], BaseLlm] | None = None,
    ) -> FallbackGemini:
        if not chain:
            raise ValueError("model chain is empty")
        return cls(model=chain[0], chain=list(chain), role=role, factory=factory)

    @property
    def capabilities(self):  # same capabilities as the head of the chain
        return self._llm(self.chain[0]).capabilities

    def _llm(self, name: str) -> BaseLlm:
        if name not in self._llms:
            self._llms[name] = (self.factory or _default_factory)(name)
        return self._llms[name]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        registry = get_quota_registry()
        await registry.ensure_loaded()
        skipped: list[dict[str, Any]] = []
        for name in self.chain:
            until = registry.exhausted_until(name)
            if until is not None:
                skipped.append(
                    {"model": name, "reason": "cached daily quota", "until": until.isoformat()}
                )
                continue
            request = llm_request.model_copy(update={"model": name})
            try:
                async for response in self._llm(name).generate_content_async(request, stream):
                    meta = dict(response.custom_metadata or {})
                    meta["chesterton_model"] = name
                    if skipped:
                        meta["chesterton_skipped"] = skipped
                    response.custom_metadata = meta
                    yield response
                return
            except Exception as exc:
                if not is_long_quota(exc):
                    exc.chesterton_model = name  # for the trace
                    raise  # per-minute 429 / 5xx / anything else: normal handling
                until = await registry.mark_exhausted(name, str(getattr(exc, "message", exc)))
                logger.warning("%s out of daily quota; falling back to next model", name)
                skipped.append({"model": name, "reason": "daily quota", "until": until.isoformat()})
        raise AllModelsExhausted(self.chain, skipped)


def make_model(chain: list[str], role: str) -> FallbackGemini:
    return FallbackGemini.create(chain, role=role)
