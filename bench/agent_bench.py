"""End-to-end agent benchmark: the same Claude Code tasks with the leantext hook off and on.

Each run is a fresh headless `claude -p` in its own temp directory, with a scrubbed environment,
no user settings, no MCP servers, and only the Read and Bash tools. The only difference between
the arms is the hook in the project's .claude/settings.json. Pages for the curl tasks are served
from a local HTTP server, so runs are repeatable and don't depend on outside sites.

    python bench/agent_bench.py                     # all tasks, 1 repeat, default model
    python bench/agent_bench.py --repeats 3 --model claude-haiku-5-5
    python bench/agent_bench.py --tasks shot-table,curl-uv

Reports per arm: answer correctness, total input tokens (everything the model processed, including
Claude Code's own system prompt on every turn), cost in USD as reported by the CLI, turns, and
wall time. Writes bench/agent_results.json and bench/agent_results.md.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from leantext.hook import config as hook_config  # noqa: E402

KEEP_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy",
            "http_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE",
            "ANTHROPIC_API_KEY")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass


def serve(directory: Path) -> int:
    handler = functools.partial(_Quiet, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd.server_address[1]


def clean_env(extra: dict) -> dict:
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    no_proxy = env.get("NO_PROXY", "")
    if "127.0.0.1" not in no_proxy:
        env["NO_PROXY"] = env["no_proxy"] = (no_proxy + ",127.0.0.1,localhost").strip(",")
    env.update(extra)
    return env


def run_one(task: dict, arm: str, port: int, model: str | None, outdir: Path, keep: bool = False,
            kinds: str = "all") -> dict:
    work = Path(tempfile.mkdtemp(prefix=f"lt-{task['id']}-{arm}-"))
    for spec in task.get("files", []):
        src, _, dest = spec.partition(":")
        shutil.copy(ROOT / src, work / (dest or Path(src).name))
    prompt = task["prompt"].format(url=f"http://127.0.0.1:{port}/{task.get('serve', '')}")

    log = outdir / f"{task['id']}.{arm}.hook.jsonl"
    log.unlink(missing_ok=True)
    if arm == "on":
        (work / ".claude").mkdir()
        (work / ".claude" / "settings.json").write_text(json.dumps(hook_config(sys.executable), indent=2))
    env = clean_env({"LEANTEXT_HOOK_LOG": str(log), "LEANTEXT_CACHE_DIR": str(work / ".ltcache"),
                     "LEANTEXT_HOOK_KINDS": kinds})

    cmd = ["claude", "-p", prompt, "--output-format", "json", "--setting-sources", "project",
           "--strict-mcp-config", "--tools", "Read,Bash",
           "--allowedTools", "Read", "Bash(curl:*)", "Bash(wget:*)"]
    if model:
        cmd += ["--model", model]
    if not keep:
        cmd.append("--no-session-persistence")
    t0 = time.time()
    p = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=600)
    wall = time.time() - t0
    try:
        d = json.loads(p.stdout)
    except ValueError:
        return {"task": task["id"], "arm": arm, "error": (p.stderr or p.stdout)[-500:], "wall_s": round(wall, 1)}

    u = d.get("usage", {})
    answer = d.get("result") or ""
    hook_log = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    if keep and d.get("session_id"):
        for tr in (Path.home() / ".claude" / "projects").glob(f"*/{d['session_id']}.jsonl"):
            shutil.copy(tr, outdir / f"{task['id']}.{arm}.{d['session_id'][:8]}.transcript.jsonl")
    shutil.rmtree(work, ignore_errors=True)
    return {
        "task": task["id"], "arm": arm,
        "correct": all(re.search(pat, answer, re.I | re.S) for pat in task["expect"]),
        "answer": answer.strip()[:400],
        "input_tokens": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0),
        "usage": {k: u.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")},
        "output_tokens": u.get("output_tokens", 0),
        "cost_usd": d.get("total_cost_usd"),
        "turns": d.get("num_turns"),
        "wall_s": round(wall, 1),
        "is_error": d.get("is_error", False),
        "hook": [{k: e.get(k) for k in ("tool", "action", "route", "tokens_before_est", "tokens_after", "reason", "command")
                  if e.get(k) is not None} for e in hook_log],
    }


def summarize(rows: list[dict], tasks: list[dict]) -> str:
    def agg(task_id, arm, key, fn=statistics.mean):
        vals = [r[key] for r in rows if r["task"] == task_id and r["arm"] == arm and r.get(key) is not None]
        return fn(vals) if vals else None

    def cell_ok(task_id, arm):
        rs = [r for r in rows if r["task"] == task_id and r["arm"] == arm and "correct" in r]
        return f"{sum(r['correct'] for r in rs)}/{len(rs)}" if rs else "err"

    def routes(task_id):
        hs = [h for r in rows if r["task"] == task_id and r["arm"] == "on" for h in r.get("hook", [])]
        if not hs:
            return "not mediated"
        h = hs[0]
        return f"{h['action']}: {h.get('route') or ''}".strip(": ") + (f" ({h['reason'][:40]}…)" if h.get("reason") else "")

    lines = ["| Task | Correct off | Correct on | Input tokens off | Input tokens on | Δ tokens | Cost off | Cost on | Turns off/on | What the hook did |",
             "|---|:---:|:---:|---:|---:|---:|---:|---:|:---:|---|"]
    tot = {"off": [0, 0.0], "on": [0, 0.0]}
    for t in tasks:
        i_off, i_on = agg(t["id"], "off", "input_tokens"), agg(t["id"], "on", "input_tokens")
        c_off, c_on = agg(t["id"], "off", "cost_usd"), agg(t["id"], "on", "cost_usd")
        if None in (i_off, i_on, c_off, c_on):
            lines.append(f"| {t['id']} | {cell_ok(t['id'], 'off')} | {cell_ok(t['id'], 'on')} | | | | | | | run error |")
            continue
        tot["off"][0] += i_off; tot["on"][0] += i_on; tot["off"][1] += c_off; tot["on"][1] += c_on
        d = (i_on - i_off) / i_off
        lines.append(f"| {t['id']} | {cell_ok(t['id'], 'off')} | {cell_ok(t['id'], 'on')} | {i_off:,.0f} | {i_on:,.0f} | "
                     f"{d:+.0%} | ${c_off:.3f} | ${c_on:.3f} | {agg(t['id'], 'off', 'turns'):.0f}/{agg(t['id'], 'on', 'turns'):.0f} | {routes(t['id'])} |")
    ok_off = sum(r.get("correct", False) for r in rows if r["arm"] == "off")
    ok_on = sum(r.get("correct", False) for r in rows if r["arm"] == "on")
    n_off = sum(1 for r in rows if r["arm"] == "off"); n_on = sum(1 for r in rows if r["arm"] == "on")
    if tot["off"][0]:
        lines.append(f"| **Total** | **{ok_off}/{n_off}** | **{ok_on}/{n_on}** | **{tot['off'][0]:,.0f}** | **{tot['on'][0]:,.0f}** | "
                     f"**{(tot['on'][0] - tot['off'][0]) / tot['off'][0]:+.0%}** | **${tot['off'][1]:.2f}** | **${tot['on'][1]:.2f}** | | |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-file", default=str(ROOT / "bench" / "agent_tasks.json"))
    ap.add_argument("--tasks", help="comma-separated task ids (default: all)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--model")
    ap.add_argument("--no-warmup", action="store_true")
    ap.add_argument("--kinds", default="all",
                    help="LEANTEXT_HOOK_KINDS for the 'on' arm (default all, so every kind is measured)")
    ap.add_argument("--keep-transcripts", action="store_true", help="save each session transcript next to the logs")
    ap.add_argument("--regrade", metavar="RESULTS_JSON", help="re-grade stored answers with the current task file; no runs")
    ap.add_argument("--out", default=str(ROOT / "bench" / "agent_results"))
    a = ap.parse_args()

    tasks = json.loads(Path(a.tasks_file).read_text())
    if a.regrade:
        rows = json.loads(Path(a.regrade).read_text())
        by_id = {t["id"]: t for t in tasks}
        for r in rows:
            if r["task"] in by_id and "answer" in r:
                r["correct"] = all(re.search(p, r["answer"], re.I | re.S) for p in by_id[r["task"]]["expect"])
        Path(a.regrade).write_text(json.dumps(rows, indent=2))
        table = summarize(rows, [t for t in tasks if any(r["task"] == t["id"] for r in rows)])
        Path(a.out + ".md").write_text(table + "\n")
        print(table)
        return 0
    if a.tasks:
        keep = set(a.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in keep]
    port = serve(ROOT)
    outdir = Path(a.out + "_logs")
    outdir.mkdir(parents=True, exist_ok=True)

    if not a.no_warmup:
        # Prime the prompt cache so neither arm pays the one-off cost of caching Claude Code's system prompt.
        w = run_one({"id": "warmup", "prompt": "Reply with just: ok", "expect": []}, "off", port, a.model, outdir)
        print(f"warm-up: {w.get('input_tokens', '?')} tokens, ${w.get('cost_usd') or 0:.3f}", file=sys.stderr)

    rows = []
    for rep in range(a.repeats):
        for t in tasks:
            for arm in (("off", "on") if rep % 2 == 0 else ("on", "off")):  # alternate order to spread drift
                r = run_one(t, arm, port, a.model, outdir, a.keep_transcripts, a.kinds)
                r["repeat"] = rep
                rows.append(r)
                print(f"{t['id']:<16} {arm:<3} " + (f"ERROR {r['error'][:120]}" if "error" in r else
                      f"{'✓' if r['correct'] else '✗'} {r['input_tokens']:>8,} tok  ${r['cost_usd']:.3f}  "
                      f"{r['turns']} turns  {r['wall_s']}s  {[h['action'] for h in r['hook']]}"), file=sys.stderr, flush=True)
                Path(a.out + ".json").write_text(json.dumps(rows, indent=2))

    table = summarize(rows, tasks)
    note = (f"\nModel: `{a.model or 'Claude Code default'}`, {a.repeats} repeat(s) per arm, fresh headless session per run. "
            "A warm-up run primes the prompt cache first. Input tokens are everything the model processed across all turns, including Claude Code's own system prompt "
            "(mostly cache reads). Cost is the CLI's `total_cost_usd`.\n")
    Path(a.out + ".md").write_text(table + "\n" + note)
    print(table + "\n" + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
