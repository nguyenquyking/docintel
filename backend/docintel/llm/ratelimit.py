"""Client-side rate limiting: requests per minute and tokens per minute.

Providers enforce both; waiting here is cheaper than collecting 429s and retrying.
The token side reserves an estimate before the call and settles the difference
with the real usage afterwards.
"""
from __future__ import annotations

import asyncio
import time


class AsyncRateLimiter:
    def __init__(self, rpm: int, tpm: int):
        self.rpm, self.tpm = max(1, rpm), max(1, tpm)
        self._requests = float(self.rpm)
        self._tokens = float(self.tpm)
        self._last = time.monotonic()
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:   # asyncio primitives are bound to one loop
            self._lock, self._loop = asyncio.Lock(), loop
        return self._lock

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed, self._last = now - self._last, now
        self._requests = min(self.rpm, self._requests + elapsed * self.rpm / 60)
        self._tokens = min(self.tpm, self._tokens + elapsed * self.tpm / 60)

    async def acquire(self, tokens: int) -> float:
        """Wait until one request and ``tokens`` tokens are available. Returns seconds waited."""
        tokens = min(max(1, tokens), self.tpm)   # one oversized request must still get through
        waited = 0.0
        while True:
            async with self._get_lock():
                self._refill()
                if self._requests >= 1 and self._tokens >= tokens:
                    self._requests -= 1
                    self._tokens -= tokens
                    return waited
                wait = max((1 - self._requests) * 60 / self.rpm, (tokens - self._tokens) * 60 / self.tpm, 0.01)
            await asyncio.sleep(wait)
            waited += wait

    def settle(self, estimated: int, actual: int) -> None:
        """Correct the token bucket once the real usage is known."""
        self._tokens = min(self.tpm, self._tokens + (estimated - actual))

    def snapshot(self) -> dict:
        self._refill()
        return {"rpm": self.rpm, "tpm": self.tpm, "requests_available": round(self._requests, 1),
                "tokens_available": int(self._tokens)}
