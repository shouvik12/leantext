import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from leantext import checks, convert

F = Path(__file__).parent / "fixtures"
os.environ["LEANTEXT_NO_CACHE"] = "1"


def run(*args):
    return subprocess.run([sys.executable, "-m", "leantext", *args, "--no-cache"],
                          capture_output=True, text=True)


# ---- HTML -------------------------------------------------------------------------------
def test_blog_strips_chrome():
    md = convert(str(F / "blog.html"), use_cache=False).markdown
    for junk in ("cookies", "Category 7", "Footer link", "Related post", "newsletter", "dataLayer"):
        assert junk not in md, junk


def test_blog_keeps_code_verbatim_with_language():
    md = convert(str(F / "blog.html"), use_cache=False).markdown
    assert "```python\nimport random, time\n\ndef backoff(" in md
    assert "```go\nif budget.Allow() {" in md


def test_blog_keeps_table_and_headings():
    r = convert(str(F / "blog.html"), use_cache=False)
    assert "| Full jitter | Smooth | Medium |" in r.markdown
    assert "## Retry budgets" in r.markdown
    assert r.suspicious == []


def test_blog_report_shows_savings():
    r = convert(str(F / "blog.html"), use_cache=False, report=True)
    assert r.out_tokens < r.baselines["naive Markdown (whole page)"] < r.baselines["raw HTML"]


# ---- PDF --------------------------------------------------------------------------------
def test_text_pdf():
    r = convert(str(F / "report.pdf"), use_cache=False)
    assert r.method in ("pdftotext", "pypdf")
    assert "Infra Report Q3 - Page 2" in r.markdown and "\f" not in r.markdown


def test_scanned_pdf_routes_to_ocr():
    r = convert(str(F / "scanned.pdf"), use_cache=False, allow_vision=False)
    assert r.method == "pdf-scanned->ocr"
    assert "Total due: 1,140.00" in r.markdown


# ---- Images -----------------------------------------------------------------------------
def test_dark_screenshot_ocr_exact():
    md = convert(str(F / "screenshot.png"), use_cache=False, allow_vision=False).markdown
    assert "devtv@1.4.0" in md
    assert "error TS2339: Property 'channelId' does not exist on type 'Spotlight'." in md


def test_blank_image_fails_with_exit_2(tmp_path):
    p = tmp_path / "blank.png"
    Image.new("RGB", (400, 200), "white").save(p)
    r = run(str(p), "--no-vision")
    assert r.returncode == 2
    assert "extraction failed" in r.stderr


# ---- Checks -----------------------------------------------------------------------------
def test_check_flags_dropped_code_and_tables():
    html = "<article><h1>a</h1><pre>x=1</pre><pre>y=2</pre><table><tr><td>1</td></tr></table></article>"
    reasons = checks.check_html(html, "# a\n\n```\nx=1\n```\n")
    assert any("code blocks: 2 in source, 1" in r for r in reasons)
    assert any("tables: 1 in source, 0" in r for r in reasons)


def test_check_flags_tiny_output():
    html = "<article>" + "<p>" + "word " * 2000 + "</p></article>"
    assert any("likely truncated" in r for r in checks.check_html(html, "word word"))


# ---- CLI contract -----------------------------------------------------------------------
def test_missing_file_exit_1():
    r = run("does/not/exist.pdf")
    assert r.returncode == 1 and "no such file" in r.stderr


def test_tab_not_implemented_exit_1():
    assert run("tab:1").returncode == 1


def test_raw_bypasses_extraction():
    r = run(str(F / "blog.html"), "--raw")
    assert r.returncode == 0 and "<nav>" in r.stdout


def test_max_tokens_truncates_with_marker():
    r = convert(str(F / "report.pdf"), use_cache=False, max_tokens=100)
    assert r.truncated and "leantext: truncated" in r.markdown


def test_report_goes_to_stderr_not_stdout():
    r = run(str(F / "blog.html"), "--report")
    assert "leantext report" in r.stderr and "leantext report" not in r.stdout


def test_json_output():
    import json

    r = run(str(F / "screenshot.png"), "--json", "--no-vision")
    d = json.loads(r.stdout)
    assert d["kind"] == "image" and d["tokens"]["output"] > 0


# ---- Real pages (tests/fixtures/real/SOURCES.md) ---------------------------------------
import shutil

R = F / "real"


def test_pypi_drops_hidden_tab_panels():
    r = convert(str(R / "pypi-uv.html"), use_cache=False)
    assert "SHA-256" not in r.markdown and "Release files" not in r.markdown
    assert "dropped" in r.method and r.suspicious == []
    assert "uv is licensed under either of" in r.markdown


