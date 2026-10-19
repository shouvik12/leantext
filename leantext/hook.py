"""Claude Code hook: route an agent's existing reads through leantext, transparently.

Wire it up as a PreToolUse hook on `Read|Bash` (see `leantext hook-config`). The agent keeps
calling its own tools; this rewrites the call before it runs:

  Read <file.pdf|.png|.html|.docx ...>  ->  Read <cached Markdown conversion>
  Bash `curl -s URL` / `wget -qO- URL`  ->  Bash `leantext URL` (falls back to the original command)

It fails open. When leantext can't produce trustworthy Markdown (bot wall, JS-only page, weak OCR,
suspicious extraction, no token saving) the original call runs untouched, so the hook never
makes a read worse than it would have been without it. Every decision is appended to a JSONL log.

Off switch: LEANTEXT_HOOK=off. Log path: LEANTEXT_HOOK_LOG (default ~/.cache/leantext/hook.jsonl).
Which file kinds Read mediates: LEANTEXT_HOOK_KINDS (default html,pdf-scanned,office; "all" adds
pdf-text and image, which lost money in the end-to-end benchmark).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

# Hooks often run with a minimal PATH; make Homebrew tools findable (Apple Silicon + Intel).
for _p in ("/opt/homebrew/bin", "/usr/local/bin"):
    if os.path.isdir(_p) and _p not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _p

CONVERTIBLE = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff",
               ".html", ".htm", ".xhtml", ".docx", ".pptx", ".xlsx", ".epub"}
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}

# A fetch of one URL to stdout, either on its own or piped into text filters (grep, head, ...).
# Downloads (-o file, > file), POSTs, and pipes into anything else are left alone: those aren't reads.
_URL = r"""(?P<q>['"]?)(?P<url>https?://[^\s'"|;&<>`$]+)(?P=q)"""
_CURL_FLAGS = r"(?:\s+-(?:s|S|L|sS|sL|Ls|sSL|fsSL|fsS|fSL|f|-silent|-location|-fail|-compressed))*"
_WGET_FLAGS = r"(?:\s+-(?:q|O-|qO-|O\s*-|-quiet|-output-document=-))+"
_FETCH = rf"(?P<fetch>\s*(?:curl{_CURL_FLAGS}|wget{_WGET_FLAGS})\s+{_URL})"
_TEXT_FILTERS = r"(?:grep|egrep|fgrep|rg|head|tail|sed|awk|cut|sort|uniq|wc|tr|cat|nl|fold|fmt|less|more)"
_FETCH_CMD = re.compile(rf"^{_FETCH}\s*(?:\|\s*(?P<rest>{_TEXT_FILTERS}\b.*))?$", re.S)


def _cache_dir() -> Path:
    d = Path(os.environ.get("LEANTEXT_CACHE_DIR", Path.home() / ".cache" / "leantext")) / "reads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log(entry: dict) -> None:
    path = Path(os.environ.get("LEANTEXT_HOOK_LOG", Path.home() / ".cache" / "leantext" / "hook.jsonl"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), **entry}) + "\n")
    except OSError:
        pass


def _emit(updated_input: dict, reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": reason,
        "updatedInput": updated_input,
    }}))


def _read(event: dict) -> None:
    ti = dict(event.get("tool_input") or {})
    raw_path = ti.get("file_path") or ""
    path = Path(raw_path if os.path.isabs(raw_path) else os.path.join(event.get("cwd") or ".", raw_path))
    ext = path.suffix.lower()
    base = {"tool": "Read", "target": str(path)}
    if ext not in CONVERTIBLE or not path.is_file():
        return  # not ours: plain text, code, missing file -> untouched, not logged

    kind = _kind(path, ext)
    if kind not in _enabled_kinds():
        _log({**base, "action": "passthrough", "reason": f"{kind}: native read is as cheap or cheaper "
              "(see bench/agent_results.md); enable with LEANTEXT_HOOK_KINDS"})
        return

    # Escape hatch: a second Read of the same file in the same session gets the original. It is not
    # advertised in the converted text: when it was, agents re-read screenshots "to check" and paid twice.
    seen_file = _cache_dir() / f"session-{re.sub(r'[^A-Za-z0-9_-]', '', str(event.get('session_id') or 'none'))}.seen"
    seen = set(seen_file.read_text().split("\n")) if seen_file.exists() else set()
    key = f"{path}:{ti.get('offset', '')}:{ti.get('limit', '')}"
    if key in seen:
        _log({**base, "action": "passthrough", "reason": "re-read: agent asked for the original"})
        return

    from .router import convert

    t0 = time.time()
    try:
        # No vision fallback here: the agent is itself a vision model, so when OCR is weak the right
        # fallback is the original image, not a second model call.
        res = convert(str(path), report=True, allow_vision=False)
    except Exception as e:  # noqa: BLE001
        _log({**base, "action": "passthrough", "reason": f"error: {e}"})
        return
    ms = round((time.time() - t0) * 1000)
    before = next(iter(res.baselines.values()), None) if ext in IMAGES else _native_estimate(res)
    info = {**base, "route": res.method, "ms": ms, "tokens_before_est": before, "tokens_after": res.out_tokens,
            "tokenizer": res.tokenizer}

    reason = None
    if res.failed:
        reason = "; ".join(res.warnings) or "no content"
    elif res.suspicious:
        reason = "suspicious: " + "; ".join(res.suspicious)
    elif ext in IMAGES and res.warnings:
        reason = "; ".join(res.warnings)  # weak OCR: the agent sees the image itself
    elif before and res.out_tokens and res.out_tokens >= before:
        reason = "no token saving"
    if reason:
        _log({**info, "action": "passthrough", "reason": reason})
        return

    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    out = _cache_dir() / f"{path.stem}.{digest}.md"
    out.write_text(
        f"<!-- leantext: {path.name} as text ({_describe(res.method)}) -->\n\n{res.markdown}"
    )
    seen.add(key)
    seen_file.write_text("\n".join(sorted(seen)))
    ti["file_path"] = str(out)
    ti.pop("pages", None)  # PDF page ranges don't apply to the Markdown file
    _log({**info, "action": "mediated", "converted": str(out)})
    _emit(ti, f"leantext: read {path.name} as Markdown ({res.method})")


