# leantext

**Everything your coding agent reads, as lean Markdown. Fewer tokens, same answers, and when it can't convert safely, it steps aside.**

- 📉 **82% fewer tokens** on real pages, papers and screenshots ([benchmark](#results))
- ✅ **21/21 correct** with and without it in end-to-end Claude Code runs. Mediation never cost an answer.
- 🔌 **Drop-in.** A Claude Code hook rewrites the agent's own `Read` and `curl` calls; nothing to call, no prompt to change.
- 🛟 **Fails open.** Bot walls, JS shells, weak OCR, dropped code or tables, no real saving: the original read runs untouched.
- 🧾 **Shows its receipts.** Every decision is logged with a reason, and `--report` prints tokens before and after.

```bash
$ leantext https://pypi.org/project/numpy/ --report > numpy.md
leantext report  (html, via trafilatura, dropped 90 hidden)
  raw HTML        63,307  ->  829   99% saved
```

## Quickstart

```bash
brew install tesseract poppler          # or: apt install tesseract-ocr poppler-utils
pip install git+https://github.com/shouvik12/leantext

leantext https://docs.python.org/3/tutorial/controlflow.html   # web page
leantext paper.pdf                                             # text or scanned PDF
leantext screenshot.png                                        # OCR, vision only if OCR is weak
leantext spec.docx                                             # Office files
```

### Inside Claude Code

```bash
leantext hook-config     # prints a snippet; merge it into .claude/settings.json
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
| PDF with a text layer | first engine whose output isn't glued together: pdftotext → pypdf → MarkItDown | — |
| Scanned PDF | render each page, then the image route | — |
| Office (docx, pptx, xlsx, epub) | MarkItDown | pandoc |
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

Also installed as the short alias `md`.

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