def test_keep_hidden_brings_them_back():
    r = convert(str(R / "pypi-uv.html"), use_cache=False, keep_hidden=True)
    assert "SHA-256" in r.markdown


def test_code_fence_lang_attr_and_no_leading_blank():
    md = convert(str(R / "pypi-uv.html"), use_cache=False).markdown
    assert "```bash\n# On macOS and Linux.\ncurl -LsSf https://astral.sh/uv/install.sh | sh\n```" in md


def test_bot_challenge_fails_cleanly():
    r = convert(str(R / "pypi-challenge.html"), use_cache=False)
    assert r.failed and "bot challenge" in r.warnings[0]
    assert run(str(R / "pypi-challenge.html")).returncode == 2


def test_js_app_shell_flagged_without_false_structure_reasons():
    r = convert(str(R / "devtv-index.html"), use_cache=False)
    assert any("JavaScript" in s for s in r.suspicious)
    assert not any(s.startswith(("headings", "tables")) for s in r.suspicious)


def test_two_column_pdf_is_not_glued():
    from leantext.extractors.pdf import glued_ratio

    r = convert(str(R / "compressed.tracemonkey-pldi-09.pdf"), use_cache=False)
    assert r.method == ("pdftotext" if shutil.which("pdftotext") else "pypdf")
    assert glued_ratio(r.markdown) < 0.02
    assert "Dynamic languages such as JavaScript are more difficult to compile" in " ".join(r.markdown.split())


def test_graphics_heavy_pdf_flagged():
    r = convert(str(R / "alphatrans.pdf"), use_cache=False)
    assert any("mostly graphics" in s for s in r.suspicious)


def test_real_table_screenshot():
    md = convert(str(R / "rich-table.png"), use_cache=False, allow_vision=False).markdown
    for key in ("Production Budget", "May 25, 2018", "Solo: A Star Wars Story", "$275,000,0000",
                "$393,151,347", "$1,332,539,889"):
        assert key in md, key


def test_real_traceback_keeps_colored_text():
    md = " ".join(convert(str(R / "rich-traceback.png"), use_cache=False, allow_vision=False).markdown.split())
    for key in ("ZeroDivisionError: division by zero", "def divide_by", "4294967296, 65536", "result = 65536.0",
                "show_locals=True"):
        assert key in md, key


def test_money_join_leaves_tuples_alone():
    from leantext.extractors.image import _fix_common

    assert _fix_common("$275,000, 0000 and (1000, 200)") == "$275,000,0000 and (1000, 200)"


def test_tags_inside_scripts_are_not_structure():
    html = "<html><body><p>" + "text " * 50 + "</p><script>x='<h2>a</h2><h2>b</h2><h2>c</h2><table>'</script></body></html>"
    assert checks.check_html(html, "text " * 50) == []


# ---- Portal pages and layout tables (live-site run, 2026-10-19) ------------------------------
def test_portal_page_keeps_headlines_via_fallback():
    # Modeled on rediff.com: trafilatura dropped all headings and kept only timestamps.
    r = convert(str(F / "real/portal-headlines.html"), use_cache=False)
    assert r.method.startswith("portal-fallback")
    assert r.suspicious == []
    assert "### News headline number 3 about something that happened today" in r.markdown
    assert "## Cricket & Sports" in r.markdown


def test_portal_fallback_strips_urls_and_menus():
    md = convert(str(F / "real/portal-headlines.html"), use_cache=False).markdown
    assert "https://" not in md and "Menu 3" not in md and "var x" not in md


def test_layout_tables_are_not_suspicious():
    # Modeled on news.ycombinator.com: nested layout tables, no header cells.
    r = convert(str(F / "real/hn-layout-tables.html"), use_cache=False)
    assert not any("tables:" in s for s in r.suspicious)
    assert "Story title 7 for the front page" in r.markdown


def test_data_table_loss_still_flagged():
    html = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    assert any("tables:" in s for s in checks.check_html(html, "just text"))


