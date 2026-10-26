"""Drift: is today's traffic still the traffic the golden set and thresholds were built on?

Input drift (language mix, document types, scanned share, length) predicts quality
and cost changes before any label arrives; output drift (null rates, confidence,
review and escalation rates) shows the model's behaviour moving. Each feature is a
categorical distribution compared with PSI between a reference window and the
current window. The canary (fixed golden documents re-run on a schedule) catches the
third kind: the provider changing the model behind an unchanged name.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..eval.stats import psi, shares
from ..extraction.schema import CRITICAL_FIELDS
from ..models import Document, EvalRun, Extraction


def _bucket_pages(n: int | None) -> str:
    if not n:
        return "unknown"
    return "1-2" if n <= 2 else "3-5" if n <= 5 else "6-20" if n <= 20 else "21-60" if n <= 60 else "61+"


def _bucket_conf(c: float | None) -> str:
    if c is None:
        return "none"
    return "<0.5" if c < 0.5 else "0.5-0.7" if c < 0.7 else "0.7-0.85" if c < 0.85 else ">=0.85"


def _features(doc: Document, ex: Extraction | None) -> dict[str, str]:
    mix = doc.route_mix or {}
    route = "vlm" if mix.get("vlm") else "ocr" if mix.get("ocr") else "text_layer"
    f = {"language": doc.language or "unknown", "doc_type": doc.doc_type or "unknown", "route": route,
         "pages": _bucket_pages(doc.page_count), "confidence": _bucket_conf(doc.confidence),
         "needs_review": str(bool(doc.needs_review))}
    if ex is not None:
        f["mode"] = ex.mode
        f["escalated"] = str(bool(ex.escalated_groups))
        values = {x["field"]: x["value"] for x in ex.fields}
        for name in CRITICAL_FIELDS:
            f[f"null:{name}"] = "null" if values.get(name) in (None, [], "") else "present"
    return f


def drift_report(current_days: int = 7, reference_days: int = 30) -> dict[str, Any]:
    now = datetime.now(UTC)
    with session_scope() as s:
        docs = s.scalars(select(Document).where(~Document.source.like("golden:%"), Document.processed_at.is_not(None))
                         .order_by(Document.processed_at)).all()
        rows = [(d, s.get(Extraction, d.latest_extraction_id) if d.latest_extraction_id else None) for d in docs]
    cur_from = now - timedelta(days=current_days)
    ref_from = cur_from - timedelta(days=reference_days)
    current = [r for r in rows if r[0].processed_at >= cur_from]
    reference = [r for r in rows if ref_from <= r[0].processed_at < cur_from]
    basis = "time windows"
    if len(reference) < 10 or len(current) < 10:   # not enough history yet: compare older half vs newer half
        half = len(rows) // 2
        reference, current, basis = rows[:half], rows[half:], "older half vs newer half of all documents"
    if not reference or not current:
        return {"basis": basis, "reference": {"n": len(reference)}, "current": {"n": len(current)}, "features": [],
                "note": "not enough processed documents to compare"}
    ref_f = [_features(d, e) for d, e in reference]
    cur_f = [_features(d, e) for d, e in current]
    names = sorted({k for f in ref_f + cur_f for k in f})
    features = []
    for name in names:
        ref_sh = shares([f.get(name, "n/a") for f in ref_f])
        cur_sh = shares([f.get(name, "n/a") for f in cur_f])
        value = psi(ref_sh, cur_sh)
        status = "significant" if value > 0.25 else "moderate" if value > 0.1 else "stable"
        features.append({"feature": name, "psi": round(value, 4), "status": status,
                         "reference": {k: round(v, 4) for k, v in ref_sh.items()},
                         "current": {k: round(v, 4) for k, v in cur_sh.items()}})
    features.sort(key=lambda f: -f["psi"])
    return {"basis": basis, "reference": {"n": len(reference)}, "current": {"n": len(current)}, "features": features}


def canary_status() -> dict[str, Any]:
    with session_scope() as s:
        runs = s.scalars(select(EvalRun).where(EvalRun.set_name == "canary", EvalRun.status == "done")
                         .order_by(EvalRun.created_at)).all()
        history = [{"id": r.id, "at": r.created_at.isoformat(), "quality": r.quality, "cost_per_1k": r.cost_per_1k}
                   for r in runs]
    alert = None
    if len(history) >= 2 and history[-1]["quality"] is not None and history[-2]["quality"] is not None:
        drop = history[-2]["quality"] - history[-1]["quality"]
        if drop >= 0.05:
            alert = f"canary quality dropped {drop:.1%} since the previous run — check for a provider-side model change"
    return {"runs": history, "alert": alert}
