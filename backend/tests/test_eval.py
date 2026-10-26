import asyncio
import json

import pytest
from sqlalchemy import select

from docintel.db import session_scope
from docintel.eval import golden
from docintel.eval.gate import compare_runs
from docintel.eval.metrics import compare
from docintel.eval.runner import create_run, run_eval
from docintel.eval.stats import half_width, mcnemar_exact, paired_bootstrap, psi, reliability, wilson
from docintel.llm.client import LLMClient
from docintel.models import GoldenDoc
from docintel.pipeline.runner import Deps
from docintel.retrieval.embeddings import Embedder
from docintel.settings import get_settings
from docintel.storage import Storage
from docintel.synth.generator import Spec, sample_specs

from .oracle import oracle


def test_compare_defines_accuracy():
    assert compare("date", "2024-01-15", "15/01/2024").outcome == "tp"
    assert compare("date", None, None).outcome == "tn"
    assert compare("date", None, "2024-01-15").outcome == "fp"
    assert compare("date", "2024-01-15", None).outcome == "fn"
    w = compare("date", "2024-01-15", "2024-01-16")
    assert w.outcome == "wrong" and (w.fp, w.fn) == (1, 1)
    assert compare("date", "2019-02-10", "2019-02-09", {"date_tolerance_days": 1}).correct
    assert compare("money", {"amount": 1500000000, "currency": "VND"}, {"amount": 1.5e9, "currency": "vnd"}).correct
    assert compare("jurisdiction", "the State of New York", "New York").correct
    p = compare("names", ["Acme Inc.", "Beta LLC"], ["ACME, Inc"])
    assert p.outcome == "partial" and (p.tp, p.fn) == (1, 1) and not p.correct
    li = compare("line_items", [{"description": "Licences", "amount": 100}], [{"description": "Licences", "amount": 100}])
    assert li.correct


def test_statistics():
    lo, hi = wilson(90, 100)
    assert lo < 0.9 < hi and hi - lo < 0.13
    assert round(half_width(0.9, 400), 3) == 0.029
    assert mcnemar_exact(3, 3) == 1.0 and mcnemar_exact(30, 5) < 1e-4
    base = [True] * 90 + [False] * 10
    cand = [False] * 25 + [True] * 65 + [True] * 5 + [False] * 5
    lo, hi = paired_bootstrap(base, cand)
    assert hi < 0
    assert psi({"en": 0.5, "vi": 0.5}, {"en": 0.5, "vi": 0.5}) == pytest.approx(0)
    assert psi({"en": 0.9, "vi": 0.1}, {"en": 0.4, "vi": 0.6}) > 0.25
    ece, bins = reliability([(0.9, True)] * 9 + [(0.9, False)])
    assert ece == pytest.approx(0.0, abs=1e-9) and bins[0]["accuracy"] == 0.9


def _import(n=6):
    specs = sample_specs(n, seed=11, scanned_rate=0.0)   # scans need a vision model: covered in test_parsing
    added = golden.import_synthetic("mini", n=n, seed=11, scanned_rate=0.0)
    assert added == n
    return {s.idx: s for s in specs}


def _responder_for_set(specs: dict[int, Spec], **kw):
    """Route each request to the oracle of the document it is about (matched by party names)."""
    oracles = {i: oracle(sp, **kw) for i, sp in specs.items()}

    def respond(payload):
        text = payload["messages"][-1]["content"] if isinstance(payload["messages"][-1]["content"], str) else ""
        for i, sp in specs.items():
            if sp.party_a[:20] in text and sp.party_b[:20] in text:
                return oracles[i](payload)
        return oracles[next(iter(oracles))](payload)

    return respond


def _deps(responder):
    s = get_settings()
    return Deps(s, LLMClient(s, responder=responder), Embedder(), Storage(s))


def test_golden_import_eval_run_and_gate():
    specs = _import(6)
    with session_scope() as s:
        docs = s.scalars(select(GoldenDoc).where(GoldenDoc.set_name == "mini")).all()
        assert len(docs) == 6 and all(d.labels["parties"] for d in docs)
    good = create_run("mini", split="all", preset="fast", name="baseline", use_cache=False)
    summary = asyncio.run(run_eval(good, deps=_deps(_responder_for_set(specs))))
    assert summary["quality"] == 1.0, json.dumps(summary["fields"], indent=1)[:2000]
    assert summary["doc_level_critical_accuracy"] == 1.0

    # a "prompt change" that breaks the notice period on every document
    def broken(cited):
        if cited.get("value"):
            cited = {**cited, "value": {"amount": cited["value"]["amount"] + 7, "unit": cited["value"]["unit"]}}
        return cited

    bad = create_run("mini", split="all", preset="fast", name="candidate", use_cache=False)
    asyncio.run(run_eval(bad, deps=_deps(_responder_for_set(specs, mistakes={"non_renewal_notice": broken}))))
    report = compare_runs(good, bad, max_drop=0.1)
    rows = {r["field"]: r for r in report["fields"]}
    assert not report["passed"]
    assert rows["non_renewal_notice_days"]["fail"] and rows["non_renewal_notice_days"]["broke"]
    assert not rows["governing_law"]["fail"]
    same = compare_runs(good, good)
    assert same["passed"]


def test_canary_is_a_stratified_subset():
    _import(9)
    with session_scope() as s:
        for g in s.scalars(select(GoldenDoc)):
            g.split = "dev"
    n = golden.make_canary("mini", per_language=1)
    assert 1 <= n <= 3
