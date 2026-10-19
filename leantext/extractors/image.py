"""Images -> text. OCR first; escalate to a vision model only when OCR is weak."""
from __future__ import annotations

import base64
import io
import mimetypes
import os
import re

from PIL import Image

MIN_CONFIDENCE = 70.0   # mean Tesseract word confidence, 0-100
MAX_LOW_SHARE = 0.25    # escalate when more than this share of words is below LOW_WORD_CONF
LOW_WORD_CONF = 50.0
MIN_WORDS = 3


def prepare(img: Image.Image) -> Image.Image:
    """Make a screenshot Tesseract-friendly. Each step comes from a real failure (see tests).

    - Window captures with transparent shadows: crop to the opaque window, flatten on white.
      (Otherwise the margin/window contrast dominates and the text disappears.)
    - Dark backgrounds (terminals, dark-mode UIs): invert, judged by the median, not the mean.
    - No binarisation: a fixed threshold erased coloured text (0/20 on a Rich traceback);
      Tesseract binarises adaptively on its own.
    - Upscale only small images; upscaling Retina captures made OCR worse.
    """
    import numpy as np

    if img.mode in ("RGBA", "LA", "P", "PA"):
        img = img.convert("RGBA")
        alpha = np.asarray(img)[..., 3]
        ys, xs = np.nonzero(alpha >= 250)
        if len(xs) and (np.ptp(xs) + 1) * (np.ptp(ys) + 1) >= 0.3 * alpha.size:
            img = img.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
        flat = Image.new("RGBA", img.size, (255, 255, 255, 255))
        flat.alpha_composite(img)
        img = flat
    g = np.asarray(img.convert("L"))
    if np.median(g) < 110:
        g = 255 - g
    out = Image.fromarray(g)
    if out.width < 800:
        f = min(3.0, 1200 / out.width)
        out = out.resize((int(out.width * f), int(out.height * f)), Image.LANCZOS)
    return out


def ocr(path: str) -> tuple[str, float, int, float]:
    """Return (text, mean_confidence, word_count, share_of_low_confidence_words)."""
    import pytesseract

    with Image.open(path) as src:
        img = prepare(src)

    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    confs = [float(c) for c, w in zip(data["conf"], data["text"]) if w.strip() and float(c) >= 0]
    words = len(confs)
    mean = sum(confs) / words if words else 0.0
    low = sum(1 for c in confs if c < LOW_WORD_CONF) / words if words else 1.0

    # Rebuild lines in reading order from Tesseract's block/par/line structure.
    lines: dict[tuple, list[str]] = {}
    for i, w in enumerate(data["text"]):
        if not w.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(w)
    out, prev_block = [], None
    for key in sorted(lines):
        if prev_block is not None and key[0] != prev_block:
            out.append("")
        out.append(" ".join(lines[key]))
        prev_block = key[0]
    return _fix_common(("\n".join(out)).strip()), mean, words, low


# Narrow, test-backed fixes for known OCR confusions. Add one per failure case, never broad rules.
_FIXES = [
    (re.compile(r"(?<=[@v\s=])l(?=\.\d)"), "1"),        # devtv@l.4.0 -> devtv@1.4.0
    (re.compile(r"(?<=\d\.)l(?=\.\d|\b)"), "1"),        # 2.l.3 -> 2.1.3
    (re.compile(r"[\u2018\u2019]"), "'"),                # curly single quotes -> straight
    (re.compile(r"[\u201c\u201d]"), '"'),
]


def _join_money(text: str) -> str:
    """'$275,000, 0000' -> '$275,000,0000': stray spaces inside currency amounts only.

    Plain digit pairs like '(1000, 200)' are left alone; they are often real tuples.
    """
    return re.sub(r"\$\d{1,3}(?:,\s?\d{3,4})+", lambda m: m.group(0).replace(" ", ""), text)


def _fix_common(text: str) -> str:
    for pat, rep in _FIXES:
        text = pat.sub(rep, text)
    return _join_money(text)


def image_tokens(path: str) -> int:
    """Claude's documented approximation: (width * height) / 750, after resize to <=1568px long edge."""
    with Image.open(path) as img:
        w, h = img.size
    long_edge = max(w, h)
    if long_edge > 1568:
        s = 1568 / long_edge
        w, h = int(w * s), int(h * s)
    return max(1, round(w * h / 750))


def vision(path: str, model: str | None = None) -> str:
    """Describe/transcribe an image with Claude. Requires ANTHROPIC_API_KEY."""
    import anthropic

    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model or os.environ.get("LEANTEXT_VISION_MODEL", "claude-haiku-5-5"),
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                {"type": "text", "text": (
                    "Transcribe this image to Markdown. Keep all visible text verbatim, "
                    "render tables as Markdown tables and code as fenced blocks. For charts or "
                    "diagrams, add a short factual description of what they show. Output only Markdown."
                )},
            ],
        }],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def extract(path: str, allow_vision: bool = True) -> tuple[str, str, list[str]]:
    """Return (markdown, method, warnings)."""
    warnings: list[str] = []
    text, conf, words, low = ocr(path)
    tag = f"ocr(conf={conf:.0f}, low={low:.0%})"
    if words >= MIN_WORDS and conf >= MIN_CONFIDENCE and low <= MAX_LOW_SHARE:
        return text + "\n", tag, warnings

    if words < MIN_WORDS:
        reason = "empty"
    elif conf < MIN_CONFIDENCE:
        reason = f"mean confidence {conf:.0f}"
    else:
        reason = f"{low:.0%} of words low-confidence"
    if allow_vision and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return vision(path) + "\n", f"vision(ocr {reason})", warnings
        except Exception as e:  # noqa: BLE001
            warnings.append(f"vision fallback failed: {e}")
    else:
        warnings.append(f"OCR {reason}; vision fallback unavailable (set ANTHROPIC_API_KEY)")
    return (text + "\n") if text else "", tag, warnings
