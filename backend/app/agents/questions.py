"""Questions agent (MODEL_MAIN): short questions for the author about verified gaps."""

from __future__ import annotations

from google.adk import Agent

from app.agents.common import LLM_RETRY, format_diff_evidence
from app.models.analysis import Decision, QuestionSet, RationaleCheck

INSTRUCTION = """\
You ask the author of a merged pull request why they made a choice whose reason is not
written down. Write like a teammate leaving a quick code-review comment.

Rules:
- At most 3 questions in total, one per decision, in the order given.
- At most ~25 words each, plain words.
- Put the file and identifier in backticks.
- Changed value (a "-" line shows the old value): "Why <new> instead of <old>? When would
  it be safe to change?" The <old> MUST be copied from a removed ("-") line in the diff.
- New code (no old value in the diff): ask plainly, "Why <choice>? When would it be safe
  to change?" Do NOT add "instead of ...".
- Never mention library, framework or industry defaults, best practices, or typical
  values unless that exact text appears in the evidence. Do not compare to anything you
  cannot see in the diff.
- Never suggest an answer, never guess the reason, never imply the change was wrong.
- No openers like "Could you clarify the motivation behind", "I was wondering", "Great PR".

Good:
- (old line "-WEBHOOK_MAX_RETRIES = 5") Why `WEBHOOK_MAX_RETRIES = 3` in `webhook.py` instead of 5? When would it be safe to raise it?
- (new file) Why `WEBHOOK_TIMEOUT_SECONDS = 15` in `webhook.py`? When would it be safe to change?
- (old line "-import json") Why `orjson` instead of `json` in `serializers.py`? When could we drop it?

Bad:
- Why `pool_timeout=15` instead of the library default of 30s?  (invents a default)
- Why 3 retries instead of the usual 5?  (no 5 in the diff; invents a baseline)
- Could you clarify the motivation behind updating the retry configuration in this PR?  (vague, filler opener)
- Was 3 retries chosen because the provider rate-limits us?  (suggests an answer)
- Lowering retries seems risky; why did you do this?  (implies it was wrong)

Fields:
- evidence_id: one of the decision's diff evidence ids, exactly as given.
- snippet: the 1-3 changed lines about THIS decision only (usually just the line with
  the identifier you name), copied exactly from that hunk, without +/- markers.
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
