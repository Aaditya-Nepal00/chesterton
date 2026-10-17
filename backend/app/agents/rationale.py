"""Rationale agent (MODEL_MAIN), run once per decision as a parallel worker."""

from __future__ import annotations

from google.adk import Agent
from google.adk.workflow import node

from app.agents.common import (
    LLM_RETRY,
    MAX_PARALLEL_LLM,
    format_diff_evidence,
    format_non_diff_evidence,
)
from app.models.analysis import Decision, RationaleCheck

INSTRUCTION = """\
You check whether anyone wrote down WHY a decision in a pull request was made.

You get one decision and the PR's written context: description, commit messages,
review discussion, linked issues, and the decision's own diff hunks (code comments
in the diff count as written reasons).

"What" is not "why". "tweak retries", "update config", "bump timeout", "use httpx" or
restating the change is NOT a reason. A reason explains the motivation or constraint:
an incident, a measurement, an upstream limit, a bug it fixes, a requirement.

- status "found": a clear reason for THIS specific change is written down.
- status "partial": there is a hint (e.g. a linked issue about the area) but not the reason.
- status "self_evident": nobody needs to write the reason down, because it is common
  knowledge for any maintainer or explicitly implied by a linked source. Examples:
    * dropping an end-of-life runtime (Python 3.8 after Oct 2024, Node 16, Go 1.20)
    * a security fix or dependency bump that links a CVE or security advisory
    * a version bump that a linked upstream change or release note requires
    * a revert that links the change it reverts and that change is broken
  NOT self_evident: picking a specific number (retries 5 -> 3, timeout 30s), choosing
  one library over another, changing a default, reordering steps, a workaround. Those
  have project-specific reasons even when they look obvious.
- status "missing": nothing explains why, and it is not self-evident.

For found/partial, copy the exact sentence (verbatim, no paraphrase, no ellipsis) into
reason_quote and list the evidence id(s) it comes from in evidence_ids. For
self_evident, note MUST state the common-knowledge reason in one line (e.g. "Python 3.8
reached end of life in October 2024"); reason_quote may be empty. For missing, leave
reason_quote empty. Use the decision's id as decision_id. Keep note to one line.
"""


def build_item(decision: Decision, evidence: dict[str, dict]) -> str:
    """The text input for one worker. Each item is independent."""
    return (
        f"== Decision {decision.id} ({decision.category}) ==\n{decision.summary}\n"
        f"Why it might matter: {decision.why_it_matters}\n\n"
        f"== Its diff hunks ==\n{format_diff_evidence(evidence, decision.diff_evidence, 8000)}\n\n"
        f"== Written context ==\n{format_non_diff_evidence(evidence, budget=16000)}\n"
    )


def build_agent(model: str):
    agent = Agent(
        name="rationale",
        model=model,
        instruction=INSTRUCTION,
        output_schema=RationaleCheck,
    )
    # parallel worker over a list of items, at most 2 LLM calls in flight,
    # retry applied to each item's agent run.
    return node(
        agent,
        parallel_worker=True,
        max_parallel_workers=MAX_PARALLEL_LLM,
        retry_config=LLM_RETRY,
    )
