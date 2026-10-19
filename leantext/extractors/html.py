"""HTML / blog pages -> Markdown.

1. Drop what a reader can't see by default (hidden elements, unselected ARIA tab panels).
2. trafilatura main-content extraction to Markdown.
3. Restore code blocks verbatim from the source, with language tags.
Fallback: markdownify over the whole <body> with obvious chrome stripped.
"""
from __future__ import annotations

import html as htmllib
import re

import trafilatura
from markdownify import markdownify

_CHROME = re.compile(
    r"<(script|style|noscript|nav|header|footer|aside|form|iframe|svg)\b.*?</\1>",
    re.S | re.I,
)
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)


def drop_hidden(html: str) -> tuple[str, int]:
    """Remove content that isn't visible without interaction. Returns (html, elements_removed).

    Removed: elements with the `hidden` attribute, aria-hidden="true", inline display:none or
    visibility:hidden, <template>, closed <dialog>, and tab panels whose controlling tab has
    aria-selected="false" (the standard ARIA tabs pattern; PyPI's "Release files" is one).
    """
    from lxml import etree
    from lxml import html as lh

    try:
        root = lh.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return html, 0

    unselected = _hidden_tabpanels(root)
    doomed = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.lower()
        if (
            el.get("hidden") is not None
            or el.get("aria-hidden") == "true"
            or _HIDDEN_STYLE.search(el.get("style") or "")
            or tag == "template"
            or (tag == "dialog" and el.get("open") is None)
            or el in unselected
        ):
            doomed.append(el)

    doomed_set = set(doomed)
    removed = 0
    for el in doomed:
        if el.getparent() is not None and not any(a in doomed_set for a in el.iterancestors()):
            el.drop_tree()
            removed += 1
    if not removed:
        return html, 0
    return lh.tostring(root, encoding="unicode"), removed


def _hidden_tabpanels(root) -> set:
    """Tab panels a reader can't see until they click a tab.

    A panel's state comes from its tab: the element that controls it (aria-controls) or labels it
    (aria-labelledby). A panel is hidden when its tab says aria-selected="false", or when a sibling
    panel is the selected one. Groups with no selected panel at all are left alone, so pages with
    incomplete tab markup don't lose content.
    """
    by_id = {el.get("id"): el for el in root.xpath("//*[@id]")}
    controls = {t.get("aria-controls"): t for t in root.xpath("//*[@aria-controls]")}

    def state(panel) -> str | None:
        tab = controls.get(panel.get("id"))
        if tab is None:
            tab = by_id.get(panel.get("aria-labelledby") or "")
        return None if tab is None else tab.get("aria-selected")

    groups: dict = {}
    for p in root.xpath('//*[@role="tabpanel"]'):
        groups.setdefault(p.getparent(), []).append(p)

    hidden = set()
    for panels in groups.values():
        states = [state(p) for p in panels]
        has_selected = "true" in states
        for p, s in zip(panels, states):
            if s == "false" or (has_selected and s != "true"):
                hidden.add(p)
    return hidden


def absolutize_links(html: str, url: str) -> str:
    """Resolve relative hrefs against the page URL (honouring <base href>) before extraction.

    trafilatura joins relative links against the site root, so `#frag` and `../ref.html` on
    https://docs.python.org/3/tutorial/x.html came out as https://docs.python.org#frag and
    https://docs.python.org/ref.html. Agents follow links; broken ones cost them a turn.
    """
    from lxml import etree
    from lxml import html as lh

    try:
        root = lh.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return html
    root.make_links_absolute(url, resolve_base_href=True, handle_failures="ignore")
    return lh.tostring(root, encoding="unicode")


def extract(html: str, url: str | None = None, keep_hidden: bool = False) -> tuple[str, str, str]:
    """Return (markdown, method, html_actually_used)."""
    removed = 0
    if not keep_hidden:
        html, removed = drop_hidden(html)
    note = f", dropped {removed} hidden" if removed else ""
    if url:
        html = absolutize_links(html, url)

    md = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_formatting=True,
        include_links=True,
        include_images=False,
        include_comments=False,
        favor_recall=True,
    )
    if md and md.strip():
        return _restore_code(html, md).strip() + "\n", "trafilatura" + note, html

    body = _CHROME.sub("", html)
    md = markdownify(body, heading_style="ATX", code_language_callback=None)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md + "\n", "markdownify-fallback" + note, html


