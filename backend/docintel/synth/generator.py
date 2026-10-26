"""Synthetic contracts with exact ground truth — the controllable half of the golden set.

A golden set needs labels you can defend, slices you control (language × length ×
scanned × hard case) and documents you are allowed to publish. Real contracts
(``datasets/cuad.py``) bring realism; these bring coverage of the hard cases on
demand, each with the page its evidence sits on:

* key terms buried deep in 60–130-page agreements, behind distractors (a liability
  cap that is not the price, an insurance "renewal" that is not auto-renewal, a
  schedule with its own effective date);
* amendments that override the original term, notice period or fee;
* redlines: a struck-through notice period still present in the text layer;
* drafts with empty signature blocks; effective dates defined as "the date of the
  last signature"; notice periods in months; evergreen terms;
* Vietnamese and bilingual (English | Vietnamese, two columns) agreements;
* scanned copies (rotation, blur, noise, JPEG) with a red company seal.

Labels are computed here with independent date arithmetic — never with the
pipeline's own code, or a bug there would be graded as correct.
"""
from __future__ import annotations

import calendar
import io
import json
import math
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import content as C
from .fonts import register_fonts

# ── spec ─────────────────────────────────────────────────────────────────────

DOC_TYPES = {
    "en": ["services_agreement", "services_agreement", "nda", "lease", "purchase_agreement", "sow"],
    "vi": ["services_agreement", "services_agreement", "lease", "purchase_agreement", "nda"],
    "bilingual": ["services_agreement", "lease", "purchase_agreement"],
}
TITLES = {
    "services_agreement": ("MASTER SERVICES AGREEMENT", "HỢP ĐỒNG DỊCH VỤ"),
    "nda": ("MUTUAL NON-DISCLOSURE AGREEMENT", "THỎA THUẬN BẢO MẬT THÔNG TIN"),
    "lease": ("OFFICE LEASE AGREEMENT", "HỢP ĐỒNG THUÊ VĂN PHÒNG"),
    "purchase_agreement": ("PURCHASE AGREEMENT", "HỢP ĐỒNG MUA BÁN HÀNG HÓA"),
    "sow": ("STATEMENT OF WORK", "BẢN MÔ TẢ CÔNG VIỆC"),
}
ROLES = {
    "services_agreement": (("Customer", "customer"), ("Provider", "vendor"), ("Bên A", "Bên B")),
    "nda": (("Discloser", "discloser"), ("Recipient", "recipient"), ("Bên A", "Bên B")),
    "lease": (("Tenant", "tenant"), ("Landlord", "landlord"), ("Bên thuê", "Bên cho thuê")),
    "purchase_agreement": (("Buyer", "buyer"), ("Seller", "seller"), ("Bên mua", "Bên bán")),
    "sow": (("Customer", "customer"), ("Provider", "vendor"), ("Bên A", "Bên B")),
}


@dataclass
class Spec:
    idx: int
    seed: int
    language: str
    doc_type: str
    length: str
    scanned: bool
    tags: list[str]
    party_a: str
    party_b: str
    signers: list[tuple[str, str]]
    effective: date
    effective_rule: str                # stated | last_signature
    signature_dates: list[date]
    expiration_type: str               # duration | fixed_date | evergreen | on_completion
    term_months: int | None
    fixed_end: date | None
    auto_renewal: bool
    renewal_months: int | None
    notice_amount: int | None
    notice_unit: str                   # days | months
    old_notice_amount: int | None      # redline: struck-through value
    tfc: bool
    tfc_days: int | None
    value_amount: float | None
    currency: str | None
    value_period: str | None
    payment_days: int | None
    law_text: str
    law_norm: str
    line_items: list[dict] = field(default_factory=list)
    amendment: dict | None = None
    variant: int = 0


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _pick_quota(rng: random.Random, n: int, weights: dict[str, float]) -> list[str]:
    out: list[str] = []
    for k, w in weights.items():
        out += [k] * round(n * w)
    while len(out) < n:
        out.append(max(weights, key=weights.get))
    out = out[:n]
    rng.shuffle(out)
    return out


def has_renewal_clause(spec: Spec) -> bool:
    return spec.expiration_type in ("duration", "fixed_date") and spec.doc_type not in ("purchase_agreement", "sow")


def sample_specs(n: int, seed: int = 7, scanned_rate: float = 0.25) -> list[Spec]:
    rng = random.Random(seed)
    langs = _pick_quota(rng, n, {"en": 0.5, "vi": 0.3, "bilingual": 0.2})
    lengths = _pick_quota(rng, n, {"short": 0.5, "medium": 0.33, "long": 0.17})
    specs = []
    for i in range(n):
        r = random.Random(seed * 1000 + i)
        lang, length = langs[i], lengths[i]
        doc_type = r.choice(DOC_TYPES[lang])
        scanned = length != "long" and r.random() < scanned_rate
        tags = []
        for tag, p in [("amendment", 0.14), ("redline", 0.12), ("draft", 0.12), ("last_signature", 0.12),
                       ("notice_months", 0.12)]:
            if r.random() < p:
                tags.append(tag)
        if length != "short":
            tags.append("distractors")
        specs.append(_make_spec(i, seed * 1000 + i, lang, doc_type, length, scanned, tags, r))
    return specs


