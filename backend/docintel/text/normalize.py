"""Normalization shared by verification, evaluation, the synthetic generator and search.

Raw strings are never compared: "15/01/2024" and "January 15, 2024" are the same
date, "1.234.567,89" and "1,234,567.89" the same amount, "Acme, Inc." and "ACME Inc"
the same party. Every rule here is a definition of "equal" that the evaluation
relies on, so each one is unit-tested.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher

# ── text ─────────────────────────────────────────────────────────────────────

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def normalize_for_match(s: str) -> str:
    """Canonical form for quote grounding: NFKC, typographic quotes/dashes unified,
    line-break hyphenation undone, whitespace collapsed, case folded."""
    s = nfkc(s).translate(_QUOTES)
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    s = s.replace("~~", "")
    return collapse_ws(s).casefold()


def strip_accents(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def partial_ratio(needle: str, hay: str) -> float:
    """Best similarity of ``needle`` against any same-length window of ``hay``."""
    if not needle or not hay:
        return 0.0
    if len(needle) >= len(hay):
        return similarity(needle, hay)
    best = 0.0
    for i, j, _ in SequenceMatcher(None, needle, hay, autojunk=False).get_matching_blocks():
        start = max(0, min(j - i, len(hay) - len(needle)))
        best = max(best, similarity(needle, hay[start:start + len(needle)]))
        if best == 1.0:
            break
    return best


def quote_score(quote: str, page_text: str) -> float:
    """How well a quoted span is supported by the page text. A quote that crosses a layout
    break (two columns, a table cell, a line the model re-flowed) is checked segment by
    segment: every segment must be on the page."""
    q, p = normalize_for_match(quote), normalize_for_match(page_text)
    if not q:
        return 0.0
    if q in p:
        return 1.0
    whole = partial_ratio(q, p)
    segments = [normalize_for_match(x) for x in re.split(r"\n+|\s*\|\s*", quote)]
    segments = [x for x in segments if len(x) >= 4]
    if len(segments) >= 2:
        per_segment = min(1.0 if x in p else partial_ratio(x, p) for x in segments)
        return max(whole, per_segment)
    return whole


# ── organizations, people, jurisdictions ─────────────────────────────────────

_ORG_NOISE = {
    "inc", "incorporated", "llc", "l l c", "ltd", "limited", "corp", "corporation", "co", "company", "plc",
    "gmbh", "pte", "pty", "jsc", "lp", "llp", "sa", "ag", "bv", "nv", "the",
}
_ORG_NOISE_VI = ["công ty", "trách nhiệm hữu hạn", "tnhh", "cổ phần", "cp", "một thành viên", "mtv"]


def norm_org(name: str) -> str:
    s = nfkc(name).casefold().translate(_QUOTES)
    s = re.sub(r"\(.*?\)", " ", s)                 # ("Customer"), ("Bên A")
    for w in _ORG_NOISE_VI:
        s = re.sub(rf"(?<!\w){re.escape(w)}(?!\w)", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(t for t in s.split() if t not in _ORG_NOISE)


def name_similarity(a: str, b: str) -> float:
    na, nb = norm_org(a), norm_org(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return max(similarity(na, nb), 0.97 if (na in nb or nb in na) and min(len(na), len(nb)) >= 6 else 0.0)


_JURISDICTION_ALIASES = {
    "vietnam": ["viet nam", "việt nam", "socialist republic of vietnam", "socialist republic of viet nam",
                "cộng hòa xã hội chủ nghĩa việt nam", "cộng hoà xã hội chủ nghĩa việt nam"],
    "england and wales": ["england & wales", "england"],
    "united states": ["usa", "u.s.", "united states of america"],
    "singapore": ["republic of singapore"],
}
def norm_jurisdiction(s: str) -> str:
    """'the laws of the State of New York' -> 'new york'; 'pháp luật nước CHXHCN Việt Nam' -> 'vietnam'."""
    s = collapse_ws(nfkc(s).casefold().translate(_QUOTES)).strip(" .,;")
    s = re.sub(r"^(?:the\s+)?laws?\s+of\s+", "", s)
    s = re.sub(r"^(?:the\s+)?(?:state|commonwealth|province)\s+of\s+", "", s)
    s = re.sub(r"^(?:pháp\s+luật|luật)\s+(?:của\s+)?(?:nước\s+)?", "", s)
    s = re.sub(r",\s*(?:usa|u\.s\.a\.?|united states(?: of america)?|canada|united kingdom|uk|australia|"
               r"(?:people's republic of )?china|india)$", "", s).strip(" .,;")
    for canon, aliases in _JURISDICTION_ALIASES.items():
        if s == canon or s in aliases:
            return canon
    return s


# ── numbers and money ────────────────────────────────────────────────────────

_SCALE = {
    "thousand": 1e3, "k": 1e3, "nghìn": 1e3, "ngàn": 1e3,
    "million": 1e6, "m": 1e6, "mm": 1e6, "triệu": 1e6,
    "billion": 1e9, "bn": 1e9, "tỷ": 1e9, "tỉ": 1e9,
}
_CURRENCY = [
    (re.compile(r"(?i)\bS\$|\bSGD\b"), "SGD"),
    (re.compile(r"(?i)\bUS\$|\bUSD\b|\$"), "USD"),
    (re.compile(r"(?i)€|\bEUR\b|\beuros?\b"), "EUR"),
    (re.compile(r"(?i)£|\bGBP\b"), "GBP"),
    (re.compile(r"(?i)₫|\bVN[DĐ]\b|\bđồng\b|(?<=\d)\s*đ\b"), "VND"),
    (re.compile(r"(?i)¥|\bJPY\b"), "JPY"),
]


def detect_currency(s: str) -> str | None:
    for pattern, code in _CURRENCY:
        if pattern.search(s):
            return code
    return None


def parse_number(s: str) -> float | None:
    """'1,234,567.89' | '1.234.567,89' | '1 234 567' | '12,50' | '5,000' -> float.
    A lone separator followed by exactly three digits is a thousands separator."""
    s = re.sub(r"[^\d.,\-]", "", s.replace(" ", ""))
    if not re.search(r"\d", s):
        return None
    neg = s.startswith("-")
    s = s.lstrip("-")
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        groups = s.split(sep)
        s = s.replace(sep, "") if (len(groups) > 2 or len(groups[-1]) == 3) else s.replace(sep, ".")
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if neg else value


_AMOUNT = re.compile(
    r"(?P<num>\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?)\s*"
    r"(?P<scale>thousand|million|billion|bn|mm|triệu|tỷ|tỉ|nghìn|ngàn)?\b", re.I)


def find_amounts(text: str) -> list[float]:
    out = []
    for m in _AMOUNT.finditer(text):
        n = parse_number(m.group("num"))
        if n is None:
            continue
        scale = (m.group("scale") or "").lower()
        out.append(n * _SCALE.get(scale, 1.0))
    return out


def parse_amount(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    amounts = find_amounts(str(value))
    return amounts[0] if amounts else None


def amounts_equal(a, b, rel: float = 0.005, abs_tol: float = 0.01) -> bool:
    pa, pb = parse_amount(a), parse_amount(b)
    if pa is None or pb is None:
        return False
    return abs(pa - pb) <= max(abs_tol, rel * abs(pb))


_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def find_integers(text: str) -> set[int]:
    """Integers written as digits or English words up to 999 ('sixty (60)', 'one hundred twenty')."""
    found = {int(m.group(0)) for m in re.finditer(r"(?<![\d.,])\d{1,4}(?![\d.,]\d)", text)}
    words = re.findall(r"[a-z]+", text.casefold())
    i = 0
    while i < len(words):
        if words[i] in _WORD_NUMBERS or words[i] == "hundred":
            total, cur = 0, 0
            while i < len(words) and (words[i] in _WORD_NUMBERS or words[i] in ("hundred", "and")):
                w = words[i]
                if w == "hundred":
                    cur = max(cur, 1) * 100
                elif w != "and":
                    cur += _WORD_NUMBERS[w]
                i += 1
            total += cur
            found.add(total)
        else:
            i += 1
    return found


# ── dates ────────────────────────────────────────────────────────────────────

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], 1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS["sept"] = 9

_DATE_PATTERNS = [
    ("iso", re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    ("vi", re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", re.I)),
    ("mdy_text", re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")),
    ("dmy_text", re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")),
    ("numeric", re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b")),
]


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str, dayfirst: bool = True) -> list[date]:
    """Every date in ``text``. Numeric dates are ambiguous ('03/04/2025'): the document's
    locale decides (dayfirst for Vietnamese/European documents, monthfirst for US ones);
    when only one reading is valid ('25/12/2024') it wins regardless."""
    out: list[date] = []
    for kind, pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            a, b, c = m.groups()
            d = None
            if kind == "iso":
                d = _mk(int(a), int(b), int(c))
            elif kind == "vi":
                d = _mk(int(c), int(b), int(a))
            elif kind == "mdy_text":
                mon = _MONTHS.get(a.casefold().rstrip("."))
                d = _mk(int(c), mon, int(b)) if mon else None
            elif kind == "dmy_text":
                mon = _MONTHS.get(b.casefold().rstrip("."))
                d = _mk(int(c), mon, int(a)) if mon else None
            elif kind == "numeric":
                x, y, year = int(a), int(b), int(c)
                first, second = (_mk(year, y, x), _mk(year, x, y)) if dayfirst else (_mk(year, x, y), _mk(year, y, x))
                d = first or second
            if d and d not in out:
                out.append(d)
    return out


def parse_date(value, dayfirst: bool = True) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    found = find_dates(str(value), dayfirst=dayfirst)
    return found[0] if found else None


# ── tokens ───────────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token count for budgeting before a call (real counts come from usage).
    Vietnamese costs more tokens per character than English in BPE vocabularies."""
    if not text:
        return 0
    letters = sum(c.isalpha() for c in text) or 1
    accented = sum(1 for c in text if ord(c) > 0x7F and c.isalpha())
    chars_per_token = 4.0 - 1.6 * min(1.0, accented / letters * 4)
    return int(len(text) / chars_per_token) + 1
