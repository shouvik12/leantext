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
