"""Turn a born-digital PDF into a realistic scan: image-only pages, slight rotation,
blur, sensor noise and JPEG artifacts. The red seal stays red (RGB)."""
from __future__ import annotations

import io
import random

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter


def degrade(img: Image.Image, rng: random.Random) -> Image.Image:
    img = img.convert("RGB").rotate(rng.uniform(-1.3, 1.3), resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))
    img = img.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.3, 0.8)))
    arr = np.asarray(img).astype(np.float32)
    noise = np.random.default_rng(rng.randint(0, 2**31)).normal(0, rng.uniform(5, 12), arr.shape[:2])[..., None]
    arr = np.clip(arr * rng.uniform(0.92, 1.0) + noise + rng.uniform(0, 10), 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=rng.randint(55, 80))
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def to_scanned_pdf(pdf_bytes: bytes, rng: random.Random, dpi: int = 150) -> bytes:
    doc = pdfium.PdfDocument(pdf_bytes)
    pages = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            pages.append(degrade(page.render(scale=dpi / 72).to_pil(), rng))
            page.close()
    finally:
        doc.close()
    out = io.BytesIO()
    pages[0].save(out, format="PDF", save_all=True, append_images=pages[1:], resolution=dpi)
    return out.getvalue()
