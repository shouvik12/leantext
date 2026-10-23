<p align="center"><img src="assets/logo.svg" alt="leantext" width="360"></p>

**Everything your coding agent reads, as lean Markdown. Fewer tokens, same answers, and when it can't convert safely, it steps aside.**

- 📉 **82% fewer tokens** on real pages, papers and screenshots ([benchmark](#results))
- ✅ **21/21 correct** with and without it in end-to-end Claude Code runs. Mediation never cost an answer.
- 🔌 **Drop-in.** A Claude Code hook rewrites the agent's own `Read` and `curl` calls; nothing to call, no prompt to change.
- 🛟 **Fails open.** Bot walls, JS shells, weak OCR, dropped code or tables, no real saving: the original read runs untouched.
- 🧾 **Shows its receipts.** Every decision is logged with a reason, and `--report` prints tokens before and after.

```bash
$ leantext https://pypi.org/project/numpy/ --report > numpy.md
leantext report  (html, via trafilatura, dropped 90 hidden)
tokenizer: tiktoken
  raw HTML                        263,562  ->      755   100% saved
  naive Markdown (whole page)      92,345  ->      755   99% saved
```

## Quickstart

**1. Install the OCR and PDF tools**

```bash
brew install tesseract poppler          # macOS
sudo apt install tesseract-ocr poppler-utils   # Debian / Ubuntu
```

**2. Install leantext** in its own environment, so it's on your `PATH` and the hook can find it:

```bash
pipx install "leantext[tokens] @ git+https://github.com/shouvik12/leantext"
```

or, with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install "leantext[tokens] @ git+https://github.com/shouvik12/leantext"
```

The `tokens` extra adds tiktoken for accurate token counts. Use `leantext[tokens,anthropic]` for the vision fallback and exact Claude counts; those need `ANTHROPIC_API_KEY`.

Optional add-ons, kept out of the default install so it stays small (about 39 MB on a Mac):

| Extra | Adds | Size |
|---|---|---|
| `office` | Word, Excel, PowerPoint and EPUB files (MarkItDown) | ~40 MB |
| `ocr` | RapidOCR as a backup OCR engine on macOS (Linux and Windows get it by default) | ~80 MB |

For example: `uv tool install "leantext[tokens,office] @ git+https://github.com/shouvik12/leantext"`, or `./install.sh --office` from a clone.

> Slow install? Your network's route to PyPI may be congested even when other sites are fast. Try another network, or point uv at a nearby PyPI mirror with `UV_DEFAULT_INDEX`.

> A bare `pip install` into Homebrew's Python fails with `externally-managed-environment`. Use pipx, uv, or a virtual environment.

**3. Try it**

```bash
leantext https://docs.python.org/3/tutorial/controlflow.html   # web page
leantext paper.pdf                                             # text or scanned PDF
leantext screenshot.png                                        # OCR, vision only if OCR is weak
leantext spec.docx                                             # Office files (needs the office extra)
leantext https://pypi.org/project/numpy/ --report > /dev/null  # see the savings
```

### Inside Claude Code

**1. Generate the hook config:**

```bash
leantext hook-config > /tmp/leantext-hook.json
```

**2. Install it.** Use `~/.claude/settings.json` for every project, or `.claude/settings.json` inside one repo.

If you don't have a settings file yet, copy it in:

```bash
mkdir -p ~/.claude && cp -n /tmp/leantext-hook.json ~/.claude/settings.json
```

If you already have one, add the `hooks` block from `/tmp/leantext-hook.json` to it. With `jq`:

```bash
jq -s '.[0] * .[1]' ~/.claude/settings.json /tmp/leantext-hook.json > /tmp/merged.json && mv /tmp/merged.json ~/.claude/settings.json
```

(This replaces any existing `PreToolUse` hooks, so merge by hand if you already have some.)

**3. Check it's working.** After the agent reads a web page or document:

```bash
tail -3 ~/.cache/leantext/hook.jsonl
```

| The agent does | The hook does |
|---|---|
| `Read page.html`, `Read scan.pdf`, `Read spec.docx` | points `Read` at a cached Markdown conversion |
| `curl -s URL`, `curl -s URL \| grep …`, `wget -qO- URL` | runs `leantext URL` in its place, keeping text filters |
| anything else (source files, `curl -o`, `curl \| jq`, POSTs) | nothing |

Re-reading the same file in a session returns the original. Turn the hook off with `LEANTEXT_HOOK=off`.

## Results

### Token-only, real inputs

| Input | Route | Without | With | Saved |
|---|---|---:|---:|---:|
| PyPI `uv` page | trafilatura, 47 hidden dropped | 27,230 | 2,286 | 92% |
| PyPI `numpy` page | trafilatura, 90 hidden dropped | 63,307 | 829 | 99% |
| Two-column research paper | pdftotext | 35,462 | 20,692 | 42% |
| Terminal screenshot: table | OCR, conf 88 | 1,999 | 87 | 96% |
| Terminal screenshot: traceback | OCR, conf 92 | 3,063 | 251 | 92% |
| Terminal screenshot: syntax | OCR, conf 92 | 1,756 | 132 | 92% |
| Terminal screenshot: markdown | OCR, conf 95 | 3,218 | 392 | 88% |
| **Total** | | **136,035** | **24,669** | **82%** |

The answer to each benchmark question survived conversion in 7/7 cases. Baselines here are estimates; run `python bench/run.py` with `ANTHROPIC_API_KEY` set for exact counts and correctness. Sources are listed in `tests/fixtures/real/SOURCES.md`.

### End to end in Claude Code

Same 7 tasks, hook off vs on, 3 fresh headless sessions each (`python bench/agent_bench.py --repeats 3`):

| Task | Correct off → on | Input tokens | Cost | Turns |
|---|---|---:|---:|---|
| Fetch a docs page (`curl`) | 3/3 → 3/3 | **−37%** | **−23%** | 3 → 2 |
| Scanned PDF | 3/3 → 3/3 | −3% | **−14%** | 3 → 3 |
| Text PDF | 3/3 → 3/3 | −22% | +16% | 5 → 3 |
| Bot-walled page | 3/3 → 3/3 | 0% | 0% | 2 → 2 |
| Download then grep (`curl -o`) | 3/3 → 3/3 | +9% | −3% | 6 → 7 (hook didn't engage) |
| Screenshot: table | 3/3 → 3/3 | +68% | +34% | 3 → 5 |
| Screenshot: traceback | 3/3 → 3/3 | +20% | +2% | 4 → 4 |

**What this means:** HTML and scanned PDFs win clearly. Screenshots *lose* inside Claude Code: an image costs about 2k tokens natively, and one extra agent turn to double-check it costs about 15k. Text PDFs save tokens but cost more, because Claude Code caches native PDF reads cheaply. So the hook's defaults follow the data: it mediates **HTML, scanned PDFs and Office files**. Text PDFs and images are opt-in with `LEANTEXT_HOOK_KINDS=all`.

Full per-run answers and hook decisions are in [`bench/agent_results.md`](bench/agent_results.md).

## Why leantext

Converters already exist: MarkItDown, Jina Reader, Firecrawl, Pandoc. leantext uses some of them, and adds what an agent needs on top:

1. **It sits under the agent.** The hook mediates reads the agent was already making. You don't rely on the agent remembering to call a tool.
2. **Cost-aware routing.** The cheapest extractor that still works wins. A vision model is paid for only when OCR fails.
3. **It knows when not to convert.** Every output is checked against its source, and anything suspicious goes through as the original.
4. **Measured, not claimed.** `--report` and a public benchmark compare the same questions with and without the layer, including the cases where it loses.

## How it decides

```
read ─▶ eligible? ─no─▶ original
          │yes
          ▼
       extract (cheapest route first, 20 s budget, killable)
          │
          ▼
       checks pass? ─no─▶ original
          │yes
          ▼
       ≥15% cheaper? ─no─▶ original
          │yes
          ▼
       lean Markdown
```

**Never converted:** code, config, plain text, lockfiles (`package-lock.json`, `uv.lock`, …), `.diff` and `.patch` files. The agent may need them byte for byte.

**Flagged as suspicious when:**
- code blocks, data tables or headings in the source are missing from the output
- the page's own title is missing from the top of the output (navigation kept instead of content)
- the output is under 10% of the main-content text, garbled, or empty
- the page is a bot challenge or a JavaScript shell whose content loads at runtime
- PDF text has glued-together words, or the pages are mostly graphics

Every decision is logged as lean, flagged or raw, with its reason:

```bash
jq -r .reason ~/.leantext/fallbacks.jsonl | sort | uniq -c
```

<details>
<summary><b>Routes per input type</b></summary>

| Input | Route | Fallback |
|---|---|---|
| HTML / blog | drop content hidden by default (`hidden`, `aria-hidden`, `display:none`, unselected ARIA tab panels) → trafilatura → code blocks restored verbatim with language tags → links resolved to absolute URLs | whole-page Markdown with menus trimmed above the title heading |
| Bot challenge / JS-only shell | exit 2 with the reason, instead of returning "please enable JavaScript" as content | — |
| PDF with a text layer | first engine whose output isn't glued together: pdftotext → pypdf → MarkItDown (if installed) | — |
| Scanned PDF | render each page, then the image route | — |
| Office (docx, pptx, xlsx, epub) | MarkItDown (`office` extra) | pandoc |
| Image | Tesseract: crop window shadows, invert dark backgrounds, upscale small images | vision model if OCR is empty, mean confidence < 70, or > 25% of words are low-confidence |
| Anything else | passthrough | — |

</details>

<details>
<summary><b>CLI reference</b></summary>

```bash
leantext URL|PATH|-  [--report]           # Markdown on stdout, report on stderr
  --raw                                   # skip extraction
  --strict                                # exit 3 on suspicious output
  --no-vision                             # OCR only, never call a model
  --keep-hidden                           # include collapsed tabs and hidden elements
  --max-tokens N                          # hard cap with a visible marker
  --json                                  # structured output for tools
  --no-cache
```


| Exit code | Meaning |
|---|---|
| 0 | Markdown on stdout |
| 1 | Input error: missing file, fetch failed, unsupported input |
| 2 | Extraction failed; nothing usable produced |
| 3 | Extraction suspicious (only with `--strict`; otherwise a warning on stderr) |

| Variable | Effect |
|---|---|
| `LEANTEXT_RAW=1` | disable conversion entirely |
| `LEANTEXT_HOOK=off` | disable the Claude Code hook |
| `LEANTEXT_HOOK_KINDS` | `html,pdf-scanned,office` (default) or `all` |
| `LEANTEXT_TOKENIZER` | `anthropic`, `tiktoken` or `estimate` |
| `LEANTEXT_CACHE_DIR`, `LEANTEXT_NO_CACHE=1` | move or disable the cache |

</details>

<details>
<summary><b>Token counting</b></summary>

`--report` always names its counter: the Anthropic `count_tokens` API when `ANTHROPIC_API_KEY` is set (exact for Claude), else tiktoken `cl100k_base`, else chars / 4, labelled *estimate*. Image baselines use Claude's documented width × height / 750 after resizing to a 1568 px long edge.

The report shows two baselines for web pages. Raw HTML is the flattering one. Whole-page Markdown is roughly what an agent's built-in fetch already returns, which makes it the honest one, and it's the one the benchmark uses.

</details>

## Known limits

- **PyPI sidebars** (latest version, supported Python versions, release date) are dropped. Check them on the page or with `pip index versions <pkg>`.
- **Client-rendered sites** (React apps, dashboards) ship an empty shell, so a plain fetch can't see their content. leantext flags this rather than pretending. Live-tab mode will fix it.
- **Bot walls** (Cloudflare, PyPI's challenge) exit 2. Save the page from your browser and pass the `.html` file.
- **Some data tables** (Wikipedia) and **code inside JavaScript components** (react.dev) can be dropped. Both are detected, and the original goes through.
- **OCR flattens code indentation.** It's fine for errors, logs and tables; for code, prefer the source file or vision.

## Roadmap

- [x] CLI and router for web pages, PDFs, screenshots and Office files
- [x] Token counting and savings report
- [x] OCR → vision fallback
- [x] Claude Code hook with an end-to-end on/off benchmark
- [x] Fallback gates: edit-safe passthrough, title check, time budget, savings floor
- [ ] MCP server
- [ ] Live browser tabs via CDP/Playwright

## Contributing

Every bad conversion should become a fixture in `tests/fixtures/` plus an assertion in `tests/test_leantext.py`. OCR confusions get a narrow rule in `extractors/image.py::_FIXES`, never a broad one.

```bash
python tests/make_fixtures.py   # regenerate fixtures
pytest -q
```

## License

Apache-2.0
