"""Geometry → structure: struck-through words, lines, blocks, tables, markdown.

Classical layout analysis is the right tool here: it is exact on born-digital PDFs,
deterministic, free, and produces the word boxes the UI needs to highlight
evidence. The model only sees its output.
"""
from __future__ import annotations

import re
from statistics import median

from .ir import Block, Word

HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s+\S|(?:ARTICLE|Article|SECTION|Section|SCHEDULE|Schedule|EXHIBIT|Exhibit|ANNEX|Annex|"
    r"AMENDMENT|Amendment|APPENDIX|Appendix|ĐIỀU|Điều|CHƯƠNG|Chương|PHỤ LỤC|Phụ lục)\b)")
SIGNATURE_RE = re.compile(
    r"(?i)in witness whereof|^\s*(?:by|name|title|date|signature)\s*:|^\s*đại diện bên|ký,? ghi rõ họ tên|"
    r"\(ký tên|^\s*ngày ký\s*:|/s/|\[signed\]|\[stamp\]")


def struck_word_indices(words: list[dict], segments: list[tuple[float, float, float]]) -> set[int]:
    """Words crossed by a horizontal stroke through their middle band are deleted text
    (redlines). Underlines sit near the bottom and table borders outside the box,
    so neither matches."""
    struck: set[int] = set()
    if not segments:
        return struck
    for i, w in enumerate(words):
        h = w["bottom"] - w["top"]
        if h <= 0:
            continue
        lo, hi = w["top"] + 0.3 * h, w["top"] + 0.75 * h
        width = w["x1"] - w["x0"]
        for x0, x1, y in segments:
            if lo <= y <= hi and width > 0:
                overlap = min(x1, w["x1"]) - max(x0, w["x0"])
                if overlap >= 0.6 * width:
                    struck.add(i)
                    break
    return struck


def group_lines(words: list[Word], min_overlap: float = 0.5) -> list[list[Word]]:
    lines: list[list[Word]] = []
    spans: list[tuple[float, float]] = []
    for w in sorted(words, key=lambda w: ((w.top + w.bottom) / 2, w.x0)):
        if lines:
            top, bottom = spans[-1]
            overlap = min(bottom, w.bottom) - max(top, w.top)
            if overlap >= min_overlap * min(bottom - top, w.bottom - w.top):
                lines[-1].append(w)
                spans[-1] = (min(top, w.top), max(bottom, w.bottom))
                continue
        lines.append([w])
        spans.append((w.top, w.bottom))
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def split_cells(line: list[Word], gap_chars: float = 2.5) -> list[str]:
    """Split a line on gaps wider than ~``gap_chars`` average characters."""
    char_w = sum(w.x1 - w.x0 for w in line) / max(1, sum(len(w.text) for w in line))
    cells, current = [], [line[0].text]
    for prev, w in zip(line, line[1:], strict=False):
        if w.x0 - prev.x1 > gap_chars * char_w:
            cells.append(" ".join(current))
            current = []
        current.append(w.text)
    cells.append(" ".join(current))
    return cells


def looks_tabular(lines: list[list[Word]]) -> bool:
    """Scanned pages: ≥3 consecutive lines that split into the same ≥3 cells."""
    run, last = 0, None
    for line in lines:
        n = len(split_cells(line)) if line else 0
        if n >= 3 and n == last:
            run += 1
            if run >= 2:
                return True
        else:
            run = 0
        last = n
    return False


def _line_text(line: list[Word]) -> str:
    return " ".join(w.text for w in line)


def _bbox(words: list[Word]) -> tuple[float, float, float, float]:
    return (min(w.x0 for w in words), min(w.top for w in words), max(w.x1 for w in words),
            max(w.bottom for w in words))


