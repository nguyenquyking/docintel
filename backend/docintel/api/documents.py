"""Documents: upload, list, inspect (with evidence), review, re-process."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, select

from ..db import session_scope
from ..extraction.schema import CRITICAL_FIELDS, REVIEW_FIELDS
from ..models import Blob, Document, Extraction, FieldReview, Job, LLMCall, Page
from ..queue.jobs import enqueue
from ..services import ingest, latest_reviews, process_key, refresh_document_facts, review_field
from ..settings import get_settings
from ..storage import Storage

router = APIRouter(prefix="/api", tags=["documents"])
MAX_UPLOAD_BYTES = 80 * 1024 * 1024


def doc_summary(d: Document) -> dict[str, Any]:
    f = d.facts or {}
    return {"id": d.id, "filename": d.filename, "title": d.title, "status": d.status, "error": d.error,
            "source": d.source, "lane": d.lane, "page_count": d.page_count, "language": d.language,
            "doc_type": f.get("document_type") or d.doc_type, "route_mix": d.route_mix or {},
            "duplicate_of": d.duplicate_of, "needs_review": d.needs_review, "confidence": d.confidence,
            "parties": f.get("parties") or [], "agreement_status": f.get("status"),
            "current_term_end": f.get("current_term_end"), "next_notice_deadline": f.get("next_notice_deadline"),
            "auto_renewal": f.get("auto_renewal"), "contract_value": f.get("contract_value"),
            "value_period": f.get("value_period"), "alert": f.get("alert"), "alert_days": f.get("alert_days"),
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "processed_at": d.processed_at.isoformat() if d.processed_at else None}


@router.post("/documents")
async def upload(files: list[UploadFile] = File(...), lane: str = Form("interactive")) -> dict[str, Any]:
    if lane not in ("interactive", "backfill"):
        raise HTTPException(400, "lane must be interactive or backfill")
    out = []
    storage = Storage()
    for f in files:
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            out.append({"filename": f.filename, "error": "file too large"})
            continue
        try:
            with session_scope() as s:
                doc, dup = ingest(s, data, f.filename or "document.pdf", lane=lane, storage=storage)
                out.append({"id": doc.id, "filename": doc.filename, "duplicate": dup, "duplicate_of": doc.duplicate_of})
        except ValueError as exc:
            out.append({"filename": f.filename, "error": str(exc)})
    return {"documents": out}


@router.get("/documents")
def list_documents(q: str | None = None, status: str | None = None, doc_type: str | None = None,
                   language: str | None = None, include_golden: bool = False, needs_review: bool | None = None,
                   limit: int = Query(100, le=500), offset: int = 0) -> dict[str, Any]:
    with session_scope() as s:
        stmt = select(Document).order_by(Document.created_at.desc())
        if not include_golden:
            stmt = stmt.where(~Document.source.like("golden:%"))
        if status:
            stmt = stmt.where(Document.status == status)
        if language:
            stmt = stmt.where(Document.language == language)
        if needs_review is not None:
            stmt = stmt.where(Document.needs_review == needs_review)
        docs = s.scalars(stmt).all()
        rows = [doc_summary(d) for d in docs]
    if doc_type:
        rows = [r for r in rows if r["doc_type"] == doc_type]
    if q:
        ql = q.casefold()
        rows = [r for r in rows if ql in (r["title"] or "").casefold() or ql in r["filename"].casefold()
                or any(ql in p.casefold() for p in r["parties"])]
    return {"total": len(rows), "documents": rows[offset:offset + limit]}


def _public_model(model: str) -> str:
    return get_settings().llm_display_name if get_settings().llm_provider != "fake" else model


@router.get("/documents/{doc_id}")
def get_document(doc_id: str) -> dict[str, Any]:
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404, "document not found")
        ex = s.get(Extraction, d.latest_extraction_id) if d.latest_extraction_id else None
        reviews = latest_reviews(s, doc_id)
        history = s.scalars(select(FieldReview).where(FieldReview.document_id == doc_id)
                            .order_by(FieldReview.created_at.desc())).all()
        pages = s.scalars(select(Page).where(Page.document_id == doc_id).order_by(Page.page_no)).all()
        calls = s.scalars(select(LLMCall).where(LLMCall.document_id == doc_id, LLMCall.run_id.is_(None))
                          .order_by(LLMCall.created_at.desc()).limit(200)).all()
        jobs = s.scalars(select(Job).where(Job.document_id == doc_id).order_by(Job.created_at.desc()).limit(20)).all()
        fields = []
        for f in (ex.fields if ex else []):
            r = reviews.get(f["field"])
            fields.append({**f, "critical": f["field"] in CRITICAL_FIELDS,
                           "effective_value": r.new_value if r and r.action == "correct" else f["value"],
                           "review": {"action": r.action, "value": r.new_value, "note": r.note,
                                      "at": r.created_at.isoformat()} if r else None})
        order = {name: i for i, name in enumerate(REVIEW_FIELDS)}
        fields.sort(key=lambda f: order.get(f["field"], 99))
        return {
            "document": doc_summary(d),
            "facts": d.facts or {},
            "extraction": None if ex is None else {
                "id": ex.id, "mode": ex.mode, "created_at": ex.created_at.isoformat(), "config": ex.config,
                "pipeline_version": ex.pipeline_version, "schema_version": ex.schema_version,
                "prompt_version": ex.prompt_version, "escalated_groups": ex.escalated_groups,
                "selected_pages": ex.selected_pages, "warnings": ex.warnings, "llm_calls": ex.llm_calls,
                "prompt_tokens": ex.prompt_tokens, "completion_tokens": ex.completion_tokens, "cost_usd": ex.cost_usd,
                "latency_ms": ex.latency_ms, "reused_from": ex.reused_from},
            "fields": fields,
            "pages": [{"page_no": p.page_no, "width": p.width, "height": p.height, "route": p.route,
                       "route_reason": p.route_reason, "ocr_engine": p.ocr_engine, "ocr_confidence": p.ocr_confidence,
                       "language": p.language, "has_table": p.has_table, "struck_words": p.struck_words,
                       "char_count": p.char_count} for p in pages],
            "reviews": [{"field": r.field, "action": r.action, "old_value": r.old_value, "new_value": r.new_value,
                         "note": r.note, "reviewer": r.reviewer, "at": r.created_at.isoformat()} for r in history],
            "llm_calls": [{"purpose": c.purpose, "tier": c.tier, "model": _public_model(c.model), "effort": c.effort,
                           "status": c.status, "prompt_tokens": c.prompt_tokens, "cached_tokens": c.cached_tokens,
                           "completion_tokens": c.completion_tokens, "reasoning_tokens": c.reasoning_tokens,
                           "cost_usd": c.cost_usd, "latency_ms": c.latency_ms, "cache_hit": c.cache_hit,
                           "error": c.error, "at": c.created_at.isoformat()} for c in calls],
            "jobs": [{"id": j.id, "status": j.status, "attempts": j.attempts, "lane": j.lane, "progress": j.progress,
                      "last_error": j.last_error, "created_at": j.created_at.isoformat()} for j in jobs],
        }


@router.get("/documents/{doc_id}/file")
def get_file(doc_id: str) -> FileResponse:
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404, "document not found")
        blob = s.get(Blob, d.blob_sha256)
        path = Storage().root / blob.path
    return FileResponse(path, media_type="application/pdf", filename=d.filename)


class ReprocessBody(BaseModel):
    preset: str | None = None
    lane: str = "interactive"


@router.post("/documents/{doc_id}/reprocess")
def reprocess(doc_id: str, body: ReprocessBody) -> dict[str, Any]:
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404, "document not found")
        d.status = "queued"
        job = enqueue(s, "process_document", document_id=doc_id, lane=body.lane,
                      payload={"force": True, "preset": body.preset},
                      idempotency_key=f"{process_key(d.blob_sha256, body.preset or 'default')}:{doc_id}:force",
                      force=True)
        return {"job_id": job.id}


class ReviewBody(BaseModel):
    field: str
    action: str
    value: Any = None
    note: str | None = None
    reviewer: str = "reviewer"


@router.post("/documents/{doc_id}/review")
def review(doc_id: str, body: ReviewBody) -> dict[str, Any]:
    if body.field not in REVIEW_FIELDS:
        raise HTTPException(400, f"unknown field {body.field}")
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None or not d.latest_extraction_id:
            raise HTTPException(404, "document has no extraction")
        try:
            review_field(s, d, body.field, body.action, body.value, body.note, body.reviewer)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"document": doc_summary(d), "facts": d.facts}


@router.post("/documents/{doc_id}/approve-all")
def approve_all(doc_id: str) -> dict[str, Any]:
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None or not d.latest_extraction_id:
            raise HTTPException(404, "document has no extraction")
        ex = s.get(Extraction, d.latest_extraction_id)
        done = latest_reviews(s, doc_id)
        for f in ex.fields:
            if f.get("needs_review") and f["field"] not in done:
                review_field(s, d, f["field"], "approve")
        refresh_document_facts(s, d)
        return {"document": doc_summary(d)}


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str) -> dict[str, Any]:
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is None:
            raise HTTPException(404, "document not found")
        if d.source.startswith("golden:"):
            raise HTTPException(400, "golden documents are managed from the evaluation page")
        s.execute(delete(Job).where(Job.document_id == doc_id, Job.status == "queued"))
        s.delete(d)
    return {"deleted": doc_id}


@router.get("/review")
def review_queue(limit: int = 200) -> dict[str, Any]:
    """Fields waiting for a human, most important first: critical fields, then lowest confidence."""
    items = []
    with session_scope() as s:
        docs = s.scalars(select(Document).where(Document.needs_review.is_(True))).all()
        for d in docs:
            ex = s.get(Extraction, d.latest_extraction_id) if d.latest_extraction_id else None
            done = latest_reviews(s, d.id)
            for f in (ex.fields if ex else []):
                if f.get("needs_review") and f["field"] not in done:
                    ev = next((e for e in f.get("evidence", []) if e.get("page")), None)
                    items.append({"document_id": d.id, "title": d.title or d.filename, "field": f["field"],
                                  "critical": f["field"] in CRITICAL_FIELDS, "value": f["value"],
                                  "confidence": f["confidence"], "flags": f.get("flags", []),
                                  "grounded": f.get("grounded"), "page": ev.get("page") if ev else None,
                                  "quote": ev.get("quote") if ev else None})
    items.sort(key=lambda i: (not i["critical"], i["confidence"]))
    return {"total": len(items), "items": items[:limit]}


@router.get("/dashboard")
def dashboard() -> dict[str, Any]:
    today = date.today()
    with session_scope() as s:
        docs = s.scalars(select(Document).where(~Document.source.like("golden:%"))).all()
        rows = [doc_summary(d) for d in docs]
        facts = {d.id: d.facts or {} for d in docs}
    processed = [r for r in rows if r["processed_at"]]
    alerts = sorted([r for r in processed if r["alert"]], key=lambda r: (r["alert_days"] if r["alert_days"] is not None else 9999))
    gaps = []
    for r in processed:
        f = facts[r["id"]]
        if f.get("status") == "draft":
            continue
        missing = [k for k in ("effective_date", "parties", "governing_law") if not f.get(k)]
        if f.get("expiration_type") not in ("evergreen", "on_completion") and not f.get("current_term_end"):
            missing.append("term end")
        if f.get("auto_renewal") and f.get("non_renewal_notice_days") is None:
            missing.append("notice period")
        if missing:
            gaps.append({"document_id": r["id"], "title": r["title"] or r["filename"], "missing": missing})
    value: dict[str, float] = {}
    for f in facts.values():
        v, period = f.get("contract_value"), f.get("value_period")
        if v and f.get("status") in ("active", "evergreen"):
            mult = {"monthly": 12, "quarterly": 4, "annual": 1}.get(period or "", None)
            if mult:
                value[v["currency"]] = value.get(v["currency"], 0.0) + v["amount"] * mult
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for f in facts.values():
        if f:
            by_type[f.get("document_type") or "unknown"] = by_type.get(f.get("document_type") or "unknown", 0) + 1
            by_status[f.get("status") or "unknown"] = by_status.get(f.get("status") or "unknown", 0) + 1
    return {
        "today": today.isoformat(),
        "kpis": {"documents": len(rows), "processed": len(processed),
                 "in_pipeline": sum(1 for r in rows if r["status"] in ("queued", "processing")),
                 "failed": sum(1 for r in rows if r["status"] == "failed"),
                 "needs_review": sum(1 for r in rows if r["needs_review"]),
                 "active": by_status.get("active", 0), "drafts": by_status.get("draft", 0),
                 "expired": by_status.get("expired", 0),
                 "auto_renewing": sum(1 for f in facts.values() if f.get("auto_renewal") and f.get("status") == "active"),
                 "decide_now": sum(1 for r in alerts if r["alert"] in ("decide", "missed"))},
        "annual_value_by_currency": {k: round(v, 2) for k, v in value.items()},
        "alerts": alerts[:50], "data_gaps": gaps[:50], "by_type": by_type, "by_status": by_status,
        "recent": sorted(rows, key=lambda r: r["created_at"] or "", reverse=True)[:10],
    }
