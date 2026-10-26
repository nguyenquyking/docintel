"""CUAD — real commercial contracts with expert labels, as a real-world golden slice.

The Contract Understanding Atticus Dataset (CUAD v1, The Atticus Project, CC BY 4.0):
510 contracts from SEC EDGAR filings, annotated by legal experts for 41 clause
categories, with normalized answers for a subset. Downloaded on demand into the data
directory; never committed. https://www.atticusprojectai.org/cuad

Mapped to our fields only where CUAD's answer determines the value:

* parties ← "Parties" (names; role labels in parentheses dropped)
* effective_date ← "Effective Date", else "Agreement Date"
* initial_term_end ← "Expiration Date" when it is a date. CUAD counts "5 years after
  2/10/2014" as 2/10/2019 where our guideline says 2/9/2019, so these labels carry a
  1-day tolerance.
* auto_renewal / renewal_term_months ← "Renewal Term"; non_renewal_notice_days ←
  "Notice Period To Terminate Renewal"; governing_law; termination_for_convenience.

Fields CUAD leaves undetermined stay unlabeled and are not graded.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pypdfium2 as pdfium

from ..db import session_scope
from ..eval.golden import ensure_set
from ..models import GoldenDoc
from ..services import ingest
from ..settings import get_settings
from ..storage import Storage

CUAD_URL = "https://zenodo.org/records/4595826/files/CUAD_v1.zip?download=1"


def _download(target: Path) -> Path:
    if target.exists() and target.stat().st_size > 50_000_000:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    with httpx.stream("GET", CUAD_URL, follow_redirects=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
    tmp.replace(target)
    return target


def _us_date(s: str) -> str | None:
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*", s or "")
    if not m:
        return None
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000 if y < 50 else 1900
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _duration_months(s: str) -> int | None:
    m = re.search(r"(\d+)\s*(year|month|day)", (s or "").lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * 12 if unit == "year" else n if unit == "month" else None


def _days(s: str) -> int | None:
    m = re.search(r"(\d+)\s*(day|month|week)", (s or "").lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * {"day": 1, "week": 7, "month": 30}[unit]


def map_row(row: dict[str, str]) -> tuple[dict, list[str]]:
    labels: dict = {}
    parties = [re.sub(r"\(.*?\)|“.*?”", "", p).strip(" ,;") for p in (row.get("Parties-Answer") or "").split(";")]
    parties = [p for p in parties if len(p) > 2]
    if parties:
        labels["parties"] = parties
    eff = _us_date(row.get("Effective Date-Answer", "")) or _us_date(row.get("Agreement Date-Answer", ""))
    if eff:
        labels["effective_date"] = eff
    exp = (row.get("Expiration Date-Answer") or "").strip()
    if _us_date(exp):
        labels["initial_term_end"] = _us_date(exp)
    renewal = (row.get("Renewal Term-Answer") or "").strip()
    notice = (row.get("Notice Period To Terminate Renewal- Answer") or row.get("Notice Period To Terminate Renewal-Answer") or "").strip()
    if not renewal:
        labels["auto_renewal"] = False if not notice else None
        if labels["auto_renewal"] is None:
            labels.pop("auto_renewal")
    elif "perpetual" not in renewal.lower():
        labels["auto_renewal"] = True
        months = _duration_months(renewal)
        if months:
            labels["renewal_term_months"] = months
        if notice and _days(notice):
            labels["non_renewal_notice_days"] = _days(notice)
    law = (row.get("Governing Law-Answer") or "").strip()
    if law:
        labels["governing_law"] = law
    tfc = (row.get("Termination For Convenience-Answer") or "").strip().lower()
    if tfc in ("yes", "no"):
        labels["termination_for_convenience"] = tfc == "yes"
    labels["_options"] = {"date_tolerance_days": 1}
    return labels, [k for k in labels if not k.startswith("_")]


def import_cuad(name: str = "cuad", n: int = 25, max_pages: int = 60, seed_stride: int = 7) -> int:
    settings = get_settings()
    archive = _download(settings.data_dir / "datasets" / "cuad" / "CUAD_v1.zip")
    ensure_set(name, "cuad", "Real contracts from CUAD v1 (The Atticus Project, CC BY 4.0) with expert labels")
    storage = Storage(settings)
    added = 0
    with zipfile.ZipFile(archive) as zf:
        pdfs = {Path(p).name: p for p in zf.namelist() if p.lower().endswith(".pdf")}
        rows = list(csv.DictReader(io.TextIOWrapper(zf.open("CUAD_v1/master_clauses.csv"), encoding="utf-8")))
        rows.sort(key=lambda r: r["Filename"])
        picked = rows[::seed_stride] + rows[1::seed_stride]
        with session_scope() as s:
            from sqlalchemy import select
            existing = {g.external_id for g in s.scalars(select(GoldenDoc).where(GoldenDoc.set_name == name))}
            for row in picked:
                if added >= n:
                    break
                fname = row["Filename"]
                path = pdfs.get(fname) or next((p for b, p in pdfs.items() if b.startswith(fname[:60])), None)
                ext_id = f"cuad-{fname[:80]}"
                if path is None or ext_id in existing:
                    continue
                data = zf.read(path)
                doc_pdf = pdfium.PdfDocument(data)
                pages = len(doc_pdf)
                doc_pdf.close()
                if pages > max_pages:
                    continue
                labels, labeled = map_row(row)
                if len(labeled) < 3:
                    continue
                doc, _ = ingest(s, data, Path(path).name, source=f"golden:{name}", lane="backfill", enqueue_job=False,
                                storage=storage)
                category = Path(path).parent.name
                s.add(GoldenDoc(set_name=name, document_id=doc.id, external_id=ext_id, split="test", labels=labels,
                                labeled_fields=labeled, evidence_pages={},
                                strata={"language": "en", "doc_type": category, "scanned": False, "tags": [],
                                        "length": "long" if pages > 40 else "medium" if pages > 5 else "short",
                                        "pages": pages, "source": "cuad"}))
                added += 1
    return added
