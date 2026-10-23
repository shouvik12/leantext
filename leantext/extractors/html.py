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


_LINE_BLOCKS = {"div", "p", "li"}


def _same_site(href: str, url: str | None) -> bool:
    """True for "#frag" links, relative links, and links to the page's own host."""
    from urllib.parse import urljoin, urlsplit

    if href.startswith("#"):
        return True
    if not href:
        return False
    if not url:
        return not urlsplit(href).netloc
    return urlsplit(urljoin(url, href)).netloc == urlsplit(url).netloc


def normalize_structure(html: str, url: str | None = None) -> str:
    """Fix two layouts that trafilatura flattens, both from Stripe's API reference.

    - A heading that is nothing but a link (<h1><a href=...>Create a customer</a></h1>) reads as
      navigation and was dropped, so the page lost its endpoint titles. The link is unwrapped when
      it stays on the same site (an anchor, or a path on the page's own host). Such headings were
      being dropped whole before, so unwrapping only adds text. Off-site links are left alone.
    - A <code> block drawn as one <div> per line, with no newline characters, came out as one
      long line. Such blocks become <pre><code> with real line breaks.
    """
    from lxml import etree
    from lxml import html as lh

    try:
        root = lh.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return html
    changed = False

    # GitHub READMEs: <div class="highlight highlight-source-python" data-snippet-clipboard-copy-content=
    # "<exact code>"><pre>...</pre></div>. The language lives on the div and the verbatim code
    # (blank lines included) in the clipboard attribute. Rebuild a plain <pre><code class=language-x>.
    # Without this, every fence lost its language and one-line blocks became inline code.
    for div in root.xpath('//div[contains(@class, "highlight") and @data-snippet-clipboard-copy-content]'):
        if not div.xpath("./pre"):
            continue
        m = re.search(r"highlight-(?:source|text)-([\w+#-]+)", div.get("class") or "")
        aliases = {"shell": "bash", "shell-session": "console", "js": "javascript", "ts": "typescript"}
        lang = aliases.get(m.group(1), m.group(1)) if m else ""
        pre = lh.Element("pre")
        code = lh.Element("code", **({"class": f"language-{lang}"} if lang else {}))
        code.text = div.get("data-snippet-clipboard-copy-content")
        pre.append(code)
        pre.tail = div.tail
        div.getparent().replace(div, pre)
        changed = True

    for h in root.xpath("//h1|//h2|//h3|//h4|//h5|//h6"):
        links = h.xpath(".//a")
        text = h.text_content().strip()
        # One link carrying most of the heading (Stripe adds a small "v1" badge beside it).
        if len(links) == 1 and text and len(links[0].text_content().strip()) >= 0.6 * len(text) \
                and _same_site(links[0].get("href") or "", url):
            links[0].drop_tag()
            changed = True

    for code in list(root.iter("code")):
        lines = [c for c in code if isinstance(c.tag, str) and c.tag.lower() in _LINE_BLOCKS]
        if len(lines) < 2 or "\n" in code.text_content():
            continue
        text = "\n".join(c.text_content().rstrip() for c in lines)
        in_pre = any(isinstance(a.tag, str) and a.tag.lower() == "pre" for a in code.iterancestors())
        new_code = lh.Element("code")
        new_code.text = text
        if in_pre:
            new_code.tail = code.tail
            code.getparent().replace(code, new_code)
        else:
            pre = lh.Element("pre")
            pre.append(new_code)
            pre.tail = code.tail
            code.getparent().replace(code, pre)
        changed = True

    return lh.tostring(root, encoding="unicode") if changed else html


_NO_SPACE_INSIDE = {"pre", "code", "kbd", "samp", "script", "style", "textarea"}


def space_adjacent_spans(html: str) -> str:
    """Put a space between sibling <span>s that touch with no whitespace, outside code.

    Stripe's API reference renders each parameter as <span>email</span><span>string</span>,
    which came out as "emailstring" and "payment_methodstring": names an agent can't use.
    Inside <pre>/<code>, syntax highlighting splits single tokens into spans, so those are left alone.
    """
    from lxml import etree
    from lxml import html as lh

    try:
        root = lh.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return html
    changed = False
    for span in root.iter("span"):
        nxt = span.getnext()
        if nxt is None or not isinstance(nxt.tag, str) or nxt.tag.lower() != "span":
            continue
        if (span.tail or "") != "":
            continue
        if any(isinstance(a.tag, str) and a.tag.lower() in _NO_SPACE_INSIDE for a in span.iterancestors()):
            continue
        left, right = span.text_content(), nxt.text_content()
        if left[-1:].isalnum() and right[:1].isalnum():
            span.tail = " "
            changed = True
    return lh.tostring(root, encoding="unicode") if changed else html


