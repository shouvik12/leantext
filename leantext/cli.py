"""leantext CLI.

Exit codes:
  0  success (Markdown on stdout)
  1  usage / input error (bad path, fetch failed, unsupported input)
  2  extraction failed (nothing usable produced)
  3  extraction suspicious (only with --strict; otherwise a warning on stderr)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .router import InputError, convert


def _report(res) -> str:
    what = "markdown served by the site" if res.method == "site-markdown" else f"{res.kind}, via {res.method}"
    lines = [f"leantext report  ({what}{', cached' if res.cached else ''})",
             f"tokenizer: {res.tokenizer}" + ("  [approximate: chars/4]" if res.tokenizer == "estimate" else "")]
    for name, n in res.baselines.items():
        saved = 1 - res.out_tokens / n if n else 0
        verdict = f"{saved:.0%} saved" if saved > 0.005 else ("no saving" if saved > -0.005 else f"{-saved:.0%} MORE")
        lines.append(f"  {name:<30} {n:>8,}  ->  {res.out_tokens:>7,}   {verdict}")
    if res.kind == "image" and res.baselines:
        img = next(iter(res.baselines.values()))
        if res.out_tokens >= img:
            lines.append("  note: text is not cheaper than the image here; consider sending the image")
    if res.truncated:
        lines.append("  output was truncated by --max-tokens")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["hook-config"]:
        from .hook import config

        print(json.dumps(config(), indent=2))
        return 0
    p = argparse.ArgumentParser(
        prog="leantext",
        description="Convert a URL, PDF, Office doc or image into clean Markdown for agents.",
    )
    p.add_argument("target", help="URL, file path, or - for stdin")
    p.add_argument("--raw", action="store_true", help="bypass extraction (escape hatch)")
    p.add_argument("--report", action="store_true", help="print tokens before vs after to stderr")
    p.add_argument("--json", action="store_true", help="emit a JSON object instead of bare Markdown")
    p.add_argument("--max-tokens", type=int, metavar="N", help="truncate output to ~N tokens with a marker")
    p.add_argument("--no-vision", action="store_true", help="never escalate images to a vision model")
    p.add_argument("--keep-hidden", action="store_true",
                   help="keep content hidden by default (collapsed tabs, hidden elements)")
    p.add_argument("--no-cache", action="store_true", help="skip the on-disk cache")
    p.add_argument("--strict", action="store_true", help="exit 3 when extraction looks suspicious")
    p.add_argument("--version", action="version", version=f"leantext {__version__}")
    a = p.parse_args(argv)

    hook_run = os.environ.get("LEANTEXT_HOOK_RUN") == "1"
    try:
        res = convert(a.target, raw_mode=a.raw, report=a.report or a.json or hook_run, max_tokens=a.max_tokens,
                      allow_vision=not a.no_vision, use_cache=not a.no_cache,
                      keep_hidden=a.keep_hidden)
    except (InputError, OSError) as e:  # bad input, network errors, unreadable files
        print(f"leantext: {a.target}: {e}" if isinstance(e, OSError) else f"leantext: {e}", file=sys.stderr)
        if hook_run:
            _hook_log(a.target, None, f"input error: {e}")
        return 1

    if a.json:
        print(json.dumps({
            "source": res.source, "kind": res.kind, "method": res.method, "markdown": res.markdown,
            "tokens": {"tokenizer": res.tokenizer, "output": res.out_tokens, "baselines": res.baselines},
            "warnings": res.warnings, "suspicious": res.suspicious, "truncated": res.truncated,
        }, indent=2))
    else:
        sys.stdout.write(res.markdown)

    for w in res.warnings:
        print(f"leantext: warning: {w}", file=sys.stderr)
    if res.suspicious:
        print("leantext: extraction suspicious: " + "; ".join(res.suspicious)
              + "  (re-run with --raw to see the source)", file=sys.stderr)
    if a.report and not a.json:
        print(_report(res), file=sys.stderr)

    if res.failed:
        why = f" ({res.fallback})" if getattr(res, "fallback", None) else ""
        print(f"leantext: extraction failed: no content produced{why}", file=sys.stderr)
        code = 2
    elif res.suspicious and a.strict:
        code = 3
    else:
        code = 0
    if hook_run:
        reason = None if code == 0 else ("; ".join(res.warnings + res.suspicious) or "failed")
        _hook_log(a.target, res, reason)
    return code


def _hook_log(target: str, res, reason: str | None) -> None:
    """Record the outcome of a hook-initiated fetch (see leantext.hook)."""
    from .hook import _log

    entry = {"tool": "Bash", "target": target, "action": "passthrough" if reason else "mediated"}
    if res is not None:
        entry.update(route=res.method, tokens_after=res.out_tokens, tokenizer=res.tokenizer,
                     tokens_before_est=res.baselines.get("raw HTML") or res.baselines.get("raw"))
    if reason:
        entry["reason"] = reason
    _log(entry)


if __name__ == "__main__":
    sys.exit(main())
