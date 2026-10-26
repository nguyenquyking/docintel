from datetime import timedelta

from docintel.db import session_scope
from docintel.models import Job
from docintel.queue import jobs


def _enqueue(n, lane="interactive", key=None):
    with session_scope() as s:
        return [jobs.enqueue(s, "process_document", lane=lane, idempotency_key=key and f"{key}{i}").id for i in range(n)]


def test_interactive_first_but_backfill_never_starves():
    _enqueue(3, "backfill", "b")
    _enqueue(3, "interactive", "i")
    order = []
    for k in range(6):
        job = jobs.claim("w1", prefer_backfill=(k % 4 == 3))
        order.append(job.lane)
        jobs.complete(job.id, "w1")
    assert order[:3] == ["interactive"] * 3 and order[3] == "backfill"


def test_idempotency_key_returns_existing_job():
    with session_scope() as s:
        a = jobs.enqueue(s, "process_document", idempotency_key="process:abc")
        b = jobs.enqueue(s, "process_document", idempotency_key="process:abc")
        assert a.id == b.id
        c = jobs.enqueue(s, "process_document", idempotency_key="process:abc", force=True)
        assert c.id != a.id


def test_expired_lease_is_reclaimed_by_another_worker():
    (job_id,) = _enqueue(1)
    job = jobs.claim("crashed-worker")
    assert job.id == job_id and jobs.claim("w2") is None          # leased
    with session_scope() as s:
        s.get(Job, job_id).lease_expires_at = jobs.utcnow() - timedelta(seconds=1)
    again = jobs.claim("w2")
    assert again.id == job_id and again.attempts == 2 and again.lease_owner == "w2"
    assert jobs.heartbeat(job_id, "crashed-worker") is False      # the old owner lost it


def test_retry_with_backoff_then_dead_letter(monkeypatch):
    monkeypatch.setenv("DOCINTEL_JOB_MAX_ATTEMPTS", "2")
    from docintel.settings import get_settings
    get_settings.cache_clear()
    (job_id,) = _enqueue(1)
    job = jobs.claim("w")
    assert jobs.fail(job.id, "w", "HTTP 503", retryable=True) == "queued"
    with session_scope() as s:
        j = s.get(Job, job_id)
        assert j.run_after > jobs.utcnow() and j.status == "queued"   # backoff: not immediately claimable
        j.run_after = jobs.utcnow()
    job = jobs.claim("w")
    assert jobs.fail(job.id, "w", "HTTP 503", retryable=True) == "dead"
    assert jobs.requeue(job_id).status == "queued"


def test_fatal_errors_skip_retries():
    (job_id,) = _enqueue(1)
    job = jobs.claim("w")
    assert jobs.fail(job.id, "w", "encrypted PDF", retryable=False) == "dead"


def test_backoff_grows_and_is_jittered():
    delays = [jobs.backoff_seconds(n) for n in (1, 2, 3, 4)]
    assert all(b > a / 2 for a, b in zip(delays, delays[1:], strict=False)) and delays[3] >= 20
