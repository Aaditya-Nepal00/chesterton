"""parse_answer agent (MODEL_MAIN): turn the author's reply into structured answers."""

from __future__ import annotations

from google.adk import Agent

from app.agents.common import LLM_RETRY, clip
from app.models.records import AnswerSet, QuestionItem

INSTRUCTION = """\
You read a pull-request author's reply to questions about why they made certain choices,
and turn it into structured answers. A single reply may answer several questions, only
some of them, or none.

For each question the reply addresses, return one answer:
- qid: the question id (Q1, Q2, ...). Only use ids from the input.
- answered: true only if the reply gives an actual reason. "Not sure", "don't know",
  "ask X", "no reason", jokes or off-topic text are answered=false.
- title: a 3-8 word noun phrase naming the decision, e.g. "Webhook retry cap of 3".
- decision: one sentence stating what was decided, using the file/identifier names.
- reason: one sentence with the reason, in the author's terms. Do not add reasons the
  author did not give.
- reason_quote: the author's own words that give the reason, copied EXACTLY from the
  reply (verbatim substring, no paraphrase, no ellipsis).
- alternatives: alternatives the author mentions (optional, else []).
- constraints: constraints the author mentions (optional, else []).
- safe_to_change_when: when the author says it would be safe to change; if they did not
  say, leave it empty. Do not invent one.
- confidence: 0.0-1.0, how sure you are this answer maps to that question.

Skip questions the reply does not address at all.
"""


def build_input(open_questions: list[QuestionItem], reply_body: str, author: str | None) -> str:
    qs = "\n\n".join(
        f"[{q.qid}] {q.text}\n  decision: {q.decision_summary}\n"
        f"  where: {q.path}:{q.line}\n  code:\n"
        + "\n".join(f"    {ln}" for ln in (q.snippet or "").splitlines())
        for q in open_questions
    )
    return f"== Open questions ==\n{qs}\n\n== Reply from @{author} ==\n{clip(reply_body, 4000)}\n"


def build_agent(model: str) -> Agent:
    return Agent(
        name="parse_answer",
        model=model,
        instruction=INSTRUCTION,
        output_schema=AnswerSet,
        output_key="parsed_answers",
        retry_config=LLM_RETRY,
    )
