"""A database-backed job queue: two lanes, leases, backoff, dead-letter.

Why in the database rather than Redis/SQS here: one less moving part for a
self-hosted deployment, and the semantics are the same ones a managed queue gives
you, implemented explicitly:

* **Lanes** — ``interactive`` (a user just uploaded) is served before ``backfill``
  (corpus re-processing), but every fourth claim prefers backfill so it never starves.
* **Leases** (visibility timeout) — a claimed job belongs to a worker until its lease
  expires; a crashed worker's job is reclaimed automatically. Long jobs heartbeat.
* **At-least-once** — a job can run twice (lease expiry), so handlers are idempotent:
  results are keyed by content hash + versions.
* **Backoff** — transient failures retry with exponential backoff and jitter; fatal
  ones (encrypted PDF) and exhausted ones go to the dead-letter state for a human.
* **Idempotency keys** — enqueueing the same work twice returns the existing job.

Claiming is a single atomic UPDATE … RETURNING (SQLite serializes writers; on
Postgres the subquery uses FOR UPDATE SKIP LOCKED so workers never block each other).
"""
from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..db import is_sqlite, session_scope
from ..models import Job
from ..settings import Settings, get_settings

ACTIVE = ("queued", "running")
TERMINAL = ("succeeded", "dead", "cancelled")


def utcnow() -> datetime:
    return datetime.now(UTC)


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def enqueue(session: Session, kind: str, *, document_id: str | None = None, payload: dict | None = None,
            lane: str = "interactive", priority: int = 0, idempotency_key: str | None = None,
            max_attempts: int | None = None, force: bool = False) -> Job:
    if idempotency_key and not force:
        existing = session.scalars(select(Job).where(Job.idempotency_key == idempotency_key,
                                                     Job.status.in_([*ACTIVE, "succeeded"]))
                                   .order_by(Job.created_at.desc())).first()
        if existing is not None:
            return existing
    job = Job(kind=kind, document_id=document_id, payload=payload or {}, lane=lane, priority=priority,
              idempotency_key=idempotency_key, max_attempts=max_attempts or get_settings().job_max_attempts)
    session.add(job)
    session.flush()
    return job


_CLAIM_SQLITE = """
UPDATE jobs SET status = 'running', lease_owner = :worker, lease_expires_at = :lease, attempts = attempts + 1,
       started_at = COALESCE(started_at, :now), updated_at = :now
WHERE id = (
    SELECT id FROM jobs
    WHERE run_after <= :now AND (status = 'queued' OR (status = 'running' AND lease_expires_at < :now))
    ORDER BY CASE WHEN lane = :first_lane THEN 0 ELSE 1 END, priority DESC, created_at ASC
    LIMIT 1)
RETURNING id
"""
_CLAIM_POSTGRES = """
UPDATE jobs SET status = 'running', lease_owner = :worker, lease_expires_at = :lease, attempts = attempts + 1,
       started_at = COALESCE(started_at, :now), updated_at = :now
WHERE id = (
    SELECT id FROM jobs
    WHERE run_after <= :now AND (status = 'queued' OR (status = 'running' AND lease_expires_at < :now))
    ORDER BY CASE WHEN lane = :first_lane THEN 0 ELSE 1 END, priority DESC, created_at ASC
    LIMIT 1 FOR UPDATE SKIP LOCKED)
RETURNING id
"""


def claim(worker_id: str, *, prefer_backfill: bool = False, settings: Settings | None = None) -> Job | None:
    settings = settings or get_settings()
    now = utcnow()
    params = {"worker": worker_id, "now": _naive(now), "lease": _naive(now + timedelta(seconds=settings.job_lease_s)),
              "first_lane": "backfill" if prefer_backfill else "interactive"}
    with session_scope(settings) as s:
        row = s.execute(text(_CLAIM_SQLITE if is_sqlite(settings) else _CLAIM_POSTGRES), params).first()
        if row is None:
            return None
        job = s.get(Job, row[0])
        s.refresh(job)
        return job


def heartbeat(job_id: str, worker_id: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    with session_scope(settings) as s:
        job = s.get(Job, job_id)
        if job is None or job.status != "running" or job.lease_owner != worker_id:
            return False
        job.lease_expires_at = utcnow() + timedelta(seconds=settings.job_lease_s)
        return True


def set_progress(job_id: str | None, **progress: Any) -> None:
    if not job_id:
        return
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is not None:
            job.progress = {**(job.progress or {}), **progress}


def complete(job_id: str, worker_id: str) -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is not None and job.lease_owner == worker_id:
            job.status, job.finished_at, job.lease_expires_at, job.last_error = "succeeded", utcnow(), None, None


def backoff_seconds(attempts: int, settings: Settings | None = None) -> float:
    """Exponential with 'equal jitter': never retry immediately, never in lockstep."""
    s = settings or get_settings()
    delay = min(s.job_backoff_cap_s, s.job_backoff_base_s * 2 ** max(0, attempts - 1))
    return random.uniform(delay / 2, delay)


def fail(job_id: str, worker_id: str, error: str, *, retryable: bool) -> str:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None or job.lease_owner != worker_id:
            return "lost"
        job.last_error = error[:4000]
        job.lease_expires_at = None
        if retryable and job.attempts < job.max_attempts:
            job.status = "queued"
            job.run_after = utcnow() + timedelta(seconds=backoff_seconds(job.attempts))
        else:
            job.status = "dead"
            job.finished_at = utcnow()
        return job.status


def requeue(job_id: str) -> Job | None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None or job.status not in ("dead", "cancelled", "succeeded"):
            return job
        job.status, job.attempts, job.run_after, job.last_error = "queued", 0, utcnow(), None
        job.lease_owner = job.lease_expires_at = job.finished_at = None
        return job


def cancel(job_id: str) -> Job | None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is not None and job.status == "queued":
            job.status, job.finished_at = "cancelled", utcnow()
        return job


def queue_stats(settings: Settings | None = None) -> dict[str, Any]:
    with session_scope(settings) as s:
        rows = s.execute(select(Job.lane, Job.status, func.count()).group_by(Job.lane, Job.status)).all()
        oldest = s.execute(select(func.min(Job.created_at)).where(Job.status == "queued")).scalar()
    lanes: dict[str, dict[str, int]] = {}
    for lane, status, n in rows:
        lanes.setdefault(lane, {})[status] = n
    age = None
    if oldest is not None:
        oldest = oldest if oldest.tzinfo else oldest.replace(tzinfo=UTC)
        age = (utcnow() - oldest).total_seconds()
    return {"lanes": lanes, "oldest_queued_age_s": age}
