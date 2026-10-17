"""Questions agent (MODEL_MAIN): short questions for the author about verified gaps."""

from __future__ import annotations

from google.adk import Agent

from app.agents.common import LLM_RETRY, format_diff_evidence
from app.models.analysis import Decision, QuestionSet, RationaleCheck

INSTRUCTION = """\
You write questions to the author of a merged pull request about decisions whose
reason is not written down anywhere. Write like a respectful senior teammate.

Rules:
- At most 3 questions in total, one per decision, in the order given.
- One or two sentences each. Name the file and the specific change.
- Ask why it was done, and when it would be safe to change it.
- Never suggest an answer, never guess the reason, never imply the change was wrong.
- It must be answerable in one line.
- evidence_id: one of the decision's diff evidence ids, exactly as given.
- snippet: 1-3 lines copied exactly from that hunk (the changed lines), without the
  leading +/- markers.
"""


def select_gaps(gaps: list[str], decisions: list[Decision], limit: int = 3) -> list[str]:
    """Pick up to `limit` gaps (gaps lists missing before partial), then restore
    decision order so the most consequential question comes first."""
    order = {d.id: i for i, d in enumerate(decisions)}
    return sorted(gaps[:limit], key=lambda g: order.get(g, len(order)))


def build_input(
    decisions: list[Decision],
    rationale: list[RationaleCheck],
    gaps: list[str],
    evidence: dict[str, dict],
    author: str | None,
) -> str:
    by_id = {d.id: d for d in decisions}
    status = {r.decision_id: r for r in rationale}
    blocks = []
    for gid in select_gaps(gaps, decisions):
        d = by_id[gid]
        r = status[gid]
        blocks.append(
            f"== {d.id} ({d.category}; rationale {r.status}) ==\n{d.summary}\n"
            f"Why it might matter: {d.why_it_matters}\n"
            + (f"Hint found: {r.reason_quote}\n" if r.reason_quote else "")
            + f"Diff evidence ids: {', '.join(d.diff_evidence)}\n"
            f"{format_diff_evidence(evidence, d.diff_evidence, 6000)}"
        )
    return f"PR author: @{author}\n\n" + "\n\n".join(blocks)


def build_agent(model: str) -> Agent:
    return Agent(
        name="questions",
        model=model,
        instruction=INSTRUCTION,
        output_schema=QuestionSet,
        output_key="questions_raw",
        retry_config=LLM_RETRY,
    )
