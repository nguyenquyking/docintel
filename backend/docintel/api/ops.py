"""Operations: health, settings, jobs, monitoring, drift, cost, search."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .. import __version__
from ..cost.model import breakeven, measured_parameters, pareto, project
from ..db import session_scope
from ..extraction.prompts import prompt_version
from ..llm.client import get_llm
from ..llm.pricing import load_pricing, save_pricing
from ..models import Job
from ..monitoring.drift import canary_status, drift_report
from ..monitoring.metrics import overview
from ..queue.jobs import cancel, queue_stats, requeue
from ..queue.worker import worker_status
from ..retrieval.embeddings import get_embedder
from ..search.engine import INDEX as SEARCH_INDEX
from ..search.engine import Filters, ask
from ..settings import get_settings
from ..versions import PARSER_VERSION, PIPELINE_VERSION, SCHEMA_VERSION

router = APIRouter(prefix="/api", tags=["operations"])


@router.get("/health")
def health() -> dict[str, Any]:
    with session_scope() as s:
        s.execute(select(1))
    return {"ok": True, "version": __version__, "worker": worker_status(),
            "versions": {"pipeline": PIPELINE_VERSION, "parser": PARSER_VERSION, "schema": SCHEMA_VERSION,
                         "prompt": prompt_version()}}


@router.get("/settings")
def settings_view() -> dict[str, Any]:
    return {**get_settings().public_view(), "pricing": load_pricing(), "rate_limit": get_llm().limiter.snapshot()}


@router.put("/settings/pricing")
def put_pricing(table: dict[str, dict[str, float]]) -> dict[str, Any]:
    return {"pricing": save_pricing(table)}


@router.get("/jobs")
def jobs(status: str | None = None, lane: str | None = None, kind: str | None = None, limit: int = 200) -> dict[str, Any]:
    with session_scope() as s:
        q = select(Job).order_by(Job.created_at.desc()).limit(limit)
        if status:
            q = q.where(Job.status == status)
        if lane:
            q = q.where(Job.lane == lane)
        if kind:
            q = q.where(Job.kind == kind)
        rows = [{"id": j.id, "kind": j.kind, "document_id": j.document_id, "lane": j.lane, "status": j.status,
                 "attempts": j.attempts, "max_attempts": j.max_attempts, "progress": j.progress,
                 "last_error": j.last_error, "run_after": j.run_after.isoformat(),
                 "created_at": j.created_at.isoformat(),
                 "started_at": j.started_at.isoformat() if j.started_at else None,
                 "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                 "payload": j.payload} for j in s.scalars(q)]
    return {"jobs": rows, "stats": queue_stats(), "worker": worker_status()}


@router.post("/jobs/{job_id}/requeue")
def requeue_job(job_id: str) -> dict[str, Any]:
    job = requeue(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return {"id": job.id, "status": job.status}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    job = cancel(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return {"id": job.id, "status": job.status}


@router.get("/monitoring/overview")
def monitoring(days: int = 30) -> dict[str, Any]:
    return overview(days)


@router.get("/monitoring/drift")
def drift(current_days: int = 7, reference_days: int = 30) -> dict[str, Any]:
    return drift_report(current_days, reference_days)


@router.get("/monitoring/canary")
def canary() -> dict[str, Any]:
    return canary_status()


@router.get("/cost/measured")
def cost_measured() -> dict[str, Any]:
    return {"measured": measured_parameters(), "pricing": load_pricing()}


@router.post("/cost/project")
def cost_project(params: dict[str, Any]) -> dict[str, Any]:
    return project(params)


@router.post("/cost/breakeven")
def cost_breakeven(params: dict[str, Any]) -> dict[str, Any]:
    return breakeven(params)


@router.get("/cost/pareto")
def cost_pareto(set_name: str | None = None, split: str | None = None) -> dict[str, Any]:
    return pareto(set_name, split)


class SearchBody(BaseModel):
    query: str
    filters: dict[str, Any] = {}
    limit: int = 20


def _filters(d: dict[str, Any]) -> Filters:
    allowed = Filters.__dataclass_fields__
    return Filters(**{k: v for k, v in d.items() if k in allowed and v not in ("", None)})


@router.post("/search")
async def search(body: SearchBody) -> dict[str, Any]:
    hits = await SEARCH_INDEX.search(body.query, _filters(body.filters), get_embedder(), k=body.limit)
    return {"results": hits}


class AskBody(BaseModel):
    question: str
    filters: dict[str, Any] = {}


@router.post("/ask")
async def ask_endpoint(body: AskBody) -> dict[str, Any]:
    return await ask(body.question, _filters(body.filters), get_llm(), get_embedder())
