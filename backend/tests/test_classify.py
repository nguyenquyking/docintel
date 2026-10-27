import asyncio
import io
import json

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from docintel.classify import classify_pdf, clean_types, load_types, pick_pages, reset_types, save_types
from docintel.llm.client import LLMClient
from docintel.settings import get_settings

FILLER = "The receiving party shall keep the Confidential Information secret and use it only for the Purpose."


def _pdf(pages: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for text in pages:
        y = 800
        for line in [text, FILLER, FILLER]:
            c.drawString(60, y, line)
            y -= 18
        c.showPage()
    c.save()
    return buf.getvalue()


def test_pick_pages_reads_head_and_tail_without_repeats():
    assert pick_pages(10, 3, 2) == [0, 1, 2, 8, 9]
    assert pick_pages(4, 3, 2) == [0, 1, 2, 3]
    assert pick_pages(5, 0, 1) == [4]


def test_taxonomy_is_cleaned_saved_and_reset():
    assert clean_types([{"code": " work permit ", "label": "", "definition": "x"}])[0]["code"] == "WORK_PERMIT"
    with pytest.raises(ValueError):
        clean_types([{"code": "OTHER"}])
    with pytest.raises(ValueError):
        clean_types([{"code": "NDA"}, {"code": "nda"}])
    save_types([{"code": "NFA", "label": "NFA", "definition": "d"}])
    assert [t["code"] for t in load_types()] == ["NFA"]
    reset_types()
    assert "NDA" in [t["code"] for t in load_types()]


def test_classify_sends_first_and_last_pages_and_checks_quotes():
    seen = {}

    def responder(payload):
        seen["user"] = payload["messages"][1]["content"]
        seen["enum"] = payload["response_format"]["json_schema"]["schema"]["properties"]["document_type"]["enum"]
        return json.dumps({"title": "MUTUAL NON-DISCLOSURE AGREEMENT", "language": "en",
                           "evidence": [{"page": 1, "quote": "MUTUAL NON-DISCLOSURE AGREEMENT"},
                                        {"page": 7, "quote": "a sentence that is on no page"}],
                           "reasoning": "titled as an NDA", "document_type": "NDA", "runner_up": "NONE",
                           "confidence": 0.97})

    pages = ["MUTUAL NON-DISCLOSURE AGREEMENT"] + [f"Clause {i} of the agreement" for i in range(2, 8)]
    llm = LLMClient(get_settings(), responder=responder)
    r = asyncio.run(classify_pdf(_pdf(pages), "x.pdf", head=2, tail=1, llm=llm))
    assert [p["number"] for p in r["pages"]] == [1, 2, 7]
    assert "Omitted: pages 3–6" in seen["user"] and "Clause 4" not in seen["user"]
    assert "OTHER" in seen["enum"] and "NDA" in seen["enum"]
    assert r["document_type"] == "NDA" and r["runner_up"] is None
    assert [e["grounded"] for e in r["evidence"]] == [True, False]
    assert r["needs_review"] is False          # only the main quote decides review


def test_low_confidence_needs_review():
    def responder(payload):
        return json.dumps({"title": None, "language": "en", "evidence": [], "reasoning": "unclear",
                           "document_type": "OTHER", "runner_up": "NDA", "confidence": 0.4})

    r = asyncio.run(classify_pdf(_pdf(["Some page"]), "y.pdf", llm=LLMClient(get_settings(), responder=responder)))
    assert r["needs_review"] and r["runner_up"] == "NDA"
    assert "no type fits" in r["review_reasons"]
