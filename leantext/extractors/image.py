"""Images -> text. OCR first; escalate to a vision model only when OCR is weak."""
from __future__ import annotations

import base64
import io
import mimetypes
import os
import re
import shutil
import sys

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


def _stats(confs: list[float]) -> tuple[float, int, float]:
    """(mean confidence, word count, share of low-confidence words) from per-word confidences 0-100."""
    words = len(confs)
    mean = sum(confs) / words if words else 0.0
    low = sum(1 for c in confs if c < LOW_WORD_CONF) / words if words else 1.0
    return mean, words, low


def engines() -> list[str]:
    """OCR engines to try, best first. LEANTEXT_OCR=tesseract|apple|rapidocr forces one.

    Tesseract is the most accurate on terminal and code screenshots, so it's used when installed.
    Without it: Apple Vision (built into macOS, no download), then RapidOCR (pip wheel, any OS).
    """
    forced = os.environ.get("LEANTEXT_OCR", "").strip().lower()
    if forced:
        return [forced]
    order = []
    if shutil.which("tesseract"):
        order.append("tesseract")
    if sys.platform == "darwin":
        order.append("apple")
    order.append("rapidocr")
    return order


def ocr(path: str) -> tuple[str, float, int, float]:
    """Return (text, mean_confidence, word_count, share_of_low_confidence_words)."""
    text, mean, words, low, _ = ocr_with_engine(path)
    return text, mean, words, low


def ocr_with_engine(path: str) -> tuple[str, float, int, float, str]:
    """Like ocr(), plus the name of the engine that produced the text."""
    errors = []
    for name in engines():
        fn = {"tesseract": _ocr_tesseract, "apple": _ocr_apple, "rapidocr": _ocr_rapid}.get(name)
        if fn is None:
            errors.append(f"unknown OCR engine {name!r}")
            continue
        try:
            text, mean, words, low = fn(path)
        except Exception as e:  # noqa: BLE001  engine missing or failed: try the next one
            errors.append(f"{name}: {e}")
            continue
        return _fix_common(text), mean, words, low, name
    raise RuntimeError("no OCR engine available (" + "; ".join(errors) + ")")


APPLE_AGREE_MIN = 0.90  # fast vs accurate pass must agree this closely, or the read counts as weak


def _apple_pass(path: str, level: str) -> tuple[str, list[float]]:
    """One Apple Vision pass. Boxes are normalised with a bottom-left origin."""
    from ocrmac import ocrmac

    results = ocrmac.OCR(path, recognition_level=level).recognize()
    lines = _group_lines([(-(r[2][1] + r[2][3] / 2), r[2][0], r[2][3], r[0]) for r in results])
    confs = [float(r[1]) * 100 for r in results for _ in r[0].split()]
    return "\n".join(lines).strip(), confs


def agreement(a: str, b: str) -> float:
    """0-1 similarity of two OCR readings, ignoring whitespace and case."""
    import difflib

    na, nb = "".join(a.lower().split()), "".join(b.lower().split())
    if not na and not nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()


def _ocr_apple(path: str) -> tuple[str, float, int, float]:
    """macOS Vision framework via ocrmac.

    Apple Vision's own confidence stayed at 100 on a slightly blurred invoice it misread (two lines
    dropped, 1,140.00 read as 1.140.00), so it can't be trusted alone. A second, 'fast' pass is the
    check: on clean images the two passes agree almost exactly, on degraded ones they diverge. When
    they disagree, confidence is capped below MIN_CONFIDENCE so the vision fallback can step in.
    """
    text, confs = _apple_pass(path, "accurate")
    mean, words, low = _stats(confs)
    if words:
        fast, _ = _apple_pass(path, "fast")
        agree = agreement(text, fast)
        if agree < APPLE_AGREE_MIN:
            mean = min(mean, agree * MIN_CONFIDENCE)
    return text, mean, words, low


_rapid_engine = None


def _ocr_rapid(path: str) -> tuple[str, float, int, float]:
    """RapidOCR (ONNX). Works best on the same prepared image Tesseract gets."""
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    global _rapid_engine
    _rapid_engine = _rapid_engine or RapidOCR()
    with Image.open(path) as src:
        img = np.asarray(prepare(src).convert("RGB"))
    result, _ = _rapid_engine(img)
    result = result or []
    boxes = []
    for box, text, score in result:
        ys = [p[1] for p in box]
        boxes.append(((min(ys) + max(ys)) / 2, min(p[0] for p in box), max(ys) - min(ys), text))
    confs = [float(score) * 100 for _, text, score in result for _ in text.split()]
    return "\n".join(_group_lines(boxes)).strip(), *_stats(confs)


def _group_lines(boxes: list[tuple[float, float, float, str]]) -> list[str]:
    """(y_center, x_left, height, text) boxes -> lines in reading order. A new line starts
    when a box's centre is more than half a box height below the current line's centre."""
    lines: list[list[tuple[float, str]]] = []
    centre = height = None
    for y, x, h, text in sorted(boxes):
        if centre is None or y - centre > max(h, height or h) / 2:
            lines.append([])
            centre, height = y, h
        lines[-1].append((x, text))
    return [" ".join(t for _, t in sorted(line)) for line in lines]


def _ocr_tesseract(path: str) -> tuple[str, float, int, float]:
    import pytesseract

    with Image.open(path) as src:
        img = prepare(src)

    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    confs = [float(c) for c, w in zip(data["conf"], data["text"]) if w.strip() and float(c) >= 0]
    mean, words, low = _stats(confs)

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
    return "\n".join(out).strip(), mean, words, low


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
    try:
        text, conf, words, low, engine = ocr_with_engine(path)
    except RuntimeError as e:
        text, conf, words, low, engine = "", 0.0, 0, 1.0, "none"
        warnings.append(str(e))
    tag = f"ocr({engine}, conf={conf:.0f}, low={low:.0%})"
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
