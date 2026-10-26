"""Relational model. Everything an answer depends on is stored with its provenance."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def uid() -> str:
    return uuid.uuid4().hex[:16]


def now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Aware UTC in Python, naive UTC in the database — SQLite has no time zones, and
    comparing naive with aware datetimes raises."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON, datetime: UTCDateTime()}


class Blob(Base):
    """Content-addressed file. Two uploads of the same bytes share one blob."""
    __tablename__ = "blobs"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(100))
    path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=now)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    blob_sha256: Mapped[str] = mapped_column(ForeignKey("blobs.sha256"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    title: Mapped[str | None] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(60), default="upload", index=True)
    lane: Mapped[str] = mapped_column(String(20), default="interactive")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(16))
    doc_type: Mapped[str | None] = mapped_column(String(40))
    route_mix: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    duplicate_of: Mapped[str | None] = mapped_column(String(32))
    latest_extraction_id: Mapped[str | None] = mapped_column(String(32))
    facts: Mapped[dict[str, Any] | None] = mapped_column(JSON)       # effective facts: extraction + corrections
    confidence: Mapped[float | None] = mapped_column(Float)          # lowest confidence among critical fields
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)
    processed_at: Mapped[datetime | None] = mapped_column()


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "page_no"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    width: Mapped[float] = mapped_column(Float)
    height: Mapped[float] = mapped_column(Float)
    route: Mapped[str] = mapped_column(String(20))                  # text_layer | ocr | vlm
    route_reason: Mapped[str] = mapped_column(String(300))
    ocr_engine: Mapped[str | None] = mapped_column(String(40))
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    text_quality: Mapped[float | None] = mapped_column(Float)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    has_table: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str | None] = mapped_column(String(16))
    struck_words: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(20), default="text")
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)


class Extraction(Base):
    __tablename__ = "extractions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(60), default="production", index=True)
    pipeline_key: Mapped[str] = mapped_column(String(200), index=True)
    pipeline_version: Mapped[str] = mapped_column(String(20))
    schema_version: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    mode: Mapped[str] = mapped_column(String(20))                     # full_document | grouped
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # raw model output, merged across groups
    fields: Mapped[list[Any]] = mapped_column(JSON, default=list)     # per-field value, evidence, checks, confidence
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict) # normalized + derived (code, not model)
    warnings: Mapped[list[Any]] = mapped_column(JSON, default=list)
    selected_pages: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    escalated_groups: Mapped[list[Any]] = mapped_column(JSON, default=list)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    reused_from: Mapped[str | None] = mapped_column(String(32))       # dedup: copied from this extraction
    created_at: Mapped[datetime] = mapped_column(default=now, index=True)


class FieldReview(Base):
    """A human decision on one field: approve the model's value or correct it.
    Corrections are live labels — the correction rate is the production accuracy signal."""
    __tablename__ = "field_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    extraction_id: Mapped[str | None] = mapped_column(String(32))
    field: Mapped[str] = mapped_column(String(60), index=True)
    action: Mapped[str] = mapped_column(String(20))                   # approve | correct
    old_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    note: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(80), default="reviewer")
    created_at: Mapped[datetime] = mapped_column(default=now, index=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(40), index=True)         # process_document | eval_run | canary
    document_id: Mapped[str | None] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    lane: Mapped[str] = mapped_column(String(20), default="interactive", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    run_after: Mapped[datetime] = mapped_column(default=now, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column()
    idempotency_key: Mapped[str | None] = mapped_column(String(300), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=now, index=True)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class LLMCall(Base):
    """One metered model call: the raw material for cost, latency and error monitoring."""
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(default=now, index=True)
    document_id: Mapped[str | None] = mapped_column(String(32), index=True)
    job_id: Mapped[str | None] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), index=True)
    purpose: Mapped[str] = mapped_column(String(60), index=True)
    tier: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(200))
    effort: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="ok")
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    request_hash: Mapped[str] = mapped_column(String(64), index=True)


class LLMCacheEntry(Base):
    __tablename__ = "llm_cache"
    request_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=now)


class GoldenSet(Base):
    __tablename__ = "golden_sets"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(40))                   # synthetic | cuad | reviewed
    created_at: Mapped[datetime] = mapped_column(default=now)


class GoldenDoc(Base):
    __tablename__ = "golden_docs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    set_name: Mapped[str] = mapped_column(ForeignKey("golden_sets.name", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    split: Mapped[str] = mapped_column(String(10), default="test", index=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON)              # gold facts, only for labeled fields
    labeled_fields: Mapped[list[Any]] = mapped_column(JSON)
    evidence_pages: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    strata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    page_texts_path: Mapped[str | None] = mapped_column(String(500))  # ground-truth text per page (OCR CER)
    created_at: Mapped[datetime] = mapped_column(default=now)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    set_name: Mapped[str] = mapped_column(String(80), index=True)
    split: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    config_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    n_docs: Mapped[int] = mapped_column(Integer, default=0)
    done_docs: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality: Mapped[float | None] = mapped_column(Float)
    cost_per_1k: Mapped[float | None] = mapped_column(Float)
    latency_p50_ms: Mapped[float | None] = mapped_column(Float)
    latency_p95_ms: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(default=now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column()


class EvalDoc(Base):
    __tablename__ = "eval_docs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True)
    golden_doc_id: Mapped[str] = mapped_column(String(32), index=True)
    extraction_id: Mapped[str | None] = mapped_column(String(32))
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    critical_correct: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)


class EvalItem(Base):
    __tablename__ = "eval_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True)
    golden_doc_id: Mapped[str] = mapped_column(String(32), index=True)
    field: Mapped[str] = mapped_column(String(60), index=True)
    gold: Mapped[Any] = mapped_column(JSON, nullable=True)
    pred: Mapped[Any] = mapped_column(JSON, nullable=True)
    outcome: Mapped[str] = mapped_column(String(10))                  # tp | tn | fp | fn | wrong | partial
    correct: Mapped[bool] = mapped_column(Boolean)
    tp: Mapped[int] = mapped_column(Integer, default=0)
    fp: Mapped[int] = mapped_column(Integer, default=0)
    fn: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_page_ok: Mapped[bool | None] = mapped_column(Boolean)
    retrieval_hit: Mapped[bool | None] = mapped_column(Boolean)


class Setting(Base):
    """Small runtime-editable settings (pricing table, calibration)."""
    __tablename__ = "settings_kv"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)
