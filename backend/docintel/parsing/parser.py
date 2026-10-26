"""PDF → DocumentIR, one routed page at a time, cached per (blob, parser version)."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ..llm.client import LLMClient
from ..settings import Settings, get_settings
from ..storage import Storage
from ..text.lang import detect_language, document_language
from ..versions import PARSER_VERSION
from .ir import DocumentIR, PageIR, Word
from .layout import (
    blocks_to_markdown,
    blocks_to_text,
    build_blocks,
    group_lines,
    looks_tabular,
    markdown_to_blocks,
    struck_word_indices,
)
from .ocr import get_engine
from .pdf import open_pdf, read_text_layer, render_page
from .router import route_scanned, route_text_layer, text_plausibility, why_no_text_layer
from .vlm import image_to_data_url, transcribe_page

log = logging.getLogger(__name__)


@dataclass
class _Pending:
    """A scanned page waiting for the VLM."""
    index: int
    page: PageIR
    image_url: str          # JPEG data URL: a 150-page scan must not sit in memory as bitmaps
    ocr_text: str | None


_ANCHOR = re.compile(r"(?i)^(?:by:|name:|title:|date:|signature|\(ký|ngày|đại)")


def signature_tokens(words: list[Word], marks: list[tuple[float, float, float, float, str]]) -> list[Word]:
    """A drawn mark next to a signature line is evidence of execution that the text layer
    does not carry. Geometry finds it for free; the token makes it visible to the model."""
    anchors = [w for w in words if _ANCHOR.match(w.text)]
    heights = sorted(w.bottom - w.top for w in words) or [10.0]
    h = heights[len(heights) // 2]
    tokens = []
    for x0, top, x1, bottom, kind in marks:
        cy = (top + bottom) / 2
        if any(abs(cy - (a.top + a.bottom) / 2) < 90 and x0 - 80 < a.x0 < x1 + 160 for a in anchors):
            label = "[stamp]" if kind == "stamp" else "[signed]"
            # a word-sized box at the mark's centre: a 60pt-tall seal must not glue lines together
            tokens.append(Word(label, x0, cy - h / 2, min(x1, x0 + 40), cy + h / 2))
    return tokens


def _page_from_text_layer(page_no: int, width: float, height: float, layer, reason: str) -> PageIR:
    struck = struck_word_indices(layer.words, layer.h_segments)
    keep = [i for i in range(len(layer.words)) if i not in struck]
    words = [Word(layer.words[i]["text"], layer.words[i]["x0"], layer.words[i]["top"], layer.words[i]["x1"],
                  layer.words[i]["bottom"]) for i in keep]
    sizes = {n: float(layer.words[i].get("size", layer.median_size)) for n, i in enumerate(keep)}
    bold = {n for n, i in enumerate(keep) if "bold" in str(layer.words[i].get("fontname", "")).lower()}
    for token in signature_tokens(words, layer.marks):
        sizes[len(words)] = layer.median_size
        words.append(token)
    blocks = build_blocks(words, sizes, bold, layer.tables, layer.median_size, page_height=float(height))
    text = blocks_to_text(blocks)
    struck_text = []
    if struck:
        run: list[str] = []
        for i in sorted(struck):
            if run and i - 1 not in struck:
                struck_text.append(" ".join(run))
                run = []
            run.append(layer.words[i]["text"])
        struck_text.append(" ".join(run))
    return PageIR(page_no=page_no, width=float(width), height=float(height), route="text_layer", route_reason=reason,
                  language=detect_language(text), text_quality=layer.quality, char_count=layer.char_count,
                  has_table=bool(layer.tables), words=words, blocks=blocks, text=text,
                  markdown=blocks_to_markdown(blocks), struck=struck_text)


def _page_from_ocr(page_no: int, width: float, height: float, ocr, scale: float, reason: str, quality) -> PageIR:
    words = [Word(w.text, w.x0 * scale, w.top * scale, w.x1 * scale, w.bottom * scale, w.conf) for w in ocr.words]
    blocks = build_blocks(words, page_height=float(height))
    text = blocks_to_text(blocks)
    return PageIR(page_no=page_no, width=float(width), height=float(height), route="ocr", route_reason=reason,
                  language=detect_language(text), ocr_engine=ocr.engine, ocr_confidence=round(ocr.mean_confidence, 4),
                  text_quality=quality, char_count=len(text), words=words, blocks=blocks, text=text,
                  markdown=blocks_to_markdown(blocks))


def _analyse(path: Path, settings: Settings) -> tuple[list[PageIR | None], list[_Pending]]:
    """CPU-bound part, run in a worker thread: text layers, rendering, local OCR, routing."""
    engine = get_engine(settings.ocr_backend, settings.ocr_languages)
    pages: list[PageIR | None] = []
    pending: list[_Pending] = []
    doc_lang_hint: str | None = None
    with open_pdf(path) as pdf:
        for index, page in enumerate(pdf.pages):
            page_no = index + 1
            layer = read_text_layer(page)
            decision = route_text_layer(layer.char_count, layer.quality, layer.image_coverage)
            if decision:
                ir = _page_from_text_layer(page_no, page.width, page.height, layer, decision.reason)
                if ir.language != "unknown":
                    doc_lang_hint = doc_lang_hint or ir.language
                pages.append(ir)
                continue
            base_reason = why_no_text_layer(layer.char_count, layer.quality, layer.image_coverage)
            image = render_page(path, index, settings.render_dpi)
            scale = float(page.width) / image.width
            ocr = engine.recognize(image) if engine else None
            ocr_lang = detect_language(ocr.text) if ocr and ocr.text else None
            tabular = bool(ocr and looks_tabular(group_lines(ocr.words)))
            plausible, why = text_plausibility(ocr.text) if ocr else (True, "")
            decision = route_scanned(
                base_reason=base_reason, ocr_engine=engine.name if engine else None,
                ocr_languages=engine.languages if engine else frozenset(), ocr_confidence=ocr.mean_confidence if ocr else None,
                ocr_language=ocr_lang, doc_language_hint=doc_lang_hint, tabular=tabular,
                min_confidence=settings.ocr_min_confidence, vlm_enabled=settings.vlm_enabled,
                implausible=None if plausible else why, unread_share=ocr.unread_share if ocr else 0.0)
            if ocr_lang in ("vi", "vi_noaccent"):
                doc_lang_hint = doc_lang_hint or "vi"
            if decision.route == "ocr" and ocr is not None:
                pages.append(_page_from_ocr(page_no, page.width, page.height, ocr, scale, decision.reason, layer.quality))
            elif decision.route == "vlm":
                placeholder = PageIR(page_no=page_no, width=float(page.width), height=float(page.height), route="vlm",
                                     route_reason=decision.reason, ocr_engine=engine.name if engine else None,
                                     ocr_confidence=round(ocr.mean_confidence, 4) if ocr else None,
                                     text_quality=layer.quality)
                pages.append(placeholder)
                pending.append(_Pending(index, placeholder, image_to_data_url(image), ocr.text if ocr else None))
            else:
                pages.append(PageIR(page_no=page_no, width=float(page.width), height=float(page.height), route="ocr",
                                    route_reason=decision.reason, text_quality=layer.quality))
    return pages, pending


async def parse_document(path: Path, sha256: str, llm: LLMClient, settings: Settings | None = None,
                         storage: Storage | None = None, use_cache: bool = True) -> DocumentIR:
    settings = settings or get_settings()
    storage = storage or Storage(settings)
    artifact = storage.artifact_dir(sha256, PARSER_VERSION) / "ir.json"
    if use_cache and (cached := storage.read_json(artifact)):
        return DocumentIR.from_json(cached)

    pages, pending = await asyncio.to_thread(_analyse, path, settings)
    sem = asyncio.Semaphore(settings.parse_concurrency)

    async def run_vlm(p: _Pending) -> None:
        async with sem:
            md = await transcribe_page(llm, p.image_url, p.ocr_text)
        blocks, struck = markdown_to_blocks(md)
        p.page.blocks = blocks
        p.page.text = blocks_to_text(blocks)
        p.page.markdown = blocks_to_markdown(blocks)
        p.page.struck = struck
        p.page.language = detect_language(p.page.text)
        p.page.char_count = len(p.page.text)
        p.page.has_table = any(b.kind == "table" for b in blocks)

    if pending:
        await asyncio.gather(*(run_vlm(p) for p in pending))

    final = [p for p in pages if p is not None]
    language = document_language([p.text for p in final])
    title = next((b.text for p in final[:1] for b in p.blocks if b.kind == "heading"), None)
    ir = DocumentIR(sha256=sha256, parser_version=PARSER_VERSION, page_count=len(final), language=language,
                    title=title[:200] if title else None, pages=final)
    storage.write_json(artifact, ir.to_json())
    return ir
