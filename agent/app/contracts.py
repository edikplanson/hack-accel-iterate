"""Shared boundaries between retrieval, orchestration, persistence and the UI."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field


def now() -> datetime:
    return datetime.now(timezone.utc)


class Source(BaseModel):
    source_id: str
    kind: Literal["database", "document"]
    title: str
    table: str | None = None
    record_id: str | None = None
    document_id: str | None = None
    page: int | None = None
    version: str | None = None
    effective_date: str | None = None
    content_hash: str | None = None
    passage: str | None = None
    retrieved_at: datetime = Field(default_factory=now)
    fictional: bool = False


class CustomerCandidate(BaseModel):
    client_id: int
    label: str
    clue: str = ""


class IdentityClues(BaseModel):
    client_id: int | None = None
    name: str | None = None
    phone: str | None = None


class IssueDetails(BaseModel):
    """Extracted conversation claims; these never update verified banking records."""
    declared_amount_eur: Decimal | None = None
    withdrawal_type: Literal['partiel', 'total'] | None = None
    mentioned_contract_id: str | None = None
    requested_timing: str | None = None


class Resolution(BaseModel):
    status: Literal["not_found", "ambiguous", "candidate", "confirmed", "conflict", "unresolved"]
    candidates: list[CustomerCandidate] = Field(default_factory=list)
    selected_client_id: int | None = None
    warnings: list[str] = Field(default_factory=list)


class Fact(BaseModel):
    category: str
    data: dict[str, Any]
    sources: list[Source] = Field(default_factory=list)


class SearchResult(BaseModel):
    passages: list[Source] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: Literal["ok", "missing", "conflicting", "unavailable"] = "ok"


class TranscriptEvent(BaseModel):
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    event_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(default=1, ge=1)
    speaker: Literal["customer", "advisor", "system"]
    text: str = Field(max_length=8000)
    is_final: bool = True
    timestamp: datetime = Field(default_factory=now)
    kind: Literal["transcript", "end_call"] = "transcript"


class TranscriptTurn(BaseModel):
    turn_id: str
    revision: int
    speaker: Literal["customer", "advisor", "system"]
    text: str
    is_final: bool
    timestamp: datetime
    event_id: str


class ExecutionEvent(BaseModel):
    event_id: int | None = None
    session_id: str
    trace_id: str
    kind: Literal["tool_started", "tool_completed", "tool_failed", "data_retrieved", "answer_produced", "queued", "processing", "failed", "customer_confirmed", "call_ended"]
    name: str
    summary: str = ""
    timestamp: datetime = Field(default_factory=now)


class ChatResponse(BaseModel):
    text: str
    chart: dict[str, Any] | None = None
    actions: list[dict[str, Any]] | None = None
    tool_calls_made: list[str] = Field(default_factory=list)
    session_id: str
    status: Literal["ready", "needs_confirmation", "needs_clarification", "processing", "error", "ended"] = "ready"
    issue_summary: str = ""
    clarification_questions: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    trace_id: str


class SessionState(BaseModel):
    session_id: str
    revision: int = 0
    processed_revision: int = 0
    turns: list[TranscriptTurn] = Field(default_factory=list)
    selected_client_id: int | None = None
    customer_confirmed: bool = False
    identity_clues: IdentityClues = Field(default_factory=IdentityClues)
    resolution: Resolution = Field(default_factory=lambda: Resolution(status="unresolved"))
    issue_summary: str = ""
    customer_statements: list[str] = Field(default_factory=list)
    issue_details: IssueDetails = Field(default_factory=IssueDetails)
    verified_facts: list[Fact] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    documentation: SearchResult = Field(default_factory=SearchResult)
    guidance: ChatResponse | None = None
    status: str = "idle"
    ended: bool = False
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    errors: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=now)


class CustomerRepository(Protocol):
    def resolve(self, clues: IdentityClues, selected_client_id: int | None = None) -> Resolution: ...
    def retrieve(self, client_id: int, categories: list[str]) -> list[Fact]: ...


class DocumentationRepository(Protocol):
    def search(self, query: str, limit: int = 5) -> SearchResult: ...
