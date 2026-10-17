"""Extract agent (MODEL_MAIN): list the concrete decisions in the diff."""

from __future__ import annotations

from google.adk import Agent

from app.agents.common import LLM_RETRY, clip, format_diff_evidence, format_non_diff_evidence
from app.models.analysis import DecisionList
from app.models.pr import PRContext

INSTRUCTION = """\
You find the decisions in a merged pull request.

A decision is a specific choice in this diff that someone could later reverse because
they don't know the reason: a number (limit, timeout, retry count, size), a dependency
added, removed or swapped, a config value, a data shape or migration, an API contract
(signature, default, status code, response format), a workaround, or an ordering.

Rules:
- Each decision's summary is ONE sentence naming the file or symbol and the concrete
  change (old -> new when visible).
- Every decision must cite at least one diff evidence id exactly as written in the
  input, in the form diff:<path>#<n>. Only cite ids that appear in the input.
- Never invent decisions. Ignore formatting, renames with no behaviour change, typo
  fixes, docs, comments, and tests that merely cover the change.
- One decision per independent choice: a retry count and a timeout are two decisions
  even if they sit next to each other. Cite only the hunk(s) for that choice.
- If a change is only a mechanical consequence of another decision in this PR (e.g.
  removing a version conditional or CI matrix entry after dropping that version), fold
  it into that decision's evidence instead of listing it separately.
- Return at most 5 decisions, most consequential first. Returning zero is fine.
- confidence: 0.0-1.0, how sure you are this is a real, deliberate choice.
- Set id to D1, D2, ... in order.
"""


def build_input(pr: PRContext, evidence: dict[str, dict]) -> str:
    note = "\nNote: some patches were truncated or omitted for length." if pr.diff_truncated else ""
    return (
        f"PR: {pr.repo}#{pr.number} — {pr.title}\nAuthor: {pr.author}{note}\n\n"
        f"== Context (description, commits, discussion, linked issues) ==\n"
        f"{format_non_diff_evidence(evidence, budget=12000)}\n\n"
        f"== Diff hunks ==\n{format_diff_evidence(evidence, budget=40000)}\n\n"
        f"Title again for reference: {clip(pr.title, 200)}"
    )


def build_agent(model: str) -> Agent:
    return Agent(
        name="extract",
        model=model,
        instruction=INSTRUCTION,
        output_schema=DecisionList,
        output_key="extracted",
        retry_config=LLM_RETRY,
    )
