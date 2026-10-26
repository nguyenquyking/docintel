"""Run an extraction configuration over a golden set and record everything needed to
compare it with any other run: per-field outcomes per document (for paired tests),
cost, latency, confidence (for calibration), evidence pages (for retrieval recall)."""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from ..db import session_scope
from ..extraction.confidence import isotonic_fit
from ..extraction.extractor import ExtractConfig
from ..llm.client import current_document, current_run
from ..models import EvalDoc, EvalItem, EvalRun, Extraction, GoldenDoc, Page, Setting, now
from ..pipeline.runner import CALIBRATION_KEY, Deps, load_calibration, parse_and_index, run_extraction
from ..text.normalize import normalize_for_match, strip_accents
from .metrics import CRITICAL_EVAL_FIELDS, EVAL_FIELDS, EVAL_TO_RAW, FieldStats, compare
from .stats import percentile, reliability

EVAL_CONCURRENCY = 3


def create_run(set_name: str, split: str = "test", preset: str = "cascade", name: str | None = None,
               overrides: dict[str, Any] | None = None, use_cache: bool = True) -> str:
    config = ExtractConfig.preset(preset, **(overrides or {}))
    config.use_cache = use_cache
    with session_scope() as s:
        q = select(GoldenDoc).where(GoldenDoc.set_name == set_name)
        if split != "all":
            q = q.where(GoldenDoc.split == split)
        n = len(s.scalars(q).all())
        if n == 0:
            raise ValueError(f"golden set {set_name!r} has no documents in split {split!r}")
        run = EvalRun(set_name=set_name, split=split, name=name or f"{preset} on {set_name}/{split}",
                      config={**config.__dict__, "preset": preset}, config_key=config.key(), n_docs=n)
        s.add(run)
        s.flush()
        return run.id


def _ocr_scores(page_texts_path: str | None, doc_id: str) -> dict[str, Any] | None:
    """Bag-of-words F1 of parsed text vs ground truth, exact and accent-folded: the gap
    between the two is diacritics lost by OCR. Reading order does not matter here."""
    if not page_texts_path or not Path(page_texts_path).exists():
        return None
    truth = json.loads(Path(page_texts_path).read_text(encoding="utf-8"))
    with session_scope() as s:
        pages = s.scalars(select(Page).where(Page.document_id == doc_id).order_by(Page.page_no)).all()
        parsed = [(p.route, p.text) for p in pages]
    if not parsed or all(route == "text_layer" for route, _ in parsed):
        return None

    def f1(a: list[str], b: list[str]) -> float:
        from collections import Counter
        ca, cb = Counter(a), Counter(b)
        overlap = sum((ca & cb).values())
        if not a or not b:
            return 0.0
        p, r = overlap / len(a), overlap / len(b)
        return 2 * p * r / (p + r) if p + r else 0.0

    exact, folded, routes = [], [], defaultdict(int)
    for (route, text), gt in zip(parsed, truth, strict=False):
        if route == "text_layer":
            continue
        routes[route] += 1
        got, want = normalize_for_match(text).split(), normalize_for_match(gt).split()
        exact.append(f1(got, want))
        folded.append(f1([strip_accents(w) for w in got], [strip_accents(w) for w in want]))
    if not exact:
        return None
    return {"word_f1": round(sum(exact) / len(exact), 4), "word_f1_folded": round(sum(folded) / len(folded), 4),
            "pages": len(exact), "routes": dict(routes)}


