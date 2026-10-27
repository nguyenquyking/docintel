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
import os
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
    # a stalled mirror must fail fast rather than hang an import for minutes
    with httpx.stream("GET", CUAD_URL, follow_redirects=True, timeout=httpx.Timeout(60, read=90)) as r:
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


def _clean_party(p: str) -> str:
    """CUAD lists party *mentions*: 'X together with Y', 'X and its affiliates', '(“Licensor”)'.
    Keep the legal name the mention starts with."""
    p = re.sub(r"\(.*?\)|“.*?”|\".*?\"", "", p)
    p = re.split(r"(?i)\s+together with\s+", p)[0]
    p = re.sub(r"(?i),?\s+(?:and|including)\s+(?:its|their)\s+(?:successors|affiliates|subsidiaries|assigns).*$", "", p)
    p = re.sub(r"(?i),?\s+if any$", "", p)
    return re.sub(r"\s+", " ", p).strip(" ,;.")


def map_row(row: dict[str, str]) -> tuple[dict, list[str]]:
    from ..text.normalize import norm_org

    labels: dict = {}
    parties, seen = [], set()
    for raw in (row.get("Parties-Answer") or "").split(";"):
        name = _clean_party(raw)
        key = norm_org(name)
        if re.search(r"(?i)\b(?:shall|hereinafter|referred to|collectively|individually)\b", name):
            continue                                   # a defined-term sentence, not a party
        if re.fullmatch(r"(?i)[\w.-]+ and [\w.-]+", name):
            continue                                   # "Miltenyi and Bellicum": the two aliases together
        if len(name) > 2 and key and key not in seen:
            seen.add(key)
            parties.append(name)
    if parties:
        labels["parties"] = parties
    eff = _us_date(row.get("Effective Date-Answer", "")) or _us_date(row.get("Agreement Date-Answer", ""))
    if eff:
        labels["effective_date"] = eff
    exp = (row.get("Expiration Date-Answer") or "").strip()
    if _us_date(exp):
        labels["initial_term_end"] = _us_date(exp)
    renewal = (row.get("Renewal Term-Answer") or "").strip()
    clause = (row.get("Renewal Term") or "").strip("[]' ").lower()
    notice = (row.get("Notice Period To Terminate Renewal- Answer") or row.get("Notice Period To Terminate Renewal-Answer") or "").strip()
    # CUAD's "Renewal Term" also covers renewal *options*; ours is automatic renewal. Decide from
    # CUAD's own clause text: automatic → yes; an option, a mutual decision or a request → no;
    # otherwise leave the field unlabeled.
    automatic = re.search(r"automatic|shall (?:be )?(?:automatically )?(?:renew|extend)|will (?:automatically )?renew|"
                          r"renew(?:s|ed)? (?:automatically|for successive)|unless (?:either|one) party", clause)
    optional = re.search(r"option|may (?:extend|renew|elect)|upon mutual|mutually agree|by mutual|request|"
                         r"if (?:the parties|both parties) agree|elect to renew", clause)
    if not renewal:
        if not notice:
            labels["auto_renewal"] = False
    elif automatic and not optional:
        labels["auto_renewal"] = True
        months = _duration_months(renewal)
        if months:
            labels["renewal_term_months"] = months
        if notice and _days(notice):
            labels["non_renewal_notice_days"] = _days(notice)
    elif optional and not automatic:
        labels["auto_renewal"] = False
    law = (row.get("Governing Law-Answer") or "").strip()
    first = law.split(";")[0].strip()
    if first and not re.search(r"(?i)\bin which\b|\bact\b|\bwhere\b", first):
        labels["governing_law"] = first
    tfc = (row.get("Termination For Convenience-Answer") or "").strip().lower()
    if tfc in ("yes", "no"):
        labels["termination_for_convenience"] = tfc == "yes"
    labels["_options"] = {"date_tolerance_days": 1}
    return labels, [k for k in labels if not k.startswith("_")]


def import_cuad(name: str = "cuad", n: int = 25, max_pages: int = 60, seed_stride: int = 7) -> int:
    settings = get_settings()
    local = os.environ.get("DOCINTEL_CUAD_ZIP")      # an already-downloaded CUAD_v1.zip
    archive = Path(local) if local else _download(settings.data_dir / "datasets" / "cuad" / "CUAD_v1.zip")
    ensure_set(name, "cuad", "Real contracts from CUAD v1 (The Atticus Project, CC BY 4.0) with expert labels")
    storage = Storage(settings)
    added = 0
    with zipfile.ZipFile(archive) as zf:
        # CUAD's CSV and archive disagree on extension case ("….PDF" vs "….pdf"): match case-insensitively
        pdfs = {Path(p).name.lower(): p for p in zf.namelist() if p.lower().endswith(".pdf")}
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
                path = pdfs.get(fname.lower())
                ext_id = f"cuad-{fname}"           # full name: several filings share long prefixes
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


def relabel_cuad(name: str = "cuad") -> int:
    """Recompute labels for an imported CUAD set after a mapping change (documents unchanged)."""
    from sqlalchemy import select
    settings = get_settings()
    local = os.environ.get("DOCINTEL_CUAD_ZIP")
    archive = Path(local) if local else _download(settings.data_dir / "datasets" / "cuad" / "CUAD_v1.zip")
    with zipfile.ZipFile(archive) as zf:
        rows = {r["Filename"].lower(): r for r in
                csv.DictReader(io.TextIOWrapper(zf.open("CUAD_v1/master_clauses.csv"), encoding="utf-8"))}
    from ..models import Document
    n = 0
    with session_scope() as s:
        for g in s.scalars(select(GoldenDoc).where(GoldenDoc.set_name == name)):
            doc = s.get(Document, g.document_id)
            row = rows.get(doc.filename.lower()) if doc else None   # the exact PDF name, case-insensitive
            if row is None:
                continue
            g.labels, g.labeled_fields = map_row(row)
            g.external_id = f"cuad-{row['Filename']}"
            n += 1
    return n
