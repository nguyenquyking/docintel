"""Which pages does the model need to read for a field group?

Long agreements put the term clause on page 87. Sending all 120 pages to the model
works but costs ~10× more per document, and "lost in the middle" is real. So for
long documents each field group gets its own context:

1. **Hybrid retrieval** — BM25 (exact clause vocabulary, English and Vietnamese)
   fused with embeddings (paraphrases, cross-lingual) by reciprocal rank fusion.
2. **Structural priors** — what a contract lawyer would check anyway: the first
   page (parties, effective date), signature blocks, amendments (they override the
   original terms), definitions, fee tables.
3. **A token budget** — pages are added by priority until the budget is spent.

The selected pages are stored with the extraction; the eval measures *evidence
recall* (was the gold evidence page selected?) separately from extraction
accuracy, because no prompt can fix a page that was never sent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from ..parsing.ir import DocumentIR
from ..text.normalize import estimate_tokens
from .bm25 import BM25, tokenize
from .chunking import ChunkData

GROUP_QUERIES: dict[str, dict[str, str]] = {
    "parties": {
        "lexical": "agreement entered into by and between parties party customer provider tenant landlord buyer seller "
                   "in witness whereof signed by name title date giữa bên a bên b đại diện ký tên chức vụ ngày ký",
        "semantic": "Who are the parties to this agreement and who signed it, with which titles and dates?",
    },
    "term": {
        "lexical": "term initial term commence effective date continue expire expiry expiration renew renewal renewed "
                   "automatically successive non-renewal notice prior terminate termination convenience amended "
                   "thời hạn hiệu lực gia hạn tự động hết hạn thông báo chấm dứt đơn phương sửa đổi",
        "semantic": "How long does the agreement last, does it renew automatically, what notice prevents renewal, and "
                    "can a party terminate for convenience?",
    },
    "commercial": {
        "lexical": "fee fees annual monthly rent price total contract price payment pay invoice invoices days receipt "
                   "consideration amount quantity unit price phí giá trị giá thuê thanh toán hóa đơn tổng cộng đơn giá thành tiền",
        "semantic": "How much is paid under the agreement, how often, and within how many days are invoices due?",
    },
    "law": {
        "lexical": "governed by construed in accordance with the laws of jurisdiction governing law "
                   "điều chỉnh giải thích theo pháp luật luật áp dụng",
        "semantic": "Which jurisdiction's law governs this agreement?",
    },
}
_AMENDMENT = re.compile(r"(?i)\bamendment\s+no\b|\bamendment\b.*\bto\b|phụ lục sửa đổi|\bsửa đổi\b")
_DEFINITIONS = re.compile(r"(?i)\bdefinitions\b|giải thích từ ngữ|định nghĩa")


@dataclass
class Selection:
    pages: list[int]
    reasons: dict[int, str]
    tokens: int


def rrf(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return scores


class DocumentIndex:
    """Per-document retrieval index, built once per extraction."""

    def __init__(self, ir: DocumentIR, chunks: list[ChunkData], embeddings: np.ndarray | None = None):
        self.ir, self.chunks, self.embeddings = ir, chunks, embeddings
        self.bm25 = BM25([tokenize(c.header(ir.title) + " " + c.text) for c in chunks])
        self.page_tokens = {p.page_no: estimate_tokens(p.markdown) for p in ir.pages}

    def rank(self, group: str, query_vec: np.ndarray | None, top_k: int) -> list[int]:
        q = GROUP_QUERIES[group]
        rankings = [self.bm25.top_k(tokenize(q["lexical"]), top_k * 3)]
        if query_vec is not None and self.embeddings is not None and len(self.embeddings):
            sims = self.embeddings @ query_vec
            rankings.append([int(i) for i in np.argsort(-sims)[: top_k * 3]])
        fused = rrf(rankings)
        return [i for i, _ in sorted(fused.items(), key=lambda kv: -kv[1])][:top_k]

    def priors(self, group: str) -> list[tuple[int, str]]:
        pages = self.ir.pages
        last = pages[-1].page_no
        sig = [c.page_start for c in self.chunks if c.kind == "signature"]
        amend = [p.page_no for p in pages if any(b.kind == "heading" and _AMENDMENT.search(b.text) for b in p.blocks)]
        out: list[tuple[int, str]] = [(1, "first page: parties and effective date")]
        if group == "parties":
            out += [(p, "signature block") for p in sig] or [(last, "last page")]
        elif group == "term":
            out += [(p, "amendment (overrides the original term)") for p in amend]
            out += [(p, "signature block (effective date may be the last signature)") for p in sig[:1]]
            defs = [p.page_no for p in pages if any(b.kind == "heading" and _DEFINITIONS.search(b.text) for b in p.blocks)]
            out += [(p, "definitions") for p in defs[:1]]
        elif group == "commercial":
            out += [(p.page_no, "table (fees / line items)") for p in pages if p.has_table][:4]
            out += [(p, "amendment (may change the fee)") for p in amend]
        return out

    def select(self, group: str, query_vec: np.ndarray | None, budget_tokens: int, top_k: int) -> Selection:
        reasons: dict[int, str] = {}
        ordered: list[int] = []
        for page_no, why in self.priors(group):
            if page_no not in reasons:
                reasons[page_no] = why
                ordered.append(page_no)
        for rank, idx in enumerate(self.rank(group, query_vec, top_k)):
            c = self.chunks[idx]
            for page_no in range(c.page_start, c.page_end + 1):
                if page_no not in reasons:
                    reasons[page_no] = f"retrieval rank {rank + 1}" + (f" ({c.section[:60]})" if c.section else "")
                    ordered.append(page_no)
        chosen, used = [], 0
        for page_no in ordered:
            cost = self.page_tokens.get(page_no, 0)
            if chosen and used + cost > budget_tokens:
                continue
            chosen.append(page_no)
            used += cost
        return Selection(sorted(chosen), {p: reasons[p] for p in chosen}, used)