def drop_empty_links(html: str) -> str:
    """Remove links with no visible text and no image.

    GitHub puts an icon-only permalink (<a class="anchor"><svg aria-hidden>) beside every README
    heading. trafilatura reads heading + empty link as navigation and dropped all 24 headings of a
    real README, keeping only the long ones. An empty link carries nothing an agent can read.
    """
    from lxml import etree
    from lxml import html as lh

    try:
        root = lh.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return html
    empty = [a for a in root.iter("a")
             if not a.text_content().strip() and not a.xpath(".//img") and a.getparent() is not None]
    if not empty:
        return html
    for a in empty:
        a.drop_tree()  # keeps the link's tail text
    return lh.tostring(root, encoding="unicode")


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
    html = drop_empty_links(html)
    html = normalize_structure(html, url)
    html = space_adjacent_spans(html)
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
        md = _restore_lists(html, _restore_code(html, md))
        return md.strip() + "\n", "trafilatura" + note, html

    body = _CHROME.sub("", html)
    md = markdownify(body, heading_style="ATX", code_language_callback=None)
    md = squeeze_blank_lines(md).strip()
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

    # One-line <pre> blocks come out of trafilatura as inline `code` on their own line, sometimes
    # glued to the next fence ("`pip install x````"). Put them back as real blocks.
    for key, (code, lang) in originals.items():
        if "\n" in code or not code.strip():
            continue
        pat = re.compile(r"^[ \t]*`" + re.escape(code.strip()) + r"`(?=```|[ \t]*$)", re.M)
        md = pat.sub(lambda _m, c=code.rstrip(), lg=lang: f"```{lg}\n{c}\n```\n", md)
    md = _FENCED.sub(repl, md)
    return squeeze_blank_lines(md)


def _restore_lists(html: str, md: str) -> str:
    """Put back short lists that trafilatura dropped as if they were menus.

    trafilatura's link-density filter removes short lists whose items start with a link. FastAPI's
    README lost "Requirements: Starlette for the web parts / Pydantic for the data parts" this way.
    A list is restored only when the paragraph right before it is in the output, which real content
    lists have ("FastAPI stands on the shoulders of giants:") and navigation menus don't.
    """
    from lxml import etree
    from lxml import html as lh

    try:
        root = lh.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return md
    def plain(text: str) -> str:
        text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)   # links and images -> their text
        text = re.sub(r"[*_`\\]", "", text)
        return " ".join(text.split())

    def own_text(li) -> str:
        """An item's text without its nested sub-lists."""
        parts = [li.text or ""]
        for child in li:
            if not (isinstance(child.tag, str) and child.tag.lower() in ("ul", "ol")):
                parts.append(child.text_content())
            parts.append(child.tail or "")
        return plain("".join(parts))

    flat = plain(md)
    for lst in root.xpath("//ul[not(ancestor::li)]|//ol[not(ancestor::li)]"):
        items = [li for li in lst if isinstance(li.tag, str) and li.tag.lower() == "li"]
        texts = [own_text(li) for li in items]
        if not items or len(items) > 12 or not all(texts) or any(t in flat for t in texts):
            continue
        # Every item just a link ("See full compatibility", "Learn more"): a menu, leave it out.
        if all(plain(" ".join(a.text_content() for a in li.iter("a"))) == t for li, t in zip(items, texts)):
            continue
        prev = lst.getprevious()
        if prev is None or not isinstance(prev.tag, str) or prev.tag.lower() not in ("p", "div"):
            continue
        intro = plain(prev.text_content())
        if len(intro) < 15:
            continue
        lines = md.split("\n")
        at = next((i for i, line in enumerate(lines) if plain(line).endswith(intro)), None)
        if at is None:
            continue
        ordered = lst.tag.lower() == "ol"
        block = []
        for n, li in enumerate(items, 1):
            item = markdownify(lh.tostring(li, encoding="unicode"), heading_style="ATX").strip()
            item = re.sub(r"^[*+-]\s+", "", item)
            block.append(f"{n}. {item}" if ordered else f"- {item}")
        lines[at + 1:at + 1] = [""] + block
        md = "\n".join(lines)
        flat = plain(md)
    return md


def squeeze_blank_lines(md: str) -> str:
    """Collapse runs of blank lines to one, except inside fenced code, where they're part of the
    code (Python puts two blank lines between top-level definitions)."""
    parts = re.split(r"(^```[^\n]*\n.*?^```[ \t]*$)", md, flags=re.S | re.M)
    return "".join(p if p.startswith("```") else re.sub(r"\n{3,}", "\n\n", p) for p in parts)


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
