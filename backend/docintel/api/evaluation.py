"""Evaluation: golden sets, runs, the regression gate."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..db import session_scope
from ..eval import golden
from ..eval.gate import compare_runs
from ..eval.runner import create_run, save_calibration
from ..models import EvalDoc, EvalItem, EvalRun, GoldenDoc
from ..queue.jobs import enqueue

router = APIRouter(prefix="/api/eval", tags=["evaluation"])


@router.get("/sets")
def sets() -> dict[str, Any]:
    return {"sets": golden.set_summary()}


class SyntheticBody(BaseModel):
    name: str = "synthetic-v1"
    n: int = 60
    seed: int = 7


@router.post("/sets/synthetic")
async def make_synthetic(body: SyntheticBody) -> dict[str, Any]:
    added = await asyncio.to_thread(golden.import_synthetic, body.name, body.n, body.seed)
    return {"added": added, "sets": golden.set_summary()}


class CuadBody(BaseModel):
    name: str = "cuad"
    n: int = 25


@router.post("/sets/cuad")
async def make_cuad(body: CuadBody) -> dict[str, Any]:
    from ..datasets.cuad import import_cuad
    try:
        added = await asyncio.to_thread(import_cuad, body.name, body.n)
    except Exception as exc:  # network, archive layout
        raise HTTPException(502, f"CUAD import failed: {exc}") from exc
    return {"added": added, "sets": golden.set_summary()}


@router.post("/sets/canary")
def make_canary(source: str = "synthetic-v1") -> dict[str, Any]:
    return {"added": golden.make_canary(source), "sets": golden.set_summary()}


@router.post("/promote/{doc_id}")
def promote(doc_id: str, set_name: str = "reviewed") -> dict[str, Any]:
    try:
        g = golden.promote_reviewed(doc_id, set_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"golden_doc_id": g.id, "labeled_fields": g.labeled_fields}


class RunBody(BaseModel):
    set_name: str = "synthetic-v1"
    split: str = "test"
    preset: str = "cascade"
    name: str | None = None
    use_cache: bool = True
    overrides: dict[str, Any] | None = None


@router.post("/runs")
def start_run(body: RunBody) -> dict[str, Any]:
    try:
        run_id = create_run(body.set_name, body.split, body.preset, body.name, body.overrides, body.use_cache)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with session_scope() as s:
        job = enqueue(s, "eval_run", payload={"run_id": run_id}, lane="backfill")
        run = s.get(EvalRun, run_id)
        run.job_id = job.id
    return {"run_id": run_id, "job_id": job.id}


def _run_row(r: EvalRun) -> dict[str, Any]:
    return {"id": r.id, "name": r.name, "set_name": r.set_name, "split": r.split, "status": r.status,
            "preset": (r.config or {}).get("preset"), "config": r.config, "n_docs": r.n_docs, "done_docs": r.done_docs,
            "quality": r.quality, "cost_per_1k": r.cost_per_1k, "latency_p50_ms": r.latency_p50_ms,
            "latency_p95_ms": r.latency_p95_ms, "error": r.error, "created_at": r.created_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None}


@router.get("/runs")
def runs(set_name: str | None = None) -> dict[str, Any]:
    with session_scope() as s:
        q = select(EvalRun).order_by(EvalRun.created_at.desc())
        if set_name:
            q = q.where(EvalRun.set_name == set_name)
        return {"runs": [_run_row(r) for r in s.scalars(q)]}


@router.get("/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    with session_scope() as s:
        r = s.get(EvalRun, run_id)
        if r is None:
            raise HTTPException(404, "run not found")
        docs = s.scalars(select(EvalDoc).where(EvalDoc.run_id == run_id)).all()
        names = {g.id: (g.external_id, g.document_id, g.strata) for g in
                 s.scalars(select(GoldenDoc).where(GoldenDoc.set_name == r.set_name))}
        metrics = {k: v for k, v in (r.metrics or {}).items() if k != "ocr_docs"}
        return {**_run_row(r), "metrics": metrics,
                "docs": [{"golden_doc_id": d.golden_doc_id, "external_id": names.get(d.golden_doc_id, ("?",))[0],
                          "document_id": names.get(d.golden_doc_id, (None, None))[1],
                          "strata": names.get(d.golden_doc_id, (None, None, {}))[2], "cost_usd": d.cost_usd,
                          "latency_ms": d.latency_ms, "critical_correct": d.critical_correct, "error": d.error}
                         for d in docs]}


@router.get("/runs/{run_id}/items")
def run_items(run_id: str, field: str | None = None, only_errors: bool = False) -> dict[str, Any]:
    with session_scope() as s:
        r = s.get(EvalRun, run_id)
        if r is None:
            raise HTTPException(404, "run not found")
        names = {g.id: (g.external_id, g.document_id) for g in
                 s.scalars(select(GoldenDoc).where(GoldenDoc.set_name == r.set_name))}
        q = select(EvalItem).where(EvalItem.run_id == run_id)
        if field:
            q = q.where(EvalItem.field == field)
        if only_errors:
            q = q.where(EvalItem.correct.is_(False))
        return {"items": [{"golden_doc_id": i.golden_doc_id, "external_id": names.get(i.golden_doc_id, ("?", None))[0],
                           "document_id": names.get(i.golden_doc_id, (None, None))[1], "field": i.field,
                           "gold": i.gold, "pred": i.pred, "outcome": i.outcome, "correct": i.correct,
                           "confidence": i.confidence, "retrieval_hit": i.retrieval_hit,
                           "evidence_page_ok": i.evidence_page_ok} for i in s.scalars(q)]}


@router.get("/compare")
def compare(base: str, candidate: str, max_drop: float = 0.03, alpha: float = 0.05) -> dict[str, Any]:
    try:
        return compare_runs(base, candidate, alpha=alpha, max_drop=max_drop)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/runs/{run_id}/calibrate")
def calibrate(run_id: str) -> dict[str, Any]:
    table = save_calibration(run_id)
    return {"fields": sorted(k for k in table if k != "*")}
