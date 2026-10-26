"""One client for every model call: OpenAI-compatible chat completions.

Works against Databricks AI Gateway, OpenAI, or a self-hosted server (vLLM, Ollama)
— the same request shape, so "vendor vs our own model" is a config change.

What it guarantees for every call:

* **Retries only what heals itself** — 429/5xx/timeouts, exponential backoff with
  full jitter, honouring ``Retry-After``. A 4xx is raised immediately with the
  provider's body verbatim: that body is the only clue the operator has.
* **Rate limiting** on requests *and* tokens per minute, before the call.
* **Exact-request cache** — identical payloads are answered from the database,
  so re-running an unchanged stage (or an eval) costs nothing.
* **Metering** — tokens (prompt, cached, completion, reasoning), latency, cost and
  purpose are recorded per call: cost per document and per stage come from here.
* **No temperature by default** — some reasoning models reject anything but 1.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..settings import Settings, get_settings
from ..text.normalize import estimate_tokens
from .auth import Auth, AuthError, DatabricksAuth, StaticKeyAuth
from .pricing import call_cost, load_pricing
from .ratelimit import AsyncRateLimiter

RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})

# Who is calling: set by the pipeline/eval runner, read when metering.
current_document: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_document", default=None)
current_job: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_job", default=None)
current_run: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_run", default=None)


class LLMError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status, self.body = status, body


class LLMRetryableError(LLMError):
    """Transient: the job queue may retry the whole document later."""


class LLMRequestError(LLMError):
    """The request itself is wrong (4xx) — retrying the same payload cannot help."""


class LLMOutputError(LLMError):
    """The model answered, but not in the requested shape (e.g. truncated JSON)."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class LLMResponse:
    content: str
    parsed: Any | None
    model: str
    tier: str
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    latency_ms: int = 0
    cache_hit: bool = False
    finish_reason: str | None = None


Responder = Callable[[dict[str, Any]], dict[str, Any] | str]


def schema_default(schema: dict[str, Any]) -> Any:
    """A minimal value that satisfies a JSON schema: what the offline fake model returns."""
    t = schema.get("type")
    types = t if isinstance(t, list) else [t]
    if "null" in types:
        return None
    if "object" in types:
        return {k: schema_default(v) for k, v in schema.get("properties", {}).items()}
    if "array" in types:
        return []
    if "enum" in schema:
        return schema["enum"][0]
    if "boolean" in types:
        return False
    if "integer" in types or "number" in types:
        return 0
    return ""


def offline_responder(payload: dict[str, Any]) -> dict[str, Any] | str:
    rf = payload.get("response_format") or {}
    if rf.get("type") == "json_schema":
        return {"content": json.dumps(schema_default(rf["json_schema"]["schema"]))}
    return {"content": ""}