def _make_spec(idx: int, seed: int, lang: str, doc_type: str, length: str, scanned: bool, tags: list[str],
               r: random.Random) -> Spec:
    vi_side = lang in ("vi", "bilingual")
    companies = C.VI_COMPANIES if vi_side else C.EN_COMPANIES
    a, b = r.sample(companies, 2)
    people = C.VI_PEOPLE if vi_side else C.EN_PEOPLE
    signers = r.sample(people, 2)
    effective = date(r.randint(2023, 2026), r.randint(1, 12), r.randint(1, 28))

    if doc_type in ("purchase_agreement", "sow"):
        expiration_type = r.choice(["on_completion", "fixed_date"]) if doc_type == "purchase_agreement" else "fixed_date"
    elif doc_type == "nda":
        expiration_type = r.choice(["duration", "duration", "evergreen"])
    else:
        expiration_type = r.choice(["duration", "duration", "duration", "fixed_date", "evergreen"])
    term_months = r.choice([12, 24, 36]) if expiration_type == "duration" else None
    fixed_end = add_months(effective, r.choice([6, 12, 18, 24])) - timedelta(days=1) if expiration_type == "fixed_date" else None

    renewable = expiration_type in ("duration", "fixed_date") and doc_type not in ("purchase_agreement", "sow")
    auto_renewal = renewable and r.random() < 0.7
    renewal_months = r.choice([12, 12, 24]) if auto_renewal else None
    notice_unit = "months" if auto_renewal and "notice_months" in tags else "days"
    notice_amount = (r.choice([1, 2, 3]) if notice_unit == "months" else r.choice([30, 45, 60, 90])) if auto_renewal else None
    if not auto_renewal:
        tags = [t for t in tags if t not in ("notice_months", "redline")]
    old_notice = None
    if "redline" in tags and notice_amount and notice_unit == "days":
        old_notice = r.choice([x for x in (30, 45, 60, 90) if x != notice_amount])
    elif "redline" in tags:
        tags.remove("redline")

    tfc = r.random() < 0.55
    tfc_days = r.choice([30, 60, 90]) if tfc else None

    currency = "VND" if lang == "vi" or (lang == "bilingual" and r.random() < 0.6) else r.choice(["USD", "USD", "EUR", "SGD", "GBP"])
    line_items: list[dict] = []
    value_amount: float | None = None
    period: str | None = None
    payment_days: int | None = r.choice([15, 30, 45, 60])
    if doc_type == "nda":
        payment_days = None
    elif doc_type == "lease":
        value_amount = float(r.choice([45, 60, 85, 120]) * (1_000_000 if currency == "VND" else 100))
        period = "monthly"
        payment_days = None
    elif doc_type in ("purchase_agreement", "sow"):
        pool = C.LINE_ITEM_POOL_VI if currency == "VND" else C.LINE_ITEM_POOL_EN
        for desc, qty, price in r.sample(pool, r.randint(2, 5)):
            unit = float(price) if currency == "VND" else float(price)
            line_items.append({"description": desc, "quantity": qty, "unit_price": unit, "amount": qty * unit})
        value_amount = float(sum(li["amount"] for li in line_items))
        period = "total"
    else:
        if r.random() < 0.35:
            pool = C.LINE_ITEM_POOL_VI if currency == "VND" else C.LINE_ITEM_POOL_EN
            for desc, qty, price in r.sample(pool, r.randint(2, 4)):
                line_items.append({"description": desc, "quantity": qty, "unit_price": float(price),
                                   "amount": float(qty * price)})
            value_amount = float(sum(li["amount"] for li in line_items))
            period = "annual"
        else:
            value_amount = float(r.choice([24, 36, 48, 60, 96, 120, 180]) * (25_000_000 if currency == "VND" else 1000))
            period = r.choice(["annual", "annual", "monthly"])
            if period == "monthly":
                value_amount = round(value_amount / 12, -3 if currency != "VND" else -6) or value_amount

    if lang == "en":
        law_text, law_norm = r.choice(C.EN_LAWS)
    else:
        law_text, law_norm = "Vietnam", "vietnam"

    effective_rule = "last_signature" if "last_signature" in tags else "stated"
    if effective_rule == "last_signature":
        first = effective - timedelta(days=r.randint(1, 9))
        sig_dates = [first, effective] if r.random() < 0.5 else [effective, first]
    else:
        sig_dates = [effective - timedelta(days=r.randint(0, 5)), effective - timedelta(days=r.randint(0, 5))]

    amendment = None
    if "amendment" in tags:
        options = []
        if term_months:
            options.append("term")
        if notice_amount and notice_unit == "days":
            options.append("notice")
        if value_amount and period in ("annual", "monthly") and not line_items:
            options.append("fee")
        if options:
            kind = r.choice(options)
            amendment = {"kind": kind, "date": add_months(effective, r.randint(3, 9))}
            if kind == "term":
                amendment["value"] = term_months + r.choice([12, 24])
            elif kind == "notice":
                amendment["value"] = r.choice([x for x in (30, 60, 90, 120) if x != notice_amount])
            else:
                amendment["value"] = float(round(value_amount * r.choice([1.1, 1.15, 1.2]), -3 if currency != "VND" else -6))
        else:
            tags.remove("amendment")

    return Spec(idx, seed, lang, doc_type, length, scanned, tags, a, b, signers, effective, effective_rule, sig_dates,
                expiration_type, term_months, fixed_end, auto_renewal, renewal_months, notice_amount, notice_unit,
                old_notice, tfc, tfc_days, value_amount, currency, period, payment_days, law_text, law_norm,
                line_items, amendment, r.randint(0, 9))


def find_spec(language: str, doc_type: str, length: str = "short", scanned: bool = False,
              tags: tuple[str, ...] = (), require=None, start_seed: int = 1, tries: int = 400) -> Spec:
    """A spec with exactly these properties (tests and demos): tries seeds until the requested
    tags survive the consistency rules (e.g. a redline needs an auto-renewal clause)."""
    for k in range(tries):
        seed = start_seed * 7919 + k
        r = random.Random(seed)
        spec = _make_spec(0, seed, language, doc_type, length, scanned, list(tags), r)
        if all(t in spec.tags for t in tags) and (require is None or require(spec)):
            return spec
    raise ValueError(f"no spec found for {language}/{doc_type}/{tags}")


# ── labels (independent arithmetic) ──────────────────────────────────────────

