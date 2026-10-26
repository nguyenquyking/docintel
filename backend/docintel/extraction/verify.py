"""Checks on what the model returned. The model reads; these checks decide how much to trust it.

* **Validation** (hard): impossible dates, pages outside the document, wrong types →
  sent back to the model with the error (repair loop).
* **Grounding**: the quote must exist on the cited page (fuzzy, tolerant of OCR
  noise and line breaks). Cheap and catches most hallucinations. If it exists on
  another page the citation is corrected and flagged.
* **Value-in-quote**: the value must be readable from its own quote (the date,
  number or amount appears in it).
* **Business rules** (soft): line items that don't add up, a term ending before it
  starts, an unsigned signature block. Flagged for review, never "repaired" —
  the document itself may be inconsistent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..parsing.ir import DocumentIR, PageIR
from ..text.lang import dayfirst_for
from ..text.normalize import (
    amounts_equal,
    find_amounts,
    find_dates,
    find_integers,
    norm_jurisdiction,
    normalize_for_match,
    parse_date,
    quote_score,
)
from .schema import FIELD_GROUP, LIST_FIELDS, UNCITED_FIELDS

GROUNDED_AT = 0.85


@dataclass
class Evidence:
    quote: str | None
    page: int | None
    grounded: bool | None = None
    score: float | None = None
    boxes: list[list[float]] = field(default_factory=list)
    page_corrected_from: int | None = None


@dataclass
class FieldResult:
    field: str
    group: str
    value: Any
    evidence: list[Evidence]
    meta: dict[str, Any] = field(default_factory=dict)
    grounded: bool | None = None
    value_in_quote: bool | None = None
    flags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    raw_confidence: float = 0.0
    tier: str = "fast"
    needs_review: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "field": self.field, "group": self.group, "value": self.value, "meta": self.meta,
            "evidence": [e.__dict__ for e in self.evidence], "grounded": self.grounded,
            "value_in_quote": self.value_in_quote, "flags": self.flags, "confidence": round(self.confidence, 4),
            "raw_confidence": round(self.raw_confidence, 4), "tier": self.tier, "needs_review": self.needs_review,
        }


# ── validation (hard errors → repair loop) ───────────────────────────────────

def validate_output(data: dict[str, Any], page_count: int) -> list[str]:
    errors: list[str] = []

    def check_date(path: str, v: Any) -> None:
        if v is None:
            return
        try:
            date.fromisoformat(v)
        except (TypeError, ValueError):
            errors.append(f"{path}: '{v}' is not a real calendar date (YYYY-MM-DD)")

    def check_page(path: str, p: Any) -> None:
        if p is not None and not (1 <= int(p) <= page_count):
            errors.append(f"{path}.page: {p} is outside the document (1..{page_count})")

    for name, v in data.items():
        if name in UNCITED_FIELDS:
            continue
        if name in LIST_FIELDS:
            for i, item in enumerate(v or []):
                check_page(f"{name}[{i}]", item.get("page"))
                if name == "signatories":
                    check_date(f"{name}[{i}].signed_date", item.get("signed_date"))
            continue
        if not isinstance(v, dict):
            continue
        check_page(name, v.get("page"))
        if name in ("effective_date", "expiration_date"):
            check_date(f"{name}.value", v.get("value"))
        if v.get("value") is not None and not v.get("quote"):
            errors.append(f"{name}: a value needs its verbatim quote")
    return errors


# ── grounding ────────────────────────────────────────────────────────────────

def _tokens(s: str) -> list[str]:
    return normalize_for_match(s).split()


def locate_boxes(quote: str, page: PageIR) -> list[list[float]]:
    """Word boxes covering the quote on the page, one rectangle per line (for highlighting)."""
    if not quote or not page.words:
        return []
    q = _tokens(quote)
    words = [normalize_for_match(w.text) for w in page.words]
    if not q:
        return []
    best, best_span = 0, None
    first = q[0]
    for i, w in enumerate(words):
        if w != first and not (len(first) > 3 and w.startswith(first[:4])):
            continue
        j, k, hits = i, 0, 0
        while j < len(words) and k < len(q) and j - i < len(q) + 8:
            if words[j] == q[k] or (len(q[k]) > 4 and words[j][:5] == q[k][:5]):
                hits += 1
                k += 1
            elif k + 1 < len(q) and words[j] == q[k + 1]:
                k += 2
                hits += 1
            j += 1
        if hits > best:
            best, best_span = hits, (i, j)
    if not best_span or best < max(1, int(0.6 * len(q))):
        return []
    rows: dict[int, list[float]] = {}
    for w in page.words[best_span[0]:best_span[1]]:
        key = int(round(w.top / 4))
        box = rows.setdefault(key, [w.x0, w.top, w.x1, w.bottom])
        box[0], box[1], box[2], box[3] = min(box[0], w.x0), min(box[1], w.top), max(box[2], w.x1), max(box[3], w.bottom)
    return [[round(v, 1) for v in b] for b in rows.values()]


def ground(quote: str | None, page_no: int | None, ir: DocumentIR) -> Evidence:
    ev = Evidence(quote, page_no)
    if not quote:
        return ev
    pages = {p.page_no: p for p in ir.pages}
    page = pages.get(page_no or -1)
    score = quote_score(quote, page.text) if page else 0.0
    if score < GROUNDED_AT:  # look elsewhere: a wrong page number is common and harmless once fixed
        best_page, best = None, score
        for p in ir.pages:
            if p.page_no == page_no:
                continue
            s = quote_score(quote, p.text)
            if s > best:
                best_page, best = p.page_no, s
        if best_page is not None and best >= max(GROUNDED_AT, score + 0.05):
            ev.page_corrected_from, ev.page, score, page = page_no, best_page, best, pages[best_page]
    ev.grounded, ev.score = score >= GROUNDED_AT, round(score, 3)
    if ev.grounded and page is not None:
        ev.boxes = locate_boxes(quote, page)
    return ev


# ── value ↔ quote consistency ────────────────────────────────────────────────

def value_in_quote(name: str, value: Any, quote: str | None, dayfirst: bool) -> bool | None:
    if value is None or not quote:
        return None
    if name in ("effective_date", "expiration_date"):
        found = find_dates(quote, dayfirst=dayfirst) or find_dates(quote, dayfirst=not dayfirst)
        if not found:
            return None
        return parse_date(value) in found
    if name in ("initial_term_months", "renewal_term_months", "termination_notice_days", "payment_terms_days"):
        ints = find_integers(quote)
        if not ints:
            return None
        v = int(value)
        return v in ints or (name.endswith("months") and v % 12 == 0 and v // 12 in ints)
    if name == "non_renewal_notice" and isinstance(value, dict):
        ints = find_integers(quote)
        return None if not ints else int(value.get("amount", -1)) in ints
    if name == "contract_value" and isinstance(value, dict):
        amounts = find_amounts(quote)
        if not amounts:
            return None
        return any(amounts_equal(a, value.get("amount")) for a in amounts)
    if name == "governing_law" and isinstance(value, str):
        target = norm_jurisdiction(value)
        q = normalize_for_match(quote)
        return target in q or target in norm_jurisdiction(q) or (target == "vietnam" and "việt nam" in q)
    return None


# ── assemble field results ───────────────────────────────────────────────────

def build_field_results(data: dict[str, Any], ir: DocumentIR, tiers: dict[str, str]) -> list[FieldResult]:
    dayfirst = dayfirst_for(ir.language)
    results: list[FieldResult] = []
    for name, raw in data.items():
        group = FIELD_GROUP.get(name, "other")
        tier = tiers.get(group, "fast")
        if name in UNCITED_FIELDS:
            results.append(FieldResult(name, group, raw, [], tier=tier))
            continue
        if name in LIST_FIELDS:
            items = raw or []
            # line items carry no quote: their description must appear on the cited page
            evidence = [ground(it.get("quote") if name != "line_items" else it.get("description"), it.get("page"), ir)
                        for it in items]
            value = [{k: v for k, v in it.items() if k not in ("quote", "page")} for it in items]
            cited = [e for e in evidence if e.quote]
            grounded = None if not cited else all(e.grounded for e in cited)
            results.append(FieldResult(name, group, value, evidence, grounded=grounded, tier=tier))
            continue
        raw = raw or {}
        value = raw.get("value")
        meta = {k: v for k, v in raw.items() if k not in ("value", "quote", "page")}
        ev = ground(raw.get("quote"), raw.get("page"), ir)
        fr = FieldResult(name, group, value, [ev], meta=meta, grounded=ev.grounded if value is not None else None,
                         tier=tier)
        if value is not None and not (name == "effective_date" and meta.get("basis") == "last_signature"):
            fr.value_in_quote = value_in_quote(name, value, ev.quote, dayfirst)
        if ev.page_corrected_from is not None:
            fr.flags.append(f"cited page {ev.page_corrected_from} corrected to {ev.page}")
        results.append(fr)
    return results


def business_rules(by_name: dict[str, FieldResult], facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Soft checks → warnings attached to the fields they concern."""
    warnings: list[dict[str, Any]] = []

    def warn(fields: list[str], code: str, message: str) -> None:
        warnings.append({"code": code, "fields": fields, "message": message})
        for f in fields:
            if f in by_name:
                by_name[f].flags.append(message)

    eff, end = facts.get("effective_date"), facts.get("initial_term_end")
    if eff and end and end < eff:
        warn(["expiration_date", "initial_term_months"], "term_before_start", "term ends before it starts")
    items = (by_name.get("line_items").value if by_name.get("line_items") else None) or []
    for i, li in enumerate(items):
        q, u, a = li.get("quantity"), li.get("unit_price"), li.get("amount")
        if None not in (q, u, a) and abs(q * u - a) > max(1.0, 0.01 * abs(a)):
            warn(["line_items"], "line_arithmetic", f"line {i + 1}: quantity × unit price ≠ amount")
    value = facts.get("contract_value")
    if value and items and all(li.get("amount") is not None for li in items):
        total = sum(li["amount"] for li in items)
        if not amounts_equal(total, value["amount"], rel=0.01):
            warn(["contract_value", "line_items"], "items_total",
                 f"line items sum to {total:,.2f} but the contract value is {value['amount']:,.2f}")
    sigs = by_name.get("signatories")
    if sigs and sigs.value and not all(s.get("is_signed") for s in sigs.value):
        warn(["signatories"], "unsigned", "a signature block is not signed — likely a draft")
    if facts.get("auto_renewal") and facts.get("non_renewal_notice_days") is None:
        warn(["non_renewal_notice"], "renewal_without_notice", "auto-renewal without a notice period")
    if facts.get("auto_renewal") and facts.get("renewal_term_months") is None:
        warn(["renewal_term_months"], "renewal_without_term", "auto-renewal without a renewal term")
    exp = by_name.get("expiration_type")
    if exp and exp.value == "evergreen" and facts.get("initial_term_end"):
        warn(["expiration_type", "expiration_date"], "evergreen_with_end", "evergreen term but an end date is stated")
    return warnings
