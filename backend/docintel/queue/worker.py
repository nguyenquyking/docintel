"""The worker: N concurrent slots claiming jobs, heartbeating leases, classifying failures."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import traceback
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ..db import session_scope
from ..llm.client import LLMError, LLMRequestError, LLMRetryableError
from ..models import Document, Job, Setting
from ..settings import Settings, get_settings
from .jobs import claim, complete, enqueue, fail, heartbeat

log = logging.getLogger("docintel.worker")
HEARTBEAT_KEY = "worker_heartbeat"


class Worker:
    def __init__(self, settings: Settings | None = None, concurrency: int | None = None):
        self.settings = settings or get_settings()
        self.concurrency = concurrency or self.settings.worker_concurrency
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.stopping = asyncio.Event()
        self.running: dict[str, str] = {}

    async def run(self) -> None:
        log.info("worker %s started with %d slots", self.worker_id, self.concurrency)
        slots = [asyncio.create_task(self._slot(i)) for i in range(self.concurrency)]
        slots.append(asyncio.create_task(self._housekeeping()))
        await self.stopping.wait()
        for t in slots:
            t.cancel()
        await asyncio.gather(*slots, return_exceptions=True)

    def stop(self) -> None:
        self.stopping.set()

    async def _slot(self, slot: int) -> None:
        claims = 0
        while not self.stopping.is_set():
            job = await asyncio.to_thread(claim, self.worker_id, prefer_backfill=(claims % 4 == 3),
                                          settings=self.settings)
            if job is None:
                await asyncio.sleep(1.0)
                continue
            claims += 1
            await self._run_job(job)

    async def _run_job(self, job: Job) -> None:
        self.running[job.id] = job.kind
        beat = asyncio.create_task(self._heartbeat(job.id))
        try:
            if job.attempts > job.max_attempts:
                raise _Fatal(f"lease expired {job.attempts - 1} times — the job keeps killing its worker")
            await self.execute(job)
            await asyncio.to_thread(complete, job.id, self.worker_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            retryable, message = classify(exc)
            status = await asyncio.to_thread(fail, job.id, self.worker_id, message, retryable=retryable)
            log.warning("job %s (%s) failed → %s: %s", job.id, job.kind, status, message.splitlines()[0][:300])
            if status == "dead" and job.document_id and job.kind == "process_document":
                with session_scope() as s:
                    doc = s.get(Document, job.document_id)
                    if doc is not None:
                        doc.status, doc.error = "failed", message[:2000]
        finally:
            beat.cancel()
            self.running.pop(job.id, None)

    async def execute(self, job: Job) -> None:
        if job.kind == "process_document":
            from ..extraction.extractor import ExtractConfig
            from ..pipeline.runner import process_document
            payload = job.payload or {}
            config = ExtractConfig.preset(payload["preset"]) if payload.get("preset") else None
            await process_document(job.document_id, job_id=job.id, force=bool(payload.get("force")), config=config)
        elif job.kind in ("eval_run", "canary"):
            from ..eval.runner import run_eval
            await run_eval(job.payload["run_id"], job_id=job.id)
        else:
            raise _Fatal(f"unknown job kind {job.kind!r}")

    async def _heartbeat(self, job_id: str) -> None:
        interval = max(5.0, self.settings.job_lease_s / 3)
        while True:
            await asyncio.sleep(interval)
            await asyncio.to_thread(heartbeat, job_id, self.worker_id, self.settings)

    async def _housekeeping(self) -> None:
        """Worker liveness for /api/health, and the scheduled canary."""
        while not self.stopping.is_set():
            with session_scope() as s:
                row = s.get(Setting, HEARTBEAT_KEY)
                value = {"worker_id": self.worker_id, "at": datetime.now(UTC).isoformat(),
                         "slots": self.concurrency, "running": dict(self.running)}
                if row:
                    row.value = value
                else:
                    s.add(Setting(key=HEARTBEAT_KEY, value=value))
            if self.settings.canary_interval_hours > 0:
                await asyncio.to_thread(maybe_schedule_canary, self.settings)
            await asyncio.sleep(15)


class _Fatal(Exception):
    pass


def classify(exc: BaseException) -> tuple[bool, str]:
    """Retry what can heal (rate limits, timeouts, an expired login being renewed);
    dead-letter what cannot (unreadable file, malformed request)."""
    from ..pipeline.runner import FatalJobError
    message = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, (FatalJobError, _Fatal)):
        return False, message
    if isinstance(exc, LLMRequestError) and exc.status in (400, 404, 422):
        return False, message
    if isinstance(exc, (LLMRetryableError, LLMError)):
        return True, message
    return True, message + "\n" + "".join(traceback.format_exception(exc))[-2000:]


def maybe_schedule_canary(settings: Settings) -> None:
    from ..eval.runner import create_run
    with session_scope() as s:
        last = s.scalars(select(Job).where(Job.kind == "canary").order_by(Job.created_at.desc())).first()
        if last is not None:
            created = last.created_at if last.created_at.tzinfo else last.created_at.replace(tzinfo=UTC)
            if datetime.now(UTC) - created < timedelta(hours=settings.canary_interval_hours):
                return
    try:
        run_id = create_run("canary", split="all", preset="cascade", name="scheduled canary")
    except ValueError:
        return   # no canary set yet
    with session_scope() as s:
        enqueue(s, "canary", payload={"run_id": run_id}, lane="backfill")


def worker_status() -> dict | None:
    with session_scope() as s:
        row = s.get(Setting, HEARTBEAT_KEY)
        if not row:
            return None
        value = dict(row.value)
    at = datetime.fromisoformat(value["at"])
    value["alive"] = datetime.now(UTC) - at < timedelta(seconds=60)
    return value
