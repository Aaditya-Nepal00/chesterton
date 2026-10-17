"""Shared agent settings and prompt-formatting helpers (deterministic)."""

from __future__ import annotations

from google.adk.workflow import RetryConfig

from app.analysis.evidence import is_diff_id

# 429 surfaces from ADK as _ResourceExhaustedError (a genai ClientError), 503 as
# genai ServerError. RetryConfig matches class names across the MRO. Other
# ClientErrors (400/403/404) are not retried: retrying them only burns quota.
LLM_RETRY = RetryConfig(
    max_attempts=3,  # 1 call + max 2 retries
    initial_delay=5.0,
    backoff_factor=2.0,
    max_delay=30.0,
    exceptions=["_ResourceExhaustedError", "ServerError"],
)
MAX_PARALLEL_LLM = 2
LONG_QUOTA_WAIT_S = 120  # a 429 asking us to wait longer than this is a daily quota


class QuotaExhausted(Exception):
    """A 429 that retrying within this run cannot fix (e.g. the free-tier daily quota)."""


def quota_retry_delay(error: BaseException) -> float | None:
    """Seconds Gemini asks us to wait (RetryInfo.retryDelay) on a 429, if present."""
    if getattr(error, "code", None) != 429:
        return None
    details = getattr(error, "details", None) or {}
    for d in (details.get("error") or details).get("details") or []:
        if isinstance(d, dict) and str(d.get("@type", "")).endswith("RetryInfo"):
            raw = str(d.get("retryDelay", "")).rstrip("s")
            try:
                return float(raw)
            except ValueError:
                return None
    return None


def is_long_quota(error: BaseException) -> bool:
    if getattr(error, "code", None) != 429:
        return False
    delay = quota_retry_delay(error)
    return "PerDay" in str(error) or (delay is not None and delay > LONG_QUOTA_WAIT_S)


MAX_EVIDENCE_CHARS = 1500
MAX_CONTEXT_CHARS = 24000


def clip(text: str | None, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def describe_evidence(eid: str, entry: dict) -> str:
    kind = entry["kind"]
    where = ""
    if entry.get("path"):
        where = f" on {entry['path']}" + (
            f":{entry['line_start']}" if entry.get("line_start") else ""
        )
    return f"[{eid}] ({kind}{where})"


def format_non_diff_evidence(evidence: dict[str, dict], budget: int = MAX_CONTEXT_CHARS) -> str:
    """pr:body, commits, comments, issues; each clipped, total within budget."""
    out: list[str] = []
    used = 0
    for eid, entry in evidence.items():
        if is_diff_id(eid):
            continue
        block = f"{describe_evidence(eid, entry)}\n{clip(entry['text'], MAX_EVIDENCE_CHARS)}"
        if used + len(block) > budget:
            out.append("[… more evidence omitted for length]")
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out) if out else "(none)"


def format_diff_evidence(
    evidence: dict[str, dict], ids: list[str] | None = None, budget: int = MAX_CONTEXT_CHARS
) -> str:
    out: list[str] = []
    used = 0
    for eid, entry in evidence.items():
        if not is_diff_id(eid) or (ids is not None and eid not in ids):
            continue
        block = f"{describe_evidence(eid, entry)}\n{entry['text']}"
        if used + len(block) > budget:
            out.append("[… more diff hunks omitted for length]")
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out) if out else "(none)"