def build_blocks(words: list[Word], sizes: dict[int, float] | None = None, bold: set[int] | None = None,
                 tables: list[tuple[tuple[float, float, float, float], list[list[str | None]]]] | None = None,
                 median_size: float = 10.0, page_height: float | None = None) -> list[Block]:
    """Lines → paragraphs/headings/signature blocks, tables kept as rows, in reading order.
    Running headers/footers (short lines in the top/bottom margin) are dropped."""
    tables = tables or []
    in_table: set[int] = set()
    for (x0, top, x1, bottom), _rows in tables:
        for i, w in enumerate(words):
            cx, cy = (w.x0 + w.x1) / 2, (w.top + w.bottom) / 2
            if x0 - 1 <= cx <= x1 + 1 and top - 1 <= cy <= bottom + 1:
                in_table.add(i)
    index = {id(w): i for i, w in enumerate(words)}
    free = [w for i, w in enumerate(words) if i not in in_table]
    lines = group_lines(free)
    if page_height:
        lines = [ln for ln in lines if not _is_furniture(ln, page_height)]
    heights = [max(w.bottom for w in ln) - min(w.top for w in ln) for ln in lines] or [10.0]
    line_h = median(heights)

    items: list[tuple[float, Block]] = []
    para: list[list[Word]] = []
    sig: dict = {}          # the open signature block: its lines and position in items

    def flush() -> None:
        if not para:
            return
        flat = [w for ln in para for w in ln]
        top = min(w.top for w in flat)
        short = all(len(ln) <= 12 for ln in para)
        is_sig = any(SIGNATURE_RE.search(_line_text(ln)) for ln in para)
        # names, titles and dates under a signature line belong to the same block
        continues = bool(sig) and short and top - sig["bottom"] < 120
        if is_sig or continues:
            if continues:
                lines = sig["lines"] + [list(ln) for ln in para]
                index = sig["index"]
            else:
                lines, index = [list(ln) for ln in para], len(items)
                items.append((top, Block("signature", "", None)))
            all_words = [w for ln in lines for w in ln]
            items[index] = (items[index][0], Block("signature", signature_text(lines), _bbox(all_words)))
            sig.update(lines=lines, index=index, bottom=max(w.bottom for w in flat))
        else:
            items.append((top, Block("paragraph", " ".join(_line_text(ln) for ln in para), _bbox(flat))))
            sig.clear()
        para.clear()

    prev_bottom = None
    for ln in lines:
        text = _line_text(ln)
        top = min(w.top for w in ln)
        idx = [index[id(w)] for w in ln]
        size = median([sizes[i] for i in idx]) if sizes else median_size
        is_bold = bool(bold) and sum(i in bold for i in idx) >= 0.6 * len(idx)
        short = len(text.split()) <= 14
        in_signature = (bool(SIGNATURE_RE.search(text)) or (bool(sig) and short and top - sig["bottom"] < 120)
                        or (short and any(SIGNATURE_RE.search(_line_text(p)) for p in para)))
        heading = short and not in_signature and (
            size >= median_size * 1.15 or is_bold or bool(HEADING_RE.match(text)) or (text.isupper() and len(text) >= 4))
        gap = (top - prev_bottom) if prev_bottom is not None else 0.0
        if heading:
            flush()
            items.append((top, Block("heading", text, _bbox(ln))))
        else:
            if para and gap > 0.9 * line_h:
                flush()
            para.append(ln)
        prev_bottom = max(w.bottom for w in ln)
    flush()

    for bbox, rows in tables:
        clean = [[(c or "").replace("\n", " ").strip() for c in row] for row in rows]
        text = "\n".join(" | ".join(r) for r in clean)
        items.append((bbox[1], Block("table", text, tuple(bbox), clean)))
    items.sort(key=lambda t: t[0])
    return [b for _, b in items]


def _is_furniture(line: list[Word], page_height: float) -> bool:
    top, bottom = min(w.top for w in line), max(w.bottom for w in line)
    return len(line) <= 12 and (top > 0.93 * page_height or bottom < 0.05 * page_height)


