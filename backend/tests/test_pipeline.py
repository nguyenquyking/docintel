import asyncio

from sqlalchemy import select

from docintel.db import session_scope
from docintel.extraction.extractor import ExtractConfig
from docintel.llm.client import LLMClient
from docintel.models import Chunk, Document, Extraction, LLMCall, Page
from docintel.pipeline.runner import Deps, process_document
from docintel.retrieval.embeddings import Embedder
from docintel.services import ingest
from docintel.settings import get_settings
from docintel.storage import Storage
from docintel.synth.generator import build_document, find_spec, labels_for, render_pdf

from .oracle import oracle

KEY_FIELDS = ["parties", "is_executed", "effective_date", "initial_term_end", "auto_renewal", "renewal_term_months",
              "non_renewal_notice_days", "notice_deadline", "termination_for_convenience", "contract_value",
              "payment_terms_days", "governing_law"]


def _deps(responder):
    s = get_settings()
    return Deps(s, LLMClient(s, responder=responder), Embedder(), Storage(s))


def _ingest(data: bytes, name="contract.pdf") -> str:
    with session_scope() as s:
        doc, _ = ingest(s, data, name, enqueue_job=False)
        return doc.id


def test_end_to_end_short_document_and_dedup():
    spec = find_spec("en", "services_agreement", tags=("amendment",))
    data = render_pdf(build_document(spec))
    deps = _deps(oracle(spec))
    doc_id = _ingest(data)
    asyncio.run(process_document(doc_id, deps=deps))
    gold = labels_for(spec)
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        assert doc.status in ("ready", "needs_review")
        for k in KEY_FIELDS:
            assert doc.facts[k] == gold[k], (k, doc.facts[k], gold[k])
        ex = s.get(Extraction, doc.latest_extraction_id)
        assert ex.mode == "full_document" and ex.llm_calls == 1
        assert s.scalars(select(Page).where(Page.document_id == doc_id)).first() is not None
        assert s.scalars(select(Chunk).where(Chunk.document_id == doc_id)).first() is not None
        grounded = [f for f in ex.fields if f["grounded"]]
        assert len(grounded) >= 8 and all(f["evidence"][0]["boxes"] for f in grounded if f["field"] != "parties")
    # identical bytes again: the extraction is reused, no model call
    dup_id = _ingest(data, "copy.pdf")
    calls_before = _calls()
    asyncio.run(process_document(dup_id, deps=deps))
    with session_scope() as s:
        dup = s.get(Document, dup_id)
        assert dup.duplicate_of == doc_id
        assert s.get(Extraction, dup.latest_extraction_id).reused_from is not None
    assert _calls() == calls_before


def _calls() -> int:
    with session_scope() as s:
        return len(s.scalars(select(LLMCall)).all())


def test_long_document_uses_grouped_retrieval_and_finds_page_87():
    spec = find_spec("en", "services_agreement", "long", require=lambda sp: sp.auto_renewal)
    data = render_pdf(build_document(spec))
    deps = _deps(oracle(spec))
    doc_id = _ingest(data)
    config = ExtractConfig.preset("cascade", full_doc_max_tokens=5000)
    asyncio.run(process_document(doc_id, deps=deps, config=config))
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        ex = s.get(Extraction, doc.latest_extraction_id)
        assert ex.mode == "grouped" and doc.page_count > 60
        term_pages = ex.selected_pages["term"]["pages"]
        renewal = next(f for f in ex.fields if f["field"] == "auto_renewal")
        gold_page = renewal["evidence"][0]["page"]
        assert gold_page in term_pages and gold_page > 40      # the clause deep in the document was selected
        assert len(term_pages) < doc.page_count / 3             # …without sending the whole document
        assert doc.facts["auto_renewal"] is True


def test_escalation_replaces_an_unverifiable_fast_answer():
    spec = find_spec("en", "nda", require=lambda sp: sp.auto_renewal and sp.notice_amount)
    data = render_pdf(build_document(spec))
    hallucinated = {"value": {"amount": 999, "unit": "days"}, "quote": "notice of 999 days is required", "page": 1}
    deps = _deps(oracle(spec, tier_mistakes={"low": {"non_renewal_notice": hallucinated}}))
    doc_id = _ingest(data)
    asyncio.run(process_document(doc_id, deps=deps, config=ExtractConfig.preset("cascade")))
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        ex = s.get(Extraction, doc.latest_extraction_id)
        assert "term" in ex.escalated_groups
        assert doc.facts["non_renewal_notice_days"] == labels_for(spec)["non_renewal_notice_days"]


def test_without_escalation_the_hallucination_is_flagged_for_review():
    spec = find_spec("en", "nda", require=lambda sp: sp.auto_renewal and sp.notice_amount)
    data = render_pdf(build_document(spec))
    hallucinated = {"value": {"amount": 999, "unit": "days"}, "quote": "notice of 999 days is required", "page": 1}
    deps = _deps(oracle(spec, mistakes={"non_renewal_notice": hallucinated}))
    doc_id = _ingest(data)
    asyncio.run(process_document(doc_id, deps=deps, config=ExtractConfig.preset("fast")))
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        ex = s.get(Extraction, doc.latest_extraction_id)
        f = next(f for f in ex.fields if f["field"] == "non_renewal_notice")
        assert f["grounded"] is False and f["needs_review"] and doc.needs_review


def test_rule_violations_force_review_but_drafts_do_not():
    from docintel.extraction.confidence import score_fields
    from docintel.extraction.derive import derive_facts
    from docintel.extraction.verify import Evidence, FieldResult, business_rules

    def fr(name, value, group="commercial"):
        return FieldResult(name, group, value, [Evidence("q", 1, True, 1.0)], grounded=True, value_in_quote=True)

    fields = [fr("contract_value", {"amount": 1000.0, "currency": "USD"}),
              fr("line_items", [{"description": "a", "quantity": 1, "unit_price": 400.0, "amount": 400.0}]),
              fr("signatories", [{"name": "A", "is_signed": False}], "parties")]
    by = {f.field: f for f in fields}
    facts = derive_facts({"contract_value": {"amount": 1000.0, "currency": "USD"},
                          "signatories": [{"name": "A", "is_signed": False}]})
    business_rules(by, facts)
    score_fields(fields, page_routes={}, full_coverage={"commercial": True, "parties": True}, calibration=None,
                 review_below=0.5)
    assert by["contract_value"].needs_review and by["line_items"].needs_review    # 400 ≠ 1000
    assert not by["signatories"].needs_review and by["signatories"].flags          # draft: informational


def test_unnamed_parties_are_sent_back_for_repair():
    from docintel.extraction.verify import validate_output
    errors = validate_output({"parties": [{"name": "Acme Inc.", "role": "customer", "quote": "Acme", "page": 1},
                                          {"name": ":", "role": "other", "quote": None, "page": None}]}, 3)
    assert any("is not a name" in e for e in errors)
