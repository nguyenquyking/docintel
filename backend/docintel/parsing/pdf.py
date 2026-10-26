"""PDF access: text layer with word boxes (pdfplumber), page rendering (pdfium).

Both libraries are permissively licensed (MIT / Apache-2.0 + BSD-3). PDFium is not
thread-safe, so every pdfium call goes through one lock.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

_PDFIUM_LOCK = threading.Lock()


class FatalParseError(Exception):
    """The file can never be parsed (encrypted, corrupt, not a PDF): don't retry."""


@dataclass
class TextLayer:
    words: list[dict]
    char_count: int
    quality: float
    image_coverage: float
    h_segments: list[tuple[float, float, float]]          # (x0, x1, y) of thin horizontal strokes
    tables: list[tuple[tuple[float, float, float, float], list[list[str | None]]]] = field(default_factory=list)
    median_size: float = 10.0
    marks: list[tuple[float, float, float, float, str]] = field(default_factory=list)   # (x0, top, x1, bottom, kind)


def open_pdf(path: Path) -> pdfplumber.PDF:
    try:
        pdf = pdfplumber.open(path)
        _ = len(pdf.pages)
        return pdf
    except Exception as exc:  # pdfminer raises a zoo of types for encrypted/corrupt files
        name = type(exc).__name__
        if "Password" in name or "encrypt" in str(exc).lower():
            raise FatalParseError("encrypted PDF (password required)") from exc
        raise FatalParseError(f"unreadable PDF: {name}: {exc}") from exc


_CID = re.compile(r"\(cid:\d+\)")


def text_quality(text: str) -> float:
    """1.0 = clean text layer; low = garbage glyph mapping (CID fonts, private-use chars)."""
    if not text:
        return 0.0
    bad = len(_CID.findall(text)) * 6
    bad += sum(1 for c in text if c == "�" or 0xE000 <= ord(c) <= 0xF8FF
               or (ord(c) < 32 and c not in "\n\t\r"))
    return max(0.0, 1.0 - bad / len(text))


def read_text_layer(page: pdfplumber.page.Page) -> TextLayer:
    words = page.extract_words(x_tolerance=1.5, y_tolerance=3, keep_blank_chars=False,
                               use_text_flow=False, extra_attrs=["size", "fontname"])
    chars = page.chars
    raw = "".join(c.get("text", "") for c in chars)
    area = float(page.width * page.height) or 1.0
    covered = 0.0
    for im in page.images:
        w = max(0.0, min(im["x1"], page.width) - max(im["x0"], 0))
        h = max(0.0, min(im["bottom"], page.height) - max(im["top"], 0))
        covered += w * h
    segments: list[tuple[float, float, float]] = []
    for ln in page.lines:
        if abs(ln["top"] - ln["bottom"]) < 1.0 and ln["x1"] - ln["x0"] > 3:
            segments.append((ln["x0"], ln["x1"], (ln["top"] + ln["bottom"]) / 2))
    for r in page.rects:
        if r["bottom"] - r["top"] < 1.6 and r["x1"] - r["x0"] > 3:
            segments.append((r["x0"], r["x1"], (r["top"] + r["bottom"]) / 2))
    tables = []
    try:
        for t in page.find_tables({"vertical_strategy": "lines", "horizontal_strategy": "lines"}):
            rows = t.extract()
            if rows and len(rows) >= 2 and max(len(r) for r in rows) >= 2:
                tables.append((tuple(t.bbox), rows))
    except Exception:  # table finder is best-effort
        tables = []
    sizes = sorted(w.get("size", 10.0) for w in words) or [10.0]
    return TextLayer(words, len(chars), text_quality(raw), min(1.0, covered / area), segments, tables,
                     sizes[len(sizes) // 2], _marks(page))


def _is_red(color) -> bool:
    if not isinstance(color, (list, tuple)) or len(color) != 3:
        return False
    r, g, b = (float(c) for c in color)
    return r > 0.6 and g < 0.4 and b < 0.4


def _marks(page) -> list[tuple[float, float, float, float, str]]:
    """Drawn things that can mean "signed": vector curves (pen strokes, seals) and images,
    clustered so one signature is one mark. Red marks are seals (common on Vietnamese contracts)."""
    raw = []
    for c in page.curves:
        if (c["x1"] - c["x0"]) > 8 and (c["bottom"] - c["top"]) > 3:
            raw.append([c["x0"], c["top"], c["x1"], c["bottom"], "stamp" if _is_red(c.get("stroking_color")) else "signed"])
    for im in page.images:
        if 20 < (im["x1"] - im["x0"]) < page.width * 0.6 and (im["bottom"] - im["top"]) < 120:
            raw.append([im["x0"], im["top"], im["x1"], im["bottom"], "signed"])
    merged: list[list] = []
    for m in sorted(raw, key=lambda m: (m[4], m[1], m[0])):
        for g in merged:
            if g[4] == m[4] and m[0] <= g[2] + 5 and m[2] >= g[0] - 5 and m[1] <= g[3] + 5 and m[3] >= g[1] - 5:
                g[0], g[1], g[2], g[3] = min(g[0], m[0]), min(g[1], m[1]), max(g[2], m[2]), max(g[3], m[3])
                break
        else:
            merged.append(list(m))
    return [tuple(g) for g in merged]


def render_page(path: Path, index: int, dpi: int = 170) -> Image.Image:
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            page = doc[index]
            image = page.render(scale=dpi / 72).to_pil().convert("RGB")
            page.close()
        finally:
            doc.close()
    return image


def page_count(path: Path) -> int:
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            return len(doc)
        finally:
            doc.close()
