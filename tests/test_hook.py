import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

F = Path(__file__).parent / "fixtures"


def hook(tool, tool_input, tmp_path, kinds="all", **env):
    event = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(tmp_path), "tool_input": tool_input}
    e = {"LEANTEXT_HOOK_LOG": str(tmp_path / "log.jsonl"), "LEANTEXT_CACHE_DIR": str(tmp_path / "cache"),
         "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path), "TMPDIR": os.environ.get("TMPDIR", "/tmp"), **env}
    if kinds is not None:
        e["LEANTEXT_HOOK_KINDS"] = kinds
    r = subprocess.run([sys.executable, "-m", "leantext.hook"], input=json.dumps(event),
                       capture_output=True, text=True, env=e)
    assert r.returncode == 0, r.stderr
    log = [json.loads(x) for x in (tmp_path / "log.jsonl").read_text().splitlines()] \
        if (tmp_path / "log.jsonl").exists() else []
    out = json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None
    return out, log


def test_pdf_read_is_rewritten_to_markdown(tmp_path):
    out, log = hook("Read", {"file_path": str(F / "real/compressed.tracemonkey-pldi-09.pdf"), "pages": "1-2"}, tmp_path)
    new = out["updatedInput"]
    assert out["permissionDecision"] == "allow" and new["file_path"].endswith(".md") and "pages" not in new
    assert "Dynamic languages such as JavaScript" in Path(new["file_path"]).read_text()
    assert log[-1]["action"] == "mediated" and log[-1]["tokens_after"] < log[-1]["tokens_before_est"]


def test_relative_path_resolved_against_cwd(tmp_path):
    (tmp_path / "shot.png").write_bytes((F / "real/rich-table.png").read_bytes())
    out, _ = hook("Read", {"file_path": "shot.png"}, tmp_path)
    assert "$1,332,539,889" in Path(out["updatedInput"]["file_path"]).read_text()


@pytest.mark.parametrize("name,why", [("pypi-challenge.html", "bot challenge"), ("devtv-index.html", "JavaScript")])
def test_untrustworthy_extraction_passes_through(tmp_path, name, why):
    out, log = hook("Read", {"file_path": str(F / "real" / name)}, tmp_path)
    assert out is None
    assert log[-1]["action"] == "passthrough" and why in log[-1]["reason"]


def test_blank_image_passes_through_to_native_vision(tmp_path):
    from PIL import Image

    Image.new("RGB", (400, 200), "white").save(tmp_path / "blank.png")
    out, log = hook("Read", {"file_path": str(tmp_path / "blank.png")}, tmp_path)
    assert out is None and log[-1]["action"] == "passthrough"


def test_source_files_untouched_and_unlogged(tmp_path):
    out, log = hook("Read", {"file_path": __file__}, tmp_path)
    assert out is None and log == []


@pytest.mark.parametrize("cmd,rewritten", [
    ("curl -s https://example.com/a", True),
    ("curl -fsSL 'https://example.com/a'", True),
    ("wget -qO- https://example.com/a", True),
    ("curl -s https://example.com/a | grep -i install", True),
    ("curl -sL https://example.com/a | head -50", True),
    ("curl -s https://example.com/a | jq .", False),
    ("curl -s https://example.com/a | tar xz", False),
    ("curl -s https://example.com/a -o page.html", False),
    ("curl -s https://example.com/a > page.html", False),
    ("curl -X POST https://example.com/a", False),
    ("echo https://example.com", False),
])
def test_bash_rewrite_scope(tmp_path, cmd, rewritten):
    out, _ = hook("Bash", {"command": cmd}, tmp_path)
    assert (out is not None) == rewritten
    if rewritten:
        new = out["updatedInput"]["command"]
        assert new.rstrip().endswith('rm -f "$__e"')
        assert cmd.split(" | ")[0] in new  # original fetch kept as the fallback
        if " | " in cmd:
            assert new.count(" | " + cmd.split(" | ", 1)[1]) == 1  # filter applied once, to whichever ran


def test_off_switch(tmp_path):
    out, log = hook("Read", {"file_path": str(F / "real/rich-table.png")}, tmp_path, LEANTEXT_HOOK="off")
    assert out is None and log == []


def test_hook_config_command():
    r = subprocess.run([sys.executable, "-m", "leantext", "hook-config"], capture_output=True, text=True)
    cfg = json.loads(r.stdout)["hooks"]["PreToolUse"][0]
    assert cfg["matcher"] == "Read|Bash" and cfg["hooks"][0]["command"].endswith("-m leantext.hook")


def test_piped_rewrite_runs_end_to_end(tmp_path):
    """Rewritten `curl URL | grep` returns grep over Markdown; on a bot wall it greps the original HTML."""
    import functools
    import http.server
    import threading

    class Q(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Q, directory=str(F / "real")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        for page, needle, expect in [("pypi-uv.html", "install.sh", "curl -LsSf https://astral.sh/uv/install.sh | sh"),
                                     ("pypi-challenge.html", "Client Challenge", "Client Challenge")]:
            out, _ = hook("Bash", {"command": f"curl -s {base}/{page} | grep -F '{needle}'"}, tmp_path)
            r = subprocess.run(["bash", "-c", out["updatedInput"]["command"]], capture_output=True, text=True,
                               env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path), "NO_PROXY": "127.0.0.1"})
            assert expect in r.stdout, (page, r.stdout[:300], r.stderr[:300])
    finally:
        httpd.shutdown()


def test_second_read_returns_original(tmp_path):
    """The converted file says 'read again for the original'; that has to be true."""
    img = str(F / "real/rich-table.png")
    first, _ = hook("Read", {"file_path": img}, tmp_path, )
    assert first is not None and "Read the same file again" not in Path(first["updatedInput"]["file_path"]).read_text()
    event = {"hook_event_name": "PreToolUse", "tool_name": "Read", "cwd": str(tmp_path), "session_id": "s1",
             "tool_input": {"file_path": img}}
    env = {"LEANTEXT_HOOK_LOG": str(tmp_path / "log2.jsonl"), "LEANTEXT_CACHE_DIR": str(tmp_path / "cache2"),
           "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path), "LEANTEXT_HOOK_KINDS": "all", "TMPDIR": os.environ.get("TMPDIR", "/tmp")}
    outs = [subprocess.run([sys.executable, "-m", "leantext.hook"], input=json.dumps(event), capture_output=True,
                           text=True, env=env).stdout.strip() for _ in range(2)]
    assert outs[0] and outs[1] == ""  # mediated, then passed through
    other = dict(event, session_id="s2")
    assert subprocess.run([sys.executable, "-m", "leantext.hook"], input=json.dumps(other), capture_output=True,
                          text=True, env=env).stdout.strip()  # a new session gets the conversion again


@pytest.mark.parametrize("name,mediated", [
    ("real/rich-table.png", False),                       # image: native is ~2k tokens, extra turns cost more
    ("real/compressed.tracemonkey-pldi-09.pdf", False),   # text PDF: native read cached cheaper
    ("scanned.pdf", True),                                # scanned PDF: won
    ("real/pypi-uv.html", True),                          # HTML: won
])
def test_default_kinds_follow_benchmark(tmp_path, name, mediated):
    out, log = hook("Read", {"file_path": str(F / name)}, tmp_path, kinds=None)
    assert (out is not None) == mediated
    if not mediated:
        assert "LEANTEXT_HOOK_KINDS" in log[-1]["reason"]
