"""Local OCR engines behind one interface.

* ``rapidocr`` — PaddleOCR (PP-OCR) detection + recognition models on ONNX Runtime.
  pip-installable, CPU-only, fast. Its default recognizer has no Vietnamese
  character set: it returns Vietnamese text *without diacritics* at high
  confidence, which is exactly why routing cannot trust confidence alone.
* ``tesseract`` — the classic engine via its CLI, when installed with the language
  packs you need (``eng+vie``).
"""
from __future__ import annotations

import csv
import io
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PIL import Image

from .ir import Word


@dataclass
class OcrResult:
    engine: str
    words: list[Word] = field(default_factory=list)     # in image pixels; the parser rescales to points
    lines: list[tuple[str, float]] = field(default_factory=list)
    mean_confidence: float = 0.0
    unread_share: float = 0.0                           # detected lines the recognizer could not read

    @property
    def text(self) -> str:
        return "\n".join(t for t, _ in self.lines)


class OcrEngine:
    name = "none"
    languages: frozenset[str] = frozenset()

    def available(self) -> bool:
        return False

    def recognize(self, image: Image.Image) -> OcrResult:
        raise NotImplementedError


def _split_line_into_words(text: str, x0: float, top: float, x1: float, bottom: float, conf: float) -> list[Word]:
    """Line-level boxes → approximate word boxes, by character share of the line width."""
    tokens = text.split()
    if not tokens:
        return []
    total = sum(len(t) for t in tokens) + (len(tokens) - 1)
    unit = (x1 - x0) / max(1, total)
    words, cursor = [], x0
    for t in tokens:
        w = len(t) * unit
        words.append(Word(t, cursor, top, cursor + w, bottom, conf))
        cursor += w + unit
    return words


MIN_LINE_SCORE = 0.5


def _summarize(engine: str, words: list[Word], lines: list[tuple[str, float]],
               all_lines: list[tuple[str, float]] | None = None) -> OcrResult:
    """Confidence is averaged over every line the detector found — including the ones the
    recognizer gave up on — so a page with unreadable lines cannot look confident."""
    scored = all_lines if all_lines is not None else lines
    weight = sum(len(t) for t, _ in scored) or 1
    mean = sum(len(t) * c for t, c in scored) / weight if scored else 0.0
    unread = sum(len(t) for t, c in scored if c < MIN_LINE_SCORE) / weight if scored else 0.0
    return OcrResult(engine, words, lines, mean, unread)


# The recognizer bundled with rapidocr is the Chinese PP-OCR model: on English text it drops the
# spaces between words ("ThisMutualNon-DisclosureAgreement") at 0.97 confidence. The English
# PP-OCRv3 recognizer (Apache-2.0, converted to ONNX by RapidAI) fixes that; it is fetched once,
# checksum-pinned, into data/models/ocr. If it cannot be fetched, the bundled model is used and
# the router's plausibility check sends such pages to the VLM.
EN_REC_URL = "https://huggingface.co/SWHL/RapidOCR/resolve/main/PP-OCRv3/en_PP-OCRv3_rec_infer.onnx"
EN_REC_SHA256 = "ef7abd8bd3629ae57ea2c28b425c1bd258a871b93fd2fe7c433946ade9b5d9ea"


def _english_rec_model() -> str | None:
    import hashlib

    import httpx

    from ..settings import get_settings
    target = get_settings().data_dir / "models" / "ocr" / "en_PP-OCRv3_rec_infer.onnx"
    if not target.exists():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = httpx.get(EN_REC_URL, follow_redirects=True, timeout=120).content
            if hashlib.sha256(data).hexdigest() != EN_REC_SHA256:
                return None
            target.write_bytes(data)
        except Exception:
            return None
    return str(target)


class RapidOcrEngine(OcrEngine):
    name = "rapidocr"
    languages = frozenset({"en"})

    @staticmethod
    @lru_cache(maxsize=1)
    def _engine():
        from rapidocr_onnxruntime import RapidOCR
        model = _english_rec_model()
        # text_score=0: keep low-score lines so unreadable text lowers the page confidence.
        # use_cls=False: the 180° line classifier flips long justified lines of a scan and the
        # recognizer then reads them upside down (23 of 51 lines on one page: conf 0.60 → 0.97).
        kwargs = {"text_score": 0.0, "use_cls": False}
        return RapidOCR(rec_model_path=model, **kwargs) if model else RapidOCR(**kwargs)

    def available(self) -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401
            return True
        except ImportError:
            return False

    def recognize(self, image: Image.Image) -> OcrResult:
        arr = np.asarray(image.convert("RGB"))[:, :, ::-1].copy()   # RGB → BGR, as OpenCV expects
        result, _ = self._engine()(arr, use_cls=False)
        words: list[Word] = []
        lines: list[tuple[str, float]] = []
        all_lines: list[tuple[str, float]] = []
        for box, text, score in result or []:
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            conf = float(score)
            all_lines.append((text, conf))
            if conf < MIN_LINE_SCORE:
                continue
            lines.append((text, conf))
            words.extend(_split_line_into_words(text, min(xs), min(ys), max(xs), max(ys), conf))
        return _summarize(self.name, words, lines, all_lines)


class TesseractEngine(OcrEngine):
    name = "tesseract"

    def __init__(self, languages: str = "eng+vie"):
        self.lang_arg = languages
        mapping = {"eng": "en", "vie": "vi"}
        self.languages = frozenset(mapping.get(x, x) for x in languages.split("+"))

    def available(self) -> bool:
        return shutil.which("tesseract") is not None

    def recognize(self, image: Image.Image) -> OcrResult:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        proc = subprocess.run(["tesseract", "stdin", "stdout", "-l", self.lang_arg, "--psm", "3", "tsv"],
                              input=buf.getvalue(), capture_output=True, check=True)
        rows = csv.DictReader(io.StringIO(proc.stdout.decode("utf-8", "replace")), delimiter="\t",
                              quoting=csv.QUOTE_NONE)
        words: list[Word] = []
        by_line: dict[tuple, list[Word]] = {}
        for r in rows:
            text = (r.get("text") or "").strip()
            conf = float(r.get("conf") or -1)
            if not text or conf < 0:
                continue
            x, y, w, h = (float(r[k]) for k in ("left", "top", "width", "height"))
            word = Word(text, x, y, x + w, y + h, conf / 100.0)
            words.append(word)
            by_line.setdefault((r["block_num"], r["par_num"], r["line_num"]), []).append(word)
        lines = [(" ".join(w.text for w in ws), sum(w.conf or 0 for w in ws) / len(ws)) for ws in by_line.values()]
        return _summarize(self.name, words, lines)


def get_engine(backend: str, languages: str = "eng+vie") -> OcrEngine | None:
    engine: OcrEngine | None = None
    if backend == "rapidocr":
        engine = RapidOcrEngine()
    elif backend == "tesseract":
        engine = TesseractEngine(languages)
    return engine if engine is not None and engine.available() else None
