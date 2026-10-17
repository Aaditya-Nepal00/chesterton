"""Guard judge (MODEL_SCREEN), a parallel worker over deterministic matches."""

from __future__ import annotations

from google.adk import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.workflow import node

from app.agents.common import LLM_RETRY, MAX_PARALLEL_LLM, clip
from app.models.records import GuardJudgement

INSTRUCTION = """\
A new pull request edits code that is covered by a recorded decision. Decide how the
change relates to that decision:

- "changes_decision": the PR changes the decided value, dependency, config or behaviour
  itself (e.g. the recorded retry cap of 3 becomes 5, the chosen library is swapped).
- "touches_nearby": the PR edits the same lines or the code right next to them without
  changing the decision (formatting, renaming nearby code, adding a comment, an
  unrelated constant in the same block).
- "unrelated": the match is a coincidence (same file, unrelated change).

Use the decision id exactly as given. note: one short neutral line describing what this
PR does to it (e.g. "Raises WEBHOOK_MAX_RETRIES from 3 to 5."). No warnings or advice.
"""


def build_item(decision: dict, match: dict) -> str:
    return (
        f"== Decision {decision['id']}: {decision['title']} ==\n"
        f"Decision: {decision.get('decision', '')}\n"
        f"Reason: {clip(decision.get('reason_quote', ''), 400)}\n"
        f"Symbols: {', '.join(decision.get('symbols') or []) or '(none)'}\n\n"
        f"== This PR's hunk in {match['path']} ==\n{clip(match['hunk_text'], 4000)}\n"
    )


def build_agent(model: str | BaseLlm):
    agent = Agent(
        name="judge",
        model=model,
        instruction=INSTRUCTION,
        output_schema=GuardJudgement,
    )
    return node(
        agent, parallel_worker=True, max_parallel_workers=MAX_PARALLEL_LLM, retry_config=LLM_RETRY
    )
