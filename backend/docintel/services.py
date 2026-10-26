"""Document-level operations shared by the API, the CLI and the worker."""
from __future__ import annotations

import mimetypes
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .extraction.derive import alert_level, derive_facts
from .extraction.schema import CRITICAL_FIELDS
from .models import Blob, Document, Extraction, FieldReview
from .queue.jobs import enqueue
from .storage import Storage
from .versions import PARSER_VERSION, PIPELINE_VERSION, SCHEMA_VERSION


def process_key(sha: str, config_key: str) -> str:
    return f"process:{sha}:{PIPELINE_VERSION}:{PARSER_VERSION}:{SCHEMA_VERSION}:{config_key}"


def ingest(session: Session, data: bytes, filename: str, *, source: str = "upload", lane: str = "interactive",
           enqueue_job: bool = True, config_key: str = "default", storage: Storage | None = None) -> tuple[Document, bool]:
    """Store the bytes (content-addressed), create the document, queue processing.
    Returns (document, duplicate) — duplicate when identical bytes were seen before."""
    storage = storage or Storage()
    if b"%PDF-" not in data[:1024]:     # the header may follow a few junk bytes (spec allows 1 KB)
        raise ValueError("only PDF files are supported")
    sha, path, created = storage.put_blob(data, ".pdf")
    if session.get(Blob, sha) is None:
        session.add(Blob(sha256=sha, size=len(data), mime=mimetypes.guess_type(filename)[0] or "application/pdf",
                         path=str(path.relative_to(storage.root))))
        session.flush()
    first = session.scalars(select(Document).where(Document.blob_sha256 == sha).order_by(Document.created_at)).first()
    doc = Document(blob_sha256=sha, filename=filename[:300], source=source, lane=lane, status="queued",
                   duplicate_of=first.id if first else None)
    session.add(doc)
    session.flush()
    if enqueue_job:
        enqueue(session, "process_document", document_id=doc.id, lane=lane,
                idempotency_key=f"{process_key(sha, config_key)}:{doc.id}")
    return doc, first is not None


def latest_reviews(session: Session, document_id: str) -> dict[str, FieldReview]:
    rows = session.scalars(select(FieldReview).where(FieldReview.document_id == document_id)
                           .order_by(FieldReview.created_at)).all()
    latest: dict[str, FieldReview] = {}
    for r in rows:
        latest[r.field] = r
    return latest


def effective_values(extraction: Extraction, reviews: dict[str, FieldReview]) -> tuple[dict[str, Any], dict[str, dict]]:
    values = {f["field"]: f["value"] for f in extraction.fields}
    meta = {f["field"]: f.get("meta") or {} for f in extraction.fields}
    for name, r in reviews.items():
        if r.action == "correct":
            values[name] = r.new_value
    return values, meta


def refresh_document_facts(session: Session, doc: Document) -> None:
    """Facts = latest extraction + human corrections, re-derived by code."""
    if not doc.latest_extraction_id:
        return
    ex = session.get(Extraction, doc.latest_extraction_id)
    if ex is None:
        return
    reviews = latest_reviews(session, doc.id)
    values, meta = effective_values(ex, reviews)
    facts = derive_facts(values, meta)
    level, days = alert_level(facts)
    facts["alert"] = level
    facts["alert_days"] = days
    doc.facts = facts
    doc.doc_type = facts.get("document_type") or doc.doc_type
    if facts.get("title"):
        doc.title = str(facts["title"])[:300]
    open_fields = [f for f in ex.fields if f.get("needs_review") and f["field"] not in reviews]
    doc.needs_review = bool(open_fields)
    crit = [f["confidence"] if f["field"] not in reviews else 1.0 for f in ex.fields if f["field"] in CRITICAL_FIELDS]
    doc.confidence = min(crit) if crit else None
    if doc.status in ("ready", "needs_review"):
        doc.status = "needs_review" if doc.needs_review else "ready"


def review_field(session: Session, doc: Document, field: str, action: str, new_value: Any = None,
                 note: str | None = None, reviewer: str = "reviewer") -> FieldReview:
    if action not in ("approve", "correct"):
        raise ValueError("action must be approve or correct")
    ex = session.get(Extraction, doc.latest_extraction_id) if doc.latest_extraction_id else None
    current = next((f["value"] for f in (ex.fields if ex else []) if f["field"] == field), None)
    r = FieldReview(document_id=doc.id, extraction_id=doc.latest_extraction_id, field=field, action=action,
                    old_value=current, new_value=new_value if action == "correct" else current, note=note,
                    reviewer=reviewer)
    session.add(r)
    doc.review_count = (doc.review_count or 0) + 1
    session.flush()
    refresh_document_facts(session, doc)
    return r
