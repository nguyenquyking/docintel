"""Search and Q&A over the corpus: the same chunks and facts that extraction produced.

Hybrid retrieval (BM25 + embeddings, reciprocal rank fusion) over section-aware
chunks, combined with **structured filters on extracted facts** — "auto-renewing
agreements whose cancel-by date is in the next 60 days" is a filter, not a vector
search. Answers cite document and page, and every cited quote is grounded against
the page text before it is shown.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select

from ..db import session_scope
from ..llm.client import LLMClient
from ..models import Chunk, Document, Page
from ..retrieval.bm25 import BM25, tokenize
from ..retrieval.embeddings import Embedder, from_bytes
from ..retrieval.hybrid import rrf
from ..text.normalize import quote_score


@dataclass
class Filters:
    doc_type: str | None = None
    language: str | None = None
    status: str | None = None
    party: str | None = None
    governing_law: str | None = None
    auto_renewal: bool | None = None
    deadline_within_days: int | None = None
    ends_within_days: int | None = None
    min_value: float | None = None
    currency: str | None = None
    include_golden: bool = False
    document_ids: list[str] | None = None


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


def matches(doc: Document, f: Filters, today: date | None = None) -> bool:
    today = today or date.today()
    facts = doc.facts or {}
    if not f.include_golden and doc.source.startswith("golden:"):
        return False
    if f.document_ids and doc.id not in f.document_ids:
        return False
    if f.doc_type and (facts.get("document_type") or doc.doc_type) != f.doc_type:
        return False
    if f.language and doc.language != f.language:
        return False
    if f.status and facts.get("status") != f.status:
        return False
    if f.party and not any(f.party.casefold() in (p or "").casefold() for p in facts.get("parties") or []):
        return False
    if f.governing_law and (facts.get("governing_law") or "") != f.governing_law.casefold():
        return False
    if f.auto_renewal is not None and facts.get("auto_renewal") is not f.auto_renewal:
        return False
    if f.deadline_within_days is not None:
        d = _date(facts.get("next_notice_deadline"))
        if not d or not (today <= d <= today + timedelta(days=f.deadline_within_days)):
            return False
    if f.ends_within_days is not None:
        d = _date(facts.get("current_term_end"))
        if not d or not (today <= d <= today + timedelta(days=f.ends_within_days)):
            return False
    if f.min_value is not None or f.currency:
        v = facts.get("contract_value") or {}
        if f.currency and v.get("currency") != f.currency.upper():
            return False
        if f.min_value is not None and (v.get("amount") or 0) < f.min_value:
            return False
    return True


class SearchIndex:
    """In-process BM25 + vector index over all chunks, rebuilt when the chunk table changes.
    At larger scale this is Postgres full-text + pgvector or a search service; the
    interface (hybrid ranking + fact filters) stays the same."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._version: tuple | None = None
        self.rows: list[tuple[int, str, int, int, str | None, str]] = []
        self.bm25: BM25 | None = None
        self.vectors: np.ndarray | None = None

    def refresh(self) -> None:
        with session_scope() as s:
            version = s.execute(select(func.count(Chunk.id), func.max(Chunk.id))).one()
            if version == self._version:
                return
            rows = s.execute(select(Chunk.id, Chunk.document_id, Chunk.page_start, Chunk.page_end, Chunk.section,
                                    Chunk.text, Chunk.embedding).order_by(Chunk.id)).all()
        with self._lock:
            self.rows = [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]
            self.bm25 = BM25([tokenize(f"{r[4] or ''} {r[5]}") for r in rows]) if rows else None
            vecs = [from_bytes(r[6]) for r in rows]
            if rows and all(v is not None for v in vecs) and len({len(v) for v in vecs}) == 1:
                self.vectors = np.vstack(vecs)
            else:
                self.vectors = None
            self._version = version

    async def search(self, query: str, filters: Filters, embedder: Embedder | None, k: int = 20) -> list[dict]:
        self.refresh()
        if not self.rows or not query.strip():
            return []
        with session_scope() as s:
            docs = {d.id: d for d in s.scalars(select(Document))}
        allowed = {i for i, r in enumerate(self.rows) if r[1] in docs and matches(docs[r[1]], filters)}
        if not allowed:
            return []
        rankings = []
        bm = self.bm25.scores(tokenize(query)) if self.bm25 else []
        rankings.append([i for i in sorted(allowed, key=lambda i: -bm[i]) if bm[i] > 0][: k * 3])
        if embedder is not None and self.vectors is not None:
            qv = await embedder.embed([query])
            if qv is not None and qv.shape[1] == self.vectors.shape[1]:
                sims = self.vectors @ qv[0]
                rankings.append([i for i in sorted(allowed, key=lambda i: -sims[i])][: k * 3])
        fused = rrf(rankings)
        out = []
        for i, score in sorted(fused.items(), key=lambda kv: -kv[1])[:k]:
            cid, doc_id, p0, p1, section, text = self.rows[i]
            d = docs[doc_id]
            out.append({"chunk_id": cid, "document_id": doc_id, "title": d.title or d.filename, "filename": d.filename,
                        "page_start": p0, "page_end": p1, "section": section, "text": text[:1200],
                        "score": round(score, 5), "doc_type": (d.facts or {}).get("document_type"),
                        "status": (d.facts or {}).get("status")})
        return out


