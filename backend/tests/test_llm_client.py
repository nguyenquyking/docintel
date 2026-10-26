import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from docintel.db import session_scope
from docintel.llm.client import LLMClient, LLMRequestError, LLMRetryableError
from docintel.models import LLMCall
from docintel.settings import get_settings


def _settings(monkeypatch, **env):
    monkeypatch.setenv("DOCINTEL_LLM_PROVIDER", "openai")
    monkeypatch.setenv("DOCINTEL_LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("DOCINTEL_LLM_API_KEY", "test-key")
    monkeypatch.setenv("DOCINTEL_LLM_MODEL_FAST", "small-model")
    monkeypatch.setenv("DOCINTEL_LLM_MAX_RETRIES", "3")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return get_settings()


def _ok(content="hello", prompt=100, completion=20, cached=0):
    return httpx.Response(200, json={"model": "small-model-2026", "choices": [{"message": {"content": content},
                                     "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                                               "prompt_tokens_details": {"cached_tokens": cached},
                                               "completion_tokens_details": {"reasoning_tokens": 5}}})


def test_retries_transient_errors_then_meters_cost(monkeypatch):
    s = _settings(monkeypatch)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-key"
        if len(seen) < 3:
            return httpx.Response(429 if len(seen) == 1 else 503, headers={"Retry-After": "0"}, text="busy")
        return _ok(prompt=1000, completion=200, cached=400)

    llm = LLMClient(s, transport=httpx.MockTransport(handler))
    r = asyncio.run(llm.chat([{"role": "user", "content": "hi"}], purpose="test", use_cache=False))
    assert r.content == "hello" and len(seen) == 3
    assert "temperature" not in seen[0] and seen[0]["reasoning_effort"] == "low"
    # 600 uncached × 1.25 + 400 cached × 0.125 + 200 output × 10, per 1M tokens (placeholder prices)
    assert r.cost_usd == pytest.approx((600 * 1.25 + 400 * 0.125 + 200 * 10) / 1e6)
    with session_scope() as db:
        call = db.scalars(select(LLMCall)).one()
        assert call.attempts == 3 and call.cached_tokens == 400 and call.reasoning_tokens == 5


def test_client_errors_are_not_retried_and_keep_the_body(monkeypatch):
    s = _settings(monkeypatch)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, text='{"message": "Unsupported value: reasoning_effort minimal"}')

    llm = LLMClient(s, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMRequestError) as err:
        asyncio.run(llm.chat([{"role": "user", "content": "hi"}], purpose="test", use_cache=False))
    assert len(calls) == 1 and "reasoning_effort minimal" in err.value.body
    with session_scope() as db:
        assert db.scalars(select(LLMCall)).one().status == "error"


def test_retry_budget_exhausted(monkeypatch):
    s = _settings(monkeypatch)
    llm = LLMClient(s, transport=httpx.MockTransport(lambda r: httpx.Response(503, headers={"Retry-After": "0"})))
    with pytest.raises(LLMRetryableError):
        asyncio.run(llm.chat([{"role": "user", "content": "hi"}], purpose="test", use_cache=False))


def test_exact_request_cache(monkeypatch):
    s = _settings(monkeypatch)
    hits = []
    llm = LLMClient(s, transport=httpx.MockTransport(lambda r: hits.append(1) or _ok("cached?")))
    msgs = [{"role": "user", "content": "same question"}]
    first = asyncio.run(llm.chat(msgs, purpose="test"))
    second = asyncio.run(llm.chat(msgs, purpose="test"))
    assert len(hits) == 1 and second.cache_hit and second.content == first.content
    # spend is zero, but config comparisons see the nominal cost of the answer
    assert second.cost_usd == first.cost_usd > 0
    with session_scope() as db:
        hit = db.scalars(select(LLMCall).where(LLMCall.cache_hit.is_(True))).one()
        assert hit.cost_usd == 0


def test_structured_output_and_truncation(monkeypatch):
    s = _settings(monkeypatch)
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"], "additionalProperties": False}
    llm = LLMClient(s, transport=httpx.MockTransport(lambda r: _ok('{"a": 3}')))
    r = asyncio.run(llm.chat([{"role": "user", "content": "x"}], purpose="t", json_schema=schema, use_cache=False))
    assert r.parsed == {"a": 3}

    def truncated(request):
        body = {"choices": [{"message": {"content": '{"a": '}, "finish_reason": "length"}], "usage": {}}
        return httpx.Response(200, json=body)

    from docintel.llm.client import LLMOutputError
    llm = LLMClient(s, transport=httpx.MockTransport(truncated))
    with pytest.raises(LLMOutputError):
        asyncio.run(llm.chat([{"role": "user", "content": "x"}], purpose="t", json_schema=schema, use_cache=False))


def test_databricks_gateway_url_normalization(monkeypatch):
    from docintel.llm.auth import DatabricksAuth

    class FakeCfg:
        host = "https://workspace.example.net/"

    for path, expected in [("/ai-gateway/openai/v1", "https://workspace.example.net/ai-gateway/openai/v1"),
                           ("/ai-gateway/openai", "https://workspace.example.net/ai-gateway/openai/v1")]:
        s = _settings(monkeypatch, DOCINTEL_DATABRICKS_GATEWAY_PATH=path)
        auth = DatabricksAuth(s)
        auth._config = FakeCfg()
        assert auth.base_url() == expected


def test_settings_never_expose_secrets(monkeypatch):
    s = _settings(monkeypatch, DOCINTEL_DATABRICKS_TOKEN="dummy-token-value")
    view = json.dumps(s.public_view())
    assert "test-key" not in view and "dummy-token-value" not in view and "llm.test" not in view
    assert "test-key" not in repr(s)
