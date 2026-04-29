from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    request_id: UUID
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: ErrorBody


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    model_id: str


class Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: UUID
    content: str = Field(max_length=4000)
    article_number: str | None = None
    section_title: str | None = None
    point_number: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    source_id: str
    source_url: str | None = None
    source_title: str | None = None
    namespace_id: str
    score: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] | None = None


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marker: str
    chunk: Chunk


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str


class StyleHints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer_max_chars: int = Field(default=2000, ge=100, le=10000)
    cite_inline: bool = True
    tone: Literal["formal", "casual"] = "formal"


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(max_length=2000)
    language: Literal["ro"]
    namespaces: list[str] = Field(min_length=1, max_length=10)
    top_k: int = Field(default=10, ge=1, le=50)
    hint_article_number: str | None = None
    rerank: bool = True
    include_answer: bool = True
    conversation_history: list[ConversationTurn] = Field(default_factory=list, max_length=15)
    style_hints: StyleHints = Field(default_factory=StyleHints)


class QueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    answer: str | None = None
    citations: list[Citation]
    usage: Usage
    latency_ms: int = Field(ge=0)
    model_version: str
    retrieval_strategy: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    trace_id: str | None = None


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace_id: str
    source_id: str
    source_type: Literal["url", "file"]
    url: HttpUrl | None = None
    mime_type_hint: str | None = None
    metadata: dict[str, Any] | None = None
    callback_url: HttpUrl | None = None

    @field_validator("url")
    @classmethod
    def require_url_if_needed(cls, value: HttpUrl | None, info: Any) -> HttpUrl | None:
        if info.data.get("source_type") == "url" and value is None:
            raise ValueError("url is required when source_type=url")
        return value


class IngestStatus(str, Enum):
    queued = "queued"
    fetching = "fetching"
    extracting = "extracting"
    chunking = "chunking"
    embedding = "embedding"
    indexing = "indexing"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class IngestProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["queued", "fetching", "extracting", "chunking", "embedding", "indexing", "done"]
    percent: int = Field(ge=0, le=100)
    chunks_created: int = Field(ge=0)


class IngestError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    retryable: bool


class IngestJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    namespace_id: str | None = None
    source_id: str | None = None
    status: IngestStatus
    progress: IngestProgress | None = None
    submitted_at: datetime
    completed_at: datetime | None = None
    estimated_completion_at: datetime | None = None
    error: IngestError | None = None
    content: str | None = None
    mime_type: str | None = None
    source_url: str | None = None
    source_title: str | None = None


class NamespaceStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace_id: str
    chunk_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    total_tokens_indexed: int = Field(ge=0)
    last_ingested_at: datetime | None = None
    embedding_model: str
    embedding_dim: int = Field(ge=1)


class HealthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "degraded", "down"]
    version: str
    uptime_seconds: int = Field(ge=0)
    dependencies: dict[str, Literal["ok", "degraded", "down"]] | None = None


class DeleteNamespaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    status: str
    sla: str


class EvalRequest(QueryRequest):
    expected_citations: list[str] = Field(default_factory=list)
    expected_answer_keywords: list[str] = Field(default_factory=list)


class EvalBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    citation_precision_at_k: float = Field(ge=0.0, le=1.0)
    keyword_match_rate: float = Field(ge=0.0, le=1.0)


class EvalResponse(QueryResponse):
    eval: EvalBlock


class SourceChunkRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    tenant_id: str
    namespace_id: str
    source_id: str
    source_url: str | None
    source_title: str | None
    article_number: str | None
    content: str
    metadata: dict[str, Any]
    embedding: list[float] | None = None
    chunk_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=utc_now)


class IdempotencyRecord(BaseModel):
    key: UUID
    tenant_id: str
    payload_hash: str
    job_id: str
    created_at: datetime = Field(default_factory=utc_now)


class SourceMetadata(BaseModel):
    source_title: str | None = None
    language: str | None = None
    document_type: str | None = None
    published_at: date | None = None
