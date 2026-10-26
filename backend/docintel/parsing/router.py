"""Per-page routing: text layer, local OCR, or a vision-language model.

The decision is made per page and recorded with its reason, because cost and
quality both depend on it and both are monitored per route:

* **text layer** when the PDF already carries good text — exact and free;
* **local OCR** for scans in a language the engine actually supports, with high
  confidence and no table structure to recover — cheap, gives word boxes;
* **VLM** for everything OCR is known to get wrong: languages outside the
  engine's character set (Vietnamese through an engine without a Vietnamese
  model comes back confidently de-accented), low confidence, and tables on
  scanned pages, where cell structure matters more than characters.
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_TEXT_CHARS = 40
MIN_TEXT_QUALITY = 0.9


@dataclass
class RouteDecision:
    route: str            # text_layer | ocr | vlm
    reason: str


def route_text_layer(char_count: int, quality: float, image_coverage: float) -> RouteDecision | None:
    if char_count >= MIN_TEXT_CHARS and quality >= MIN_TEXT_QUALITY:
        return RouteDecision("text_layer", f"text layer OK ({char_count} chars, quality {quality:.2f})")
    return None


def why_no_text_layer(char_count: int, quality: float, image_coverage: float) -> str:
    if char_count < MIN_TEXT_CHARS:
        return f"no usable text layer ({char_count} chars, image covers {image_coverage:.0%} of the page)"
    return f"text layer is garbage (quality {quality:.2f} < {MIN_TEXT_QUALITY})"


def text_plausibility(text: str) -> tuple[bool, str]:
    """Is OCR output shaped like language? Recognizers can be confidently wrong in ways their
    confidence never shows: dropped spaces ('ThisMutualNon-DisclosureAgreement', conf 0.97)
    or dropped diacritics. Word-length statistics catch the first; language ID the second."""
    tokens = [t for t in text.split() if any(c.isalpha() for c in t)]
    if len(tokens) < 15:
        return True, ""
    mean_len = sum(len(t) for t in tokens) / len(tokens)
    long_share = sum(len(t) >= 16 for t in tokens) / len(tokens)
    if mean_len > 8.0 or long_share > 0.08:
        return False, f"OCR text implausible (mean word length {mean_len:.1f}, {long_share:.0%} run-on tokens: spaces lost)"
    return True, ""


def route_scanned(*, base_reason: str, ocr_engine: str | None, ocr_languages: frozenset[str],
                  ocr_confidence: float | None, ocr_language: str | None, doc_language_hint: str | None,
                  tabular: bool, min_confidence: float, vlm_enabled: bool,
                  implausible: str | None = None, unread_share: float = 0.0) -> RouteDecision:
    if ocr_engine is None:
        if vlm_enabled:
            return RouteDecision("vlm", f"{base_reason}; no local OCR engine → VLM")
        return RouteDecision("ocr", f"{base_reason}; no OCR engine and VLM disabled → empty page")
    lang = ocr_language if ocr_language not in (None, "unknown") else doc_language_hint
    vietnamese = lang in ("vi", "vi_noaccent", "bilingual")
    if not vlm_enabled:
        return RouteDecision("ocr", f"{base_reason}; OCR ({ocr_engine}, conf {ocr_confidence or 0:.2f}); VLM disabled")
    if vietnamese and "vi" not in ocr_languages:
        seen = "text came back without diacritics" if ocr_language == "vi_noaccent" else "Vietnamese document"
        return RouteDecision(
            "vlm", f"{base_reason}; {ocr_engine} has no Vietnamese model ({seen}, conf {ocr_confidence or 0:.2f}) → VLM")
    if implausible:
        return RouteDecision("vlm", f"{base_reason}; {implausible}, although conf is {ocr_confidence or 0:.2f} → VLM")
    if ocr_confidence is not None and ocr_confidence < min_confidence:
        unread_note = f", {unread_share:.0%} of the text unreadable" if unread_share else ""
        return RouteDecision("vlm", f"{base_reason}; OCR conf {ocr_confidence:.2f} < {min_confidence:.2f}{unread_note} → VLM")
    if tabular:
        return RouteDecision("vlm", f"{base_reason}; table on a scanned page → VLM (cell structure)")
    return RouteDecision(
        "ocr", f"{base_reason}; OCR ({ocr_engine}) conf {ocr_confidence or 0:.2f} ≥ {min_confidence:.2f}, language {lang}")
