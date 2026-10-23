"""PDF / Office -> Markdown.

PDFs with a text layer: try engines in order of quality and use the first whose output scores
clean (spaces between words, sane token shapes). On real two-column papers MarkItDown/pdfminer
glues words together and interleaves columns, so it is last in line for PDFs:
    pdftotext (poppler, if installed)  ->  pypdf  ->  MarkItDown
Scanned PDFs (no text layer) are rasterised and sent down the image/OCR path.
Office documents: MarkItDown, with pandoc as fallback.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

TEXT_LAYER_MIN_CHARS = 50  # per page, on average
GLUED_MAX = 0.02           # share of "words" longer than 25 chars before output counts as glued


def has_text_layer(path: str) -> bool:
    from pypdf import PdfReader

    reader = PdfReader(path)
    n = len(reader.pages) or 1
    sample = reader.pages[: min(5, n)]
    chars = sum(len((p.extract_text() or "").strip()) for p in sample)
    return chars / max(1, len(sample)) >= TEXT_LAYER_MIN_CHARS


def glued_ratio(text: str) -> float:
    """Share of whitespace-separated tokens over 25 characters, ignoring URLs and hashes."""
    words = [w for w in re.findall(r"\S+", text) if not re.match(r"(https?://|[0-9a-f]{20,}$)", w, re.I)]
    return sum(1 for w in words if len(w) > 25) / max(1, len(words))


# ------------------------------------------------------------------ engines

def _pdftotext(path: str) -> str:
    if not shutil.which("pdftotext"):
        return ""
    r = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _pypdf(path: str) -> str:
    from pypdf import PdfReader

    return "\f".join((p.extract_text() or "") for p in PdfReader(path).pages)


OFFICE_ADDON_HINT = ("Office files need the optional add-on: re-run ./install.sh --office, "
                     "or: uv tool install --force \"leantext[office]\"  (or install pandoc)")


def office_available() -> bool:
    """True if Word/Excel/PowerPoint files can be converted (MarkItDown or pandoc present)."""
    import importlib.util

    return importlib.util.find_spec("markitdown") is not None or shutil.which("pandoc") is not None


def _markitdown(path: str) -> str:
    try:
        from markitdown import MarkItDown
    except ImportError:
        raise RuntimeError(OFFICE_ADDON_HINT) from None

    return MarkItDown().convert(path).text_content or ""


def _pandoc(path: str) -> str:
    if not shutil.which("pandoc"):
        return ""
    r = subprocess.run(["pandoc", path, "-t", "gfm", "--wrap=none"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


_LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
                            "￾": "", "­": ""})


def _tidy(text: str) -> str:
    """Ligatures expanded, words re-joined across line-end hyphens, pages separated, blank runs squeezed."""
    text = text.translate(_LIGATURES).replace("\r\n", "\n")
    text = re.sub(r"(?<=[a-z])-\n(?=[a-z])", "", text)
    text = text.replace("\f", "\n\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = _reflow(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def _reflow(text: str) -> str:
    """Join hard-wrapped prose lines back into paragraphs.

    PDF text keeps every line break of the printed page. Agents' file readers number each line, so a
    14-page paper became ~1,900 numbered 42-character lines. A line is joined to the next only when
    it is long (>= 65% of the typical line width) and the next line starts like a continuation, so
    headings, table rows, list items and code keep their own lines.
    """
    lines = text.split("\n")
    widths = sorted(len(l) for l in lines if len(l) > 20)
    if not widths:
        return text
    typical = widths[int(len(widths) * 0.75)]
    out: list[str] = []
    for line in lines:
        prev = out[-1] if out else ""
        if (prev and line.strip() and len(prev.split("\n")[-1]) >= 0.65 * typical
                and not re.match(r"\s*([-*•▪◦]|\d+[.)]|#|```|\|)\s", line)
                and not prev.rstrip().endswith(":")
                and (line[:1].islower() or line[:1] in "([\"'" or line[:1].isdigit() or prev.rstrip()[-1:] not in ".?!")):
            out[-1] = prev.rstrip() + " " + line.lstrip()
        else:
            out.append(line)
    return "\n".join(out)


# ------------------------------------------------------------------ checks

def graphics_pages(path: str) -> list[int]:
    """1-based pages that are mostly pictures or drawings: little text, several image/path objects."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as c

    out = []
    pdf = pdfium.PdfDocument(path)
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            tp = page.get_textpage()
            chars = len(tp.get_text_range().strip())
            kinds = [o.type for o in page.get_objects(max_depth=2)]
            images = kinds.count(c.FPDF_PAGEOBJ_IMAGE)
            paths = kinds.count(c.FPDF_PAGEOBJ_PATH)
            tp.close()
            page.close()
            if chars < 400 and (images >= 1 or paths >= 5):
                out.append(i + 1)
    finally:
        pdf.close()
    return out


# ------------------------------------------------------------------ OCR route for scans

def _ocr_pages(path: str, allow_vision: bool) -> tuple[str, list[str]]:
    import pypdfium2 as pdfium

    from . import image

    pdf = pdfium.PdfDocument(path)
    parts, warnings = [], []
    with tempfile.TemporaryDirectory() as td:
        for i in range(len(pdf)):
            png = os.path.join(td, f"p{i}.png")
            page = pdf[i]
            page.render(scale=2).to_pil().save(png)
            page.close()
            md, method, w = image.extract(png, allow_vision=allow_vision)
            warnings += [f"page {i + 1}: {x}" for x in w]
            parts.append(f"<!-- page {i + 1} ({method}) -->\n{md.strip()}")
    pdf.close()
    return "\n\n".join(parts) + "\n", warnings


# ------------------------------------------------------------------ entry

def extract(path: str, allow_vision: bool = True) -> tuple[str, str, list[str], list[str]]:
    """Return (markdown, method, warnings, suspicious)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf" and not has_text_layer(path):
        md, w = _ocr_pages(path, allow_vision)
        return md, "pdf-scanned->ocr", w, []

    engines = [("pdftotext", _pdftotext), ("pypdf", _pypdf), ("markitdown", _markitdown)] if ext == ".pdf" \
        else [("markitdown", _markitdown), ("pandoc", _pandoc)]
    tried: list[tuple[float, str, str]] = []
    errors: list[str] = []
    for name, fn in engines:
        try:
            text = _tidy(fn(path)) if ext == ".pdf" else fn(path).strip() + "\n"
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
            continue
        if not text.strip():
            continue
        score = glued_ratio(text)
        if score <= GLUED_MAX:
            tried = [(score, name, text)]
            break
        tried.append((score, name, text))

    if not tried:
        return "", "none", errors or ["no engine produced text"], []

    score, name, text = min(tried)  # cleanest available
    suspicious = []
    if score > GLUED_MAX:
        suspicious.append(f"{score:.0%} of words look glued together (two-column layout?); "
                          "install poppler-utils for pdftotext")
    if ext == ".pdf":
        g = graphics_pages(path)
        if g:
            shown = ", ".join(map(str, g[:8])) + ("…" if len(g) > 8 else "")
            suspicious.append(f"page(s) {shown} are mostly graphics; the text may not describe them")
    return text, name, [], suspicious
