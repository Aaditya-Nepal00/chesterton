"""Screen agent (MODEL_SCREEN): is there any decision worth asking about?"""

from __future__ import annotations

import json

from google.adk import Agent
from google.adk.models.base_llm import BaseLlm

from app.agents.common import LLM_RETRY, clip
from app.analysis.evidence import split_hunks
from app.analysis.signals import file_priority
from app.models.analysis import ScreenResult, Signals
from app.models.pr import PRContext

INSTRUCTION = """\
You screen merged pull requests. Decide whether this PR contains a decision that a
future maintainer might undo without knowing why: a changed limit, timeout or retry
value, a new or swapped dependency, a config or infrastructure change, a data or
schema migration, a public API change, a workaround, a revert, or a deliberate ordering.

Not consequential: documentation, typos, formatting, comment-only changes, version
bumps and release housekeeping, and PRs that only add or adjust tests.

The "signals" are heuristics computed by a script; use them as hints, not as truth.
Answer with consequential=true/false and 1-3 short reasons naming the specific
files or changes you based it on.
"""

MAX_EXCERPT_HUNKS = 12
MAX_EXCERPT_LINES = 12


def build_input(pr: PRContext, signals: Signals) -> str:
    files = "\n".join(
        f"- {f.status} {f.path} (+{f.additions}/-{f.deletions})"
        + (f" [patch omitted: {f.patch_skipped}]" if f.patch_skipped else "")
        for f in pr.files[:60]
    )
    if len(pr.files) > 60:
        files += f"\n- … {len(pr.files) - 60} more files"
    excerpts: list[str] = []
    for f in sorted(pr.files, key=lambda f: file_priority(f.path)):
        for h in split_hunks(f.patch):
            if len(excerpts) >= MAX_EXCERPT_HUNKS:
                break
            changed = [k + t for k, _, t in h.lines if k in "+-"][:MAX_EXCERPT_LINES]
            if changed:
                excerpts.append(f"{f.path} {h.header}\n" + "\n".join(changed))
    return (
        f"Title: {pr.title}\n"
        f"Author: {pr.author}\n"
        f"Labels: {', '.join(pr.labels) or '(none)'}\n\n"
        f"Description:\n{clip(pr.body, 2000) or '(empty)'}\n\n"
        f"Signals:\n{json.dumps(signals.model_dump(), indent=1)}\n\n"
        f"Files:\n{files}\n\n"
        f"Diff excerpts (changed lines only):\n" + ("\n\n".join(excerpts) or "(none)")
    )


def build_agent(model: str | BaseLlm) -> Agent:
    return Agent(
        name="screen",
        model=model,
        instruction=INSTRUCTION,
        output_schema=ScreenResult,
        output_key="screen",
        retry_config=LLM_RETRY,
    )
