"""Intermediate representation of a parsed document.

One IR feeds everything downstream: extraction prompts (``markdown`` with page
markers), quote grounding and highlight boxes (``words``), retrieval and search
(``blocks``), and monitoring (``route`` per page). It is computed once per
(blob, parser version) and cached as an artifact.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    conf: float | None = None   # OCR confidence; None for the text layer

    def to_list(self) -> list:
        return [self.text, round(self.x0, 1), round(self.top, 1), round(self.x1, 1), round(self.bottom, 1),
                None if self.conf is None else round(self.conf, 3)]

    @classmethod
    def from_list(cls, v: list) -> Word:
        return cls(v[0], v[1], v[2], v[3], v[4], v[5])


@dataclass
class Block:
    kind: str                                  # heading | paragraph | table | signature
    text: str
    bbox: tuple[float, float, float, float] | None = None
    rows: list[list[str]] | None = None        # tables only


@dataclass
class PageIR:
    page_no: int
    width: float
    height: float
    route: str                                 # text_layer | ocr | vlm
    route_reason: str
    language: str = "unknown"
    ocr_engine: str | None = None
    ocr_confidence: float | None = None
    text_quality: float | None = None
    char_count: int = 0
    has_table: bool = False
    words: list[Word] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    text: str = ""
    markdown: str = ""
    struck: list[str] = field(default_factory=list)


@dataclass
class DocumentIR:
    sha256: str
    parser_version: str
    page_count: int
    language: str
    title: str | None
    pages: list[PageIR]

    @property
    def page_texts(self) -> dict[int, str]:
        return {p.page_no: p.text for p in self.pages}

    def markdown(self, pages: list[int] | None = None) -> str:
        wanted = set(pages) if pages else None
        return "\n\n".join(f"=== Page {p.page_no} ===\n{p.markdown}" for p in self.pages
                           if wanted is None or p.page_no in wanted)

    def route_mix(self) -> dict[str, int]:
        mix: dict[str, int] = {}
        for p in self.pages:
            mix[p.route] = mix.get(p.route, 0) + 1
        return mix

    # ── (de)serialization: words as compact lists, the IR of a 150-page scan stays small ──
    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        for page, src in zip(d["pages"], self.pages, strict=True):
            page["words"] = [w.to_list() for w in src.words]
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> DocumentIR:
        pages = []
        for p in d["pages"]:
            p = dict(p)
            p["words"] = [Word.from_list(w) for w in p["words"]]
            p["blocks"] = [Block(**{**b, "bbox": tuple(b["bbox"]) if b.get("bbox") else None}) for b in p["blocks"]]
            pages.append(PageIR(**p))
        return cls(d["sha256"], d["parser_version"], d["page_count"], d["language"], d.get("title"), pages)