def labels_for(spec: Spec) -> dict:
    term_months = spec.term_months
    notice = spec.notice_amount
    value = spec.value_amount
    if spec.amendment:
        if spec.amendment["kind"] == "term":
            term_months = spec.amendment["value"]
        elif spec.amendment["kind"] == "notice":
            notice = spec.amendment["value"]
        else:
            value = spec.amendment["value"]
    executed = "draft" not in spec.tags
    if spec.effective_rule == "last_signature":
        effective = max(spec.signature_dates) if executed else None
    else:
        effective = spec.effective
    if spec.expiration_type == "duration" and effective and term_months:
        end = add_months(effective, term_months) - timedelta(days=1)
    elif spec.expiration_type == "fixed_date":
        end = spec.fixed_end
    else:
        end = None
    notice_days = None
    deadline = None
    if spec.auto_renewal and notice:
        notice_days = notice * 30 if spec.notice_unit == "months" else notice
        if end:
            deadline = add_months(end, -notice) if spec.notice_unit == "months" else end - timedelta(days=notice)
    exp_type = {"duration": "duration", "fixed_date": "fixed_date", "evergreen": "evergreen",
                "on_completion": "on_completion"}[spec.expiration_type]
    return {
        "document_type": spec.doc_type,
        "parties": [spec.party_a, spec.party_b],
        "signatories": [s[0] for s in spec.signers],
        "is_executed": executed,
        "effective_date": effective.isoformat() if effective else None,
        "expiration_type": exp_type,
        "initial_term_end": end.isoformat() if end else None,
        "auto_renewal": spec.auto_renewal if has_renewal_clause(spec) else None,   # silent document → null
        "renewal_term_months": spec.renewal_months,
        "non_renewal_notice_days": notice_days,
        "notice_deadline": deadline.isoformat() if deadline else None,
        "termination_for_convenience": spec.tfc,
        "contract_value": {"amount": value, "currency": spec.currency} if value is not None else None,
        "value_period": spec.value_period if value is not None else None,
        "payment_terms_days": spec.payment_days,
        "governing_law": spec.law_norm,
        "line_items": [dict(li) for li in spec.line_items],
    }


# ── formatting helpers ───────────────────────────────────────────────────────

def en_date(d: date, v: int) -> str:
    return [f"{C.EN_MONTHS[d.month - 1]} {d.day}, {d.year}", f"{d.day} {C.EN_MONTHS[d.month - 1]} {d.year}",
            f"the {C.ordinal(d.day)} day of {C.EN_MONTHS[d.month - 1]}, {d.year}"][v % 3]


def vi_date(d: date, v: int) -> str:
    return f"ngày {d.day:02d} tháng {d.month:02d} năm {d.year}" if v % 2 == 0 else f"{d.day:02d}/{d.month:02d}/{d.year}"


def money_en(amount: float, cur: str, v: int) -> str:
    if cur == "VND":
        return f"VND {amount:,.0f}"
    symbol = {"USD": "$", "EUR": "€", "GBP": "£", "SGD": "S$"}[cur]
    return f"{cur} {amount:,.2f}" if v % 2 == 0 else f"{symbol}{amount:,.2f}"


def money_vi(amount: float, cur: str, v: int) -> str:
    if cur == "VND":
        s = f"{amount:,.0f}".replace(",", ".")
        return f"{s} VNĐ" if v % 2 == 0 else f"{s} đồng"
    s = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {cur}"


def en_num(n: int) -> str:
    return f"{C.en_words(n)} ({n})"


def vi_num(n: int) -> str:
    return f"{n} ({C.vi_words(n)})"


# ── clauses: (english, vietnamese), with an evidence snippet ─────────────────

