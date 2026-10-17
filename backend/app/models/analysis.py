"""Analysis models: deterministic signals, LLM outputs, and the final result.

LLM output schemas never fail validation on length: lists are truncated and
numbers clamped in validators, while the JSON schema still tells the model the limit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ConstantChange(BaseModel):
    path: str
    line: int | None = None
    old: str
    new: str
    name: str


class Signals(BaseModel):
    score: int = 0
    dependency_files: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    migration_files: list[str] = Field(default_factory=list)
    constant_changes: list[ConstantChange] = Field(default_factory=list)
    is_revert: bool = False
    is_docs_only: bool = False
    is_release_or_bot: bool = False


class ScreenResult(BaseModel):
    consequential: bool
    reasons: list[str] = Field(default_factory=list)


DecisionCategory = Literal[
    "dependency",
    "config_constant",
    "architecture",
    "data_migration",
    "api_contract",
    "infra",
    "revert",
    "workaround",
    "other",
]


class Decision(BaseModel):
    id: str = Field(description="Decision id, e.g. D1. Reassigned in order after extraction.")
    summary: str = Field(description="One sentence naming the specific choice made in the diff.")
    category: DecisionCategory
    diff_evidence: list[str] = Field(
        description="One or more diff evidence ids (diff:<path>#<n>) showing the change."
    )
    why_it_matters: str = Field(description="What could break if someone reversed it unknowingly.")
    confidence: float = Field(description="0.0-1.0: how sure you are this is a real decision.")

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return min(1.0, max(0.0, float(v)))


class DecisionList(BaseModel):
    decisions: list[Decision] = Field(default_factory=list, json_schema_extra={"maxItems": 5})

    @field_validator("decisions", mode="before")
    @classmethod
    def _cap(cls, v: list) -> list:
        return list(v or [])[:5]


RationaleStatus = Literal["found", "partial", "missing", "self_evident"]


class RationaleCheck(BaseModel):
    decision_id: str
    status: RationaleStatus
    reason_quote: str | None = Field(
        default=None, description="Exact sentence copied from the evidence (found/partial only)."
    )
    evidence_ids: list[str] = Field(
        default_factory=list, description="Evidence ids containing reason_quote."
    )
    note: str = Field(
        default="",
        description="One line. Required for self_evident: state the common-knowledge reason.",
    )


class Question(BaseModel):
    decision_id: str
    text: str
    evidence_id: str = Field(description="The diff evidence id the question is about.")
    snippet: str = Field(description="1-3 lines copied exactly from that diff hunk.")


class QuestionSet(BaseModel):
    questions: list[Question] = Field(default_factory=list, json_schema_extra={"maxItems": 3})

    @field_validator("questions", mode="before")
    @classmethod
    def _cap(cls, v: list) -> list:
        return list(v or [])[:3]


class RenderedQuestion(BaseModel):
    """A verified question with its location resolved from the evidence index."""

    decision_id: str
    text: str
    evidence_id: str
    snippet: str
    path: str | None = None
    line: int | None = None
    url: str | None = None


class AnalysisResult(BaseModel):
    repo: str
    number: int
    title: str = ""
    url: str = ""
    author: str | None = None
    run_id: str
    status: Literal["skipped", "no_gaps", "gaps_found"]
    skip_reason: str | None = None
    signals: Signals | None = None
    screen: ScreenResult | None = None
    decisions: list[Decision] = Field(default_factory=list)
    rationale: list[RationaleCheck] = Field(default_factory=list)
    verifier_notes: list[str] = Field(default_factory=list)
    questions: list[RenderedQuestion] = Field(default_factory=list)
    comment_markdown: str | None = None
    models: dict[str, str | None] = Field(default_factory=dict)
    # evidence id -> html_url (diff hunk line, review/issue comment, commit, linked issue)
    evidence_urls: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
