"""A fake model that answers from the generator's ground truth, quoting the pages it was sent.

It lets the whole pipeline (selection, validation, grounding, derivation, eval, gate)
run offline and deterministically. ``mistakes`` injects controlled errors."""
from __future__ import annotations

import json
import re
from typing import Any

from docintel.synth.generator import Spec, labels_for


def _pages(payload: dict) -> dict[int, str]:
    user = next(m["content"] for m in payload["messages"] if m["role"] == "user")
    parts = re.split(r"=== Page (\d+) ===\n", user)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _find(pages: dict[int, str], *patterns: str) -> tuple[str | None, int | None]:
    for page_no, text in pages.items():
        for line in text.splitlines():
            for pat in patterns:
                if re.search(pat, line, re.I):
                    return line.strip("# ").strip()[:280], page_no
    return None, None


def oracle(spec: Spec, mistakes: dict[str, Any] | None = None, tier_mistakes: dict[str, dict] | None = None):
    labels = labels_for(spec)
    mistakes = mistakes or {}
    tier_mistakes = tier_mistakes or {}

    def respond(payload: dict) -> dict:
        schema = payload["response_format"]["json_schema"]["schema"]
        wanted = set(schema["properties"])
        pages = _pages(payload)
        effort = payload.get("reasoning_effort")
        tier_bad = tier_mistakes.get(effort or "", {})
        out: dict[str, Any] = {}

        def cited(value, *patterns):
            q, p = _find(pages, *patterns) if value is not None else (None, None)
            if value is not None and q is None:
                return {"value": None, "quote": None, "page": None}
            return {"value": value, "quote": q, "page": p}

        if "parties" in wanted:
            out["parties"] = []
            for name in labels["parties"]:
                q, p = _find(pages, re.escape(name[:25]))
                out["parties"].append({"name": name, "role": "other", "quote": q, "page": p})
            out["signatories"] = []
            for (name, title), d in zip(spec.signers, spec.signature_dates, strict=True):
                q, p = _find(pages, re.escape(name))
                executed = "draft" not in spec.tags
                out["signatories"].append({"name": name, "title": title, "party": None,
                                           "signed_date": d.isoformat() if executed else None,
                                           "is_signed": executed, "quote": q, "page": p})
        if "effective_date" in wanted:
            basis = "last_signature" if spec.effective_rule == "last_signature" else "stated_date"
            eff = cited(labels["effective_date"], r"effective date|ngày hiệu lực|hiệu lực")
            eff["basis"] = basis
            out["effective_date"] = eff
            exp = spec.expiration_type if spec.expiration_type != "duration" else "duration"
            out["expiration_type"] = cited(exp, r"initial term|remain in (force|effect)|continue in full|thời hạn|hiệu lực đến|thanh lý|fully performed")
            out["expiration_date"] = cited(spec.fixed_end.isoformat() if spec.fixed_end else None, r"remain in effect until|hiệu lực đến hết")
            term = spec.amendment["value"] if spec.amendment and spec.amendment["kind"] == "term" else spec.term_months
            out["initial_term_months"] = cited(term, r"amended so that the Initial Term|Sửa đổi khoản .*Thời hạn" if term != spec.term_months else r"initial term|thời hạn của hợp đồng là|thời hạn \d+|remain in force")
            out["auto_renewal"] = cited(labels["auto_renewal"], r"renew|gia hạn")
            out["renewal_term_months"] = cited(spec.renewal_months, r"renew|gia hạn")
            notice = None
            if spec.auto_renewal and spec.notice_amount:
                amount = spec.amendment["value"] if spec.amendment and spec.amendment["kind"] == "notice" else spec.notice_amount
                notice = {"amount": amount, "unit": spec.notice_unit}
            out["non_renewal_notice"] = cited(notice, r"changed to|thời hạn thông báo không gia hạn" if spec.amendment and spec.amendment["kind"] == "notice" else r"non-renewal|not wish to renew|không gia hạn")
            out["termination_for_convenience"] = cited(spec.tfc, r"for convenience|đơn phương")
            out["termination_notice_days"] = cited(spec.tfc_days, r"for convenience|đơn phương")
        if "contract_value" in wanted:
            value = labels["contract_value"]
            pattern = r"fee payable under the Agreement is|phí dịch vụ là" if spec.amendment and spec.amendment["kind"] == "fee" else r"fee of|monthly rent|total contract price|phí dịch vụ|giá thuê|tổng giá trị"
            cv = cited(value, pattern)
            cv["period"] = labels["value_period"] if value else None
            out["contract_value"] = cv
            out["payment_terms_days"] = cited(spec.payment_days, r"invoice|hóa đơn")
            out["line_items"] = [{**li, "page": next(iter(pages))} for li in labels["line_items"]]
        if "governing_law" in wanted:
            out["document_type"] = spec.doc_type
            out["title"] = None
            out["governing_law"] = cited({"new york": "New York", "delaware": "Delaware", "california": "California",
                                          "singapore": "Singapore", "england and wales": "England and Wales",
                                          "vietnam": "Vietnam"}[spec.law_norm], r"governed|điều chỉnh")
        for field, bad in {**mistakes, **tier_bad}.items():
            if field in out:
                out[field] = bad(out[field]) if callable(bad) else bad
        return {"content": json.dumps({k: out[k] for k in wanted if k in out}),
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}

    return respond
