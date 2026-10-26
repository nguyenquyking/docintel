"""Live quality, cost and health from what the pipeline already records.

There is no ground truth in production, so live quality is read from proxies that
correlate with it — and from the one real label stream, human corrections:

* **correction rate** per field (corrected / reviewed) — the closest thing to live accuracy;
* **review rate**, **grounding failures**, **null rates**, **low-confidence share**;
* **cost per document**, tokens, cache hit share, **latency** p50/p95, LLM error rate;
* **route mix** — the share of pages that needed OCR or a VLM drives both cost and risk.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..eval.stats import percentile
from ..extraction.schema import CRITICAL_FIELDS, REVIEW_FIELDS
from ..models import Document, Extraction, FieldReview, Job, LLMCall, Page


def _day(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).date().isoformat() if dt else None


def overview(days: int = 30) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    with session_scope() as s:
        docs = s.scalars(select(Document).where(~Document.source.like("golden:%"))).all()
        exts = s.scalars(select(Extraction).where(Extraction.purpose == "production",
                                                  Extraction.created_at >= since)).all()
        calls = s.scalars(select(LLMCall).where(LLMCall.created_at >= since)).all()
        reviews = s.scalars(select(FieldReview).where(FieldReview.created_at >= since)).all()
        jobs = s.scalars(select(Job).where(Job.created_at >= since, Job.kind == "process_document")).all()
        pages = s.execute(select(Page.route, Page.ocr_confidence, Page.language)
                          .join(Document, Document.id == Page.document_id)
                          .where(~Document.source.like("golden:%"))).all()

    status: dict[str, int] = defaultdict(int)
    for d in docs:
        status[d.status] += 1

    daily: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for d in docs:
        if d.created_at and d.created_at >= since:
            daily[_day(d.created_at)]["uploads"] += 1
    for e in exts:
        k = _day(e.created_at)
        daily[k]["extractions"] += 1
        daily[k]["cost_usd"] += e.cost_usd
        daily[k]["reused"] += 1 if e.reused_from else 0
    for c in calls:
        k = _day(c.created_at)
        daily[k]["llm_calls"] += 1
        daily[k]["llm_errors"] += c.status != "ok"
        daily[k]["llm_cost_usd"] += c.cost_usd
        daily[k]["prompt_tokens"] += c.prompt_tokens
        daily[k]["cached_tokens"] += c.cached_tokens
        daily[k]["completion_tokens"] += c.completion_tokens
    series = [{"day": k, **{m: round(v, 6) for m, v in vals.items()}} for k, vals in sorted(daily.items())]

    fresh = [e for e in exts if not e.reused_from]
    lat = [e.latency_ms for e in fresh]
    wall = [(j.finished_at - j.started_at).total_seconds() * 1000 for j in jobs
            if j.finished_at and j.started_at and j.status == "succeeded"]
    by_purpose: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for c in calls:
        key = c.purpose.split(":")[0] if not c.purpose.startswith("extract") else c.purpose
        by_purpose[key]["calls"] += 1
        by_purpose[key]["cost_usd"] += c.cost_usd
        by_purpose[key]["prompt_tokens"] += c.prompt_tokens
        by_purpose[key]["completion_tokens"] += c.completion_tokens
    prompt_total = sum(c.prompt_tokens for c in calls if not c.cache_hit)

    field_health = []
    latest_by_doc = {}
    for e in sorted(exts, key=lambda e: e.created_at):
        latest_by_doc[e.document_id] = e
    for name in REVIEW_FIELDS:
        vals = [f for e in latest_by_doc.values() for f in e.fields if f["field"] == name]
        if not vals:
            continue
        nonnull = [f for f in vals if f["value"] not in (None, [], "")]
        grounded = [f for f in nonnull if f.get("grounded") is not None]
        rv = [r for r in reviews if r.field == name]
        field_health.append({
            "field": name, "critical": name in CRITICAL_FIELDS, "n": len(vals),
            "null_rate": round(1 - len(nonnull) / len(vals), 4),
            "grounded_rate": round(sum(1 for f in grounded if f["grounded"]) / len(grounded), 4) if grounded else None,
            "mean_confidence": round(sum(f["confidence"] for f in vals) / len(vals), 4),
            "review_rate": round(sum(1 for f in vals if f.get("needs_review")) / len(vals), 4),
            "reviewed": len(rv),
            "correction_rate": round(sum(1 for r in rv if r.action == "correct") / len(rv), 4) if rv else None,
        })

    routes: dict[str, int] = defaultdict(int)
    ocr_conf = []
    for route, conf, _lang in pages:
        routes[route] += 1
        if route == "ocr" and conf is not None:
            ocr_conf.append(conf)
    return {
        "window_days": days,
        "documents": {"total": len(docs), "by_status": dict(status),
                      "needs_review": sum(1 for d in docs if d.needs_review)},
        "series": series,
        "extraction": {
            "count": len(exts), "fresh": len(fresh), "reused": len(exts) - len(fresh),
            "cost_per_doc_usd": round(sum(e.cost_usd for e in fresh) / len(fresh), 6) if fresh else None,
            "latency_ms": {"p50": percentile(lat, 0.5), "p95": percentile(lat, 0.95)},
            "job_wall_ms": {"p50": percentile(wall, 0.5), "p95": percentile(wall, 0.95)},
            "escalation_rate": round(sum(1 for e in fresh if e.escalated_groups) / len(fresh), 4) if fresh else None,
            "modes": {m: sum(1 for e in fresh if e.mode == m) for m in ("full_document", "grouped")},
        },
        "llm": {
            "calls": len(calls), "errors": sum(1 for c in calls if c.status != "ok"),
            "error_rate": round(sum(1 for c in calls if c.status != "ok") / len(calls), 4) if calls else None,
            "cache_hit_rate": round(sum(1 for c in calls if c.cache_hit) / len(calls), 4) if calls else None,
            "prompt_cache_share": round(sum(c.cached_tokens for c in calls) / prompt_total, 4) if prompt_total else None,
            "cost_usd": round(sum(c.cost_usd for c in calls), 6),
            "by_purpose": {k: {m: round(v, 6) for m, v in vals.items()} for k, vals in by_purpose.items()},
        },
        "pages": {"by_route": dict(routes),
                  "ocr_confidence_p10": percentile(ocr_conf, 0.1), "ocr_confidence_p50": percentile(ocr_conf, 0.5)},
        "fields": field_health,
        "reviews": {"total": len(reviews), "corrections": sum(1 for r in reviews if r.action == "correct")},
    }
