"""Price table per model tier, in USD per 1M tokens.

The defaults are PLACEHOLDERS shaped like a typical GPT-class list price — set the
real numbers for your contract on the Cost page (stored in the database) before
reading any dollar figure. Prices are per tier rather than per model name so the
table stays meaningful when tiers point at the same model with different
reasoning effort: the difference then shows up as reasoning (output) tokens.
"""
from __future__ import annotations

from typing import Any

DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "fast": {"input": 1.25, "cached_input": 0.125, "output": 10.0},
    "strong": {"input": 1.25, "cached_input": 0.125, "output": 10.0},
    "vision": {"input": 1.25, "cached_input": 0.125, "output": 10.0},
}
PRICING_KEY = "pricing"


def load_pricing() -> dict[str, dict[str, float]]:
    from ..db import session_scope
    from ..models import Setting

    try:
        with session_scope() as s:
            row = s.get(Setting, PRICING_KEY)
            if row and isinstance(row.value, dict):
                merged = {k: dict(v) for k, v in DEFAULT_PRICING.items()}
                for tier, prices in row.value.items():
                    merged.setdefault(tier, {}).update({k: float(v) for k, v in prices.items()})
                return merged
    except Exception:
        pass
    return {k: dict(v) for k, v in DEFAULT_PRICING.items()}


def save_pricing(table: dict[str, Any]) -> dict[str, dict[str, float]]:
    from ..db import session_scope
    from ..models import Setting

    clean = {tier: {k: float(v) for k, v in prices.items() if k in ("input", "cached_input", "output")}
             for tier, prices in table.items()}
    with session_scope() as s:
        row = s.get(Setting, PRICING_KEY)
        if row:
            row.value = clean
        else:
            s.add(Setting(key=PRICING_KEY, value=clean))
    return load_pricing()


def call_cost(pricing: dict[str, dict[str, float]], tier: str, prompt_tokens: int, completion_tokens: int,
              cached_tokens: int = 0) -> float:
    """completion_tokens already includes reasoning tokens (OpenAI usage semantics)."""
    p = pricing.get(tier) or pricing.get("fast") or DEFAULT_PRICING["fast"]
    uncached = max(0, prompt_tokens - cached_tokens)
    return (uncached * p["input"] + cached_tokens * p.get("cached_input", p["input"])
            + completion_tokens * p["output"]) / 1e6