class LLMClient:
    def __init__(self, settings: Settings | None = None, *, responder: Responder | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, record: bool = True):
        self.settings = settings or get_settings()
        self.provider = self.settings.llm_provider
        self.responder = responder or (offline_responder if self.provider == "fake" else None)
        self.record = record
        self._transport = transport
        self._auth: Auth | None = None
        self._base_url: str | None = None
        self.limiter = AsyncRateLimiter(self.settings.llm_rpm, self.settings.llm_tpm)
        self._sem: asyncio.Semaphore | None = None
        self._sem_loop: asyncio.AbstractEventLoop | None = None
        self._pricing = load_pricing() if record else None
        self._pricing_loaded = time.monotonic()

    # ── configuration ────────────────────────────────────────────────────
    def model_for(self, tier: str) -> str:
        s = self.settings
        model = {"fast": s.llm_model_fast, "strong": s.llm_model_strong or s.llm_model_fast,
                 "vision": s.vision_model}.get(tier, s.llm_model_fast)
        if not model and self.provider != "fake":
            raise LLMRequestError(f"no model configured for tier '{tier}': set DOCINTEL_LLM_MODEL_FAST")
        return model or f"fake-{tier}"

    def effort_for(self, tier: str) -> str | None:
        s = self.settings
        return ({"fast": s.llm_effort_fast, "strong": s.llm_effort_strong, "vision": s.llm_effort_vision}
                .get(tier) or None)

    def _auth_and_url(self) -> tuple[Auth, str]:
        if self._auth is None:
            s = self.settings
            if self.provider == "databricks":
                auth = DatabricksAuth(s)
                self._base_url = auth.base_url()
                self._auth = auth
            else:
                if not s.llm_base_url:
                    raise LLMRequestError("set DOCINTEL_LLM_BASE_URL for the openai provider")
                self._base_url = s.llm_base_url.rstrip("/")
                self._auth = StaticKeyAuth(s.llm_api_key.get_secret_value() if s.llm_api_key else "")
        return self._auth, self._base_url or ""

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._sem is None or self._sem_loop is not loop:
            self._sem, self._sem_loop = asyncio.Semaphore(self.settings.llm_max_concurrency), loop
        return self._sem

    def pricing(self) -> dict:
        if self._pricing is None or time.monotonic() - self._pricing_loaded > 30:
            self._pricing, self._pricing_loaded = load_pricing(), time.monotonic()
        return self._pricing

    # ── the call ─────────────────────────────────────────────────────────
    async def chat(self, messages: list[dict[str, Any]], *, purpose: str, tier: str = "fast",
                   json_schema: dict[str, Any] | None = None, schema_name: str = "output",
                   max_output_tokens: int | None = None, use_cache: bool | None = None,
                   temperature: float | None = None) -> LLMResponse:
        model, effort = self.model_for(tier), self.effort_for(tier)
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if effort:
            payload["reasoning_effort"] = effort
        if max_output_tokens:
            payload["max_completion_tokens"] = max_output_tokens
        if temperature is not None and self.settings.llm_send_temperature:
            payload["temperature"] = temperature
        if json_schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": schema_name, "strict": True, "schema": json_schema}}
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        images = sum(1 for m in messages if isinstance(m.get("content"), list)
                     for part in m["content"] if part.get("type") == "image_url")
        use_cache = self.settings.llm_cache if use_cache is None else use_cache

        if use_cache and self.record:
            cached = self._cache_get(request_hash)
            if cached is not None:
                resp = self._to_response(cached, model, tier, json_schema, latency_ms=0, cache_hit=True)
                resp.cost_usd = 0.0
                self._meter(purpose, tier, model, effort, resp, request_hash, images=images, attempts=0)
                return resp

        estimated = sum(estimate_tokens(m["content"]) if isinstance(m.get("content"), str)
                        else sum(estimate_tokens(p.get("text", "")) for p in m["content"]) for m in messages)
        estimated += images * 1100 + (max_output_tokens or 1500)
        await self.limiter.acquire(estimated)
        started = time.monotonic()
        attempts = 0
        try:
            async with self._semaphore():
                raw, attempts = await self._send(payload)
        except LLMError as exc:
            self._meter_error(purpose, tier, model, effort, exc, request_hash, images, attempts or 1,
                              int((time.monotonic() - started) * 1000))
            raise
        latency = int((time.monotonic() - started) * 1000)
        try:
            resp = self._to_response(raw, model, tier, json_schema, latency_ms=latency, cache_hit=False)
        except LLMOutputError as exc:  # paid for, but unusable: meter it as an error with its tokens
            usage = self._usage(raw)
            cost = call_cost(self.pricing(), tier, usage.prompt_tokens, usage.completion_tokens,
                             usage.cached_tokens) if self.provider != "fake" else 0.0
            self._meter_error(purpose, tier, model, effort, exc, request_hash, images, attempts, latency,
                              usage=usage, cost=cost)
            raise
        self.limiter.settle(estimated, resp.usage.prompt_tokens + resp.usage.completion_tokens)
        resp.cost_usd = call_cost(self.pricing(), tier, resp.usage.prompt_tokens, resp.usage.completion_tokens,
                                  resp.usage.cached_tokens) if self.provider != "fake" else 0.0
        if use_cache and self.record and resp.finish_reason != "length":
            self._cache_put(request_hash, raw)
        self._meter(purpose, tier, model, effort, resp, request_hash, images=images, attempts=attempts)
        return resp

    async def _send(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        if self.responder is not None:
            out = self.responder(payload)
            if asyncio.iscoroutine(out):
                out = await out
            if isinstance(out, str):
                out = {"content": out}
            if "choices" not in out:
                out = {"model": payload["model"], "choices": [{"message": {"content": out.get("content", "")},
                                                               "finish_reason": out.get("finish_reason", "stop")}],
                       "usage": out.get("usage", {})}
            return out, 1
        try:
            auth, base = self._auth_and_url()
        except AuthError as exc:
            raise LLMRequestError(str(exc)) from exc
        s = self.settings
        last: LLMError | None = None
        async with httpx.AsyncClient(timeout=s.llm_timeout_s, transport=self._transport) as http:
            for attempt in range(1, s.llm_max_retries + 1):
                try:
                    headers = {**await auth.headers(), "Content-Type": "application/json"}
                except AuthError as exc:
                    raise LLMRequestError(str(exc)) from exc
                retry_after: float | None = None
                try:
                    r = await http.post(f"{base}/chat/completions", json=payload, headers=headers)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last = LLMRetryableError(f"{type(exc).__name__}: {exc}")
                else:
                    if r.status_code == 200:
                        return r.json(), attempt
                    body = r.text[:4000]
                    if r.status_code in RETRY_STATUSES:
                        last = LLMRetryableError(f"HTTP {r.status_code}", r.status_code, body)
                        ra = r.headers.get("Retry-After")
                        retry_after = float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
                    else:
                        raise LLMRequestError(f"HTTP {r.status_code}: {body[:500]}", r.status_code, body)
                if attempt < s.llm_max_retries:
                    delay = retry_after if retry_after is not None else random.uniform(0, min(30.0, 1.0 * 2 ** (attempt - 1)))
                    await asyncio.sleep(delay)
        assert last is not None
        raise last

    # ── parsing / metering ───────────────────────────────────────────────
    @staticmethod
    def _usage(raw: dict[str, Any]) -> Usage:
        u = raw.get("usage") or {}
        return Usage(
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
            reasoning_tokens=int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
            cached_tokens=int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
        )

    @staticmethod
    def _to_response(raw: dict[str, Any], model: str, tier: str, json_schema: dict | None, *, latency_ms: int,
                     cache_hit: bool) -> LLMResponse:
        choice = (raw.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        usage = LLMClient._usage(raw)
        finish = choice.get("finish_reason")
        parsed = None
        if json_schema is not None:
            if finish == "length":
                raise LLMOutputError("output truncated at the token limit", body=content[:500])
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise LLMOutputError(f"model did not return JSON: {exc}", body=content[:500]) from exc
        return LLMResponse(content, parsed, raw.get("model") or model, tier, usage, 0.0, latency_ms, cache_hit, finish)

    def _cache_get(self, key: str) -> dict | None:
        from ..db import session_scope
        from ..models import LLMCacheEntry
        with session_scope() as s:
            row = s.get(LLMCacheEntry, key)
            return dict(row.response) if row else None

    def _cache_put(self, key: str, raw: dict) -> None:
        from ..db import session_scope
        from ..models import LLMCacheEntry
        try:
            with session_scope() as s:
                if s.get(LLMCacheEntry, key) is None:
                    s.add(LLMCacheEntry(request_hash=key, response=raw))
        except Exception:
            pass  # a concurrent writer stored the same answer first

    def _meter(self, purpose: str, tier: str, model: str, effort: str | None, resp: LLMResponse,
               request_hash: str, *, images: int, attempts: int) -> None:
        if not self.record:
            return
        from ..db import session_scope
        from ..models import LLMCall
        with session_scope() as s:
            s.add(LLMCall(
                document_id=current_document.get(), job_id=current_job.get(), run_id=current_run.get(),
                purpose=purpose, tier=tier, model=resp.model or model, effort=effort, status="ok",
                attempts=attempts, prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens, reasoning_tokens=resp.usage.reasoning_tokens,
                cached_tokens=resp.usage.cached_tokens, image_count=images, latency_ms=resp.latency_ms,
                cost_usd=resp.cost_usd, cache_hit=resp.cache_hit, request_hash=request_hash))

    def _meter_error(self, purpose: str, tier: str, model: str, effort: str | None, exc: LLMError,
                     request_hash: str, images: int, attempts: int, latency_ms: int,
                     usage: Usage | None = None, cost: float = 0.0) -> None:
        if not self.record:
            return
        from ..db import session_scope
        from ..models import LLMCall
        u = usage or Usage()
        with session_scope() as s:
            s.add(LLMCall(
                document_id=current_document.get(), job_id=current_job.get(), run_id=current_run.get(),
                purpose=purpose, tier=tier, model=model, effort=effort, status="error", http_status=exc.status,
                error=str(exc)[:2000], attempts=attempts, image_count=images, latency_ms=latency_ms,
                prompt_tokens=u.prompt_tokens, completion_tokens=u.completion_tokens,
                reasoning_tokens=u.reasoning_tokens, cached_tokens=u.cached_tokens, cost_usd=cost,
                request_hash=request_hash))


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def set_llm(client: LLMClient | None) -> None:
    """Tests and the CLI swap the process-wide client."""
    global _client
    _client = client
