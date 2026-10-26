"""Facts: the normalized, derived view of an extraction — what eval grades and the
product shows. Computed by code from the extracted values (and any human
corrections), never by the model.

Conventions (also in docs/evaluation.md, the annotation guideline):
* a term of N months from effective date E ends on E + N months − 1 day;
* the notice deadline is term end − notice (calendar months when stated in months);
* notice in months is normalized to days as 30 × months for comparison;
* the current term rolls forward by the renewal term while auto-renewal applies.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from ..text.normalize import norm_jurisdiction


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _date(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def notice_days(notice: dict | None) -> int | None:
    if not notice or notice.get("amount") is None:
        return None
    mult = {"days": 1, "weeks": 7, "months": 30}.get(notice.get("unit") or "days", 1)
    return int(notice["amount"]) * mult


def notice_before(end: date, notice: dict) -> date:
    amount, unit = int(notice["amount"]), notice.get("unit") or "days"
    if unit == "months":
        return add_months(end, -amount)
    return end - timedelta(days=amount * (7 if unit == "weeks" else 1))


def derive_facts(values: dict[str, Any], meta: dict[str, dict] | None = None, today: date | None = None) -> dict[str, Any]:
    meta = meta or {}
    today = today or date.today()
    parties = values.get("parties") or []
    signatories = values.get("signatories") or []
    executed = all(bool(s.get("is_signed")) for s in signatories) if signatories else None

    effective = _date(values.get("effective_date"))
    if effective is None and (meta.get("effective_date") or {}).get("basis") == "last_signature" and executed:
        dates = [_date(s.get("signed_date")) for s in signatories]
        if dates and all(dates):
            effective = max(dates)

    exp_type = values.get("expiration_type")
    end = _date(values.get("expiration_date"))
    months = values.get("initial_term_months")
    if end is None and effective and months and exp_type in (None, "duration", "not_stated"):
        end = add_months(effective, int(months)) - timedelta(days=1)
    if exp_type in ("evergreen", "on_completion"):
        end = None if values.get("expiration_date") is None else end
    exp_norm = {"fixed_date": "fixed_date", "duration": "duration", "evergreen": "evergreen",
                "on_completion": "on_completion"}.get(exp_type) if exp_type else None
    if exp_norm is None and end is not None:
        exp_norm = "fixed_date" if values.get("expiration_date") else "duration"

    auto = values.get("auto_renewal")
    renewal = values.get("renewal_term_months")
    notice = values.get("non_renewal_notice")
    deadline = notice_before(end, notice) if (auto and end and notice and notice.get("amount") is not None) else None

    value = values.get("contract_value")
    money = ({"amount": float(value["amount"]), "currency": str(value["currency"]).upper()}
             if isinstance(value, dict) and value.get("amount") is not None and value.get("currency") else None)
    law = values.get("governing_law")

    # product view: where does the agreement stand today?
    current_end, next_deadline = end, deadline
    if auto and end and renewal:
        while current_end and current_end < today:
            current_end = add_months(current_end + timedelta(days=1), int(renewal)) - timedelta(days=1)
        if notice and notice.get("amount") is not None and current_end:
            next_deadline = notice_before(current_end, notice)
    if executed is False:
        status = "draft"
    elif exp_norm == "evergreen":
        status = "evergreen"
    elif current_end:
        status = "expired" if current_end < today else "active"
    elif exp_norm == "on_completion" and executed:
        status = "active"          # in force until both sides have performed; no date to watch
    else:
        status = "unknown"

    return {
        "document_type": values.get("document_type"),
        "title": values.get("title"),
        "parties": [p.get("name") for p in parties if p.get("name")],
        "party_roles": {p.get("name"): p.get("role") for p in parties if p.get("name")},
        "signatories": [s.get("name") for s in signatories if s.get("name")],
        "is_executed": executed,
        "effective_date": _iso(effective),
        "expiration_type": exp_norm,
        "initial_term_end": _iso(end),
        "auto_renewal": auto,
        "renewal_term_months": renewal,
        "non_renewal_notice_days": notice_days(notice) if auto is not False else None,
        "notice_deadline": _iso(deadline),
        "termination_for_convenience": values.get("termination_for_convenience"),
        "termination_notice_days": values.get("termination_notice_days"),
        "contract_value": money,
        "value_period": (meta.get("contract_value") or {}).get("period") if money else None,
        "payment_terms_days": values.get("payment_terms_days"),
        "governing_law": norm_jurisdiction(law) if law else None,
        "line_items": [{k: li.get(k) for k in ("description", "quantity", "unit_price", "amount")}
                       for li in values.get("line_items") or []],
        "status": status,
        "current_term_end": _iso(current_end),
        "next_notice_deadline": _iso(next_deadline) if status in ("active",) else None,
    }


def alert_level(facts: dict[str, Any], today: date | None = None) -> tuple[str | None, int | None]:
    """Layered renewal alerts: plan (≤90 days before the cancel-by date), decide (≤30 days),
    missed (deadline passed, term still running — it will renew). Expiring contracts
    without auto-renewal get 'expiring' when the end is ≤90 days away."""
    today = today or date.today()
    if facts.get("status") != "active":
        return None, None
    deadline = _date(facts.get("next_notice_deadline"))
    end = _date(facts.get("current_term_end"))
    if facts.get("auto_renewal") and deadline:
        days = (deadline - today).days
        if days < 0 and end and end >= today:
            return "missed", days
        if 0 <= days <= 30:
            return "decide", days
        if 30 < days <= 90:
            return "plan", days
        return None, days
    if end and not facts.get("auto_renewal"):
        days = (end - today).days
        if 0 <= days <= 90:
            return "expiring", days
    return None, None
