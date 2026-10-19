"""Benchmark: the same question, answered with and without leantext.

Baseline = what an agent gets by default:
  html  -> whole-page Markdown (like a typical web-fetch tool)   [--baseline raw for raw HTML]
  pdf   -> the PDF sent natively as a document block
  image -> the image sent natively

Without ANTHROPIC_API_KEY it runs in token-only mode (no correctness column).
With a key, token counts come from the API's own usage numbers, so they're exact.

  python bench/run.py                       # token-only
  ANTHROPIC_API_KEY=... python bench/run.py --model claude-haiku-5-5
  python bench/run.py --inputs bench/my_inputs.json --out bench/results.md
  (Add URLs to inputs.json: {"source": "https://..."} works too.)
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from leantext import convert  # noqa: E402
from leantext.router import _baselines, _fetch, _kind_for  # noqa: E402

SYSTEM = "Answer the question using only the provided content. Be brief and exact."

# USD per million input tokens; override with --price. Used only for the cost column.
DEFAULT_PRICE = 1.0


def _baseline_blocks(src: str, kind: str, baseline: str) -> tuple[list[dict], int]:
    """Content blocks an agent would see without leantext, plus an offline token estimate."""
    path = src if not src.startswith("http") else None
    raw = None
    if path is None or kind in ("html", "text"):
        raw = _fetch(src)[0] if path is None else Path(path).read_bytes()
    if kind == "html":
        b = _baselines("html", raw, None)
        if baseline == "raw":
            text, est = raw.decode("utf-8", "replace"), b["raw HTML"]
        else:
            from markdownify import markdownify
            page = re.sub(r"<(script|style|noscript)\b.*?</\1>", "", raw.decode("utf-8", "replace"), flags=re.S | re.I)
            text, est = markdownify(page, heading_style="ATX"), b["naive Markdown (whole page)"]
        return [{"type": "text", "text": text}], est
    data = Path(path).read_bytes() if path else raw
    b64 = base64.standard_b64encode(data).decode()
    if kind == "pdf":
        est = _baselines("pdf", None, path)["pages as images"] if path else 0
        return [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}], est
    if kind == "image":
        mime = mimetypes.guess_type(path or src)[0] or "image/png"
        est = _baselines("image", None, path)["image input"] if path else 0
        return [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}], est
    return [{"type": "text", "text": data.decode("utf-8", "replace")}], 0


def _ask(client, model, blocks, question):
    msg = client.messages.create(
        model=model, max_tokens=300, system=SYSTEM,
        messages=[{"role": "user", "content": [*blocks, {"type": "text", "text": question}]}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    return text, msg.usage.input_tokens


def _correct(answer: str, expect: list[str]) -> bool:
    return all(re.search(p, answer, re.I) for p in expect)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", default=str(ROOT / "bench" / "inputs.json"))
    ap.add_argument("--out", default=str(ROOT / "bench" / "results.md"))
    ap.add_argument("--model", default=os.environ.get("LEANTEXT_BENCH_MODEL", "claude-haiku-5-5"))
    ap.add_argument("--baseline", choices=["default", "raw"], default="default")
    ap.add_argument("--price", type=float, default=DEFAULT_PRICE, help="USD per 1M input tokens")
    ap.add_argument("--no-vision", action="store_true")
    a = ap.parse_args()

    live = bool(os.environ.get("ANTHROPIC_API_KEY"))
    client = None
    if live:
        import anthropic
        client = anthropic.Anthropic()

    rows = []
    for item in json.loads(Path(a.inputs).read_text()):
        src = item["source"]
        src = src if src.startswith("http") else str(ROOT / src)
        kind = _kind_for(src, "", Path(src).read_bytes()[:1024] if not src.startswith("http") else b"")
        t0 = time.time()
        res = convert(src, report=True, allow_vision=not a.no_vision)
        lt_ms = (time.time() - t0) * 1000
        base_blocks, base_est = _baseline_blocks(src, res.kind if src.startswith("http") else kind, a.baseline)

        row = {"id": item["id"], "kind": res.kind, "method": res.method, "leantext_ms": round(lt_ms),
               "base_tokens": base_est, "lean_tokens": res.out_tokens, "tokenizer": res.tokenizer,
               "base_ok": None, "lean_ok": None, "lean_has": _correct(res.markdown, item["expect"])}
        if live:
            ans_b, row["base_tokens"] = _ask(client, a.model, base_blocks, item["question"])
            ans_l, row["lean_tokens"] = _ask(client, a.model, [{"type": "text", "text": res.markdown}], item["question"])
            row.update(base_ok=_correct(ans_b, item["expect"]), lean_ok=_correct(ans_l, item["expect"]),
                       base_answer=ans_b.strip(), lean_answer=ans_l.strip(), tokenizer="api usage")
        rows.append(row)
        print(f"{row['id']:<24} {row['base_tokens']:>7,} -> {row['lean_tokens']:>6,}", file=sys.stderr)

    Path(a.out).with_suffix(".json").write_text(json.dumps(rows, indent=2))
    Path(a.out).write_text(render(rows, a, live))
    print(Path(a.out).read_text())
    return 0


def render(rows, a, live) -> str:
    ok = lambda v: "—" if v is None else ("✅" if v else "❌")  # noqa: E731
    tb = sum(r["base_tokens"] for r in rows)
    tl = sum(r["lean_tokens"] for r in rows)
    out = ["| Input | Type | Route | Tokens without | Tokens with | Saved | Answer in text | Correct without | Correct with |",
           "|---|---|---|---:|---:|---:|:---:|:---:|:---:|"]
    for r in rows:
        saved = 1 - r["lean_tokens"] / r["base_tokens"] if r["base_tokens"] else 0
        out.append(f"| {r['id']} | {r['kind']} | {r['method']} | {r['base_tokens']:,} | {r['lean_tokens']:,} | "
                   f"{saved:.0%} | {ok(r['lean_has'])} | {ok(r['base_ok'])} | {ok(r['lean_ok'])} |")
    total_saved = 1 - tl / tb if tb else 0
    cb = sum(1 for r in rows if r["base_ok"]); cl = sum(1 for r in rows if r["lean_ok"])
    ch = sum(1 for r in rows if r["lean_has"])
    out.append(f"| **Total** | | | **{tb:,}** | **{tl:,}** | **{total_saved:.0%}** | **{ch}/{len(rows)}** | "
               + (f"**{cb}/{len(rows)}** | **{cl}/{len(rows)}** |" if live else "— | — |"))
    note = (f"\nModel: `{a.model}`. Token counts are the API's reported input tokens (question and system prompt "
            f"included in both arms). Baseline: {'raw HTML' if a.baseline == 'raw' else 'whole-page Markdown for HTML, native PDF/image blocks otherwise'}. "
            f"Cost at ${a.price}/M input tokens: ${tb * a.price / 1e6:.4f} -> ${tl * a.price / 1e6:.4f}.\n"
            if live else
            f"\nToken-only run (no API key): baselines are estimates (`{rows[0]['tokenizer'] if rows else '?'}` "
            "for text; width×height/750 for images and rendered PDF pages). Set ANTHROPIC_API_KEY for exact "
            "counts and the correctness columns. \"Answer in text\" checks the expected answer appears in leantext's "
            "output: necessary for a correct answer, not proof of one.\n")
    return "\n".join(out) + "\n" + note


if __name__ == "__main__":
    sys.exit(main())
