"""Regression gate: did this prompt / model / pipeline change make anything worse?

Paired comparison on the same golden documents: per field, the documents that flip
from correct to wrong (b) and from wrong to correct (c). McNemar's exact test asks
whether b vs c is more lopsided than chance; a paired bootstrap gives the CI of the
accuracy change. The gate fails when a critical field regresses significantly, or
any field drops by more than a hard margin even if the test is underpowered — in
which case the answer is "look at the flips", which the report always lists.

Run it twice on an unchanged configuration first: flips between identical runs are
the noise floor (LLMs are not deterministic), and a real regression has to exceed it.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..models import EvalItem, EvalRun, GoldenDoc
from .metrics import CRITICAL_EVAL_FIELDS
from .stats import mcnemar_exact, paired_bootstrap


def compare_runs(base_id: str, cand_id: str, *, alpha: float = 0.05, max_drop: float = 0.03,
                 critical: list[str] | None = None, max_cost_increase: float | None = None) -> dict[str, Any]:
    critical = critical or CRITICAL_EVAL_FIELDS
    with session_scope() as s:
        base_run, cand_run = s.get(EvalRun, base_id), s.get(EvalRun, cand_id)
        if base_run is None or cand_run is None:
            raise ValueError("unknown run id")
        if base_run.set_name != cand_run.set_name:
            raise ValueError("runs must use the same golden set")
        base = {(i.golden_doc_id, i.field): i for i in s.scalars(select(EvalItem).where(EvalItem.run_id == base_id))}
        cand = {(i.golden_doc_id, i.field): i for i in s.scalars(select(EvalItem).where(EvalItem.run_id == cand_id))}
        names = {g.id: g.external_id for g in s.scalars(select(GoldenDoc).where(GoldenDoc.set_name == base_run.set_name))}
        meta = {r.id: {"id": r.id, "name": r.name, "quality": r.quality, "cost_per_1k": r.cost_per_1k,
                       "latency_p50_ms": r.latency_p50_ms, "config": r.config}
                for r in (base_run, cand_run)}

    fields: dict[str, dict[str, list]] = {}
    for key, b in base.items():
        if key not in cand:
            continue
        doc_id, field = key
        c = cand[key]
        f = fields.setdefault(field, {"b": [], "c": [], "broke": [], "fixed": []})
        f["b"].append(b.correct)
        f["c"].append(c.correct)
        if b.correct and not c.correct:
            f["broke"].append({"doc": names.get(doc_id, doc_id), "golden_doc_id": doc_id, "gold": b.gold, "pred": c.pred})
        elif c.correct and not b.correct:
            f["fixed"].append({"doc": names.get(doc_id, doc_id), "golden_doc_id": doc_id, "gold": b.gold, "pred": c.pred})

    rows, passed = [], True
    for field, f in sorted(fields.items()):
        n = len(f["b"])
        delta = (sum(f["c"]) - sum(f["b"])) / n if n else 0.0
        p = mcnemar_exact(len(f["broke"]), len(f["fixed"]))
        lo, hi = paired_bootstrap(f["b"], f["c"])
        is_critical = field in critical
        fail = (delta <= -max_drop) or (is_critical and delta < 0 and p < alpha)
        passed = passed and not fail
        rows.append({"field": field, "critical": is_critical, "n": n, "base_accuracy": round(sum(f["b"]) / n, 4),
                     "cand_accuracy": round(sum(f["c"]) / n, 4), "delta": round(delta, 4), "p_value": round(p, 5),
                     "ci": [round(lo, 4), round(hi, 4)], "broke": f["broke"], "fixed": f["fixed"], "fail": fail})
    cost_note = None
    b_cost, c_cost = meta[base_id]["cost_per_1k"], meta[cand_id]["cost_per_1k"]
    if max_cost_increase is not None and b_cost and c_cost and c_cost > b_cost * (1 + max_cost_increase):
        passed = False
        cost_note = f"cost per 1k docs rose {c_cost / b_cost - 1:.0%} (limit {max_cost_increase:.0%})"
    return {"passed": passed, "base": meta[base_id], "candidate": meta[cand_id], "fields": rows,
            "rules": {"alpha": alpha, "max_drop": max_drop, "critical": critical,
                      "max_cost_increase": max_cost_increase}, "cost_note": cost_note}
