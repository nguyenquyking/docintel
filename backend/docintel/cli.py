"""Command line: run the service, the worker, golden sets, evals and the regression gate.

    python -m docintel serve                    # API + worker + built frontend
    python -m docintel golden synthetic --n 60  # generate + import the synthetic golden set
    python -m docintel eval run --preset cascade --split test --wait
    python -m docintel eval gate BASE_RUN CANDIDATE_RUN   # exit code 1 on regression (CI)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import typer

from .db import init_db, session_scope

app = typer.Typer(add_completion=False, help="DocIntel — agreement intelligence")
golden_app = typer.Typer(help="Golden sets")
eval_app = typer.Typer(help="Evaluation runs and the regression gate")
app.add_typer(golden_app, name="golden")
app.add_typer(eval_app, name="eval")


def _setup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()


@app.command()
def serve(host: str | None = None, port: int | None = None, reload: bool = False) -> None:
    """Run the API (and, unless DOCINTEL_RUN_WORKER_IN_API=false, the worker)."""
    import uvicorn

    from .settings import get_settings
    s = get_settings()
    _setup()
    uvicorn.run("docintel.api.app:app", host=host or s.api_host, port=port or s.api_port, reload=reload)


@app.command()
def worker(concurrency: int | None = None) -> None:
    """Run a standalone worker (production: as many replicas as the rate limit allows)."""
    from .queue.worker import Worker
    _setup()
    asyncio.run(Worker(concurrency=concurrency).run())


@app.command()
def ingest(paths: list[Path], lane: str = "backfill") -> None:
    """Queue PDF files or folders (backfill lane by default)."""
    from .services import ingest as do_ingest
    _setup()
    files = [p for path in paths for p in ([path] if path.is_file() else sorted(path.rglob("*.pdf")))]
    with session_scope() as s:
        for f in files:
            doc, dup = do_ingest(s, f.read_bytes(), f.name, lane=lane)
            typer.echo(f"{doc.id}  {'duplicate' if dup else 'new      '}  {f}")
    typer.echo(f"queued {len(files)} file(s) on the {lane} lane")


@app.command()
def process(path: Path, preset: str = "cascade") -> None:
    """Process one PDF synchronously and print its facts (debugging)."""
    from .extraction.extractor import ExtractConfig
    from .models import Document
    from .pipeline.runner import process_document
    from .services import ingest as do_ingest
    _setup()
    with session_scope() as s:
        doc, _ = do_ingest(s, path.read_bytes(), path.name, enqueue_job=False)
        doc_id = doc.id
    asyncio.run(process_document(doc_id, config=ExtractConfig.preset(preset), force=True))
    with session_scope() as s:
        d = s.get(Document, doc_id)
        typer.echo(json.dumps(d.facts, indent=2, ensure_ascii=False))


@app.command()
def demo(n: int = 12, seed: int = 2026, upload_url: str | None = None) -> None:
    """Generate sample contracts — some with cancel-by dates in the coming weeks — and queue them.
    With --upload-url (e.g. http://127.0.0.1:8077) they go through the HTTP API like a user upload."""
    from datetime import date

    import httpx

    from .extraction.derive import derive_facts
    from .services import ingest as do_ingest
    from .settings import get_settings
    from .synth.generator import build_document, find_spec, labels_for, render_pdf

    _setup()
    today = date.today()

    def soon(lo: int, hi: int):
        def ok(spec) -> bool:
            lab = labels_for(spec)
            if not (spec.auto_renewal and lab["initial_term_end"] and "draft" not in spec.tags):
                return False
            notice = {"amount": spec.notice_amount, "unit": spec.notice_unit}
            facts = derive_facts({"effective_date": lab["effective_date"], "expiration_type": spec.expiration_type,
                                  "expiration_date": spec.fixed_end.isoformat() if spec.fixed_end else None,
                                  "initial_term_months": spec.term_months, "auto_renewal": True,
                                  "renewal_term_months": spec.renewal_months, "non_renewal_notice": notice,
                                  "signatories": [{"name": "x", "is_signed": True}]}, today=today)
            d = facts.get("next_notice_deadline")
            return bool(d) and lo <= (date.fromisoformat(d) - today).days <= hi
        return ok

    plan = [("en", "services_agreement", "short", (), soon(5, 28)), ("en", "services_agreement", "medium", (), soon(40, 85)),
            ("vi", "lease", "short", (), soon(10, 60)), ("bilingual", "services_agreement", "short", (), None),
            ("en", "nda", "short", ("draft",), None), ("en", "purchase_agreement", "short", (), None),
            ("vi", "services_agreement", "medium", (), None), ("en", "services_agreement", "long", ("amendment",), None),
            ("en", "lease", "short", ("redline",), soon(-20, -1)), ("vi", "purchase_agreement", "short", (), None),
            ("en", "nda", "short", ("scan",), None), ("vi", "lease", "short", ("scan",), soon(90, 200))]
    out_dir = get_settings().data_dir / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    import random

    from .synth.scan import to_scanned_pdf
    for k, (lang, typ, length, tags, req) in enumerate(plan[:n]):
        scanned = "scan" in tags
        tags = tuple(t for t in tags if t != "scan")
        spec = find_spec(lang, typ, length, scanned=scanned, tags=tags, require=req, start_seed=seed + k, tries=4000)
        data = render_pdf(build_document(spec))
        if scanned:
            data = to_scanned_pdf(data, random.Random(seed + k))
        path = out_dir / f"demo-{k + 1:02d}-{lang}-{typ}{'-scan' if scanned else ''}.pdf"
        path.write_bytes(data)
        files.append(path)
    if upload_url:
        r = httpx.post(f"{upload_url.rstrip('/')}/api/documents", data={"lane": "interactive"}, timeout=120,
                       files=[("files", (f.name, f.read_bytes(), "application/pdf")) for f in files])
        r.raise_for_status()
        typer.echo(json.dumps(r.json(), indent=1))
    else:
        with session_scope() as s:
            for f in files:
                doc, _ = do_ingest(s, f.read_bytes(), f.name, lane="interactive")
                typer.echo(f"{doc.id}  {f.name}")
    typer.echo(f"queued {len(files)} demo contracts")


@golden_app.command("synthetic")
def golden_synthetic(name: str = "synthetic-v1", n: int = 60, seed: int = 7, canary: bool = True) -> None:
    """Generate the synthetic golden set (PDFs + labels) and import it."""
    from .eval.golden import import_synthetic, make_canary
    _setup()
    t = time.time()
    added = import_synthetic(name, n, seed)
    typer.echo(f"imported {added} documents into {name} in {time.time() - t:.1f}s")
    if canary:
        typer.echo(f"canary set: {make_canary(name)} documents")


@golden_app.command("cuad")
def golden_cuad(name: str = "cuad", n: int = 25, relabel: bool = False) -> None:
    """Import real contracts from CUAD (CC BY 4.0) with their expert labels (--relabel: remap only)."""
    from .datasets.cuad import import_cuad, relabel_cuad
    _setup()
    if relabel:
        typer.echo(f"relabeled {relabel_cuad(name)} CUAD contracts in {name}")
    else:
        typer.echo(f"imported {import_cuad(name, n)} CUAD contracts into {name}")


@golden_app.command("list")
def golden_list() -> None:
    from .eval.golden import set_summary
    _setup()
    typer.echo(json.dumps(set_summary(), indent=2, ensure_ascii=False))


@eval_app.command("run")
def eval_run(set_name: str = "synthetic-v1", split: str = "test", preset: str = "cascade",
             name: str | None = None, no_cache: bool = False, calibrate: bool = False) -> None:
    """Run an eval in this process and print the summary."""
    from .eval.runner import create_run, run_eval
    _setup()
    run_id = create_run(set_name, split, preset, name, use_cache=not no_cache)
    typer.echo(f"run {run_id}: {preset} on {set_name}/{split}")
    summary = asyncio.run(run_eval(run_id, fit_calibration=calibrate))
    typer.echo(json.dumps({k: summary[k] for k in ("quality", "doc_level_critical_accuracy", "cost_per_1k_docs",
                                                   "latency_ms", "evidence_recall", "calibration", "modes")},
                          indent=2, default=str))
    typer.echo(f"run id: {run_id}")


@eval_app.command("report")
def eval_report(set_name: str = "synthetic-v1", split: str = "test", latest: bool = True) -> None:
    """Markdown table of finished runs on one set/split (latest run per name)."""
    from sqlalchemy import select

    from .models import EvalRun
    _setup()
    with session_scope() as s:
        runs = s.scalars(select(EvalRun).where(EvalRun.set_name == set_name, EvalRun.split == split,
                                               EvalRun.status == "done").order_by(EvalRun.created_at)).all()
        rows: dict[str, EvalRun] = {}
        for r in runs:
            rows[r.name if latest else r.id] = r
        typer.echo("| Configuration | Critical-field accuracy | Documents fully right | $ / 1k docs | p50 latency |")
        typer.echo("|---|---|---|---|---|")
        for r in rows.values():
            m = r.metrics or {}
            p50 = r.latency_p50_ms or 0
            typer.echo(f"| {r.name} | {r.quality:.1%} | {m.get('doc_level_critical_accuracy', 0):.1%} | "
                       f"${r.cost_per_1k:.2f} | {p50 / 1000:.1f} s |")


@eval_app.command("calibrate")
def eval_calibrate(run_ids: list[str]) -> None:
    """Refit confidence calibration from one or more finished runs (dev split, real documents)."""
    from .eval.runner import save_calibration
    _setup()
    table = save_calibration(run_ids)
    typer.echo(f"calibration fitted on {len(run_ids)} run(s): {sorted(k for k in table if k != '*')}")


@eval_app.command("gate")
def eval_gate(base: str, candidate: str, max_drop: float = 0.03, alpha: float = 0.05,
              max_cost_increase: float | None = None) -> None:
    """Compare two runs; exit 1 if the candidate regresses (use in CI)."""
    from .eval.gate import compare_runs
    _setup()
    r = compare_runs(base, candidate, alpha=alpha, max_drop=max_drop, max_cost_increase=max_cost_increase)
    for row in r["fields"]:
        mark = "FAIL" if row["fail"] else "ok  "
        typer.echo(f"{mark} {row['field']:28} {row['base_accuracy']:.1%} -> {row['cand_accuracy']:.1%} "
                   f"Δ{row['delta']:+.1%}  p={row['p_value']:.3g}  broke={len(row['broke'])} fixed={len(row['fixed'])}")
    if r["cost_note"]:
        typer.echo(r["cost_note"])
    typer.echo("GATE PASSED" if r["passed"] else "GATE FAILED")
    sys.exit(0 if r["passed"] else 1)


if __name__ == "__main__":
    app()