def signature_text(lines: list[list[Word]]) -> str:
    """Signature blocks sit side by side (one column per party). Reading them row by row
    interleaves the parties ("Lê Hoàng Minh Võ Ngọc Lan"); split at the column gap and read
    each column top to bottom instead."""
    rows = "\n".join(_line_text(ln) for ln in lines)
    xs0 = min(w.x0 for ln in lines for w in ln)
    xs1 = max(w.x1 for ln in lines for w in ln)
    mid = (xs0 + xs1) / 2

    def spans(ln: list[Word]) -> bool:
        """A full-width line ("IN WITNESS WHEREOF, the parties…"): no wide gap at the middle."""
        lefts = [w for w in ln if w.x1 <= mid]
        rights = [w for w in ln if w.x0 >= mid]
        if any(w.x0 < mid < w.x1 for w in ln):
            return True
        return bool(lefts and rights) and min(w.x0 for w in rights) - max(w.x1 for w in lefts) < 25

    # two columns ⇔ on most lines with words on both sides, a wide empty gap spans the middle
    two_sided = [ln for ln in lines if any(w.x1 <= mid for w in ln) and any(w.x0 >= mid for w in ln)
                 or any(w.x0 < mid < w.x1 for w in ln)]
    gapped = [ln for ln in two_sided if not spans(ln)]
    if len(lines) < 3 or not two_sided or len(gapped) < 0.6 * len(two_sided):
        return rows
    head = []
    body = list(lines)
    while body and spans(body[0]):          # leading full-width lines stay whole, above the columns
        head.append(body.pop(0))
    left = [[w for w in ln if (w.x0 + w.x1) / 2 < mid] for ln in body]
    right = [[w for w in ln if (w.x0 + w.x1) / 2 >= mid] for ln in body]
    if sum(1 for ln in right if ln) < 2 or sum(1 for ln in left if ln) < 2:
        return rows
    col = lambda rs: "\n".join(_line_text(r) for r in rs if r)  # noqa: E731
    prefix = col(head) + "\n\n" if head else ""
    return prefix + col(left) + "\n\n" + col(right)


def blocks_to_markdown(blocks: list[Block]) -> str:
    out = []
    for b in blocks:
        if b.kind == "heading":
            out.append(f"## {b.text}")
        elif b.kind == "table" and b.rows:
            width = max(len(r) for r in b.rows)
            rows = [[c.replace("|", "\\|") for c in r] + [""] * (width - len(r)) for r in b.rows]
            out.append("| " + " | ".join(rows[0]) + " |")
            out.append("|" + "---|" * width)
            out.extend("| " + " | ".join(r) + " |" for r in rows[1:])
        else:
            out.append(b.text)
    return "\n\n".join(out)


def blocks_to_text(blocks: list[Block]) -> str:
    return "\n".join(b.text for b in blocks)


def markdown_to_blocks(md: str) -> tuple[list[Block], list[str]]:
    """Parse VLM markdown back into blocks; ``~~struck~~`` spans are removed and returned."""
    struck = re.findall(r"~~(.+?)~~", md, flags=re.S)
    md = re.sub(r"~~(.+?)~~", "", md, flags=re.S)
    blocks: list[Block] = []
    table: list[list[str]] = []
    para: list[str] = []

    def flush_para() -> None:
        if para:
            text = " ".join(s.strip() for s in para if s.strip())
            if text:
                kind = "signature" if SIGNATURE_RE.search(text) else "paragraph"
                blocks.append(Block(kind, text))
            para.clear()

    def flush_table() -> None:
        if table:
            blocks.append(Block("table", "\n".join(" | ".join(r) for r in table), None, [r[:] for r in table]))
            table.clear()

    for raw in md.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("|"):
            flush_para()
            if "-" in line and set(line.strip()) <= set("|-: "):
                continue  # the |---|---| separator row
            table.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        flush_table()
        if line.startswith("#"):
            flush_para()
            blocks.append(Block("heading", line.lstrip("#").strip()))
        elif not line.strip():
            flush_para()
        else:
            para.append(re.sub(r"[*_`]{1,3}", "", line))
    flush_para()
    flush_table()
    return blocks, [collapse for collapse in (re.sub(r"\s+", " ", s).strip() for s in struck) if collapse]
