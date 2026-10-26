"""One document through the pipeline: parse → index → extract → verify → persist.

Every stage is idempotent and cached by content: the IR by (blob, parser version),
model answers by exact request, and a document whose bytes were already processed
with the same versions and config reuses that extraction at zero model cost.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from ..db import session_scope
from ..extraction.extractor import ExtractConfig, ExtractionOutcome, Extractor
from ..extraction.prompts import prompt_version
from ..llm.client import LLMClient, LLMRequestError, current_document, current_job, get_llm
from ..models import Blob, Chunk, Document, Extraction, Page, Setting, now
from ..parsing.ir import DocumentIR
from ..parsing.parser import parse_document
from ..parsing.pdf import FatalParseError
from ..queue.jobs import set_progress
from ..retrieval.chunking import ChunkData, chunk_document
from ..retrieval.embeddings import Embedder, get_embedder, to_bytes
from ..services import refresh_document_facts
from ..settings import Settings, get_settings
from ..storage import Storage
from ..versions import PIPELINE_VERSION, SCHEMA_VERSION

CALIBRATION_KEY = "calibration"


class FatalJobError(Exception):
    """Retrying cannot help (unreadable file, malformed request): dead-letter it."""


def pipeline_key(config: ExtractConfig, llm: LLMClient) -> str:
    models = "|".join(f"{t}={llm.model_for(t)}:{llm.effort_for(t)}" for t in ("fast", "strong", "vision"))
    return f"{PIPELINE_VERSION}|{SCHEMA_VERSION}|{prompt_version()}|{config.key()}|{models}"


def load_calibration() -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.get(Setting, CALIBRATION_KEY)
        return dict(row.value) if row and isinstance(row.value, dict) else None


@dataclass
class Deps:
    settings: Settings
    llm: LLMClient
    embedder: Embedder
    storage: Storage

    @classmethod
    def default(cls) -> Deps:
        s = get_settings()
        return cls(s, get_llm(), get_embedder(s), Storage(s))


async def parse_and_index(doc_id: str, deps: Deps, job_id: str | None = None) -> tuple[DocumentIR, list[ChunkData], Any]:
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        blob = s.get(Blob, doc.blob_sha256)
        blob_path = deps.storage.root / blob.path
        sha = doc.blob_sha256
    set_progress(job_id, stage="parse")
    t = time.monotonic()
    try:
        ir = await parse_document(Path(blob_path), sha, deps.llm, deps.settings, deps.storage)
    except FatalParseError as exc:
        raise FatalJobError(str(exc)) from exc
    parse_ms = int((time.monotonic() - t) * 1000)

    set_progress(job_id, stage="index", parse_ms=parse_ms)
    t = time.monotonic()
    chunks = chunk_document(ir)
    embeddings = None
    if chunks:
        try:
            embeddings = await deps.embedder.embed([f"{c.header(ir.title)}\n{c.text}" for c in chunks])
        except Exception:   # embeddings are an optimization; BM25 still works
            embeddings = None
    with session_scope() as s:
        s.execute(delete(Page).where(Page.document_id == doc_id))
        s.execute(delete(Chunk).where(Chunk.document_id == doc_id))
        for p in ir.pages:
            s.add(Page(document_id=doc_id, page_no=p.page_no, width=p.width, height=p.height, route=p.route,
                       route_reason=p.route_reason[:300], ocr_engine=p.ocr_engine, ocr_confidence=p.ocr_confidence,
                       text_quality=p.text_quality, char_count=p.char_count, has_table=p.has_table,
                       language=p.language, struck_words=sum(len(x.split()) for x in p.struck), text=p.text))
        for i, c in enumerate(chunks):
            s.add(Chunk(document_id=doc_id, ordinal=c.ordinal, page_start=c.page_start, page_end=c.page_end,
                        section=c.section, kind=c.kind, text=c.text,
                        embedding=to_bytes(embeddings[i]) if embeddings is not None else None))
        doc = s.get(Document, doc_id)
        doc.page_count, doc.language, doc.route_mix = ir.page_count, ir.language, ir.route_mix()
        doc.title = doc.title or ir.title
    set_progress(job_id, index_ms=int((time.monotonic() - t) * 1000))
    return ir, chunks, embeddings


def _persist(doc_id: str, outcome: ExtractionOutcome, config: ExtractConfig, key: str, purpose: str,
             reused_from: str | None = None) -> str:
    with session_scope() as s:
        ex = Extraction(document_id=doc_id, purpose=purpose, pipeline_key=key, pipeline_version=PIPELINE_VERSION,
                        schema_version=SCHEMA_VERSION, prompt_version=prompt_version(),
                        config={**config.__dict__}, mode=outcome.mode, data=outcome.data,
                        fields=[f.to_json() for f in outcome.fields], facts=outcome.facts, warnings=outcome.warnings,
                        selected_pages=outcome.selected_pages, escalated_groups=outcome.escalated,
                        llm_calls=outcome.stats.calls, prompt_tokens=outcome.stats.prompt_tokens,
                        completion_tokens=outcome.stats.completion_tokens, cost_usd=round(outcome.stats.cost_usd, 6),
                        latency_ms=outcome.latency_ms, reused_from=reused_from)
        s.add(ex)
        s.flush()
        if purpose == "production":
            doc = s.get(Document, doc_id)
            doc.latest_extraction_id = ex.id
            doc.status = "ready"
            doc.error = None
            doc.processed_at = now()
            refresh_document_facts(s, doc)
        return ex.id


def _reuse(doc_id: str, key: str) -> str | None:
    """Dedup: identical bytes already extracted with the same versions and config."""
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        prev = s.scalars(select(Extraction).join(Document, Document.id == Extraction.document_id)
                         .where(Document.blob_sha256 == doc.blob_sha256, Extraction.pipeline_key == key,
                                Extraction.purpose == "production", Extraction.document_id != doc_id)
                         .order_by(Extraction.created_at.desc())).first()
        if prev is None:
            return None
        clone = Extraction(document_id=doc_id, purpose="production", pipeline_key=key,
                           pipeline_version=prev.pipeline_version, schema_version=prev.schema_version,
                           prompt_version=prev.prompt_version, config=prev.config, mode=prev.mode, data=prev.data,
                           fields=prev.fields, facts=prev.facts, warnings=prev.warnings,
                           selected_pages=prev.selected_pages, escalated_groups=prev.escalated_groups,
                           llm_calls=0, prompt_tokens=0, completion_tokens=0, cost_usd=0.0, latency_ms=0,
                           reused_from=prev.id)
        s.add(clone)
        s.flush()
        doc.latest_extraction_id = clone.id
        doc.status = "ready"
        doc.processed_at = now()
        refresh_document_facts(s, doc)
        return clone.id


async def run_extraction(doc_id: str, ir: DocumentIR, chunks: list[ChunkData], embeddings: Any, config: ExtractConfig,
                         deps: Deps, *, purpose: str = "production", job_id: str | None = None,
                         calibration: dict | None = None) -> tuple[str, ExtractionOutcome]:
    set_progress(job_id, stage="extract")
    extractor = Extractor(deps.llm, deps.embedder if embeddings is not None else None, deps.settings)
    outcome = await extractor.extract(ir, chunks, config, embeddings, calibration)
    key = pipeline_key(config, deps.llm)
    ex_id = _persist(doc_id, outcome, config, key, purpose)
    set_progress(job_id, stage="done", extract_ms=outcome.latency_ms, llm_calls=outcome.stats.calls,
                 cost_usd=round(outcome.stats.cost_usd, 6), mode=outcome.mode, escalated=outcome.escalated)
    return ex_id, outcome


async def process_document(doc_id: str, *, job_id: str | None = None, force: bool = False,
                           config: ExtractConfig | None = None, deps: Deps | None = None) -> str:
    deps = deps or Deps.default()
    config = config or ExtractConfig.from_settings(deps.settings)
    tok_doc, tok_job = current_document.set(doc_id), current_job.set(job_id)
    try:
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            if doc is None:
                raise FatalJobError(f"document {doc_id} not found")
            doc.status = "processing"
        key = pipeline_key(config, deps.llm)
        ir, chunks, embeddings = await parse_and_index(doc_id, deps, job_id)
        if not force and (reused := _reuse(doc_id, key)):
            set_progress(job_id, stage="done", reused=True)
            return reused
        try:
            ex_id, _ = await run_extraction(doc_id, ir, chunks, embeddings, config, deps, job_id=job_id,
                                            calibration=load_calibration())
        except LLMRequestError as exc:
            if exc.status in (400, 404, 422):
                raise FatalJobError(str(exc)) from exc
            raise
        return ex_id
    except Exception as exc:
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            if doc is not None:
                doc.status = "failed" if isinstance(exc, FatalJobError) else "queued"
                doc.error = f"{type(exc).__name__}: {exc}"[:2000]
        raise
    finally:
        current_document.reset(tok_doc)
        current_job.reset(tok_job)