# ---- OCR engine selection (Tesseract optional) ----------------------------------------------
def test_ocr_engine_order(monkeypatch):
    from leantext.extractors import image

    monkeypatch.delenv("LEANTEXT_OCR", raising=False)
    monkeypatch.setattr(image.shutil, "which", lambda b: None)
    monkeypatch.setattr(image.sys, "platform", "linux")
    assert image.engines() == ["rapidocr"]
    monkeypatch.setattr(image.sys, "platform", "darwin")
    assert image.engines() == ["apple", "rapidocr"]
    monkeypatch.setattr(image.shutil, "which", lambda b: "/usr/bin/" + b)
    assert image.engines() == ["tesseract", "apple", "rapidocr"]
    monkeypatch.setenv("LEANTEXT_OCR", "rapidocr")
    assert image.engines() == ["rapidocr"]


def test_ocr_falls_back_when_engine_fails(monkeypatch):
    from leantext.extractors import image

    monkeypatch.setattr(image, "engines", lambda: ["tesseract", "rapidocr"])
    monkeypatch.setattr(image, "_ocr_tesseract", lambda p: (_ for _ in ()).throw(OSError("not installed")))
    text, *_, engine = image.ocr_with_engine(str(R / "rich-table.png"))
    assert engine == "rapidocr" and "Production Budget" in text


def test_rapidocr_without_tesseract(monkeypatch):
    monkeypatch.setenv("LEANTEXT_OCR", "rapidocr")
    md = convert(str(R / "rich-table.png"), use_cache=False, allow_vision=False).markdown
    for key in ("Production Budget", "May 25, 2018", "Solo: A Star Wars Story", "$1,332,539,889"):
        assert key in md, key
    r = convert(str(F / "scanned.pdf"), use_cache=False, allow_vision=False)
    assert "Total due: 1,140.00" in r.markdown and "rapidocr" in r.markdown


# ---- Apple Vision: a fast/accurate disagreement marks the read as weak ----------------------
def _fake_ocrmac(monkeypatch, readings):
    """Stand-in for ocrmac: returns one (text, conf, bbox) per line, per recognition level."""
    import types

    class OCR:
        def __init__(self, path, recognition_level="accurate"):
            self.level = recognition_level

        def recognize(self):
            lines = readings[self.level]
            return [(t, 1.0, (0.1, 0.9 - i * 0.1, 0.5, 0.05)) for i, t in enumerate(lines)]

    pkg = types.ModuleType("ocrmac")
    pkg.ocrmac = types.SimpleNamespace(OCR=OCR)
    monkeypatch.setitem(sys.modules, "ocrmac", pkg)
    monkeypatch.setitem(sys.modules, "ocrmac.ocrmac", pkg.ocrmac)


def test_apple_agreeing_passes_keep_confidence(monkeypatch):
    from leantext.extractors import image

    clean = ["INVOICE 2026-0417", "Bill to: Example Studio Ltd.", "Total due: 1,140.00"]
    _fake_ocrmac(monkeypatch, {"accurate": clean, "fast": clean})
    text, conf, words, low = image._ocr_apple("x.png")
    assert text.splitlines() == clean and conf == 100


def test_apple_disagreeing_passes_flag_weak(monkeypatch):
    # The real misread from a slightly blurred invoice on macOS 26, conf=100 from Apple Vision.
    from leantext.extractors import image

    _fake_ocrmac(monkeypatch, {
        "accurate": ["NVOICE 2026-0417", "1: 95.00 per Nev", "Total due: 1.140.00", "Payment within 30 days of anu"],
        "fast": ["INVOICE 2026-0417", "Bill to Exampie Studlo", "Rate 95 00 per hour", "Tota due 1140.00"],
    })
    monkeypatch.setenv("LEANTEXT_OCR", "apple")
    md, method, warnings = image.extract("x.png", allow_vision=False)
    assert "ocr(apple" in method and float(method.split("conf=")[1].split(",")[0]) < image.MIN_CONFIDENCE
    assert any("mean confidence" in w for w in warnings)


# ---- GitHub README headings (real run on github.com/shouvik12/devtv, 2026-10-22) -----------
def test_github_readme_headings_survive():
    # GitHub wraps each heading with an icon-only permalink; trafilatura dropped all 24 headings.
    from leantext.extractors import html as hx

    def h(lvl, t, slug):
        return (f'<div class="markdown-heading" dir="auto"><h{lvl} tabindex="-1" class="heading-element" '
                f'dir="auto">{t}</h{lvl}><a id="user-content-{slug}" class="anchor" aria-label="Permalink: {t}" '
                f'href="#{slug}"><svg class="octicon" aria-hidden="true"><path d="M7"></path></svg></a></div>')

    para = "<p>" + "DEV·TV is one HTML file with no build step, no server and no login. " * 4 + "</p>"
    titles = ["What it is", "Channels", "Papers (channels 06 and 07)", "License"]
    body = "".join(h(2, t, t.lower().replace(" ", "-")) + para for t in titles)
    page = f'<html><body><article class="markdown-body">{body}</article></body></html>'
    md, _, _ = hx.extract(page, url="https://github.com/shouvik12/devtv")
    for t in titles:
        assert f"## {t}" in md, t
    assert "Permalink" not in md


