"""What "accurate" means, in code. This module is the annotation guideline's executable twin.

Per field and document the outcome is one of:

* ``tn`` — gold says absent, prediction absent (correct: null is an answer);
* ``tp`` — both present and equal after normalization;
* ``fp`` — predicted a value the document does not contain (hallucination);
* ``fn`` — missed a value that is there;
* ``wrong`` — both present but different: counted as a false positive AND a false
  negative (a bad output and a miss);
* ``partial`` — list fields (parties, line items) matched only in part.

Equality is defined per type — dates by calendar day, money by amount (±0.5%) and
currency, names by normalized similarity, jurisdictions by canonical name — so a
formatting difference is never an error, and a lost diacritic in a name is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..text.normalize import amounts_equal, name_similarity, norm_jurisdiction, parse_date, similarity
from .stats import wilson

EVAL_FIELDS: dict[str, str] = {
    "document_type": "enum",
    "parties": "names",
    "signatories": "names",
    "is_executed": "bool",
    "effective_date": "date",
    "expiration_type": "enum",
    "initial_term_end": "date",
    "auto_renewal": "bool",
    "renewal_term_months": "int",
    "non_renewal_notice_days": "int",
    "notice_deadline": "date",
    "termination_for_convenience": "bool",
    "contract_value": "money",
    "value_period": "enum",
    "payment_terms_days": "int",
    "governing_law": "jurisdiction",
    "line_items": "line_items",
}
CRITICAL_EVAL_FIELDS = ["parties", "is_executed", "effective_date", "initial_term_end", "auto_renewal",
                        "non_renewal_notice_days", "notice_deadline", "contract_value"]
# eval field → the extracted (raw) field whose confidence, evidence and group it inherits
EVAL_TO_RAW = {
    "document_type": "document_type", "parties": "parties", "signatories": "signatories",
    "is_executed": "signatories", "effective_date": "effective_date", "expiration_type": "expiration_type",
    "initial_term_end": "expiration_date", "auto_renewal": "auto_renewal",
    "renewal_term_months": "renewal_term_months", "non_renewal_notice_days": "non_renewal_notice",
    "notice_deadline": "non_renewal_notice", "termination_for_convenience": "termination_for_convenience",
    "contract_value": "contract_value", "value_period": "contract_value", "payment_terms_days": "payment_terms_days",
    "governing_law": "governing_law", "line_items": "line_items",
}
NAME_MATCH = 0.85


@dataclass
class FieldOutcome:
    outcome: str
    correct: bool
    tp: int = 0
    fp: int = 0
    fn: int = 0


def _empty(v: Any) -> bool:
    return v is None or v == [] or v == ""


def _greedy(preds: list, golds: list, sim, threshold: float) -> int:
    pairs = sorted(((sim(p, g), i, j) for i, p in enumerate(preds) for j, g in enumerate(golds)), reverse=True)
    used_p, used_g, matched = set(), set(), 0
    for s, i, j in pairs:
        if s < threshold:
            break
        if i not in used_p and j not in used_g:
            used_p.add(i)
            used_g.add(j)
            matched += 1
    return matched


def equal(ftype: str, gold: Any, pred: Any, date_tolerance_days: int = 0) -> bool:
    if ftype == "date":
        g, p = parse_date(gold), parse_date(pred)
        return g is not None and p is not None and abs((g - p).days) <= date_tolerance_days
    if ftype == "money":
        return (isinstance(gold, dict) and isinstance(pred, dict)
                and str(gold.get("currency", "")).upper() == str(pred.get("currency", "")).upper()
                and amounts_equal(pred.get("amount"), gold.get("amount")))
    if ftype == "jurisdiction":
        return norm_jurisdiction(str(gold)) == norm_jurisdiction(str(pred))
    if ftype == "int":
        try:
            return int(gold) == int(pred)
        except (TypeError, ValueError):
            return False
    return gold == pred


def compare(ftype: str, gold: Any, pred: Any, options: dict | None = None) -> FieldOutcome:
    """``options`` come from the golden document (e.g. CUAD date labels carry a 1-day tolerance)."""
    tolerance = int((options or {}).get("date_tolerance_days", 0))
    if ftype == "names":
        g, p = [x for x in (gold or []) if x], [x for x in (pred or []) if x]
        if not g and not p:
            return FieldOutcome("tn", True)
        m = _greedy(p, g, name_similarity, NAME_MATCH)
        fp, fn = len(p) - m, len(g) - m
        ok = fp == 0 and fn == 0
        return FieldOutcome("tp" if ok else ("fp" if not g else "fn" if not p else "partial"), ok, m, fp, fn)
    if ftype == "line_items":
        g, p = list(gold or []), list(pred or [])
        if not g and not p:
            return FieldOutcome("tn", True)

        def row_sim(a: dict, b: dict) -> float:
            same_amount = amounts_equal(a.get("amount"), b.get("amount"))
            return similarity(str(a.get("description", "")).casefold(), str(b.get("description", "")).casefold()) \
                if same_amount else 0.0

        m = _greedy(p, g, row_sim, 0.8)
        fp, fn = len(p) - m, len(g) - m
        ok = fp == 0 and fn == 0
        return FieldOutcome("tp" if ok else ("fp" if not g else "fn" if not p else "partial"), ok, m, fp, fn)
    if _empty(gold) and _empty(pred):
        return FieldOutcome("tn", True)
    if _empty(gold):
        return FieldOutcome("fp", False, fp=1)
    if _empty(pred):
        return FieldOutcome("fn", False, fn=1)
    if equal(ftype, gold, pred, tolerance):
        return FieldOutcome("tp", True, tp=1)
    return FieldOutcome("wrong", False, fp=1, fn=1)


@dataclass
class FieldStats:
    n: int = 0
    correct: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)

    def add(self, o: FieldOutcome) -> None:
        self.n += 1
        self.correct += int(o.correct)
        self.tp, self.fp, self.fn = self.tp + o.tp, self.fp + o.fp, self.fn + o.fn
        self.outcomes[o.outcome] = self.outcomes.get(o.outcome, 0) + 1

    def summary(self) -> dict[str, Any]:
        p = self.tp / (self.tp + self.fp) if self.tp + self.fp else None
        r = self.tp / (self.tp + self.fn) if self.tp + self.fn else None
        f1 = 2 * p * r / (p + r) if p is not None and r is not None and p + r else (None if p is None or r is None else 0.0)
        lo, hi = wilson(self.correct, self.n)
        return {"n": self.n, "accuracy": round(self.correct / self.n, 4) if self.n else None,
                "accuracy_ci": [round(lo, 4), round(hi, 4)], "precision": None if p is None else round(p, 4),
                "recall": None if r is None else round(r, 4), "f1": None if f1 is None else round(f1, 4),
                "tp": self.tp, "fp": self.fp, "fn": self.fn, "outcomes": self.outcomes}