INDEX = SearchIndex()

ASK_SYSTEM = """You answer questions about a collection of contracts using only the passages and facts provided.
Cite every claim: document_id and page of the passage, plus a short verbatim quote from that passage.
If the passages do not contain the answer, say so plainly. Answer in the language of the question."""

ASK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answer", "citations"],
    "properties": {
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["document_id", "page", "quote"],
            "properties": {"document_id": {"type": "string"}, "page": {"type": "integer"},
                           "quote": {"type": "string"}}}},
    },
}


async def ask(question: str, filters: Filters, llm: LLMClient, embedder: Embedder | None, k: int = 8) -> dict:
    hits = await INDEX.search(question, filters, embedder, k=k)
    if not hits:
        return {"answer": "No matching documents.", "citations": [], "passages": []}
    doc_ids = list(dict.fromkeys(h["document_id"] for h in hits))
    with session_scope() as s:
        facts = {d.id: (d.title or d.filename, d.facts or {}) for d in s.scalars(select(Document).where(Document.id.in_(doc_ids)))}
    fact_lines = []
    for did in doc_ids:
        title, f = facts.get(did, ("", {}))
        fact_lines.append(f"- {did} | {title} | parties: {', '.join(f.get('parties') or [])} | status: {f.get('status')} | "
                          f"term end: {f.get('current_term_end')} | auto-renewal: {f.get('auto_renewal')} | "
                          f"cancel-by: {f.get('next_notice_deadline')} | value: {f.get('contract_value')} | "
                          f"governing law: {f.get('governing_law')}")
    passages = "\n\n".join(f"[document_id={h['document_id']} page={h['page_start']}] {h['text']}" for h in hits)
    messages = [{"role": "system", "content": ASK_SYSTEM},
                {"role": "user", "content": f"Question: {question}\n\nExtracted facts:\n" + "\n".join(fact_lines) +
                 f"\n\nPassages:\n{passages}"}]
    resp = await llm.chat(messages, purpose="ask", tier="fast", json_schema=ASK_SCHEMA, schema_name="answer",
                          max_output_tokens=4000)
    out = resp.parsed or {"answer": resp.content, "citations": []}
    with session_scope() as s:
        for c in out.get("citations", []):
            page = s.scalars(select(Page).where(Page.document_id == c.get("document_id"),
                                                Page.page_no == c.get("page"))).first()
            score = quote_score(c.get("quote", ""), page.text) if page else 0.0
            c["grounded"] = score >= 0.85
            c["score"] = round(score, 3)
            c["title"] = facts.get(c.get("document_id"), ("", {}))[0]
    out["passages"] = hits
    out["cost_usd"] = resp.cost_usd
    return out
