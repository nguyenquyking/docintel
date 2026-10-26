"""Field confidence: signals → raw score → calibrated probability.

No single signal is trustworthy (OCR confidence was 0.96 on Vietnamese text with every
diacritic missing), so the raw score combines several, and calibration maps it to
"how often is a field with this score actually correct?" using eval results
(isotonic regression, per field). The review threshold is then a precision target,
not a guess.
"""
from __future__ import annotations

from typing import Any

from .schema import CRITICAL_FIELDS
from .verify import FieldResult


def _expected_null(name: str, values: dict[str, Any]) -> bool:
    """A null that the rest of the extraction predicts (no notice period when nothing renews)."""
    exp, auto, tfc = values.get("expiration_type"), values.get("auto_renewal"), values.get("termination_for_convenience")
    return {
        "expiration_date": exp in ("duration", "evergreen", "on_completion"),
        "initial_term_months": exp in ("fixed_date", "evergreen", "on_completion"),
        "renewal_term_months": auto is False or exp in ("evergreen", "on_completion"),
        "non_renewal_notice": auto is False or exp in ("evergreen", "on_completion"),
        "auto_renewal": exp in ("evergreen", "on_completion"),
        "termination_notice_days": tfc is False,
        "contract_value": values.get("document_type") == "nda",
        "payment_terms_days": values.get("document_type") in ("nda", "lease"),
        "line_items": values.get("document_type") in ("nda", "lease"),
    }.get(name, False)


def raw_score(fr: FieldResult, *, page_routes: dict[int, tuple[str, float | None]], full_coverage: bool,
              values: dict[str, Any] | None = None) -> float:
    if fr.value is None or fr.value == []:
        # "not in the document" is more believable when the model saw the whole document,
        # and when the other fields predict it
        base = 0.78 if full_coverage else 0.6
        if _expected_null(fr.field, values or {}):
            base += 0.15
        return max(0.05, min(0.97, base - 0.1 * len(fr.flags)))
    if fr.field in ("document_type", "title"):
        return 0.85
    s = 0.55
    if fr.grounded is True:
        s += 0.3
    elif fr.grounded is False:
        s -= 0.35
    if fr.value_in_quote is True:
        s += 0.12
    elif fr.value_in_quote is False:
        s -= 0.3
    pages = [e.page for e in fr.evidence if e.page]
    for p in pages[:1]:
        route, conf = page_routes.get(p, ("text_layer", None))
        if route == "ocr" and conf is not None:
            s -= max(0.0, 0.95 - conf)
        elif route == "vlm":
            s -= 0.04
    s -= 0.12 * len(fr.flags)
    if fr.tier == "strong":
        s += 0.03
    return min(0.99, max(0.02, s))


def isotonic_fit(points: list[tuple[float, bool]]) -> list[list[float]]:
    """Pool-adjacent-violators: a monotone step function score → P(correct).
    Returns [[upper_score, probability], ...] in increasing score order."""
    by_score: dict[float, list[float]] = {}
    for score, correct in points:
        acc = by_score.setdefault(round(float(score), 4), [0.0, 0.0])
        acc[0] += float(correct)
        acc[1] += 1.0
    blocks: list[list[float]] = []   # [upper_score, sum_correct, count]
    for score in sorted(by_score):
        total, count = by_score[score]
        blocks.append([score, total, count])
        while len(blocks) > 1 and blocks[-2][1] / blocks[-2][2] >= blocks[-1][1] / blocks[-1][2]:
            last = blocks.pop()
            blocks[-1] = [last[0], blocks[-1][1] + last[1], blocks[-1][2] + last[2]]
    return [[b[0], round(b[1] / b[2], 4)] for b in blocks]


def apply_calibration(score: float, table: list[list[float]] | None) -> float:
    if not table:
        return score
    for upper, p in table:
        if score <= upper:
            return p
    return table[-1][1]


def score_fields(results: list[FieldResult], *, page_routes: dict[int, tuple[str, float | None]],
                 full_coverage: dict[str, bool], calibration: dict[str, Any] | None, review_below: float) -> None:
    calibration = calibration or {}
    values = {fr.field: fr.value for fr in results}
    for fr in results:
        fr.raw_confidence = raw_score(fr, page_routes=page_routes, full_coverage=full_coverage.get(fr.group, False),
                                      values=values)
        fr.confidence = apply_calibration(fr.raw_confidence, calibration.get(fr.field) or calibration.get("*"))
        fr.needs_review = fr.confidence < review_below and (fr.field in CRITICAL_FIELDS or bool(fr.flags))


def document_confidence(results: list[FieldResult]) -> float:
    crit = [fr.confidence for fr in results if fr.field in CRITICAL_FIELDS]
    return min(crit) if crit else 0.0