# ---- Running from a folder with a file named like a stdlib module (real run, 2026-10-22) ----
def test_cwd_with_stdlib_named_file(tmp_path):
    # ~/Downloads held an html.py; the extraction worker imported it and every input failed.
    (tmp_path / "html.py").write_text("raise ImportError('shadowed stdlib html')\n")
    img = tmp_path / "shot.png"
    shutil.copy(F / "screenshot.png", img)
    exe = shutil.which("leantext", path=os.path.dirname(sys.executable)) or shutil.which("leantext")
    r = subprocess.run([exe, "shot.png", "--no-vision", "--no-cache"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "devtv@1.4.0" in r.stdout


# ---- Truncated download (real run: arXiv PDF cut off at 1.6 of 2.2 MB, 2026-10-22) ------------
def test_truncated_pdf_fails_cleanly(tmp_path):
    p = tmp_path / "cut.pdf"
    p.write_bytes((R / "compressed.tracemonkey-pldi-09.pdf").read_bytes()[:40000])
    r = run(str(p), "--report", "--no-cache")
    assert r.returncode == 2, r.stderr
    assert "Traceback" not in r.stderr and "extractor error" in r.stderr


# ---- Sites that serve their own Markdown to agents (Stripe's API reference does) ---------------
def _serve(handler_cls):
    import http.server
    import threading

    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_prefers_site_markdown_when_offered():
    import http.server

    md = "# Create a customer\n\n`POST /v1/customers`\n\n- `email` (string, optional)\n"

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            wants_md = "text/markdown" in (self.headers.get("Accept") or "")
            body = md if wants_md else "<html><body><p>html version</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8" if wants_md else "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass

    srv = _serve(H)
    try:
        r = convert(f"http://127.0.0.1:{srv.server_port}/api/customers/create", use_cache=False)
    finally:
        srv.shutdown()
    assert r.method == "site-markdown" and not r.raw and not r.failed
    assert r.markdown.strip() == md.strip()


def test_site_markdown_report_measures_the_html_page():
    import http.server

    md = "# Create a customer\n\n`POST /v1/customers`\n"
    page = "<html><body><nav>" + "<a href='/x'>Menu</a> " * 200 + "</nav><h1>Create a customer</h1></body></html>"

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            wants_md = (self.headers.get("Accept") or "").startswith("text/markdown")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown" if wants_md else "text/html")
            self.end_headers()
            self.wfile.write((md if wants_md else page).encode())

        def log_message(self, *a):
            pass

    srv = _serve(H)
    try:
        r = run(f"http://127.0.0.1:{srv.server_port}/c", "--report", "--no-cache")
    finally:
        srv.shutdown()
    assert "markdown served by the site" in r.stderr
    assert "raw HTML" in r.stderr and "saved" in r.stderr and "no saving" not in r.stderr


def test_html_sites_unaffected_by_markdown_preference():
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            chrome = "<nav>" + "<a href='/x'>Menu item</a> " * 60 + "</nav><script>" + "var a=1;" * 300 + "</script>"
            self.wfile.write(("<html><body>" + chrome + "<article><h1>Title here</h1><p>" + "Real content. " * 40
                              + "</p></article></body></html>").encode())

        def log_message(self, *a):
            pass

    srv = _serve(H)
    try:
        r = convert(f"http://127.0.0.1:{srv.server_port}/page", use_cache=False)
    finally:
        srv.shutdown()
    assert r.method.startswith("trafilatura") and "Real content." in r.markdown


# ---- Stripe API reference markup (real run on docs.stripe.com/api/customers/create, 2026-10-22) ---
_STRIPE_LIKE = """<html><body><nav>""" + "<a href='/x'>Menu</a> " * 40 + """</nav><main>
<h1><span><a href="/api/customers/create">Create a customer</a></span><span>&nbsp;v1</span></h1>
<p>Creates a new customer object. """ + "Customers let you track payments. " * 10 + """</p>
<h3>Parameters</h3><ul>
<li><h4><div><span><span><span>email</span></span><span>string</span></span></div></h4>
<div><p>Customer's email address. This may be up to 512 characters.</p></div></li>
<li><h4><div><span><span><span>address</span></span><span>object</span><span>Required if calculating taxes</span></span></div></h4>
<div><p>The customer's address.</p></div></li></ul>
<pre><code><div><span></span><span><span>curl</span><span> https://api.stripe.com/v1/customers \\</span></span></div><div><span></span><span><span>  -d </span><span>"name=Jenny Rosen"</span></span></div></code></pre>
<pre><code><span class="k">def</span><span class="n">fn</span></code></pre>
</main></body></html>"""


def test_stripe_heading_link_names_and_line_div_code():
    from leantext.extractors import html as hx

    md, _, _ = hx.extract(_STRIPE_LIKE, url="https://docs.stripe.com/api/customers/create")
    assert "# Create a customer" in md
    assert "email string" in md and "emailstring" not in md
    assert "address object Required if calculating taxes" in md
    assert "curl https://api.stripe.com/v1/customers \\\n  -d \"name=Jenny Rosen\"" in md
    assert "deffn" in md  # spans inside code are never spaced apart: they split tokens on purpose


def test_offsite_heading_links_are_kept():
    from leantext.extractors import html as hx

    page = '<html><body><h2><a href="https://other.example.org/post">Elsewhere</a></h2><h2><a href="/here">Here</a></h2></body></html>'
    out = hx.normalize_structure(page, "https://site.example.com/page")
    assert 'href="https://other.example.org/post"' in out and 'href="/here"' not in out


# ---- GitHub code blocks (real runs on openai-python and fastapi READMEs, 2026-10-22) ----------
def test_github_code_blocks_exact_tagged_and_one_liners_kept():
    from leantext.extractors import html as hx

    code = 'from x import y\n\n\ndef f():\n    return 1'

    def block(lang, text):
        esc = text.replace("&", "&amp;").replace('"', "&quot;").replace("\n", "&#10;")
        body = text.replace("&", "&amp;").replace("<", "&lt;")
        return (f'<div class="highlight highlight-source-{lang} notranslate" dir="auto" '
                f'data-snippet-clipboard-copy-content="{esc}"><pre>{body}</pre></div>')

    para = "<p>" + "Install the package and run the example below. " * 5 + "</p>"
    page = (f'<html><body><article class="markdown-body"><h2>Install</h2>{para}'
            f'{block("shell", "pip install openai")}{block("python", code)}'
            f'<h2>Usage</h2>{para}{block("python", "print(1)")}</article></body></html>')
    md, _, _ = hx.extract(page, url="https://github.com/o/r")
    assert "```bash\npip install openai\n```" in md
    assert "```python\n" + code + "\n```" in md          # two blank lines between defs survive
    assert "```python\nprint(1)\n```" in md
    assert "`pip install openai`" not in md.replace("```bash\npip install openai\n```", "")


# ---- Short lists dropped as menus (real run on github.com/fastapi/fastapi, 2026-10-22) -----------
def test_short_content_list_restored_but_link_menus_not():
    from leantext.extractors import html as hx

    para = "<p>" + "FastAPI is a modern web framework for building APIs with Python. " * 4 + "</p>"
    page = (f"<html><body><article>{para}<h2>Requirements</h2><p>FastAPI stands on the shoulders of giants:</p>"
            '<ul><li><a href="https://starlette.dev/">Starlette</a> for the web parts.</li>'
            '<li><a href="https://pydantic.dev/docs/">Pydantic</a> for the data parts.</li></ul>'
            f"<h2>Support</h2><p>Some parts of this feature may have varying levels of support.</p>"
            '<ul><li><a href="/compat">See full compatibility</a></li><li><a href="/learn">Learn more</a></li></ul>'
            f"<h2>Installation</h2>{para}</article></body></html>")
    md, _, _ = hx.extract(page, url="https://github.com/fastapi/fastapi")
    assert "- [Starlette](https://starlette.dev/) for the web parts." in md
    assert "- [Pydantic](https://pydantic.dev/docs/) for the data parts." in md
    assert "See full compatibility" not in md
    assert md.count("for the web parts") == 1


def test_images_get_a_longer_time_budget(monkeypatch):
    # rich-table.png took 21s on macOS (Apple Vision, two passes) and was dropped at the 20s limit.
    from leantext import router

    seen = {}

    def fake(args, budget):
        seen["budget"] = budget
        raise TimeoutError("stop here")

    monkeypatch.setattr(router, "_extract_bounded", fake)
    router.convert(str(R / "rich-table.png"), use_cache=False, allow_vision=False)
    assert seen["budget"] >= 60
