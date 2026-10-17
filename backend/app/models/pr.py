"""Pull-request context models (what we fetch from GitHub)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ChangedFile(BaseModel):
    path: str
    status: str
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    patch_truncated: bool = False
    patch_skipped: str | None = None  # why the patch was not kept (lockfile, generated, budget)


class Commit(BaseModel):
    sha: str
    message: str
    author: str | None = None


CommentKind = Literal["review_comment", "review", "issue_comment", "linked_issue_comment"]


class Comment(BaseModel):
    id: str
    kind: CommentKind
    author: str | None = None
    body: str
    url: str | None = None
    path: str | None = None
    line: int | None = None
    created_at: datetime | None = None


class LinkedIssue(BaseModel):
    number: int
    title: str
    body: str = ""
    url: str | None = None


class PRContext(BaseModel):
    repo: str
    number: int
    title: str
    url: str
    author: str | None = None
    author_is_bot: bool = False
    merged_at: datetime | None = None
    base_branch: str | None = None
    head_ref: str | None = None
    state: str | None = None  # OPEN / CLOSED / MERGED
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    files: list[ChangedFile] = Field(default_factory=list)
    commits: list[Commit] = Field(default_factory=list)
    comments: list[Comment] = Field(default_factory=list)
    linked_issues: list[LinkedIssue] = Field(default_factory=list)
    diff_truncated: bool = False
    fetched_at: datetime
