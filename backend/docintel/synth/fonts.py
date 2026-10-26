"""Find a TrueType font that covers Vietnamese, on macOS, Linux or Windows.

No font file ships with the repository: system fonts (Arial, DejaVu Sans, Liberation)
already cover Latin Extended Additional, and matplotlib bundles DejaVu if present.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

CANDIDATES = [
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
]


@lru_cache
def find_fonts() -> tuple[str, str]:
    env = os.environ.get("DOCINTEL_SYNTH_FONT")
    if env and Path(env).exists():
        bold = os.environ.get("DOCINTEL_SYNTH_FONT_BOLD", env)
        return env, bold if Path(bold).exists() else env
    for regular, bold in CANDIDATES:
        if Path(regular).exists():
            return regular, bold if Path(bold).exists() else regular
    try:
        import matplotlib
        base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        if (base / "DejaVuSans.ttf").exists():
            return str(base / "DejaVuSans.ttf"), str(base / "DejaVuSans-Bold.ttf")
    except ImportError:
        pass
    raise RuntimeError("no Unicode TrueType font found; set DOCINTEL_SYNTH_FONT to a .ttf covering Vietnamese")


@lru_cache
def register_fonts() -> tuple[str, str]:
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    regular, bold = find_fonts()
    pdfmetrics.registerFont(TTFont("DocSans", regular))
    pdfmetrics.registerFont(TTFont("DocSans-Bold", bold))
    addMapping("DocSans", 0, 0, "DocSans")
    addMapping("DocSans", 1, 0, "DocSans-Bold")
    addMapping("DocSans", 0, 1, "DocSans")
    addMapping("DocSans", 1, 1, "DocSans-Bold")
    return "DocSans", "DocSans-Bold"
