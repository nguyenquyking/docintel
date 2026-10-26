import asyncio
import random
import re

import pytest

from docintel.llm.client import LLMClient
from docintel.parsing.parser import parse_document
from docintel.settings import get_settings
from docintel.storage import Storage, sha256_bytes
from docintel.synth.generator import build_document, find_spec, render_pdf
from docintel.synth.scan import to_scanned_pdf


def _write(tmp_path, data: bytes):
    path = tmp_path / "doc.pdf"
    path.write_bytes(data)
    return path, sha256_bytes(data)


def _parse(tmp_path, data: bytes, llm=None):
    path, sha = _write(tmp_path, data)
    return asyncio.run(parse_document(path, sha, llm or LLMClient(get_settings())))


def test_text_layer_strikethrough_and_signatures(tmp_path):
    spec = find_spec("en", "services_agreement", tags=("redline",))
    ir = _parse(tmp_path, render_pdf(build_document(spec)))
    assert all(p.route == "text_layer" for p in ir.pages)
    struck = " ".join(s for p in ir.pages for s in p.struck)
    assert f"({spec.old_notice_amount})" in struck            # the deleted notice period was detected
    clause = next(line for p in ir.pages for line in p.text.splitlines() if "renew" in line and "notif" in line
                  or "non-renewal" in line)
    assert f"({spec.old_notice_amount}) days" not in clause
    assert f"({spec.notice_amount})" in clause
    assert "[signed]" in ir.pages[-1].text and ir.language == "en"


def test_draft_has_no_signature_marks(tmp_path):
    spec = find_spec("en", "nda", tags=("draft",))
    ir = _parse(tmp_path, render_pdf(build_document(spec)))
    assert "[signed]" not in "\n".join(p.text for p in ir.pages)


def test_line_items_become_a_table(tmp_path):
    spec = find_spec("en", "purchase_agreement")
    ir = _parse(tmp_path, render_pdf(build_document(spec)))
    page = next(p for p in ir.pages if p.has_table)
    assert re.search(r"^\| # \| Description \| Qty", page.markdown, re.M)
    assert spec.line_items[0]["description"] in page.markdown


def test_vietnamese_seal_and_bilingual_language(tmp_path):
    spec = find_spec("bilingual", "services_agreement")
    ir = _parse(tmp_path, render_pdf(build_document(spec)))
    assert ir.language == "bilingual"
    assert "[stamp]" in ir.pages[-1].text and "[signed]" in ir.pages[-1].text


def test_signature_blocks_are_read_column_by_column(tmp_path):
    spec = find_spec("vi", "lease")
    ir = _parse(tmp_path, render_pdf(build_document(spec)))
    sig = next(b.text for p in ir.pages for b in p.blocks if b.kind == "signature" and spec.signers[0][0] in b.text)
    (name_a, title_a), (name_b, _) = spec.signers
    assert sig.index(name_a) < sig.index(title_a) < sig.index(name_b)      # party A's column, then party B's
    assert f"{name_a} {name_b}" not in sig


def test_scanned_english_goes_to_local_ocr(tmp_path):
    spec = find_spec("en", "nda", scanned=True)
    data = to_scanned_pdf(render_pdf(build_document(spec)), random.Random(1))
    ir = _parse(tmp_path, data)
    assert ir.pages[0].route == "ocr", ir.pages[0].route_reason
    assert ir.pages[0].ocr_confidence > 0.85 and "agreement" in ir.pages[0].text.lower()
    assert ir.pages[0].words and ir.pages[0].words[0].conf is not None


def test_scanned_vietnamese_goes_to_the_vlm(tmp_path):
    spec = find_spec("vi", "lease", scanned=True)
    data = to_scanned_pdf(render_pdf(build_document(spec)), random.Random(2))
    seen = []

    def vlm(payload):
        seen.append(payload)
        return "## HỢP ĐỒNG THUÊ VĂN PHÒNG\n\nHợp đồng này được ký kết ~~bản nháp~~ giữa các Bên.\n\n| STT | Hạng mục |\n|---|---|\n| 1 | Văn phòng |"

    ir = _parse(tmp_path, data, LLMClient(get_settings(), responder=vlm))
    page = ir.pages[0]
    assert page.route == "vlm" and "Vietnamese" in page.route_reason, page.route_reason
    assert "ký kết" in page.text and "bản nháp" not in page.text and page.struck == ["bản nháp"]
    assert page.has_table and seen and seen[0]["model"]


def test_parse_is_cached_per_blob_and_parser_version(tmp_path):
    spec = find_spec("en", "nda")
    data = render_pdf(build_document(spec))
    first = _parse(tmp_path, data)
    path, sha = _write(tmp_path, data)
    artifact = Storage().artifact_dir(sha, first.parser_version) / "ir.json"
    assert artifact.exists()
    second = _parse(tmp_path, data)
    assert second.to_json() == first.to_json()


def test_corrupt_pdf_is_fatal(tmp_path):
    from docintel.parsing.pdf import FatalParseError
    with pytest.raises(FatalParseError):
        _parse(tmp_path, b"%PDF-1.4 this is not really a pdf")


def test_numbered_clauses_are_paragraphs_not_headings(tmp_path):
    spec = find_spec("en", "services_agreement", "medium")
    ir = _parse(tmp_path, render_pdf(build_document(spec)))
    blocks = [b for p in ir.pages for b in p.blocks]
    headings = [b.text for b in blocks if b.kind == "heading"]
    assert any(h.endswith("Term and Termination") for h in headings)
    assert not any(re.match(r"^\d+\.\d+\s", h) for h in headings)        # "12.2 Thereafter, …" is a clause
    clause = next(b.text for b in blocks if b.kind == "paragraph" and b.text.split(" ", 1)[0].endswith(".2")
                  and "renew" in b.text)
    assert clause.split(" ", 1)[0].count(".") == 1                      # one clause per paragraph


def test_flat_signature_strokes_still_count(monkeypatch):
    from docintel.parsing import pdf as pdfmod

    class FakePage:
        width = 595
        curves = [{"x0": 314, "x1": 406, "top": 433.2, "bottom": 435.9, "stroking_color": (0.12, 0.23, 0.58)}]
        images = []

    marks = pdfmod._marks(FakePage())
    assert marks and marks[0][4] == "signed"
