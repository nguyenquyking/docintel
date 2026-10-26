"""Section-aware chunks with page ranges — the LLM-ready unit for retrieval and search.

A chunk never mixes two sections, tables stay tables (split by rows, header
repeated), signature blocks stay whole, and every chunk knows its pages so a
search hit or a retrieved passage can be cited and highlighted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..parsing.ir import DocumentIR


@dataclass
class ChunkData:
    ordinal: int
    text: str
    page_start: int
    page_end: int
    section: str | None
    kind: str = "text"   # text | table | signature

    def header(self, title: str | None) -> str:
        """Contextual header: what a reader would need to know where this passage sits."""
        parts = [p for p in (title, self.section) if p]
        return " › ".join(parts)


def _split_long(words: list[str], max_words: int) -> list[list[str]]:
    text = " ".join(words)
    sentences = re.split(r"(?<=[.;:])\s+", text)
    out, cur = [], []
    for s in sentences:
        sw = s.split()
        if cur and len(cur) + len(sw) > max_words:
            out.append(cur)
            cur = []
        cur.extend(sw)
        while len(cur) > max_words:
            out.append(cur[:max_words])
            cur = cur[max_words:]
    if cur:
        out.append(cur)
    return out


def chunk_document(ir: DocumentIR, max_words: int = 200, min_words: int = 50) -> list[ChunkData]:
    chunks: list[ChunkData] = []
    cur: list[str] = []
    pages: list[int] = []
    section: str | None = None

    def emit(words: list[str], pgs: list[int], kind: str = "text") -> None:
        if words:
            chunks.append(ChunkData(len(chunks), " ".join(words), min(pgs), max(pgs), section, kind))

    def flush() -> None:
        nonlocal cur, pages
        emit(cur, pages)
        cur, pages = [], []

    for page in ir.pages:
        for b in page.blocks:
            if b.kind == "heading":
                if len(cur) >= min_words:
                    flush()
                section = b.text[:200]
                cur.extend(b.text.split())
                pages.append(page.page_no)
            elif b.kind == "table" and b.rows:
                flush()
                header, body = b.rows[0], b.rows[1:]

                def emit_table(rows: list[list[str]], page_no: int = page.page_no, head: list[str] = header,
                               sec: str | None = section) -> None:
                    text = "\n".join(" | ".join(r) for r in [head, *rows])
                    chunks.append(ChunkData(len(chunks), text, page_no, page_no, sec, "table"))

                batch: list[list[str]] = []
                for row in body:
                    batch.append(row)
                    if sum(len(" ".join(r).split()) for r in batch) > max_words:
                        emit_table(batch)
                        batch = []
                if batch or not body:
                    emit_table(batch)
            elif b.kind == "signature":
                flush()
                emit(b.text.split(), [page.page_no], "signature")
            else:
                words = b.text.split()
                if len(words) > max_words:
                    flush()
                    for part in _split_long(words, max_words):
                        emit(part, [page.page_no])
                    continue
                if len(cur) + len(words) > max_words:
                    flush()
                cur.extend(words)
                pages.append(page.page_no)
    flush()
    return chunks