class Doc:
    """Builds the logical document; renders to PDF; records evidence snippets."""

    def __init__(self, spec: Spec):
        self.spec = spec
        self.rng = random.Random(spec.seed + 99)
        self.elements: list[tuple] = []
        self.evidence: dict[str, str] = {}
        self.section_no = 0
        self.term_section = "12"

    # element helpers
    def heading(self, en: str, vi: str) -> None:
        self.section_no += 1
        n = self.section_no
        self.elements.append(("heading", f"{n}. {en}", f"Điều {n}. {vi}"))

    def para(self, en: str, vi: str, evidence: list[str] | None = None, snippet_lang: str | None = None) -> None:
        self.elements.append(("para", en, vi))
        for f in evidence or []:
            self.evidence[f] = self._snippet(en, vi, snippet_lang)

    def _snippet(self, en: str, vi: str, lang: str | None = None) -> str:
        lang = lang or ("vi" if self.spec.language == "vi" else "en")
        text = re.sub(r"<strike>.*?</strike>", " ", vi if lang == "vi" else en)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        words = text.split()
        return " ".join(words[:9])

    def filler(self, topic_en: str, topic_vi: str, paragraphs: int) -> None:
        en_pool = C.EN_FILLER.get(topic_en) or C.EN_FILLER["Miscellaneous"]
        vi_pool = C.VI_FILLER.get(topic_vi) or C.VI_FILLER["Điều khoản chung"]
        roles = ROLES[self.spec.doc_type]
        for k in range(paragraphs):
            en = " ".join(self.rng.choice(en_pool) for _ in range(self.rng.randint(2, 4)))
            vi = " ".join(self.rng.choice(vi_pool) for _ in range(self.rng.randint(2, 3)))
            en = en.replace("{provider}", f"the {roles[1][0]}").replace("{customer}", f"the {roles[0][0]}")
            en = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), en)
            vi = vi.replace("{provider}", roles[2][1]).replace("{customer}", roles[2][0])
            self.elements.append(("para", f"{self.section_no}.{k + 1} {escape(en)}", f"{self.section_no}.{k + 1} {escape(vi)}"))

    # clauses
    def preamble(self) -> None:
        s, v = self.spec, self.spec.variant
        ra, rb, rvi = ROLES[s.doc_type]
        a, b = escape(s.party_a), escape(s.party_b)
        title_en, title_vi = TITLES[s.doc_type]
        if s.effective_rule == "last_signature":
            en = (f'This {title_en.title()} (the "Agreement") is made by and between {a} ("{ra[0]}") and {b} '
                  f'("{rb[0]}"), and becomes effective on the date of the last signature below (the "Effective Date").')
            vi = (f"Hợp đồng này được ký kết giữa {a} (\"{rvi[0]}\") và {b} (\"{rvi[1]}\") và có hiệu lực kể từ "
                  "ngày Bên ký sau cùng ký vào Hợp đồng (\"Ngày hiệu lực\").")
        else:
            en = (f'This {title_en.title()} (the "Agreement") is entered into as of {en_date(s.effective, v)} '
                  f'(the "Effective Date") by and between {a} ("{ra[0]}") and {b} ("{rb[0]}").')
            vi = (f"Hợp đồng này được ký kết {vi_date(s.effective, v)} (\"Ngày hiệu lực\") giữa {a} "
                  f"(\"{rvi[0]}\") và {b} (\"{rvi[1]}\").")
        self.para(en, vi, ["parties", "effective_date"])
        if s.language != "en":
            addr = self.rng.sample(C.VI_ADDRESSES, 2)
            self.para(f"{ra[0]}: {a}, registered office at {escape(addr[0])}.", f"{rvi[0]}: {a}; Địa chỉ: {escape(addr[0])}.")
            self.para(f"{rb[0]}: {b}, registered office at {escape(addr[1])}.", f"{rvi[1]}: {b}; Địa chỉ: {escape(addr[1])}.")

    def term_clause(self) -> None:
        s, v = self.spec, self.spec.variant
        if s.expiration_type == "duration":
            m = s.term_months
            if v % 2 == 0 or m % 12:
                en = (f"The initial term of this Agreement commences on the Effective Date and continues for "
                      f"{en_num(m)} months (the \"Initial Term\").")
                vi = f"Thời hạn của Hợp đồng là {vi_num(m)} tháng kể từ Ngày hiệu lực (\"Thời hạn ban đầu\")."
            else:
                y = m // 12
                en = (f"This Agreement shall remain in force for a period of {en_num(y)} year{'s' if y > 1 else ''} "
                      "from the Effective Date (the \"Initial Term\").")
                vi = f"Hợp đồng có thời hạn {vi_num(y)} năm kể từ Ngày hiệu lực (\"Thời hạn ban đầu\")."
        elif s.expiration_type == "fixed_date":
            en = (f"This Agreement shall remain in effect until {en_date(s.fixed_end, v)}, unless terminated earlier "
                  "in accordance with this Section.")
            vi = f"Hợp đồng có hiệu lực đến hết {vi_date(s.fixed_end, v)}, trừ trường hợp chấm dứt trước thời hạn."
        elif s.expiration_type == "evergreen":
            en = ("This Agreement shall continue in full force and effect until terminated by either party in "
                  f"accordance with Section {self.term_section}.")
            vi = (f"Hợp đồng có hiệu lực không xác định thời hạn cho đến khi được chấm dứt theo quy định tại Điều "
                  f"{self.term_section}.")
        else:
            en = "This Agreement shall remain in effect until both parties have fully performed their obligations hereunder."
            vi = "Hợp đồng tự động thanh lý khi hai Bên đã hoàn thành đầy đủ các nghĩa vụ theo Hợp đồng."
        self.para(f"{self.section_no}.1 {en}", f"{self.section_no}.1 {vi}", ["initial_term_end", "expiration_type"])

    def renewal_clause(self) -> None:
        s, v = self.spec, self.spec.variant
        if not has_renewal_clause(s):
            return
        if s.auto_renewal:
            n = s.notice_amount
            unit_en, unit_vi = ("months", "tháng") if s.notice_unit == "months" else ("days", "ngày")
            old_en = f"<strike>{en_num(s.old_notice_amount)} days</strike> " if s.old_notice_amount else ""
            old_vi = f"<strike>{vi_num(s.old_notice_amount)} ngày</strike> " if s.old_notice_amount else ""
            r = s.renewal_months
            if v % 2 == 0 or r % 12:
                en = (f"Thereafter, this Agreement shall automatically renew for successive {en_num(r)}-month renewal "
                      f"terms (each, a \"Renewal Term\") unless either party gives the other written notice of "
                      f"non-renewal at least {old_en}{en_num(n)} {unit_en} prior to the end of the then-current term.")
            else:
                en = (f"Upon expiry of the Initial Term, this Agreement will be renewed automatically for further "
                      f"periods of {en_num(r // 12)} year{'s' if r > 12 else ''} each, unless either party notifies "
                      f"the other in writing, no later than {old_en}{en_num(n)} {unit_en} before the expiry date, "
                      "that it does not wish to renew.")
            vi = (f"Khi hết thời hạn, Hợp đồng được tự động gia hạn thêm {vi_num(r)} tháng cho mỗi lần gia hạn, trừ "
                  f"trường hợp một Bên gửi thông báo bằng văn bản về việc không gia hạn cho Bên kia ít nhất "
                  f"{old_vi}{vi_num(n)} {unit_vi} trước ngày hết hạn.")
            fields = ["auto_renewal", "renewal_term_months", "non_renewal_notice_days", "notice_deadline"]
        else:
            en = ("This Agreement shall not renew automatically. Any extension shall require a written amendment "
                  "signed by both parties.")
            vi = "Việc gia hạn Hợp đồng chỉ được thực hiện khi hai Bên thỏa thuận bằng văn bản."
            fields = ["auto_renewal"]
        self.para(f"{self.section_no}.2 {en}", f"{self.section_no}.2 {vi}", fields)

    def termination_clause(self) -> None:
        s = self.spec
        if s.tfc:
            en = (f"Either party may terminate this Agreement for convenience at any time by giving not less than "
                  f"{en_num(s.tfc_days)} days' prior written notice to the other party.")
            vi = (f"Mỗi Bên có quyền đơn phương chấm dứt Hợp đồng mà không cần nêu lý do bằng cách thông báo bằng "
                  f"văn bản cho Bên kia trước ít nhất {vi_num(s.tfc_days)} ngày.")
        else:
            en = ("Neither party may terminate this Agreement for convenience. This Agreement may be terminated only "
                  "for material breach in accordance with this Section.")
            vi = ("Không Bên nào được đơn phương chấm dứt Hợp đồng trước thời hạn, trừ trường hợp Bên kia vi phạm "
                  "nghiêm trọng nghĩa vụ theo Hợp đồng.")
        self.para(f"{self.section_no}.3 {en}", f"{self.section_no}.3 {vi}", ["termination_for_convenience"])
        self.para(f"{self.section_no}.4 Either party may terminate this Agreement immediately upon written notice if "
                  "the other party commits a material breach that is not remedied within thirty (30) days of notice.",
                  f"{self.section_no}.4 Một Bên có quyền chấm dứt Hợp đồng ngay khi Bên kia vi phạm nghiêm trọng và không "
                  "khắc phục trong vòng 30 (ba mươi) ngày kể từ ngày nhận được thông báo.")

    def value_clause(self) -> None:
        s, v = self.spec, self.spec.variant
        roles = ROLES[s.doc_type]
        if s.value_amount is None:
            return
        cur, amt = s.currency, s.value_amount
        if s.doc_type == "lease":
            en = f"The monthly rent is {money_en(amt, cur, v)}, payable in advance on the first Business Day of each month."
            vi = f"Giá thuê hằng tháng là {money_vi(amt, cur, v)}, thanh toán trước vào ngày làm việc đầu tiên của mỗi tháng."
        elif s.value_period == "total":
            en = f"The total contract price is {money_en(amt, cur, v)}, as itemised in the table below."
            vi = f"Tổng giá trị Hợp đồng là {money_vi(amt, cur, v)}, chi tiết theo bảng dưới đây."
        elif s.value_period == "monthly":
            en = (f"In consideration of the Services, the {roles[0][0]} shall pay the {roles[1][0]} a monthly fee of "
                  f"{money_en(amt, cur, v)}, exclusive of applicable taxes.")
            vi = f"Phí dịch vụ hằng tháng là {money_vi(amt, cur, v)} (chưa bao gồm thuế giá trị gia tăng)."
        else:
            tail_en = ", being the sum of the fees itemised in the table below" if s.line_items else ""
            tail_vi = ", là tổng các khoản phí tại bảng dưới đây" if s.line_items else ""
            en = (f"In consideration of the Services, the {roles[0][0]} shall pay the {roles[1][0]} an annual fee of "
                  f"{money_en(amt, cur, v)} (the \"Annual Fee\"){tail_en}, exclusive of applicable taxes.")
            vi = f"Phí dịch vụ hằng năm là {money_vi(amt, cur, v)}{tail_vi} (chưa bao gồm thuế giá trị gia tăng)."
        self.para(f"{self.section_no}.1 {en}", f"{self.section_no}.1 {vi}", ["contract_value", "value_period"])
        if s.line_items:
            self.line_items_table()
        if s.payment_days:
            en = (f"The {roles[0][0]} shall pay each undisputed invoice within {en_num(s.payment_days)} days of receipt.")
            vi = (f"{roles[2][0]} thanh toán cho {roles[2][1]} trong vòng {vi_num(s.payment_days)} ngày kể từ ngày "
                  "nhận được hóa đơn hợp lệ.")
            self.para(f"{self.section_no}.2 {en}", f"{self.section_no}.2 {vi}", ["payment_terms_days"])

    def line_items_table(self) -> None:
        s = self.spec
        vi_side = s.language == "vi"
        cur = s.currency
        fmt = (lambda x: money_vi(x, cur, 1).replace(" đồng", "")) if vi_side else (lambda x: f"{x:,.2f}")
        header = ["STT", "Hạng mục", "Số lượng", f"Đơn giá ({cur})", f"Thành tiền ({cur})"] if vi_side else \
            ["#", "Description", "Qty", f"Unit Price ({cur})", f"Amount ({cur})"]
        rows = [header]
        for k, li in enumerate(s.line_items, 1):
            rows.append([str(k), li["description"], f"{li['quantity']:,}".replace(",", "." if vi_side else ","),
                         fmt(li["unit_price"]), fmt(li["amount"])])
        total = sum(li["amount"] for li in s.line_items)
        rows.append(["", "Tổng cộng" if vi_side else "Total", "", "", fmt(total)])
        self.elements.append(("table", rows))
        self.evidence["line_items"] = s.line_items[0]["description"]

    def law_clause(self) -> None:
        s = self.spec
        en = (f"This Agreement shall be governed by and construed in accordance with the laws of {s.law_text}, "
              "without regard to its conflict of laws principles.") if s.language == "en" else \
            "This Agreement shall be governed by and construed in accordance with the laws of the Socialist Republic of Vietnam."
        vi = "Hợp đồng này được điều chỉnh và giải thích theo pháp luật nước Cộng hòa Xã hội Chủ nghĩa Việt Nam."
        self.para(f"{self.section_no}.1 {en}", f"{self.section_no}.1 {vi}", ["governing_law"])

    def distractors(self, kind: str) -> None:
        s, v = self.spec, self.spec.variant
        cur = s.currency or "USD"
        if kind == "liability":
            cap = (s.value_amount or 100_000) * 3
            self.para(f"{self.section_no}.9 In no event shall either party's aggregate liability under this Agreement "
                      f"exceed {money_en(cap, cur, v + 1)}.",
                      f"{self.section_no}.9 Tổng mức trách nhiệm bồi thường của mỗi Bên theo Hợp đồng không vượt quá "
                      f"{money_vi(cap, cur, v + 1)}.")
        elif kind == "insurance":
            self.para(f"{self.section_no}.8 The {ROLES[s.doc_type][1][0]} shall maintain and renew annually its "
                      "professional liability insurance and shall provide certificates of insurance upon request.",
                      f"{self.section_no}.8 {ROLES[s.doc_type][2][1]} duy trì và gia hạn hằng năm hợp đồng bảo hiểm "
                      "trách nhiệm nghề nghiệp và cung cấp giấy chứng nhận bảo hiểm khi được yêu cầu.")
        elif kind == "schedule_date":
            other = add_months(s.effective, -self.rng.randint(4, 30))
            self.para(f"This Data Processing Schedule is effective as of {en_date(other, v + 1)} and applies to all "
                      "processing of Personal Data under any agreement between the parties.",
                      f"Phụ lục xử lý dữ liệu này có hiệu lực từ {vi_date(other, v + 1)} và áp dụng cho mọi hoạt động "
                      "xử lý dữ liệu cá nhân giữa các Bên.")

    def signatures(self, dates: list[date] | None = None, label: str = "signatures") -> None:
        s = self.spec
        signed = "draft" not in s.tags or label != "signatures"
        dates = dates or s.signature_dates
        roles = ROLES[s.doc_type]
        blocks = []
        for k, (company, (name, title)) in enumerate(zip([s.party_a, s.party_b], s.signers, strict=True)):
            blocks.append({"role_en": roles[k][0].upper(), "role_vi": "ĐẠI DIỆN " + roles[2][k].upper(),
                           "company": company, "name": name, "title": title,
                           "date": dates[k] if signed else None, "signed": signed,
                           "stamp": signed and s.language != "en"})
        self.elements.append(("signatures", blocks, label))
        if label == "signatures":
            intro = {"en": "IN WITNESS WHEREOF, the parties have executed this Agreement",
                     "vi": "Hai Bên đã đọc, hiểu rõ và đồng ý ký tên dưới đây",
                     "bilingual": "IN WITNESS WHEREOF"}[s.language]
            self.evidence["signatories"] = intro
            self.evidence["is_executed"] = intro

    def amendment(self) -> None:
        s, v = self.spec, self.spec.variant
        am = s.amendment
        if not am:
            return
        title_en, title_vi = TITLES[s.doc_type]
        self.elements.append(("pagebreak",))
        self.elements.append(("title", "AMENDMENT NO. 1", "PHỤ LỤC SỬA ĐỔI SỐ 01"))
        self.para(f"This Amendment No. 1 is made on {en_date(am['date'], v)} between {escape(s.party_a)} and "
                  f"{escape(s.party_b)} and amends the {title_en.title()} between them (the \"Agreement\").",
                  f"Phụ lục này được lập {vi_date(am['date'], v)} giữa {escape(s.party_a)} và {escape(s.party_b)} và "
                  f"sửa đổi {title_vi.capitalize()} giữa hai Bên (\"Hợp đồng\").")
        if am["kind"] == "term":
            en = (f"Section {self.term_section}.1 of the Agreement is amended so that the Initial Term is "
                  f"{en_num(am['value'])} months from the Effective Date.")
            vi = f"Sửa đổi khoản {self.term_section}.1: Thời hạn của Hợp đồng là {vi_num(am['value'])} tháng kể từ Ngày hiệu lực."
            fields = ["initial_term_end", "notice_deadline"]
        elif am["kind"] == "notice":
            en = (f"The period of written notice of non-renewal in Section {self.term_section}.2 is changed to "
                  f"{en_num(am['value'])} days.")
            vi = f"Sửa đổi khoản {self.term_section}.2: thời hạn thông báo không gia hạn là {vi_num(am['value'])} ngày."
            fields = ["non_renewal_notice_days", "notice_deadline"]
        else:
            en = f"With effect from the date of this Amendment, the fee payable under the Agreement is {money_en(am['value'], s.currency, v)} per {'year' if s.value_period == 'annual' else 'month'}."
            vi = f"Kể từ ngày Phụ lục này có hiệu lực, phí dịch vụ là {money_vi(am['value'], s.currency, v)} mỗi {'năm' if s.value_period == 'annual' else 'tháng'}."
            fields = ["contract_value"]
        self.para(en, vi, fields)
        self.para("All other terms of the Agreement remain unchanged and in full force and effect.",
                  "Các điều khoản khác của Hợp đồng giữ nguyên hiệu lực.")
        self.signatures([am["date"], am["date"]], label="amendment")