async def _eval_doc(run_id: str, g: GoldenDoc, config: ExtractConfig, deps: Deps, calibration) -> None:
    token = current_document.set(g.document_id)
    try:
        ir, chunks, emb = await parse_and_index(g.document_id, deps)
        ex_id, outcome = await run_extraction(g.document_id, ir, chunks, emb, config, deps, purpose=f"eval:{run_id}",
                                              calibration=calibration)
    except Exception as exc:
        with session_scope() as s:
            s.add(EvalDoc(run_id=run_id, golden_doc_id=g.id, error=f"{type(exc).__name__}: {exc}"[:2000]))
            run = s.get(EvalRun, run_id)
            run.done_docs += 1
        return
    finally:
        current_document.reset(token)
    fields = {f.field: f for f in outcome.fields}
    items, crit_ok = [], True
    for name in g.labeled_fields:
        ftype = EVAL_FIELDS.get(name)
        if ftype is None:
            continue
        o = compare(ftype, g.labels.get(name), outcome.facts.get(name), g.labels.get("_options"))
        raw = EVAL_TO_RAW[name]
        if name == "initial_term_end" and fields.get("expiration_date") and fields["expiration_date"].value is None:
            raw = "initial_term_months"
        fr = fields.get(raw)
        gold_page = (g.evidence_pages or {}).get(name)
        cited = next((e.page for e in fr.evidence if e.page), None) if fr else None
        group = fr.group if fr else None
        selected = (outcome.selected_pages.get(group) or {}).get("pages") if group else None
        items.append(EvalItem(
            run_id=run_id, golden_doc_id=g.id, field=name, gold=g.labels.get(name), pred=outcome.facts.get(name),
            outcome=o.outcome, correct=o.correct, tp=o.tp, fp=o.fp, fn=o.fn,
            confidence=fr.confidence if fr else None,
            evidence_page_ok=(cited == gold_page) if gold_page and cited else None,
            retrieval_hit=(gold_page in selected) if gold_page and selected else None))
        if name in CRITICAL_EVAL_FIELDS and not o.correct:
            crit_ok = False
    ocr = _ocr_scores(g.page_texts_path, g.document_id)
    with session_scope() as s:
        s.add_all(items)
        s.add(EvalDoc(run_id=run_id, golden_doc_id=g.id, extraction_id=ex_id, cost_usd=outcome.stats.cost_usd,
                      latency_ms=outcome.latency_ms, critical_correct=crit_ok))
        run = s.get(EvalRun, run_id)
        run.done_docs += 1
        if ocr:
            run.metrics = {**(run.metrics or {}), "ocr_docs": {**(run.metrics or {}).get("ocr_docs", {}), g.id: ocr}}


async def run_eval(run_id: str, job_id: str | None = None, deps: Deps | None = None,
                   fit_calibration: bool = False) -> dict[str, Any]:
    deps = deps or Deps.default()
    with session_scope() as s:
        run = s.get(EvalRun, run_id)
        if run is None:
            raise ValueError(f"eval run {run_id} not found")
        cfg = {k: v for k, v in run.config.items() if k != "preset"}
        config = ExtractConfig(**cfg)
        q = select(GoldenDoc).where(GoldenDoc.set_name == run.set_name)
        if run.split != "all":
            q = q.where(GoldenDoc.split == run.split)
        goldens = list(s.scalars(q.order_by(GoldenDoc.external_id)))
        run.status, run.job_id, run.done_docs, run.error = "running", job_id, 0, None
        run.metrics = {}
        s.execute(delete(EvalItem).where(EvalItem.run_id == run_id))
        s.execute(delete(EvalDoc).where(EvalDoc.run_id == run_id))
    token = current_run.set(run_id)
    calibration = None if fit_calibration else load_calibration()
    sem = asyncio.Semaphore(EVAL_CONCURRENCY)

    async def one(g: GoldenDoc) -> None:
        async with sem:
            await _eval_doc(run_id, g, config, deps, calibration)

    try:
        await asyncio.gather(*(one(g) for g in goldens))
        summary = summarize(run_id)
        with session_scope() as s:
            run = s.get(EvalRun, run_id)
            run.metrics = {**summary, "ocr_docs": (run.metrics or {}).get("ocr_docs", {})}
            run.quality = summary["quality"]
            run.cost_per_1k = summary["cost_per_1k_docs"]
            run.latency_p50_ms = summary["latency_ms"]["p50"]
            run.latency_p95_ms = summary["latency_ms"]["p95"]
            run.status, run.finished_at = "done", now()
        if fit_calibration:
            save_calibration(run_id)
        return summary
    except Exception as exc:
        with session_scope() as s:
            run = s.get(EvalRun, run_id)
            run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
        raise
    finally:
        current_run.reset(token)


