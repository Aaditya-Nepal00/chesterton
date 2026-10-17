"""Smoke test: Gemini via google-genai (free tier, Flash models only).

Lists models, probes candidate models with a one-line prompt, tests structured
output, and writes MODEL_MAIN / MODEL_SCREEN into the repo-root .env.
"""

from __future__ import annotations

import re
import sys
import time

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from app.config import ENV_FILE, get_settings

MAIN_CANDIDATES = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
]
SCREEN_CANDIDATES = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
]


class DecisionCheck(BaseModel):
    decision: str
    has_written_reason: bool
    evidence: list[str]


def describe_error(exc: Exception) -> str:
    if isinstance(exc, errors.APIError):
        tag = ""
        if exc.code == 429:
            tag = "RATE LIMIT (429) "
        elif exc.code == 404:
            tag = "NOT FOUND (404) "
        elif exc.code in (400, 403) and re.search(
            r"billing|permission|quota|paid|free tier", str(exc.message), re.I
        ):
            tag = f"BILLING/PERMISSION ({exc.code}) "
        return f"{tag}{exc.code} {exc.status}: {(exc.message or '')[:200]}"
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def probe(client: genai.Client, model: str) -> dict:
    start = time.perf_counter()
    try:
        resp = client.models.generate_content(
            model=model,
            contents="Reply with exactly: OK",
            config=types.GenerateContentConfig(max_output_tokens=256),
        )
        latency = time.perf_counter() - start
        text = (resp.text or "").strip()
        return {"model": model, "ok": True, "latency": latency, "reply": text, "error": None}
    except Exception as exc:  # noqa: BLE001 - we report every failure
        latency = time.perf_counter() - start
        return {
            "model": model,
            "ok": False,
            "latency": latency,
            "reply": None,
            "error": describe_error(exc),
        }


def write_env_models(model_main: str | None, model_screen: str | None) -> None:
    """Set MODEL_MAIN / MODEL_SCREEN in .env; every other line is left byte-for-byte."""
    updates = {k: v for k, v in (("MODEL_MAIN", model_main), ("MODEL_SCREEN", model_screen)) if v}
    if not updates:
        return
    raw = ENV_FILE.read_bytes() if ENV_FILE.exists() else b""
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    lines = raw.splitlines(keepends=True)
    seen: set[str] = set()
    for i, line in enumerate(lines):
        m = re.match(rb"^\s*(MODEL_MAIN|MODEL_SCREEN)\s*=", line)
        if m:
            key = m.group(1).decode()
            if key in updates:
                ending = line[len(line.rstrip(b"\r\n")) :] or newline
                lines[i] = f"{key}={updates[key]}".encode() + ending
                seen.add(key)
    missing = [k for k in updates if k not in seen]
    if missing and lines and not lines[-1].endswith((b"\n", b"\r")):
        lines[-1] += newline
    for key in missing:
        lines.append(f"{key}={updates[key]}".encode() + newline)
    ENV_FILE.write_bytes(b"".join(lines))


def main() -> int:
    settings = get_settings()
    print(f"Backend: {settings.gemini_backend}")
    if not settings.google_genai_use_vertexai and not settings.google_api_key:
        print("SKIPPED: GOOGLE_API_KEY not set")
        return 1

    client = genai.Client()  # reads GOOGLE_GENAI_USE_VERTEXAI / GOOGLE_API_KEY from env

    print("\n== a. models.list() ==")
    try:
        models = client.models.list(config={"page_size": 200})
        names = sorted(m.name for m in models if "gemini" in (m.name or ""))
        print(f"{len(names)} gemini models:")
        for n in names:
            print(f"  {n}")
    except Exception as exc:  # noqa: BLE001
        print(f"listing failed (non-fatal): {describe_error(exc)}")

    print("\n== b. one-line generation probes ==")
    results: dict[str, list[dict]] = {"MAIN": [], "SCREEN": []}
    for group, candidates in (("MAIN", MAIN_CANDIDATES), ("SCREEN", SCREEN_CANDIDATES)):
        for model in candidates:
            r = probe(client, model)
            results[group].append(r)
            status = f"OK reply={r['reply']!r}" if r["ok"] else f"FAIL {r['error']}"
            print(f"  [{group:6}] {model:24} {r['latency']:6.2f}s  {status}")
            if not r["ok"] and r["error"].startswith("RATE LIMIT"):
                print("  -> 429 hit; not retrying.")

    model_main = next((r["model"] for r in results["MAIN"] if r["ok"]), None)
    model_screen = next((r["model"] for r in results["SCREEN"] if r["ok"]), None)
    print(f"\nFirst working MAIN: {model_main}\nFirst working SCREEN: {model_screen}")

    print("\n== c. structured output ==")
    if model_main:
        start = time.perf_counter()
        try:
            resp = client.models.generate_content(
                model=model_main,
                contents=(
                    "PR diff: MAX_RETRIES changed from 5 to 3 in payments/webhook.py. "
                    "PR description: 'tweak retries'. No other discussion. "
                    "Identify the decision, whether a written reason exists, and quote evidence."
                ),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=DecisionCheck,
                ),
            )
            parsed = resp.parsed
            print(f"  model={model_main} latency={time.perf_counter() - start:.2f}s")
            print(f"  parsed type: {type(parsed).__name__}")
            print(f"  {parsed!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {describe_error(exc)}")
    else:
        print("  SKIPPED: no working MAIN model")

    print("\n== d. write .env ==")
    write_env_models(model_main, model_screen)
    print(f"  MODEL_MAIN={model_main}  MODEL_SCREEN={model_screen} (written to {ENV_FILE.name})")
    return 0 if model_main and model_screen else 1


if __name__ == "__main__":
    sys.exit(main())