def build_document(spec: Spec) -> Doc:
    d = Doc(spec)
    s = spec
    title_en, title_vi = TITLES[s.doc_type]
    d.elements.append(("title", title_en, title_vi))
    d.preamble()
    per_topic = {"short": 0, "medium": d.rng.randint(8, 14), "long": d.rng.randint(50, 68)}[s.length]
    target = per_topic > 0
    distract = "distractors" in s.tags

    def fillers(topics_en: list[str], topics_vi: list[str], paragraphs: int) -> None:
        for t_en, t_vi in zip(topics_en, topics_vi, strict=False):
            d.heading(t_en, t_vi)
            d.filler(t_en, t_vi, paragraphs)

    if target:
        d.heading("Definitions", "Giải thích từ ngữ")
        d.filler("Definitions", "Giải thích từ ngữ", per_topic)
    if s.doc_type != "nda":
        d.heading("Services" if s.doc_type != "lease" else "Premises", "Phạm vi công việc" if s.doc_type != "lease" else "Đối tượng thuê")
        d.filler("Services", "Quyền và nghĩa vụ của các Bên", max(2, per_topic // 2))
        d.heading("Fees and Payment", "Giá trị Hợp đồng và thanh toán")
        d.value_clause()
    else:
        d.heading("Confidential Information", "Thông tin bảo mật")
        d.filler("Confidentiality", "Bảo mật thông tin", max(2, per_topic // 2))
    if target:
        long_topics_en = C.EN_FILLER_TOPICS_LONG * (3 if s.length == "long" else 1)
        long_topics_vi = (C.VI_FILLER_TOPICS_LONG * 6)[: len(long_topics_en)]
        half = len(long_topics_en) // 2
        fillers(long_topics_en[:half], long_topics_vi[:half], per_topic)
        if distract:
            d.heading("Limitation of Liability", "Giới hạn trách nhiệm")
            d.filler("Warranties", "Quyền và nghĩa vụ của các Bên", 3)
            d.distractors("liability")
            d.heading("Insurance", "Bảo hiểm")
            d.filler("Compliance", "Quyền và nghĩa vụ của các Bên", 2)
            d.distractors("insurance")
        fillers(long_topics_en[half:], long_topics_vi[half:], per_topic)
    d.heading("Term and Termination", "Thời hạn và chấm dứt Hợp đồng")
    d.term_section = str(d.section_no)
    d.term_clause()
    d.renewal_clause()
    d.termination_clause()
    if target:
        d.heading("Notices", "Thông báo")
        d.filler("Notices", "Điều khoản chung", 3)
    d.heading("Governing Law", "Luật áp dụng")
    d.law_clause()
    d.heading("Miscellaneous", "Điều khoản chung")
    d.filler("Miscellaneous", "Điều khoản chung", 3 if target else 2)
    d.signatures()
    if target and distract:
        d.elements.append(("pagebreak",))
        d.elements.append(("title", "SCHEDULE 1 - DATA PROCESSING", "PHỤ LỤC 1 - XỬ LÝ DỮ LIỆU"))
        d.distractors("schedule_date")
        d.filler("Data Protection", "Bảo vệ dữ liệu cá nhân", max(4, per_topic // 2))
    d.amendment()
    return d


# ── rendering ────────────────────────────────────────────────────────────────

class SignatureMark(Flowable):
    """A hand-drawn-looking signature (vector curves), optionally under a red company seal."""

    def __init__(self, seed: int, stamp: bool = False, width: float = 130, height: float = 28):
        super().__init__()
        self.seed, self.stamp, self.width, self.height = seed, stamp, width, height

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        rng = random.Random(self.seed)
        c = self.canv
        c.saveState()
        c.setStrokeColor(colors.HexColor("#1f3a93"))
        c.setLineWidth(1.1)
        path = c.beginPath()
        x, y = 4.0, self.height * 0.45
        path.moveTo(x, y)
        for _ in range(5):
            x2 = x + rng.uniform(14, 24)
            path.curveTo(x + rng.uniform(2, 8), y + rng.uniform(6, 13), x2 - rng.uniform(2, 8), y - rng.uniform(6, 13),
                         x2, self.height * 0.45 + rng.uniform(-4, 4))
            x = x2
        c.drawPath(path, stroke=1, fill=0)
        if self.stamp:
            c.setStrokeColor(colors.Color(0.82, 0.1, 0.12))
            c.setLineWidth(1.6)
            cx, cy = self.width * 0.62, self.height * 0.5
            c.circle(cx, cy, 30, stroke=1, fill=0)
            c.circle(cx, cy, 23, stroke=1, fill=0)
            star = c.beginPath()
            for k in range(10):
                r = 9 if k % 2 == 0 else 4
                a = math.pi / 2 + k * math.pi / 5
                (star.moveTo if k == 0 else star.lineTo)(cx + r * math.cos(a), cy + r * math.sin(a))
            star.close()
            c.drawPath(star, stroke=1, fill=0)
        c.restoreState()


def _styles(font: str, bold: str) -> dict[str, ParagraphStyle]:
    body = ParagraphStyle("body", fontName=font, fontSize=9.6, leading=12.6, spaceAfter=5, alignment=TA_JUSTIFY)
    return {
        "body": body,
        "cell": ParagraphStyle("cell", parent=body, fontSize=8.6, leading=10.8, spaceAfter=0, alignment=0),
        "head": ParagraphStyle("head", parent=body, fontName=bold, fontSize=11, leading=14, spaceBefore=8, spaceAfter=4,
                               alignment=0),
        "title": ParagraphStyle("title", parent=body, fontName=bold, fontSize=15, leading=19, alignment=TA_CENTER,
                                spaceAfter=10),
        "center": ParagraphStyle("center", parent=body, alignment=TA_CENTER),
    }


def render_pdf(doc: Doc) -> bytes:
    font, bold = register_fonts()
    st = _styles(font, bold)
    s = doc.spec
    lang = s.language
    buf = io.BytesIO()
    title_en, title_vi = TITLES[s.doc_type]
    short = title_vi if lang == "vi" else title_en

    def footer(canvas, pdf):
        canvas.saveState()
        canvas.setFont(font, 7.5)
        canvas.drawString(20 * mm, 12 * mm, short.title() if lang != "vi" else short)
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"{'Trang' if lang == 'vi' else 'Page'} {pdf.page}")
        canvas.restoreState()

    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=20 * mm, title=short, author="DocIntel synthetic", invariant=1)
    story: list = []
    width = A4[0] - 40 * mm
    if lang == "vi":
        story.append(Paragraph("<b>CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM</b>", st["center"]))
        story.append(Paragraph("Độc lập - Tự do - Hạnh phúc", st["center"]))
        story.append(Spacer(1, 8))
    bi_rows: list[list] = []

    def flush_bi():
        if bi_rows:
            t = Table([[Paragraph(a, st["cell"]), Paragraph(b, st["cell"])] for a, b in bi_rows],
                      colWidths=[width / 2, width / 2], hAlign="LEFT")
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
            story.append(t)
            bi_rows.clear()

    for el in doc.elements:
        kind = el[0]
        if kind != "para":
            flush_bi()
        if kind == "title":
            text = el[1] if lang == "en" else el[2] if lang == "vi" else f"{el[1]}<br/>{el[2]}"
            story.append(Paragraph(text, st["title"]))
        elif kind == "heading":
            text = el[1] if lang == "en" else el[2] if lang == "vi" else f"{el[1]} / {el[2].split('. ', 1)[1]}"
            story.append(Paragraph(text, st["head"]))
        elif kind == "para":
            if lang == "bilingual":
                bi_rows.append((el[1], el[2]))
            else:
                story.append(Paragraph(el[1] if lang == "en" else el[2], st["body"]))
        elif kind == "table":
            rows = [[Paragraph(escape(str(c)), st["cell"]) for c in r] for r in el[1]]
            t = Table(rows, colWidths=[10 * mm, width - 10 * mm - 3 * 30 * mm, 22 * mm, 34 * mm, 34 * mm],
                      repeatRows=1, hAlign="LEFT")
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8ecf3")),
                                   ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            story.append(Spacer(1, 4))
            story.append(t)
            story.append(Spacer(1, 6))
        elif kind == "pagebreak":
            story.append(PageBreak())
        elif kind == "signatures":
            if el[2] == "signatures":
                intro = ("IN WITNESS WHEREOF, the parties have executed this Agreement as of the dates written below."
                         if lang == "en" else "Hai Bên đã đọc, hiểu rõ và đồng ý ký tên dưới đây." if lang == "vi"
                         else "IN WITNESS WHEREOF / Hai Bên cùng ký tên dưới đây.")
            else:
                intro = ("Signed for and on behalf of the parties:" if lang == "en" else
                         "Đại diện hai Bên ký xác nhận:" if lang == "vi" else
                         "Signed for and on behalf of the parties / Đại diện hai Bên ký xác nhận:")
            cells = []
            for k, b in enumerate(el[1]):
                if lang == "en":
                    head = f"<b>{b['role_en']}</b>"
                    date_line = f"Date: {en_date(b['date'], s.variant)}" if b["date"] else "Date: ____________________"
                    before: list[str] = []
                    after = ["By: ____________________", f"Name: {escape(b['name'])}", f"Title: {escape(b['title'])}",
                             date_line]
                else:
                    head = f"<b>{b['role_vi']}</b>" + (f" / <b>{b['role_en']}</b>" if lang == "bilingual" else "")
                    date_line = (f"Ngày ký: {b['date'].day:02d}/{b['date'].month:02d}/{b['date'].year}"
                                 if b["date"] else "Ngày ký: ____/____/________")
                    before = ["(Ký, ghi rõ họ tên và đóng dấu)"]
                    after = [f"<b>{escape(b['name'])}</b>", escape(b["title"]), date_line]
                flow = [Paragraph(head, st["cell"]), Paragraph(escape(b["company"]), st["cell"])]
                flow.extend(Paragraph(x, st["cell"]) for x in before)
                flow.append(SignatureMark(s.seed * 7 + k, stamp=b["stamp"]) if b["signed"] else Spacer(1, 28))
                flow.extend(Paragraph(x, st["cell"]) for x in after)
                cells.append(flow)
            t = Table([cells], colWidths=[width / 2, width / 2], hAlign="LEFT")
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.append(KeepTogether([Spacer(1, 10), Paragraph(intro, st["body"]), t]))
    flush_bi()
    pdf.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# ── evidence pages and page texts ────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def page_texts_of(pdf_bytes: bytes, left_column: bool = False) -> list[str]:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if left_column:  # two-column bilingual layout: read the English column on its own
            return [p.crop((0, 0, p.width / 2, p.height)).extract_text() or "" for p in pdf.pages]
        return [p.extract_text() or "" for p in pdf.pages]


def find_evidence_pages(doc: Doc, texts: list[str]) -> dict[str, int]:
    normed = [_norm(t) for t in texts]
    out: dict[str, int] = {}
    for f, snippet in doc.evidence.items():
        target = _norm(snippet)
        hits = [i + 1 for i, t in enumerate(normed) if target and target in t]
        if hits:
            out[f] = hits[-1] if f in ("signatories", "is_executed") else hits[0]
    if doc.spec.amendment:   # the amendment states the final value: that is the evidence
        am_pages = [i + 1 for i, t in enumerate(normed) if "amendment no. 1" in t or "phụ lục sửa đổi số 01" in t]
        if am_pages:
            kind = doc.spec.amendment["kind"]
            for f in {"term": ["initial_term_end"], "notice": ["non_renewal_notice_days"],
                      "fee": ["contract_value"]}[kind]:
                out[f] = am_pages[0]
    return out


# ── public API ───────────────────────────────────────────────────────────────

@dataclass
class GeneratedDoc:
    external_id: str
    filename: str
    spec: dict
    labels: dict
    evidence_pages: dict
    strata: dict
    split: str
    page_texts_file: str


def generate_set(out_dir: Path, n: int = 60, seed: int = 7, name: str = "synthetic-v1",
                 scanned_rate: float = 0.25) -> list[GeneratedDoc]:
    from .scan import to_scanned_pdf

    out_dir = Path(out_dir)
    (out_dir / "docs").mkdir(parents=True, exist_ok=True)
    (out_dir / "texts").mkdir(parents=True, exist_ok=True)
    manifest: list[GeneratedDoc] = []
    for spec in sample_specs(n, seed, scanned_rate):
        doc = build_document(spec)
        digital = render_pdf(doc)
        texts = page_texts_of(digital)
        evidence = find_evidence_pages(doc, page_texts_of(digital, left_column=True) if spec.language == "bilingual" else texts)
        data = to_scanned_pdf(digital, random.Random(spec.seed)) if spec.scanned else digital
        ext_id = f"{name}-{spec.idx:03d}"
        fname = f"{ext_id}-{spec.language}-{spec.doc_type}-{spec.length}{'-scan' if spec.scanned else ''}.pdf"
        (out_dir / "docs" / fname).write_bytes(data)
        texts_file = out_dir / "texts" / f"{ext_id}.json"
        texts_file.write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
        strata = {"language": spec.language, "doc_type": spec.doc_type, "length": spec.length,
                  "scanned": spec.scanned, "has_table": bool(spec.line_items), "pages": len(texts),
                  "tags": sorted(spec.tags)}
        split = "dev" if spec.idx % 3 == 0 else "test"
        spec_d = asdict(spec)
        manifest.append(GeneratedDoc(ext_id, fname, json.loads(json.dumps(spec_d, default=str)), labels_for(spec),
                                     evidence, strata, split, str(texts_file.relative_to(out_dir))))
    (out_dir / "manifest.json").write_text(
        json.dumps([asdict(m) for m in manifest], ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest
