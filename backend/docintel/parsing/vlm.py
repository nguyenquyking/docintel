"""Page transcription with a vision-language model, for the pages OCR can't do well."""
from __future__ import annotations

import base64
import io

from PIL import Image

from ..llm.client import LLMClient

VLM_INSTRUCTIONS = """Transcribe this scanned contract page into Markdown.
- Copy the text exactly, in its original language, keeping every diacritic and every digit.
- Do not translate, summarize, correct or complete anything.
- Headings as '## ', tables as Markdown pipe tables (one row per line).
- Wrap struck-through (deleted) text in ~~ ~~.
- If a signature line is signed, write [signed] at that spot; if it is blank, keep the blank line.
- Return only the Markdown. Return an empty string for a blank page."""


def image_to_data_url(image: Image.Image, max_side: int = 2000, quality: int = 85) -> str:
    img = image.convert("RGB")
    scale = max_side / max(img.size)
    if scale < 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


async def transcribe_page(llm: LLMClient, image_url: str, ocr_hint: str | None = None) -> str:
    content: list[dict] = [{"type": "text", "text": VLM_INSTRUCTIONS}]
    if ocr_hint:
        content.append({"type": "text", "text": "An OCR engine read this page as follows. It may have dropped "
                        "diacritics or misread characters — trust the image, not this text:\n" + ocr_hint[:6000]})
    content.append({"type": "image_url", "image_url": {"url": image_url}})
    resp = await llm.chat([{"role": "user", "content": content}], purpose="vlm_page", tier="vision",
                          max_output_tokens=8000)
    return resp.content.strip()