def summarize(run_id: str) -> dict[str, Any]:
    with session_scope() as s:
        run = s.get(EvalRun, run_id)
        items = s.scalars(select(EvalItem).where(EvalItem.run_id == run_id)).all()
        docs = s.scalars(select(EvalDoc).where(EvalDoc.run_id == run_id)).all()
        goldens = {g.id: g for g in s.scalars(select(GoldenDoc).where(GoldenDoc.set_name == run.set_name))}
        ocr_docs = (run.metrics or {}).get("ocr_docs", {})
        modes = defaultdict(int)
        for d in docs:
            if d.extraction_id:
                ex = s.get(Extraction, d.extraction_id)
                if ex:
                    modes[ex.mode] += 1
                    for g in ex.escalated_groups or []:
                        modes[f"escalated:{g}"] += 1

    per_field: dict[str, FieldStats] = defaultdict(FieldStats)
    slices: dict[str, dict[str, FieldStats]] = defaultdict(lambda: defaultdict(FieldStats))
    from .metrics import FieldOutcome
    for it in items:
        o = FieldOutcome(it.outcome, it.correct, it.tp, it.fp, it.fn)
        per_field[it.field].add(o)
        if it.field in CRITICAL_EVAL_FIELDS:
            g = goldens.get(it.golden_doc_id)
            strata = g.strata if g else {}
            for key in ("language", "length", "scanned", "doc_type", "has_table"):
                if key in strata:
                    slices[key][str(strata[key])].add(o)
            for tag in strata.get("tags", []):
                slices["tag"][tag].add(o)

    crit_acc = [per_field[f].correct / per_field[f].n for f in CRITICAL_EVAL_FIELDS if per_field[f].n]
    ok_docs = [d for d in docs if d.error is None]
    costs = [d.cost_usd for d in ok_docs]
    lat = [d.latency_ms for d in ok_docs]
    conf_points = [(it.confidence, it.correct) for it in items if it.confidence is not None]
    ece, bins = reliability(conf_points)
    retr = [it.retrieval_hit for it in items if it.retrieval_hit is not None]
    evid = [it.evidence_page_ok for it in items if it.evidence_page_ok is not None]
    ocr_vals = list(ocr_docs.values())
    by_lang: dict[str, list] = defaultdict(list)
    for gid, v in ocr_docs.items():
        lang = (goldens.get(gid).strata if goldens.get(gid) else {}).get("language", "?")
        by_lang[lang].append(v)
    return {
        "docs": len(docs), "failed_docs": len(docs) - len(ok_docs),
        "quality": round(sum(crit_acc) / len(crit_acc), 4) if crit_acc else None,
        "doc_level_critical_accuracy": round(sum(1 for d in ok_docs if d.critical_correct) / len(docs), 4) if docs else None,
        "fields": {f: per_field[f].summary() for f in EVAL_FIELDS if f in per_field},
        "slices": {k: {v: {"n": st.n, "accuracy": round(st.correct / st.n, 4) if st.n else None}
                       for v, st in sorted(vals.items())} for k, vals in slices.items()},
        "cost_per_1k_docs": round(1000 * sum(costs) / len(costs), 4) if costs else None,
        "cost_total_usd": round(sum(costs), 6),
        "latency_ms": {"p50": percentile(lat, 0.5), "p95": percentile(lat, 0.95)},
        "calibration": {"ece": ece, "bins": bins, "n": len(conf_points)},
        "evidence_recall": round(sum(retr) / len(retr), 4) if retr else None,
        "evidence_page_accuracy": round(sum(evid) / len(evid), 4) if evid else None,
        "modes": dict(modes),
        "ocr": {lang: {"docs": len(v), "word_f1": round(sum(x["word_f1"] for x in v) / len(v), 4),
                       "word_f1_folded": round(sum(x["word_f1_folded"] for x in v) / len(v), 4)}
                for lang, v in by_lang.items()} if ocr_vals else {},
    }


def save_calibration(run_id: str) -> dict[str, Any]:
    """Isotonic map raw confidence → P(correct), per extracted field, from an eval run."""
    with session_scope() as s:
        items = s.scalars(select(EvalItem).where(EvalItem.run_id == run_id)).all()
        docs = {d.golden_doc_id: d.extraction_id for d in s.scalars(select(EvalDoc).where(EvalDoc.run_id == run_id))}
        raw_conf: dict[tuple[str, str], float] = {}
        for gid, ex_id in docs.items():
            ex = s.get(Extraction, ex_id) if ex_id else None
            for f in (ex.fields if ex else []):
                raw_conf[(gid, f["field"])] = f.get("raw_confidence", f.get("confidence", 0.0))
    points: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for it in items:
        raw = EVAL_TO_RAW.get(it.field)
        if raw and (it.golden_doc_id, raw) in raw_conf and it.field not in ("is_executed", "value_period", "notice_deadline"):
            points[raw].append((raw_conf[(it.golden_doc_id, raw)], it.correct))
    table = {f: isotonic_fit(pts) for f, pts in points.items() if len(pts) >= 15}
    table["*"] = isotonic_fit([p for pts in points.values() for p in pts])
    with session_scope() as s:
        row = s.get(Setting, CALIBRATION_KEY)
        value = {**table, "_source_run": run_id}
        if row:
            row.value = value
        else:
            s.add(Setting(key=CALIBRATION_KEY, value=value))
    return table