# Defaults follow bench/agent_results.md. In Claude Code, a screenshot costs ~2k tokens natively while
# each extra agent turn costs ~15k, and native PDF reading cached more cheaply than converted text, so
# those kinds lost money. HTML and scanned PDFs won; Office files can't be read natively at all.
DEFAULT_KINDS = {"html", "pdf-scanned", "office"}
ALL_KINDS = {"html", "pdf-scanned", "office", "pdf-text", "image"}


def _enabled_kinds() -> set[str]:
    raw = os.environ.get("LEANTEXT_HOOK_KINDS", "").strip().lower()
    if not raw:
        return DEFAULT_KINDS
    if raw == "all":
        return ALL_KINDS
    return {k.strip() for k in raw.split(",") if k.strip()}


def _kind(path: Path, ext: str) -> str:
    if ext in IMAGES:
        return "image"
    if ext in (".html", ".htm", ".xhtml"):
        return "html"
    if ext == ".pdf":
        from .extractors.pdf import has_text_layer

        try:
            return "pdf-text" if has_text_layer(str(path)) else "pdf-scanned"
        except Exception:  # noqa: BLE001 - malformed PDF: let the native reader deal with it
            return "pdf-text"
    return "office"


def _describe(method: str) -> str:
    if method.startswith("ocr"):
        m = re.search(r"conf=(\d+)", method)
        return f"OCR, confidence {m.group(1)}%" if m else "OCR"
    return method


def _native_estimate(res) -> int | None:
    """What the agent would otherwise load: a native PDF block (pages as images), else the raw text."""
    for k in ("pages as images", "raw HTML", "raw"):
        if k in res.baselines:
            return res.baselines[k]
    return None


def _bash(event: dict) -> None:
    ti = dict(event.get("tool_input") or {})
    cmd = ti.get("command") or ""
    m = _FETCH_CMD.match(cmd)
    if not m:
        if re.search(r"\b(curl|wget)\b", cmd):
            _log({"tool": "Bash", "action": "not-eligible", "command": cmd[:300]})
        return
    url, fetch, rest = m.group("url"), m.group("fetch").strip(), m.group("rest")
    # The Bash tool may not pass our environment through, so carry the log/cache settings explicitly.
    carry = " ".join(f"{k}={shlex.quote(os.environ[k])}" for k in ("LEANTEXT_HOOK_LOG", "LEANTEXT_CACHE_DIR")
                     if os.environ.get(k))
    lt = f"LEANTEXT_HOOK_RUN=1 {carry} " + shlex.quote(sys.executable) + " -m leantext --strict --no-vision " + shlex.quote(url)
    # Fail open: if leantext exits non-zero (bot wall, JS shell, suspicious), the original fetch runs.
    # The CLI logs its own outcome (mediated or passthrough) because only it knows how extraction went.
    read = (f"{{ __lt=$({lt} 2>\"$__e\") && printf '%s\\n' \"$__lt\" "
            f"|| {{ cat \"$__e\" >&2; {fetch}; }}; }}")
    ti["command"] = f"__e=$(mktemp); {read}" + (f" | {rest}" if rest else "") + "; rm -f \"$__e\""
    _log({"tool": "Bash", "target": url, "action": "rewritten", "command": cmd[:300]})
    _emit(ti, f"leantext: fetch {url} as Markdown (original command runs if extraction fails)")


def main() -> int:
    if os.environ.get("LEANTEXT_HOOK", "").lower() in ("off", "0", "false"):
        return 0
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("hook_event_name") != "PreToolUse":
        return 0
    tool = event.get("tool_name")
    try:
        if tool == "Read":
            _read(event)
        elif tool == "Bash":
            _bash(event)
    except Exception as e:  # noqa: BLE001 - a hook bug must never block the agent's read
        _log({"tool": tool, "action": "passthrough", "reason": f"hook error: {e}"})
    return 0


CONFIG = {
    "hooks": {
        "PreToolUse": [{
            "matcher": "Read|Bash",
            "hooks": [{"type": "command", "command": "", "timeout": 120}],
        }]
    }
}


def config(python: str | None = None) -> dict:
    cfg = json.loads(json.dumps(CONFIG))
    cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = f"{python or sys.executable} -m leantext.hook"
    return cfg


if __name__ == "__main__":
    sys.exit(main())
