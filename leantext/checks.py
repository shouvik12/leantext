"""Structural sanity checks: did extraction silently drop code, tables, or most of the content?

Compares structure in the source against structure in the Markdown output.
Returns human-readable reasons; empty list means nothing suspicious.
"""
from __future__ import annotations

import re

_FENCE = re.compile(r"^(```|~~~)", re.M)
_INDENTED_CODE = re.compile(r"(?:^(?: {4}|\t).+\n?){2,}", re.M)
_MD_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", re.M)
_MD_HEADING = re.compile(r"^#{1,6}\s", re.M)


def _md_code_blocks(md: str) -> int:
    return len(_FENCE.findall(md)) // 2 + len(_INDENTED_CODE.findall(md))


_NON_CONTENT = re.compile(r"<(script|style|noscript|template)\b.*?</\1>", re.S | re.I)


def _html_main(html: str) -> str:
    """Prefer <article>/<main> when present so sidebar widgets don't inflate the counts.

    Script and template bodies are removed first: tags inside JS strings aren't page structure.
    """
    html = _NON_CONTENT.sub(" ", html)
    for tag in ("article", "main"):
        m = re.search(rf"<{tag}\b.*?</{tag}>", html, re.S | re.I)
        if m:
            return m.group(0)
    return html


def _visible_text_len(html: str) -> int:
    t = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return len(re.sub(r"\s+", " ", t))


def _data_tables(html: str) -> int:
    """Tables that hold data, not page layout.

    Hacker News builds its whole page from nested tables; flattening those loses nothing. A table
    counts as layout when it nests inside, or contains, another table and has no header cells
    (<th>, <thead>, <caption>). Everything else counts as data.
    """
    from lxml import etree
    from lxml import html as lh

    try:
        root = lh.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return len(re.findall(r"<table\b", html, re.I))
    n = 0
    for t in root.iter("table"):
        headed = bool(t.xpath(".//th | .//thead | ./caption"))
        nested = bool(t.xpath("ancestor::table | .//table"))
        if headed or not nested:
            n += 1
    return n


def check_html(html: str, md: str) -> list[str]:
    reasons: list[str] = []
    main = _html_main(html)
    src_pre = len(re.findall(r"<pre\b", main, re.I))
    src_tables = _data_tables(main)
    src_heads = len(re.findall(r"<h[1-6]\b", main, re.I))
    out_code = _md_code_blocks(md)
    out_tables = len(_MD_TABLE_SEP.findall(md))
    out_heads = len(_MD_HEADING.findall(md))

    if src_pre and out_code < src_pre:
        reasons.append(f"code blocks: {src_pre} in source, {out_code} in output")
    if src_tables and out_tables < src_tables:
        reasons.append(f"tables: {src_tables} in source, {out_tables} in output")
    if src_heads >= 3 and out_heads == 0:
        reasons.append(f"headings: {src_heads} in source, none in output")

    script_bytes = sum(len(m.group(0)) for m in re.finditer(r"<script\b.*?</script>", html, re.S | re.I))
    if len(html) > 20000 and script_bytes > 0.5 * len(html) and _visible_text_len(_NON_CONTENT.sub(" ", html)) < 2000:
        reasons.append(f"page is {script_bytes / len(html):.0%} JavaScript with little visible text; "
                       "content probably loads at runtime and isn't in this HTML")

    src_len = _visible_text_len(main)
    if src_len > 2000 and len(md) < 0.10 * src_len:
        reasons.append(f"output is {len(md) / src_len:.0%} of main-content text; likely truncated")
    reasons += check_title(html, md)
    reasons += check_garbled(md)
    return reasons


_TITLE_SPLIT = re.compile(r"\s+[·|\u2013\u2014-]\s+")
_WORD = re.compile(r"[a-z0-9][a-z0-9'+#.-]{2,}")


def _meta(html: str, prop: str) -> str:
    m = re.search(rf'<meta[^>]+property=["\']{prop}["\'][^>]+content=["\']([^"\']+)', html, re.I) \
        or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{prop}["\']', html, re.I)
    return m.group(1) if m else ""


def _page_title(html: str) -> tuple[str, str]:
    """(title, site words): the page's own title, and the site/brand text to ignore."""
    import html as htmllib

    raw = _meta(html, "og:title")
    if not raw:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        raw = m.group(1) if m else ""
    raw = htmllib.unescape(re.sub(r"\s+", " ", raw)).strip()
    parts = _TITLE_SPLIT.split(raw)
    # "Add basic GitHub Actions CI · Issue #1 · astral-sh/uv": the trailing parts and
    # og:site_name are site chrome, and site names show up all over a site's menus.
    site = " ".join(parts[1:]) + " " + _meta(html, "og:site_name")
    return parts[0] if parts else "", site


def check_title(html: str, md: str, head_chars: int = 2000) -> list[str]:
    """Right page, wrong content: the page's own title should appear near the top of the output.

    Catches extractions that kept site chrome (GitHub's navigation menu) instead of the article.
    Brand words are ignored, because a menu full of "GitHub Actions" would otherwise match
    "Add basic GitHub Actions CI". Skipped for short or generic titles.
    """
    title, site = _page_title(html)
    brand = set(_WORD.findall(site.lower()))
    words = set(_WORD.findall(title.lower())) - brand
    if len(words) < 3:
        return []
    head = md[:head_chars].lower()
    found = sum(1 for w in words if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", head))
    if found / len(words) < 0.6:
        return [f"page title missing from output ({found}/{len(words)} title words); "
                "extraction may have kept navigation instead of content"]
    return []
    head = md[:head_chars].lower()
    found = sum(1 for w in words if w in head)
    if found / len(words) < 0.5:
        return [f"page title missing from output ({found}/{len(words)} title words); "
                "extraction may have kept navigation instead of content"]
    return []


# Replacement characters and stray control bytes: what broken OCR and bad decoding leave behind.
_JUNK = re.compile(r"[\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]")


def check_garbled(md: str, max_ratio: float = 0.05) -> list[str]:
    text = md.strip()
    if not text:
        return []
    ratio = len(_JUNK.findall(text)) / len(text)
    if ratio > max_ratio:
        return [f"output is {ratio:.0%} unreadable characters; looks garbled"]
    return []


def check_text(md: str, min_chars: int = 40) -> list[str]:
    if len(md.strip()) < min_chars:
        return [f"output is only {len(md.strip())} characters"]
    return check_garbled(md)


# Bot walls and JavaScript-only shells: a fetch "succeeds" but there's no content in the HTML.
_WALL = re.compile(
    r"client challenge|just a moment\.\.\.|checking your browser|verify you are (a )?human|"
    r"enable javascript|javascript is (disabled|required)|attention required|cf-browser-verification|"
    r"captcha|access denied|are you a robot",
    re.I,
)
_SPA_ROOT = re.compile(r'<div[^>]+id="(root|app|__next|__nuxt|svelte)"[^>]*>\s*</div>', re.I)


def blocked(html: str, md: str) -> str | None:
    """Return a reason when the page is a bot challenge or an empty client-rendered shell."""
    visible = _visible_text_len(html)
    if visible < 3000 and _WALL.search(md + " " + html[:20000]):
        return "page is a bot challenge or asks for JavaScript; nothing to extract from this HTML"
    if visible < 300 and (_SPA_ROOT.search(html) or html.lower().count("<script") >= 3):
        return "page renders client-side (empty HTML shell); it needs a real browser"
    return None
