"""Models for the question -> answer -> decision record loop, and the guard."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

QuestionStatus = Literal["open", "answered"]


class QuestionItem(BaseModel):
    qid: str  # Q1, Q2, ...
    decision_id: str
    text: str
    evidence_id: str
    paths: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    status: QuestionStatus = "open"
    decision_summary: str = ""
    category: str = "other"
    path: str | None = None
    line: int | None = None
    evidence_url: str | None = None
    snippet: str = ""
    decision_number: int | None = None
    record_pr_url: str | None = None


class QuestionSetDoc(BaseModel):
    repo: str
    pr: int
    pr_url: str
    pr_title: str = ""
    author: str | None = None
    comment_id: int | None = None
    comment_url: str | None = None
    run_id: str
    questions: list[QuestionItem]
    processed_comment_ids: list[int] = Field(default_factory=list)
    created_at: datetime | None = None

    def open_questions(self) -> list[QuestionItem]:
        return [q for q in self.questions if q.status == "open"]


class ParsedAnswer(BaseModel):
    qid: str = Field(description="The question id this answers, e.g. Q1.")
    answered: bool = Field(description="False for 'don't know', 'not sure', off-topic replies.")
    title: str = Field(default="", description="3-8 word noun phrase naming the decision.")
    decision: str = Field(default="", description="One sentence: what was decided.")
    reason: str = Field(default="", description="One sentence: why, in the author's terms.")
    reason_quote: str = Field(
        default="", description="The author's words giving the reason, copied verbatim."
    )
    alternatives: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    safe_to_change_when: str = Field(
        default="", description="When it would be safe to change, from the answer; else ''."
    )
    confidence: float = 0.5

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return min(1.0, max(0.0, float(v)))


class AnswerSet(BaseModel):
    answers: list[ParsedAnswer] = Field(default_factory=list, json_schema_extra={"maxItems": 3})

    @field_validator("answers", mode="before")
    @classmethod
    def _cap(cls, v: list) -> list:
        return list(v or [])[:3]


class DecisionRecord(BaseModel):
    number: int
    id: str  # "0001"
    slug: str
    file_path: str  # decisions/0001-slug.md
    title: str
    repo: str
    owner: str | None
    paths: list[str]
    symbols: list[str]
    source_pr: int
    source_pr_url: str
    source_pr_title: str = ""
    answer_url: str | None
    answer_comment_id: int
    recorded: str  # YYYY-MM-DD
    qid: str
    question: str
    decision: str
    reason: str
    reason_quote: str
    alternatives: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    safe_to_change_when: str = ""
    evidence_url: str | None = None
    evidence_path: str | None = None
    evidence_line: int | None = None
    record_pr_url: str | None = None
    record_pr_number: int | None = None
    status: Literal["recorded"] = "recorded"


Verdict = Literal["changes_decision", "touches_nearby", "unrelated"]


class GuardJudgement(BaseModel):
    decision_id: str = Field(description="The decision id given in the input, e.g. 0001.")
    verdict: Verdict
    note: str = Field(default="", description="One short line: what this PR does to it.")