_PRE = re.compile(r"<pre\b[^>]*>(.*?)</pre>", re.S | re.I)
_LANG = re.compile(
    r'class="[^"]*\b(?:language|lang)-([\w+#-]+)|\b(?:data-)?lang="([\w+#-]+)"', re.I
)
_FENCED = re.compile(r"^```[^\n]*\n(.*?)\n?```[ \t]*$", re.S | re.M)


def _norm(code: str) -> str:
    return "\n".join(line.strip() for line in code.splitlines() if line.strip())


_OPEN = re.compile(r"<pre\b[^>]*>\s*(?:<code\b[^>]*>)?", re.I)


def _lang(block: str) -> str:
    """Language from the opening <pre> tag and an immediate <code> child, not from spans inside."""
    m = _LANG.search(_OPEN.match(block).group(0))
    return (m.group(1) or m.group(2)) if m else ""


def _restore_code(html: str, md: str) -> str:
    """Trafilatura drops blank lines and language tags inside code. Put the original back.

    Each fenced block in the output is matched to a <pre> in the source by its non-blank content,
    then replaced with the verbatim source text and a language tag when the source declares one.
    """
    originals: dict[str, tuple[str, str]] = {}
    for m in _PRE.finditer(html):
        code = htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip("\n")
        originals.setdefault(_norm(code), (code, _lang(m.group(0))))

    def repl(m: re.Match) -> str:
        hit = originals.get(_norm(m.group(1)))
        if not hit:
            return m.group(0)
        code, lang = hit
        # Trailing blank line keeps a closing fence off the next heading or paragraph.
        return f"```{lang}\n{code}\n```\n"

    md = _FENCED.sub(repl, md)
    return re.sub(r"\n{3,}", "\n\n", md)


# --- Portal fallback -------------------------------------------------------------------------
# trafilatura looks for one main article. News portals (rediff.com and similar) have none: the
# content is a wall of linked headlines, which trafilatura discards as navigation. When the
# structural check flags its output, the router retries with this whole-page conversion.

_PORTAL_DROP = re.compile(
    r"<(script|style|noscript|template|iframe|svg|form|select|button)\b.*?</\1>", re.S | re.I
)
_MD_LINK = re.compile(r"(!?)\[([^\]]*)\]\([^)]*\)")


def _clean_portal(md: str) -> str:
    out = []
    for line in md.splitlines():
        s = line.strip()
        links = len(re.findall(r"(?<!!)\[[^\]]*\]\([^)]*\)", s))
        # A line of 3+ glued links with no heading marker is a menu, not content.
        if links >= 3 and not s.startswith("#"):
            continue
        s = _MD_LINK.sub(lambda m: "" if m.group(1) else m.group(2), s).strip()
        s = re.sub(r"\s{2,}", " ", s)
        if not re.search(r"[A-Za-z0-9]", s.lstrip("#")):
            continue
        # Bare section headings left with no text after unlinking ("## ").
        if re.fullmatch(r"#+", s):
            continue
        out.append(s)
    text = "\n\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def extract_portal(html: str) -> str:
    """Whole-page Markdown with link URLs, images and menu rows removed; headings kept."""
    m = re.search(r"<body\b.*?</body>", html, re.S | re.I)
    body = _PORTAL_DROP.sub(" ", m.group(0) if m else html)
    md = markdownify(body, heading_style="ATX")
    return _trim_to_title(html, _clean_portal(md))


def _trim_to_title(html: str, md: str) -> str:
    """Drop site chrome above the page's own title heading.

    App-style pages (GitHub issues, dashboards) put hundreds of menu lines before the content.
    When a heading in the output matches the page title, that's where the content starts.
    No matching heading: leave the output alone.
    """
    from ..checks import _WORD, _page_title

    title, site = _page_title(html)
    words = set(_WORD.findall(title.lower())) - set(_WORD.findall(site.lower()))
    if len(words) < 2:
        return md
    lines = md.split("\n")
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("#"):
            continue
        low = line.lower()
        hit = sum(1 for w in words if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", low))
        if hit / len(words) >= 0.8:
            return "\n".join(lines[i:]).strip() + "\n"
    return md
