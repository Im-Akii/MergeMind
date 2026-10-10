"""Core data models shared across the engine, agents and store."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Phase(StrEnum):
    # issue
    NEW = "NEW"
    TRIAGING = "TRIAGING"
    NEEDS_INFO = "NEEDS_INFO"
    DUPLICATE_SUGGESTED = "DUPLICATE_SUGGESTED"
    QUESTION = "QUESTION"
    OUT_OF_SCOPE_SUGGESTED = "OUT_OF_SCOPE_SUGGESTED"
    FEATURE_REVIEW = "FEATURE_REVIEW"
    SECURITY_ESCALATED = "SECURITY_ESCALATED"
    ESCALATED = "ESCALATED"
    ACCEPTED = "ACCEPTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ACCESS_OFFERED = "ACCESS_OFFERED"
    ACCEPTED_UNCLAIMED = "ACCEPTED_UNCLAIMED"
    ACCESS_GRANTED = "ACCESS_GRANTED"
    WORKING = "WORKING"
    # pull request (tracked on the linked issue)
    PR_ANALYSING = "PR_ANALYSING"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    SAFETY_HOLD = "SAFETY_HOLD"
    READY_FOR_MAINTAINER = "READY_FOR_MAINTAINER"
    MERGING = "MERGING"
    POST_MERGE_WATCH = "POST_MERGE_WATCH"
    REVERT_PROPOSED = "REVERT_PROPOSED"
    DONE = "DONE"
    # terminal / blocking
    CLOSED = "CLOSED"
    PERMISSION_BLOCKED = "PERMISSION_BLOCKED"


TERMINAL = {Phase.DONE, Phase.CLOSED}


class Event(BaseModel):
    id: int | None = None
    repo: str
    number: int
    source: Literal["poll", "webhook", "timer", "command", "internal"]
    kind: str
    external_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class IssueState(BaseModel):
    repo: str
    number: int
    title: str = ""
    author: str = ""
    phase: Phase = Phase.NEW
    version: int = 0
    disposition: str | None = None
    contributor: str | None = None
    pr_number: int | None = None
    approved_by: str | None = None
    approved_head_sha: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)  # scope, acceptance criteria, etc.


class Effect(BaseModel):
    """Something the engine must do. Handlers are idempotent; `key` dedupes them."""

    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)

    def key(self, state: IssueState) -> str:
        import hashlib
        import json

        raw = json.dumps(
            [state.repo, state.number, self.kind, self.payload, state.version], sort_keys=True
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ------------------------------------------------------------ evidence / reports


class Evidence(BaseModel):
    id: str  # ev_xxx
    kind: Literal[
        "code_region", "commit", "issue", "doc", "diff", "test_result", "impact", "check_run"
    ]
    locator: str
    sha: str | None = None
    excerpt: str = ""
    score: float | None = None
    sensitive: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)


class Claim(BaseModel):
    text: str
    evidence: list[str]


class Region(BaseModel):
    evidence: str
    file: str
    start: int
    end: int
    symbol: str | None = None
    role: Literal["root_cause", "entry_point", "related"] = "related"


class MissingField(BaseModel):
    field: str
    why: str


class TriageReport(BaseModel):
    issue_type: Literal["bug", "feature", "question", "docs", "chore", "support", "spam"]
    summary: str
    disposition: Literal[
        "accept",
        "needs_info",
        "duplicate",
        "question",
        "feature_review",
        "out_of_scope",
        "security",
        "escalate",
    ]
    duplicate_of: int | None = None
    missing_info: list[MissingField] = Field(default_factory=list)
    localization: list[Region] = Field(default_factory=list)
    localization_confidence: float = 0.0
    scope: list[Region] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    regression_test_hint: str | None = None
    open_questions: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    disposition_confidence: float = 0.0
