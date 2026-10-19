"""Detect the input type, route it to the right extractor, run checks, optionally measure."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field

from . import cache, checks, tokens
from .extractors import html as html_x
from .extractors import image as image_x
from .extractors import pdf as pdf_x

VERSION_SALT = "leantext-0.2.4"
UA = "Mozilla/5.0 (compatible; leantext/0.2; +https://github.com/shouvik12/leantext)"
MAX_BYTES = 25 * 1024 * 1024

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
OFFICE_EXT = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".odt", ".epub"}
HTML_EXT = {".html", ".htm", ".xhtml"}

# ---------------------------------------------------------------- fallback rules
MIN_SAVINGS = 0.15          # lean output must be at least 15% cheaper than the raw read
TIME_BUDGET_S = 20.0        # max for extraction; past this the read goes raw
MIN_IMAGE_TEXT_COVERAGE = 0.15  # images whose text covers less than this are mostly visual
LOG_PATH = os.path.expanduser(os.environ.get("LEANTEXT_LOG", "~/.leantext/fallbacks.jsonl"))

# Files where every character matters: never convert, never truncate.
EXACT_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
               "Gemfile.lock", "go.sum", "uv.lock", "composer.lock", "bun.lockb"}
EXACT_EXT = {".diff", ".patch", ".lock"}


class InputError(Exception):
    pass


@dataclass
class Result:
    markdown: str
    kind: str                     # html | pdf | office | image | text
    method: str                   # which extractor actually produced the output
    source: str
    warnings: list[str] = field(default_factory=list)
    suspicious: list[str] = field(default_factory=list)
    baselines: dict[str, int] = field(default_factory=dict)  # name -> tokens
    out_tokens: int | None = None
    tokenizer: str | None = None
    truncated: bool = False
    cached: bool = False
    raw: bool = False             # True when leantext stepped aside; see fallback
    fallback: str | None = None   # why it stepped aside
    elapsed_s: float = 0.0

    @property
    def failed(self) -> bool:
        return not self.markdown.strip()


# ---------------------------------------------------------------- input resolution

def _fetch(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - user-supplied URL is the point
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise InputError(f"{url}: larger than {MAX_BYTES // 2**20} MB")
    return data, ctype


def _kind_for(path: str, ctype: str = "", head: bytes = b"") -> str:
    ext = os.path.splitext(path.split("?")[0])[1].lower()
    if ctype == "application/pdf" or head.startswith(b"%PDF") or ext == ".pdf":
        return "pdf"
    if ctype.startswith("image/") or ext in IMAGE_EXT:
        return "image"
    if ext in OFFICE_EXT:
        return "office"
    if "html" in ctype or ext in HTML_EXT or re.match(rb"\s*(<!doctype html|<html)", head[:512], re.I):
        return "html"
    return "text"


def _ext_for(kind: str, ctype: str) -> str:
    if kind == "pdf":
        return ".pdf"
    if kind == "image":
        return "." + (ctype.split("/")[-1] if "/" in ctype else "png").replace("jpeg", "jpg")
    return ".bin"


# ---------------------------------------------------------------- baselines (what the agent sees without leantext)

def _pdf_page_image_tokens(path: str) -> int:
    import pypdfium2 as pdfium

    total = 0
    pdf = pdfium.PdfDocument(path)
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            w, h = page.get_size()  # points; 2x render ~ 144dpi
            page.close()
            w, h = w * 2, h * 2
            s = min(1.0, 1568 / max(w, h))
            total += round((w * s) * (h * s) / 750)
    finally:
        pdf.close()
    return total


def _baselines(kind: str, raw: bytes | None, path: str | None) -> dict[str, int]:
    b: dict[str, int] = {}
    if kind == "html" and raw is not None:
        page = raw.decode("utf-8", "replace")
        b["raw HTML"] = tokens.count(page)
        from markdownify import markdownify

        stripped = re.sub(r"<(script|style|noscript)\b.*?</\1>", "", page, flags=re.S | re.I)
        b["naive Markdown (whole page)"] = tokens.count(markdownify(stripped, heading_style="ATX"))
    elif kind == "pdf" and path:
        from pypdf import PdfReader

        text = "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
        if text.strip():
            b["plain text dump"] = tokens.count(text)
        b["pages as images"] = _pdf_page_image_tokens(path)
    elif kind == "image" and path:
        b["image input"] = image_x.image_tokens(path)
    elif raw is not None:
        b["raw"] = tokens.count(raw.decode("utf-8", "replace"))
    return b


# ---------------------------------------------------------------- fallback helpers

def _exact(path: str) -> bool:
    name = os.path.basename(path)
    return name in EXACT_NAMES or os.path.splitext(name)[1].lower() in EXACT_EXT


def _raw_baseline(kind: str, raw: bytes | None, path: str | None) -> int | None:
    """Tokens the agent would spend reading this without leantext."""
    try:
        if kind == "html" and raw is not None:
            return tokens.count(raw.decode("utf-8", "replace"))
        if kind == "image" and path:
            return image_x.image_tokens(path)
        if kind == "pdf" and path:
            return _pdf_page_image_tokens(path)
    except Exception:  # noqa: BLE001 - a baseline must never break a read
        return None
    return None  # office: no fair raw baseline, savings rule skipped


def _raw_result(kind: str, target: str, raw: bytes | None, path: str | None, reason: str) -> Result:
    """What the agent gets when leantext steps aside.

    html/text: the original text. pdf: the pypdf text layer when there is one.
    image/office/textless pdf: empty markdown with raw=True, which tells the hook to let the
    agent's own Read go through untouched (for an image, that means the image itself).
    """
    md, method = "", "raw"
    if kind in ("html", "text") and raw is not None:
        md = raw.decode("utf-8", "replace")
    elif kind == "pdf" and path:
        try:
            from pypdf import PdfReader

            md = "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
            method = "raw(pypdf)"
        except Exception:  # noqa: BLE001
            md = ""
    return Result(md, kind, method, target, raw=True, fallback=reason)


def _extract_bounded(args: tuple, budget: float) -> Result:
    """Run _extract in a separate interpreter so a hung extractor can be killed, not abandoned.

    A plain subprocess (python -m leantext.router --worker), not multiprocessing: spawn mode
    re-runs the caller's __main__ script in the child, which breaks stdin/REPL/notebook callers
    and re-executes unguarded user scripts.
    """
    import pickle
    import subprocess

    try:
        proc = subprocess.run([sys.executable, "-m", "leantext.router", "--worker"],
                              input=pickle.dumps(args), capture_output=True, timeout=budget)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"extraction exceeded {budget:.0f}s budget") from None
    if proc.returncode != 0 or not proc.stdout:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(err[-1] if err else f"extractor exited {proc.returncode}")
    status, payload = pickle.loads(proc.stdout)
    if status == "err":
        raise RuntimeError(payload)
    return payload


def _worker_main() -> None:
    import pickle

    # Import under the real module name, so Result pickles as leantext.router.Result
    # rather than __main__.Result, which the parent couldn't load.
    from leantext import router

    args = pickle.loads(sys.stdin.buffer.read())
    try:
        out = ("ok", router._extract(*args))
    except Exception as e:  # noqa: BLE001
        out = ("err", f"{type(e).__name__}: {e}")
    sys.stdout.buffer.write(pickle.dumps(out))
    sys.stdout.buffer.flush()


def _log(res: Result, baseline: int | None) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        saved = None
        if not res.raw and baseline and res.out_tokens is not None:
            saved = round(1 - res.out_tokens / baseline, 4)
        entry = {"ts": time.time(), "source": res.source, "kind": res.kind, "method": res.method,
                 "mode": "raw" if res.raw else ("flagged" if res.suspicious else "lean"),
                 "reason": res.fallback or ("suspicious: " + "; ".join(res.suspicious) if res.suspicious else "ok"),
                 "raw_tokens": baseline, "lean_tokens": None if res.raw else res.out_tokens,
                 "saved": saved, "elapsed_s": res.elapsed_s, "cached": res.cached,
                 "suspicious": res.suspicious}
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001 - logging must never break a read
        pass


# ---------------------------------------------------------------- main entry

def convert(
    target: str,
    *,
    raw_mode: bool = False,
    report: bool = False,
    max_tokens: int | None = None,
    allow_vision: bool = True,
    use_cache: bool = True,
    keep_hidden: bool = False,
    edit_safe: bool = False,
    strict: bool = False,
    budget_s: float = TIME_BUDGET_S,
    min_savings: float = MIN_SAVINGS,
) -> Result:
    """Convert target to lean Markdown, or step aside with res.raw=True and a reason.

    edit_safe: also pass local .html files through untouched (plain text always passes through).
               Off by default because the hook already serves the original on a second read.
    strict:    if the structural checks flag the output (dropped code, tables, garbled text),
               step aside instead of returning a suspicious conversion. Off by default: the
               portal fallback can legitimately leave minor flags. Turn on in the benchmark.
    On any step-aside, markdown is empty and res.fallback says why, so callers that already
    pass through failed reads keep working.
    """
    start = time.monotonic()
    raw_mode = raw_mode or os.environ.get("LEANTEXT_RAW") == "1"
    if target.startswith(("tab:", "cdp:")):
        raise InputError("live browser tabs are planned (build step 6) and not implemented yet")

    is_url = bool(re.match(r"https?://", target, re.I))
    tmp: str | None = None
    raw: bytes | None = None
    ctype = ""

    if target == "-":
        raw = sys.stdin.buffer.read()
        kind = _kind_for("", "", raw)
        path = None
    elif is_url:
        raw, ctype = _fetch(target)
        kind = _kind_for(target, ctype, raw[:1024])
        path = None
    else:
        if not os.path.exists(target):
            raise InputError(f"{target}: no such file")
        path = target
        with open(path, "rb") as f:
            head = f.read(1024)
        kind = _kind_for(path, "", head)
        if kind in ("html", "text"):
            with open(path, "rb") as f:
                raw = f.read()
        if _exact(path):
            res = _raw_result(kind, target, raw, path, "exact content matters")
            res.elapsed_s = round(time.monotonic() - start, 3)
            _log(res, None)
            return res
        if edit_safe and kind in ("html", "text"):
            res = _raw_result(kind, target, raw, path, "editable text file")
            res.elapsed_s = round(time.monotonic() - start, 3)
            _log(res, None)
            return res

    # Binary content from a URL or stdin needs a real file for the extractors.
    if path is None and kind in ("pdf", "image", "office"):
        fd, tmp = tempfile.mkstemp(suffix=_ext_for(kind, ctype))
        with os.fdopen(fd, "wb") as f:
            f.write(raw or b"")
        path = tmp

    try:
        content_id = raw if raw is not None else open(path, "rb").read()  # noqa: SIM115
        ck = cache.key(VERSION_SALT, kind, str(raw_mode), str(allow_vision), str(keep_hidden), content_id)
        hit = cache.get(ck) if use_cache and not raw_mode else None

        baseline = None if raw_mode else _raw_baseline(kind, raw, path)

        def step_aside(reason: str, warnings: list[str] | None = None, sus: list[str] | None = None) -> Result:
            # Empty markdown = failed, so the hook lets the agent's own Read through untouched.
            r = Result("", kind, "raw", target, raw=True, fallback=reason)
            r.warnings = list(warnings or [])
            r.suspicious = list(sus or [])
            return r

        if raw_mode:
            res = _extract(kind, target, raw, path, True, allow_vision, keep_hidden)
            res.raw, res.fallback = True, "raw requested"
        elif hit:
            res = Result(**{k: v for k, v in hit.items() if k in Result.__dataclass_fields__})
            res.cached = True
        elif kind == "text":
            res = _extract(kind, target, raw, path, False, allow_vision, keep_hidden)
            res.raw, res.fallback = True, "plain text passthrough"
        else:
            try:
                res = _extract_bounded((kind, target, raw, path, False, allow_vision, keep_hidden),
                                       budget_s)
            except TimeoutError as e:
                res = step_aside(str(e))
            except Exception as e:  # noqa: BLE001
                res = step_aside(f"extractor error: {e}")
            else:
                if res.failed:
                    res = step_aside("extraction produced nothing", res.warnings, res.suspicious)
                elif strict and res.suspicious:
                    res = step_aside("checks flagged output: " + "; ".join(res.suspicious),
                                     res.warnings, res.suspicious)
                elif kind == "image" and getattr(res, "_coverage", None) is not None \
                        and res._coverage < MIN_IMAGE_TEXT_COVERAGE and "vision" not in res.method:
                    res = step_aside(f"mostly visual ({res._coverage:.0%} text coverage)")
                elif baseline:
                    out = tokens.count(res.markdown)
                    if out > baseline * (1 - min_savings):
                        res = step_aside(f"savings too small ({1 - out / baseline:.0%})", res.warnings)
            if use_cache and not res.raw and not res.failed:
                cache.put(ck, {k: getattr(res, k) for k in ("markdown", "kind", "method", "source", "warnings", "suspicious")})

        # Never truncate exact or raw content: the agent asked for the real thing.
        if max_tokens and not res.raw:
            res.markdown, res.truncated = tokens.truncate(res.markdown, max_tokens)
        res.out_tokens = tokens.count(res.markdown) if res.markdown else 0
        if report:
            res.tokenizer = tokens.backend()
            res.baselines = _baselines(kind, raw, path)
        res.elapsed_s = round(time.monotonic() - start, 3)
        _log(res, baseline)
        return res
    finally:
        if tmp:
            os.unlink(tmp)


def _extract(kind, target, raw, path, raw_mode, allow_vision, keep_hidden=False) -> Result:
    src = target
    if kind == "html":
        page = raw.decode("utf-8", "replace")
        if raw_mode:
            return Result(page, kind, "raw", src)
        md, method, used = html_x.extract(page, url=target if target.startswith("http") else None,
                                          keep_hidden=keep_hidden)
        wall = checks.blocked(page, md)
        if wall:
            return Result("", kind, method, src,
                          warnings=[wall + " (live browser tabs are planned; for now save the page "
                                    "from your browser and pass the .html file)"])
        sus = checks.check_html(used, md)
        if sus:
            # trafilatura found no single article (news portals, link hubs). Retry on the whole page
            # and keep the retry only if it clears more of the structural checks.
            alt = html_x.extract_portal(used)
            alt_sus = checks.check_html(used, alt)
            if len(alt_sus) < len(sus):
                return Result(alt, kind, method.replace("trafilatura", "portal-fallback", 1), src,
                              warnings=["trafilatura kept too little (" + "; ".join(sus) +
                                        "); used whole-page portal fallback"],
                              suspicious=alt_sus)
        return Result(md, kind, method, src, suspicious=sus)

    if kind in ("pdf", "office"):
        if raw_mode and kind == "pdf":
            from pypdf import PdfReader

            text = "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
            return Result(text, kind, "raw(pypdf)", src)
        md, method, w, sus = pdf_x.extract(path, allow_vision=allow_vision and not raw_mode)
        return Result(md, kind, method, src, warnings=w, suspicious=sus + checks.check_text(md))

    if kind == "image":
        if raw_mode:
            text, conf, *_ = image_x.ocr(path)
            return Result(text + "\n", kind, f"raw-ocr(conf={conf:.0f})", src)
        out = image_x.extract(path, allow_vision=allow_vision)
        md, method, w = out[:3]
        res = Result(md, kind, method, src, warnings=w, suspicious=checks.check_text(md, 10))
        # Optional 4th value: share of the image covered by text (0-1). Lets the router send
        # mostly-visual screenshots (charts, badges, layouts) to the agent as the image itself.
        if len(out) > 3:
            res._coverage = out[3]
        return res

    text = raw.decode("utf-8", "replace")
    return Result(text, "text", "passthrough", src)


if __name__ == "__main__" and "--worker" in sys.argv:
    _worker_main()
