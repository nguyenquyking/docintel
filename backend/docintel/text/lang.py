"""Language identification for the languages the pipeline routes on (en / vi).

The interesting case is ``vi_noaccent``: Vietnamese text whose diacritics were lost
— what an OCR model without a Vietnamese character set returns, with high
confidence. Detecting it is how the router knows that a confident OCR result is
still unusable and sends the page to the VLM instead.
"""
from __future__ import annotations

import re

VI_CHARS = set("ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ")
VI_ASCII_WORDS = {
    "hop", "dong", "cong", "ty", "ben", "va", "cua", "cac", "duoc", "nay", "theo", "trong", "khong", "nhung",
    "ngay", "thang", "nam", "hieu", "luc", "dieu", "khoan", "thanh", "toan", "gia", "tri", "bang", "viec",
    "nguoi", "voi", "cho", "mot", "nhu", "phai", "thoi", "han", "chiu", "trach", "nhiem", "dich", "vu",
    "phap", "luat", "thong", "bao", "truoc", "ky", "tai", "dai", "dien", "giua", "cung", "cap",
}
EN_WORDS = {
    "the", "and", "of", "to", "in", "shall", "this", "agreement", "party", "parties", "be", "by", "for", "or",
    "any", "with", "such", "as", "on", "that", "is", "will", "not", "under", "each", "its", "all", "from",
}
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_language(text: str) -> str:
    """'en' | 'vi' | 'vi_noaccent' | 'unknown' for one page or block of text."""
    letters = [c for c in text.casefold() if c.isalpha()]
    if len(letters) < 20:
        return "unknown"
    vi_ratio = sum(c in VI_CHARS for c in letters) / len(letters)
    words = _WORD.findall(text.casefold())
    if not words:
        return "unknown"
    en_hits = sum(w in EN_WORDS for w in words) / len(words)
    vi_ascii_hits = sum(w in VI_ASCII_WORDS for w in words) / len(words)
    if vi_ratio >= 0.04:
        return "vi"
    if vi_ascii_hits >= 0.12 and vi_ascii_hits > en_hits:
        return "vi_noaccent"
    if en_hits >= 0.04:
        return "en"
    return "unknown"


def line_language_mix(text: str) -> dict[str, float]:
    """Share of characters per language, measured line by line (bilingual layouts)."""
    totals: dict[str, int] = {}
    for line in re.split(r"\n| \| ", text):   # table cells are separate segments (two-column layouts)
        lang = detect_language(line) if len(line) >= 25 else None
        if lang in ("en", "vi", "vi_noaccent"):
            key = "vi" if lang.startswith("vi") else lang
            totals[key] = totals.get(key, 0) + len(line)
    total = sum(totals.values()) or 1
    return {k: v / total for k, v in totals.items()}


def document_language(page_texts: list[str]) -> str:
    """'en' | 'vi' | 'bilingual' | 'unknown', weighted by characters across pages."""
    weights: dict[str, float] = {}
    for text in page_texts:
        for lang, share in line_language_mix(text).items():
            weights[lang] = weights.get(lang, 0.0) + share * len(text)
    total = sum(weights.values())
    if not total:
        return "unknown"
    en, vi = weights.get("en", 0.0) / total, weights.get("vi", 0.0) / total
    if en >= 0.25 and vi >= 0.25:
        return "bilingual"
    return "vi" if vi > en else "en"


def dayfirst_for(language: str | None) -> bool:
    """Numeric dates: day/month/year everywhere except US-style English documents.
    English contracts in this corpus are treated as month-first unless they are bilingual."""
    return language not in ("en",)
