"""Extraction orchestration: context selection → model call(s) → validation & repair →
verification → confidence → escalation → facts.

Two modes, chosen per document by size (and comparable in the eval):

* **full document** — short documents: one call, all field groups, the model sees
  everything; "not found" answers are trustworthy.
* **grouped** — long documents: one call per field group, each with only the pages
  retrieval and structural priors select (see ``retrieval/hybrid.py``).

**Cascade:** everything runs on the fast tier first. Groups whose critical fields
come back low-confidence (ungrounded quote, value not in its quote, rule
violations) are re-extracted on the strong tier with a wider context; the better
verified answer wins. The expensive model is paid only where the cheap one is
unsure — that ratio is what the cost model calls the escalation rate.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from ..llm.client import LLMClient, LLMOutputError, LLMResponse
from ..parsing.ir import DocumentIR
from ..retrieval.chunking import ChunkData
from ..retrieval.embeddings import Embedder
from ..retrieval.hybrid import GROUP_QUERIES, DocumentIndex
from ..settings import Settings, get_settings
from ..text.normalize import estimate_tokens
from .confidence import document_confidence, score_fields
from .derive import derive_facts
from .prompts import build_messages, repair_message
from .schema import CRITICAL_FIELDS, GROUP_PROPERTIES, GROUPS, group_schema
from .verify import FieldResult, build_field_results, business_rules, validate_output


@dataclass
class ExtractConfig:
    name: str = "cascade"
    mode: str = "auto"                 # auto | full | grouped
    first_tier: str = "fast"
    escalation: bool = True
    escalate_below: float = 0.6
    full_doc_max_tokens: int = 24_000
    group_budget_tokens: int = 12_000
    top_k: int = 6
    max_repairs: int = 2
    review_below: float = 0.8
    use_cache: bool = True

    @classmethod
    def from_settings(cls, settings: Settings | None = None, **overrides: Any) -> ExtractConfig:
        s = settings or get_settings()
        base = cls(escalation=s.escalation, escalate_below=s.escalate_below, full_doc_max_tokens=s.full_doc_max_tokens,
                   group_budget_tokens=s.group_context_max_tokens, top_k=s.retrieval_top_k,
                   max_repairs=s.max_repair_attempts, review_below=s.review_below)
        for k, v in overrides.items():
            setattr(base, k, v)
        return base

    @classmethod
    def preset(cls, name: str, settings: Settings | None = None, **overrides: Any) -> ExtractConfig:
        presets = {
            "cascade": {"first_tier": "fast", "escalation": True},
            "fast": {"first_tier": "fast", "escalation": False},
            "strong": {"first_tier": "strong", "escalation": False},
            "long-context": {"mode": "full", "first_tier": "fast", "escalation": False, "full_doc_max_tokens": 10**9},
            "retrieval-only": {"mode": "grouped", "first_tier": "fast", "escalation": False},
        }
        if name not in presets:
            raise ValueError(f"unknown preset {name!r}; choose from {sorted(presets)}")
        return cls.from_settings(settings, name=name, **{**presets[name], **overrides})

    def key(self) -> str:
        d = asdict(self)
        d.pop("name")
        d.pop("use_cache")
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]


@dataclass
class CallStats:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def add(self, r: LLMResponse) -> None:
        self.calls += 1
        self.prompt_tokens += r.usage.prompt_tokens
        self.completion_tokens += r.usage.completion_tokens
        self.cached_tokens += r.usage.cached_tokens
        self.cost_usd += r.cost_usd

    def merge(self, other: CallStats) -> None:
        self.calls += other.calls
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cached_tokens += other.cached_tokens
        self.cost_usd += other.cost_usd
        self.errors += other.errors


@dataclass
class ExtractionOutcome:
    mode: str
    data: dict[str, Any]
    fields: list[FieldResult]
    facts: dict[str, Any]
    warnings: list[dict[str, Any]]
    selected_pages: dict[str, Any]
    escalated: list[str]
    stats: CallStats
    latency_ms: int
    confidence: float

    @property
    def needs_review(self) -> bool:
        return any(fr.needs_review for fr in self.fields)


def values_and_meta(fields: list[FieldResult]) -> tuple[dict[str, Any], dict[str, dict]]:
    return {fr.field: fr.value for fr in fields}, {fr.field: fr.meta for fr in fields}


def _empty_group(group: str) -> dict[str, Any]:
    from ..llm.client import schema_default
    return schema_default(group_schema([group]))


class Extractor:
    def __init__(self, llm: LLMClient, embedder: Embedder | None = None, settings: Settings | None = None):
        self.llm = llm
        self.embedder = embedder
        self.settings = settings or get_settings()

    async def _call(self, groups: list[str], ir: DocumentIR, pages: list[int], tier: str,
                    config: ExtractConfig) -> tuple[dict[str, Any], CallStats]:
        messages = build_messages(groups, ir.markdown(pages), title=ir.title, language=ir.language,
                                  total_pages=ir.page_count, page_numbers=pages)
        schema = group_schema(groups)
        name = "contract_" + ("full" if len(groups) == len(GROUPS) else "_".join(groups))
        stats = CallStats()
        parsed: dict[str, Any] | None = None
        for _attempt in range(config.max_repairs + 1):
            try:
                resp = await self.llm.chat(messages, purpose=f"extract:{'+'.join(groups)}", tier=tier,
                                           json_schema=schema, schema_name=name, max_output_tokens=16_000,
                                           use_cache=config.use_cache)
            except LLMOutputError as exc:
                stats.errors.append(str(exc))
                messages = [*messages, repair_message([f"the previous answer was unusable ({exc}); answer concisely"])]
                continue
            stats.add(resp)
            parsed = resp.parsed
            errors = validate_output(parsed, ir.page_count)
            if not errors:
                return parsed, stats
            stats.errors += errors
            messages = [*messages, {"role": "assistant", "content": resp.content}, repair_message(errors)]
        if parsed is None:
            parsed = {}
            for g in groups:
                parsed.update(_empty_group(g))
        return _sanitize(parsed, ir.page_count), stats

    async def _query_vectors(self) -> dict[str, np.ndarray | None]:
        if self.embedder is None:
            return {g: None for g in GROUPS}
        vecs = await self.embedder.embed([GROUP_QUERIES[g]["semantic"] for g in GROUPS])
        if vecs is None:
            return {g: None for g in GROUPS}
        return {g: vecs[i] for i, g in enumerate(GROUPS)}

    async def extract(self, ir: DocumentIR, chunks: list[ChunkData], config: ExtractConfig,
                      chunk_embeddings: np.ndarray | None = None,
                      calibration: dict[str, Any] | None = None) -> ExtractionOutcome:
        started = time.monotonic()
        all_pages = [p.page_no for p in ir.pages]
        total_tokens = sum(estimate_tokens(p.markdown) for p in ir.pages)
        mode = "full_document" if config.mode == "full" or (
            config.mode == "auto" and total_tokens <= config.full_doc_max_tokens) else "grouped"
        stats = CallStats()
        selected: dict[str, Any] = {}
        tiers = {g: config.first_tier for g in GROUPS}
        index: DocumentIndex | None = None
        qvecs: dict[str, np.ndarray | None] = {}

        if mode == "full_document":
            data, st = await self._call(GROUPS, ir, all_pages, config.first_tier, config)
            stats.merge(st)
            for g in GROUPS:
                selected[g] = {"pages": all_pages, "reasons": {"*": "whole document"}, "tokens": total_tokens,
                               "tier": config.first_tier}
        else:
            index = DocumentIndex(ir, chunks, chunk_embeddings)
            qvecs = await self._query_vectors()
            selections = {g: index.select(g, qvecs[g], config.group_budget_tokens, config.top_k) for g in GROUPS}
            results = await asyncio.gather(*(self._call([g], ir, selections[g].pages, config.first_tier, config)
                                             for g in GROUPS))
            data = {}
            for g, (out, st) in zip(GROUPS, results, strict=True):
                data.update(out)
                stats.merge(st)
                sel = selections[g]
                selected[g] = {"pages": sel.pages, "reasons": {str(k): v for k, v in sel.reasons.items()},
                               "tokens": sel.tokens, "tier": config.first_tier}

        coverage = {g: set(selected[g]["pages"]) == set(all_pages) for g in GROUPS}
        page_routes = {p.page_no: (p.route, p.ocr_confidence) for p in ir.pages}

        def evaluate(d: dict[str, Any], t: dict[str, str]) -> tuple[list[FieldResult], dict, list]:
            fields = build_field_results(d, ir, t)
            values, meta = values_and_meta(fields)
            facts = derive_facts(values, meta)
            warnings = business_rules({f.field: f for f in fields}, facts)
            score_fields(fields, page_routes=page_routes, full_coverage=coverage, calibration=calibration,
                         review_below=config.review_below)
            return fields, facts, warnings

        fields, facts, warnings = evaluate(data, tiers)
        escalated: list[str] = []
        if config.escalation and config.first_tier != "strong":
            weak = [g for g in GROUPS if any(fr.group == g and fr.field in CRITICAL_FIELDS
                                             and fr.confidence < config.escalate_below for fr in fields)]
            if weak:
                async def retry(g: str) -> tuple[str, list[int], dict, CallStats]:
                    if mode == "full_document" or index is None:
                        pages = all_pages
                    else:
                        pages = index.select(g, qvecs.get(g), int(config.group_budget_tokens * 1.8),
                                             config.top_k * 2).pages
                    out, st = await self._call([g], ir, pages, "strong", config)
                    return g, pages, out, st

                for g, pages, out, st in await asyncio.gather(*(retry(g) for g in weak)):
                    stats.merge(st)
                    candidate = {**data, **out}
                    cand_tiers = {**tiers, g: "strong"}
                    cand_fields, _, _ = evaluate(candidate, cand_tiers)
                    if _group_score(cand_fields, g) >= _group_score(fields, g):
                        data, tiers = candidate, cand_tiers
                        selected[g] = {**selected[g], "pages": pages, "tier": "strong", "escalated": True}
                        escalated.append(g)
                    else:
                        selected[g] = {**selected[g], "escalation_rejected": True}
                    fields, facts, warnings = evaluate(data, tiers)

        return ExtractionOutcome(mode, data, fields, facts, warnings, selected, escalated, stats,
                                 int((time.monotonic() - started) * 1000), document_confidence(fields))


def _group_score(fields: list[FieldResult], group: str) -> float:
    scores = [fr.confidence for fr in fields if fr.group == group and fr.field in CRITICAL_FIELDS]
    scores = scores or [fr.confidence for fr in fields if fr.group == group]
    return sum(scores) / len(scores) if scores else 0.0


def _sanitize(data: dict[str, Any], page_count: int) -> dict[str, Any]:
    """After the last repair attempt: drop values that still fail validation rather than store them."""
    from datetime import date
    for name in ("effective_date", "expiration_date"):
        v = data.get(name)
        if isinstance(v, dict) and v.get("value"):
            try:
                date.fromisoformat(v["value"])
            except ValueError:
                v["value"] = None
    for v in data.values():
        if isinstance(v, dict) and v.get("page") is not None and not (1 <= v["page"] <= page_count):
            v["page"] = None
    for g, props in GROUP_PROPERTIES.items():
        for name in props:
            data.setdefault(name, _empty_group(g)[name])
    return data
