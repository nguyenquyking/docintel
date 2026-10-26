"""Golden sets: where labels come from.

* ``synthetic`` — generated contracts with exact labels and evidence pages; controllable
  coverage of the hard cases (see ``synth/generator.py``).
* ``cuad`` — real commercial contracts from the CUAD dataset (CC BY 4.0, expert
  annotations), mapped to the fields CUAD labels; downloaded on demand, never committed.
* ``reviewed`` — production documents a human reviewed: every approved or corrected
  field becomes a label. This is how the golden set grows from real traffic.
* ``canary`` — a small stratified subset re-run on a schedule to detect silent
  provider-side model changes.

A golden document only carries labels for the fields it actually labels
(``labeled_fields``); metrics are computed over labeled fields only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..models import Document, GoldenDoc, GoldenSet
from ..services import ingest, latest_reviews
from ..settings import get_settings
from ..storage import Storage
from .metrics import EVAL_FIELDS


def ensure_set(name: str, source: str, description: str = "") -> None:
    with session_scope() as s:
        if s.get(GoldenSet, name) is None:
            s.add(GoldenSet(name=name, source=source, description=description))


def import_synthetic(name: str = "synthetic-v1", n: int = 60, seed: int = 7, out_dir: Path | None = None,
                     scanned_rate: float = 0.25) -> int:
    from ..synth.generator import generate_set

    settings = get_settings()
    out_dir = out_dir or settings.data_dir / "golden" / name
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        generate_set(out_dir, n=n, seed=seed, name=name, scanned_rate=scanned_rate)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ensure_set(name, "synthetic", f"{len(manifest)} generated contracts (seed {seed}): EN/VI/bilingual, short to "
                                  "130 pages, scans, amendments, redlines, drafts")
    storage = Storage(settings)
    added = 0
    with session_scope() as s:
        existing = {g.external_id for g in s.scalars(select(GoldenDoc).where(GoldenDoc.set_name == name))}
        for item in manifest:
            if item["external_id"] in existing:
                continue
            data = (out_dir / "docs" / item["filename"]).read_bytes()
            doc, _ = ingest(s, data, item["filename"], source=f"golden:{name}", lane="backfill", enqueue_job=False,
                            storage=storage)
            s.add(GoldenDoc(set_name=name, document_id=doc.id, external_id=item["external_id"], split=item["split"],
                            labels=item["labels"], labeled_fields=list(EVAL_FIELDS),
                            evidence_pages=item["evidence_pages"], strata=item["strata"],
                            page_texts_path=str(out_dir / item["page_texts_file"])))
            added += 1
    return added


def make_canary(source_set: str = "synthetic-v1", name: str = "canary", per_language: int = 2) -> int:
    """A small, stratified, fixed subset: re-run on a schedule, any drop means the model moved."""
    ensure_set(name, "canary", f"stratified subset of {source_set}, re-run on a schedule")
    with session_scope() as s:
        if s.scalars(select(GoldenDoc).where(GoldenDoc.set_name == name)).first():
            return 0
        docs = s.scalars(select(GoldenDoc).where(GoldenDoc.set_name == source_set, GoldenDoc.split == "dev")
                         .order_by(GoldenDoc.external_id)).all()
        picked: dict[str, int] = {}
        n = 0
        for g in docs:
            lang = g.strata.get("language", "?")
            if g.strata.get("length") == "long" or picked.get(lang, 0) >= per_language:
                continue
            picked[lang] = picked.get(lang, 0) + 1
            s.add(GoldenDoc(set_name=name, document_id=g.document_id, external_id=g.external_id, split="all",
                            labels=g.labels, labeled_fields=g.labeled_fields, evidence_pages=g.evidence_pages,
                            strata=g.strata, page_texts_path=g.page_texts_path))
            n += 1
        return n


def promote_reviewed(document_id: str, name: str = "reviewed") -> GoldenDoc:
    """Turn a human-reviewed production document into a golden document. Only fields a
    person approved or corrected become labels — never the model's unreviewed output."""
    from ..eval.metrics import EVAL_TO_RAW
    from ..extraction.derive import derive_facts
    from ..models import Extraction
    from ..services import effective_values

    ensure_set(name, "reviewed", "production documents labeled through human review")
    with session_scope() as s:
        doc = s.get(Document, document_id)
        if doc is None or not doc.latest_extraction_id:
            raise ValueError("document has no extraction to promote")
        reviews = latest_reviews(s, document_id)
        if not reviews:
            raise ValueError("no reviewed fields yet: approve or correct fields first")
        ex = s.get(Extraction, doc.latest_extraction_id)
        values, meta = effective_values(ex, reviews)
        facts = derive_facts(values, meta)
        reviewed_raw = set(reviews)
        labeled = [f for f, raw in EVAL_TO_RAW.items() if raw in reviewed_raw]
        labels = {f: facts.get(f) for f in labeled}
        existing = s.scalars(select(GoldenDoc).where(GoldenDoc.set_name == name,
                                                     GoldenDoc.document_id == document_id)).first()
        strata = {"language": doc.language, "doc_type": facts.get("document_type"),
                  "length": "long" if (doc.page_count or 0) > 40 else "medium" if (doc.page_count or 0) > 5 else "short",
                  "scanned": bool((doc.route_mix or {}).get("ocr") or (doc.route_mix or {}).get("vlm")), "tags": []}
        if existing:
            existing.labels, existing.labeled_fields, existing.strata = labels, labeled, strata
            return existing
        g = GoldenDoc(set_name=name, document_id=document_id, external_id=f"{name}-{document_id}", split="test",
                      labels=labels, labeled_fields=labeled, evidence_pages={}, strata=strata)
        s.add(g)
        s.flush()
        return g


def set_summary() -> list[dict[str, Any]]:
    out = []
    with session_scope() as s:
        for gs in s.scalars(select(GoldenSet).order_by(GoldenSet.created_at)):
            docs = s.scalars(select(GoldenDoc).where(GoldenDoc.set_name == gs.name)).all()
            strata: dict[str, dict[str, int]] = {}
            for g in docs:
                for key in ("language", "length", "doc_type", "scanned"):
                    v = str(g.strata.get(key))
                    strata.setdefault(key, {})[v] = strata.setdefault(key, {}).get(v, 0) + 1
                for tag in g.strata.get("tags", []):
                    strata.setdefault("tags", {})[tag] = strata.setdefault("tags", {}).get(tag, 0) + 1
            splits: dict[str, int] = {}
            for g in docs:
                splits[g.split] = splits.get(g.split, 0) + 1
            out.append({"name": gs.name, "source": gs.source, "description": gs.description, "docs": len(docs),
                        "splits": splits, "strata": strata, "created_at": gs.created_at.isoformat()})
    return out
