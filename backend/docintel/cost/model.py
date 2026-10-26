"""The cost question: what does the whole corpus cost, which configuration gives the best
quality per dollar, and when would our own model beat vendor pricing?

All three answers are formulas over *measured* inputs — tokens per page by language,
share of pages actually sent, escalation rate, scanned share, VLM tokens per page,
dedup rate, prompt-cache share — taken from this pipeline's own metering, with the
price table as the only assumption.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..llm.pricing import load_pricing
from ..models import Document, EvalRun, Extraction, LLMCall, Page
from ..text.normalize import estimate_tokens


def prompt_overhead_tokens() -> int:
    """Instructions + JSON schema sent with every extraction call (the cacheable prefix)."""
    import json

    from ..extraction.prompts import SYSTEM
    from ..extraction.schema import GROUPS, group_schema
    return estimate_tokens(SYSTEM) + estimate_tokens(json.dumps(group_schema(GROUPS)))


def measured_parameters() -> dict[str, Any]:
    with session_scope() as s:
        docs = s.scalars(select(Document).where(Document.processed_at.is_not(None))).all()
        exts = s.scalars(select(Extraction).where(Extraction.reused_from.is_(None))).all()
        calls = s.scalars(select(LLMCall).where(LLMCall.status == "ok", LLMCall.cache_hit.is_(False))).all()
        pages = s.execute(select(Page.document_id, Page.route, Page.language, Page.text)).all()
    if not docs:
        return {}
    by_lang: dict[str, list[int]] = defaultdict(list)
    doc_tokens: dict[str, int] = defaultdict(int)
    routes: dict[str, int] = defaultdict(int)
    for doc_id, route, lang, text in pages:
        t = estimate_tokens(text or "")
        by_lang[lang or "unknown"].append(t)
        doc_tokens[doc_id] += t
        routes[route] += 1
    total_pages = sum(routes.values()) or 1
    extract_calls = [c for c in calls if c.purpose.startswith("extract")]
    vlm_calls = [c for c in calls if c.purpose == "vlm_page"]
    overhead = prompt_overhead_tokens()
    sent_fraction = []   # document tokens actually sent per document, relative to the whole document
    for e in exts:
        full = doc_tokens.get(e.document_id)
        if full and e.prompt_tokens:
            sent_fraction.append(max(0.0, (e.prompt_tokens - overhead * max(1, e.llm_calls)) / full))
    prompt_tokens = sum(c.prompt_tokens for c in extract_calls) or 1
    return {
        "documents": len(docs),
        "pages_per_doc": round(total_pages / len(docs), 2),
        "tokens_per_page": {k: round(sum(v) / len(v), 1) for k, v in by_lang.items() if v},
        "tokens_per_page_all": round(sum(doc_tokens.values()) / total_pages, 1),
        "sent_fraction": round(sum(sent_fraction) / len(sent_fraction), 4) if sent_fraction else None,
        "prompt_overhead_tokens": overhead,
        "output_tokens_per_doc": round(sum(e.completion_tokens for e in exts) / len(exts), 1) if exts else None,
        "prompt_tokens_per_doc": round(sum(e.prompt_tokens for e in exts) / len(exts), 1) if exts else None,
        "escalation_rate": round(sum(1 for e in exts if e.escalated_groups) / len(exts), 4) if exts else None,
        "grouped_share": round(sum(1 for e in exts if e.mode == "grouped") / len(exts), 4) if exts else None,
        "scanned_page_share": round((routes.get("ocr", 0) + routes.get("vlm", 0)) / total_pages, 4),
        "vlm_share_of_scanned": round(routes.get("vlm", 0) / max(1, routes.get("ocr", 0) + routes.get("vlm", 0)), 4),
        "vlm_tokens_per_page": round(sum(c.prompt_tokens for c in vlm_calls) / len(vlm_calls), 1) if vlm_calls else None,
        "vlm_output_tokens_per_page": round(sum(c.completion_tokens for c in vlm_calls) / len(vlm_calls), 1) if vlm_calls else None,
        "dedup_rate": round(sum(1 for d in docs if d.duplicate_of) / len(docs), 4),
        "prompt_cache_share": round(sum(c.cached_tokens for c in extract_calls) / prompt_tokens, 4),
        "cost_per_doc_usd": round(sum(e.cost_usd for e in exts) / len(exts), 6) if exts else None,
        "reasoning_share_of_output": round(sum(c.reasoning_tokens for c in calls) / max(1, sum(c.completion_tokens for c in calls)), 4),
    }


def project(p: dict[str, Any]) -> dict[str, Any]:
    """Corpus cost from explicit assumptions. Every input is shown next to the result."""
    pricing = p.get("pricing") or load_pricing()
    n = float(p.get("n_docs", 1_000_000)) * (1 - float(p.get("dedup_rate", 0.0)))
    pages = n * float(p.get("pages_per_doc", 10))
    tpp = float(p.get("tokens_per_page", 650))
    sent = float(p.get("sent_fraction", 1.0))
    cached = float(p.get("prompt_cache_share", 0.0))
    out_doc = float(p.get("output_tokens_per_doc", 1200))
    overhead = float(p.get("prompt_overhead_tokens", 1500))          # instructions + schema per call
    esc = float(p.get("escalation_rate", 0.0))
    esc_ctx = float(p.get("escalation_context_multiplier", 0.5))      # an escalated group re-reads part of the doc
    scanned = float(p.get("scanned_share", 0.0))
    vlm_share = float(p.get("vlm_share_of_scanned", 0.0))
    vlm_in = float(p.get("vlm_tokens_per_page", 1100))
    vlm_out = float(p.get("vlm_output_tokens_per_page", 700))
    ocr_per_1k = float(p.get("ocr_cost_per_1k_pages", 0.0))
    discount = float(p.get("batch_discount", 0.0))

    def tok_cost(tier: str, inp: float, outp: float, cache: float = 0.0) -> float:
        pr = pricing[tier]
        return (inp * (1 - cache) * pr["input"] + inp * cache * pr.get("cached_input", pr["input"])
                + outp * pr["output"]) / 1e6

    in_fast = n * (pages / n * tpp * sent + overhead) if n else 0.0
    fast = tok_cost("fast", in_fast, n * out_doc, cached)
    strong = tok_cost("strong", in_fast * esc * esc_ctx, n * esc * out_doc * 0.6, cached)
    vlm_pages = pages * scanned * vlm_share
    vision = tok_cost("vision", vlm_pages * vlm_in, vlm_pages * vlm_out)
    ocr = pages * scanned * (1 - vlm_share) * ocr_per_1k / 1000
    llm_total = (fast + strong + vision) * (1 - discount)
    total = llm_total + ocr
    return {
        "inputs": {**{k: v for k, v in p.items() if k != "pricing"}, "pricing": pricing},
        "effective_docs": round(n), "pages": round(pages),
        "tokens": {"fast_input": round(in_fast), "fast_output": round(n * out_doc),
                   "vision_input": round(vlm_pages * vlm_in)},
        "breakdown_usd": {"fast_tier": round(fast * (1 - discount), 2), "strong_tier": round(strong * (1 - discount), 2),
                          "vision": round(vision * (1 - discount), 2), "ocr": round(ocr, 2)},
        "total_usd": round(total, 2),
        "per_1k_docs_usd": round(1000 * total / n, 4) if n else None,
    }


def breakeven(p: dict[str, Any]) -> dict[str, Any]:
    """Self-hosting: price per 1M tokens = GPU $/h ÷ (tokens/s × 3600 × utilization ÷ 1e6).
    Hosting alone rarely beats a small vendor model; the win needs a distilled small model
    that matches the *expensive* model's quality on our fields — proven on the golden set."""
    gpu = float(p.get("gpu_hour_usd", 2.5))
    tps = float(p.get("throughput_tokens_per_s", 10_000))
    util = float(p.get("utilization", 0.6))
    fixed = float(p.get("fixed_monthly_usd", 15_000))
    vendor = float(p.get("vendor_price_per_m", 3.0))
    volume_m = float(p.get("monthly_tokens_m", 2_000))
    self_price = gpu / (tps * 3600 * util / 1e6)
    margin = vendor - self_price
    breakeven_m = fixed / margin if margin > 0 else None
    vendor_monthly = volume_m * vendor
    self_monthly = volume_m * self_price + fixed
    return {"self_host_price_per_m": round(self_price, 4), "vendor_price_per_m": vendor,
            "breakeven_tokens_per_month_m": None if breakeven_m is None else round(breakeven_m, 1),
            "monthly_cost_vendor": round(vendor_monthly, 2), "monthly_cost_self_host": round(self_monthly, 2),
            "monthly_savings": round(vendor_monthly - self_monthly, 2),
            "verdict": ("self-hosting cannot beat this vendor price" if breakeven_m is None else
                        "self-host pays off at this volume" if volume_m >= breakeven_m else
                        f"stay with the vendor until volume exceeds {breakeven_m:,.0f}M tokens/month")}


def pareto(set_name: str | None = None) -> dict[str, Any]:
    with session_scope() as s:
        q = select(EvalRun).where(EvalRun.status == "done", EvalRun.quality.is_not(None),
                                  EvalRun.cost_per_1k.is_not(None))
        if set_name:
            q = q.where(EvalRun.set_name == set_name)
        runs = s.scalars(q.order_by(EvalRun.created_at)).all()
        points = [{"id": r.id, "name": r.name, "set": r.set_name, "split": r.split, "quality": r.quality,
                   "cost_per_1k": r.cost_per_1k, "latency_p50_ms": r.latency_p50_ms,
                   "preset": (r.config or {}).get("preset")} for r in runs]
    frontier, best = [], -1.0
    for pt in sorted(points, key=lambda x: (x["cost_per_1k"], -x["quality"])):
        if pt["quality"] > best:
            frontier.append(pt["id"])
            best = pt["quality"]
    for pt in points:
        pt["on_frontier"] = pt["id"] in frontier
    return {"points": points, "frontier": frontier}
