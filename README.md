# leantext

**Everything an agent reads, as clean Markdown, with proof of the tokens saved.**

```
leantext <url | path | -> [--report]
```

Blogs lose the nav bars, cookie banners and sidebars. PDFs become Markdown, and scanned ones go through OCR. Screenshots go through OCR first and escalate to a vision model only when OCR is weak. The agent only ever sees Markdown.

| Input | Type | Route | Tokens without | Tokens with | Saved | Answer in text | Correct without | Correct with |
|---|---|---|---:|---:|---:|:---:|:---:|:---:|
| pypi-uv | html | trafilatura, dropped 47 hidden | 27,230 | 2,286 | 92% | ✅ | — | — |
| pypi-numpy | html | trafilatura, dropped 90 hidden | 63,307 | 829 | 99% | ✅ | — | — |
| paper-tracemonkey | pdf | pdftotext | 35,462 | 20,692 | 42% | ✅ | — | — |
| shot-rich-table | image | ocr(conf=88, low=11%) | 1,999 | 87 | 96% | ✅ | — | — |
| shot-rich-traceback | image | ocr(conf=92, low=2%) | 3,063 | 251 | 92% | ✅ | — | — |
| shot-rich-syntax | image | ocr(conf=92, low=3%) | 1,756 | 132 | 92% | ✅ | — | — |
| shot-rich-markdown | image | ocr(conf=95, low=0%) | 3,218 | 392 | 88% | ✅ | — | — |
| **Total** | | | **136,035** | **24,669** | **82%** | **7/7** | — | — |

> Real pages (PyPI project pages, a two-column research paper, Rich's terminal screenshots); sources in `tests/fixtures/real/SOURCES.md`. Token-only run: baselines are estimates, and "Answer in text" means the answer survives conversion, which is necessary but not proof of equal quality. Run `python bench/run.py --inputs bench/inputs-real.json` with `ANTHROPIC_API_KEY` set for exact counts and the correctness columns.

## Why this exists

Agents burn tokens on page chrome, raw PDF dumps and screenshots. Converters already exist (MarkItDown, Jina Reader, Firecrawl, Pandoc). leantext adds two things on top of them:

1. **Cost-aware routing.** It picks the cheapest extractor that still works, and pays for a vision model only when OCR fails.
2. **Measured savings.** A built-in `--report` and a public benchmark compare the same questions answered with and without the layer.

## Install

```bash
pip install -e .                 # needs tesseract for OCR, and poppler for the best PDF text:
                                 # apt install tesseract-ocr poppler-utils  /  brew install tesseract poppler
pip install -e ".[anthropic]"    # optional: vision fallback + exact token counts
pip install -e ".[tokens]"       # optional: tiktoken counter
```

This installs two commands, `leantext` and the shorter alias `md`.

## Usage

```bash
leantext https://example.com/post            # Markdown on stdout
leantext report.pdf --report                 # + token comparison on stderr
leantext screenshot.png --no-vision          # OCR only, never call a model
leantext page.html --raw                     # escape hatch: skip extraction
leantext page.html --keep-hidden             # include collapsed tabs and hidden elements
leantext big.pdf --max-tokens 4000           # hard cap with a visible marker
leantext page.html --json                    # structured output for tools
cat page.html | leantext -                   # stdin
```

```
$ leantext tests/fixtures/blog.html --report > /dev/null
leantext report  (html, via trafilatura)
tokenizer: estimate  [approximate: chars/4]
  raw HTML                          9,250  ->      284   97% saved
  naive Markdown (whole page)       1,676  ->      284   83% saved
```

The report compares the output against **two** baselines. Raw HTML is the flattering one. Whole-page Markdown is roughly what an agent's built-in fetch tool already gives it, which makes it the honest one, and it's the one the benchmark uses.

## Use it inside Claude Code (no agent changes)

The agent doesn't need to know leantext exists. A `PreToolUse` hook rewrites its own reads before they run:

| The agent does | The hook does |
|---|---|
| `Read page.html` / `Read scan.pdf` / `Read spec.docx` | points `Read` at a cached Markdown conversion (text PDFs and images: opt-in, see results below) |
| `curl -s URL` or `curl -s URL \| grep ...` (also `wget -qO-`) | runs `leantext URL` in its place, keeping any text filters |
| anything else (source files, `curl -o file`, `curl \| jq`, POSTs) | nothing |

```bash
leantext hook-config        # prints the snippet; merge it into .claude/settings.json
```

**It fails open.** When leantext can't produce trustworthy Markdown (bot wall, JS-only page, suspicious extraction, weak OCR, or no token saving), the original read runs unchanged. For a failed fetch, the agent also sees leantext's reason on stderr. Images never go to a second vision model in this mode, because the agent already is one: weak OCR means the agent gets the image itself.

Every decision is logged to `~/.cache/leantext/hook.jsonl` (`mediated`, `passthrough` with a reason, or `not-eligible` with the command). Turn the hook off for a session with `LEANTEXT_HOOK=off`.

### Does it help a real agent? (Claude Code, end to end)

Same 7 tasks, hook off vs on, 3 fresh headless sessions per arm, every file kind enabled (`python bench/agent_bench.py --repeats 3`):

| Task | Correct off | Correct on | Input tokens off | Input tokens on | Δ tokens | Cost off | Cost on | Turns off/on | What the hook did |
|---|:---:|:---:|---:|---:|---:|---:|---:|:---:|---|
| pdf-paper | 3/3 | 3/3 | 89,471 | 70,112 | -22% | $0.115 | $0.134 | 5/3 | mediated: pdftotext |
| pdf-scanned | 3/3 | 3/3 | 47,371 | 45,853 | -3% | $0.042 | $0.036 | 3/3 | mediated: pdf-scanned->ocr |
| shot-table | 3/3 | 3/3 | 47,955 | 80,495 | +68% | $0.044 | $0.059 | 3/5 | mediated: ocr(conf=88, low=11%) |
| shot-traceback | 3/3 | 3/3 | 50,493 | 60,349 | +20% | $0.055 | $0.056 | 4/4 | mediated: ocr(conf=92, low=2%) |
| curl-uv | 3/3 | 3/3 | 48,586 | 30,682 | -37% | $0.043 | $0.033 | 3/2 | rewritten |
| curl-numpy | 3/3 | 3/3 | 104,750 | 114,504 | +9% | $0.065 | $0.063 | 6/7 | not-eligible |
| curl-botwall | 3/3 | 3/3 | 31,486 | 31,570 | +0% | $0.036 | $0.036 | 2/2 | rewritten |
| **Total** | **21/21** | **21/21** | **420,112** | **433,565** | **+3%** | **$0.40** | **$0.42** | | |

What this shows:

- **Accuracy held: 21/21 with and without the hook.** Mediation never cost the agent an answer.
- **HTML is the win.** Every mediated curl finished in 2 turns at about 30k tokens. Unmediated runs of the same tasks ranged from 30k to 184k tokens and up to 11 turns, depending on how much raw HTML the agent waded through.
- **Recovery works.** On the bot-wall page, leantext refused 3/3 times, the original fetch ran, and the agent still described the page correctly.
- **Screenshots lost.** A screenshot costs Claude about 2k tokens natively, while each extra agent turn costs about 15k, so OCR text saves little and any extra turn (the agent double-checking the image) wipes that out.
- **Text PDFs lost on cost.** Tokens fell 22% and turns went from 5 to 3, but Claude Code's native PDF reading caches more cheaply than one large text block, so cost rose 16%.
- **Coverage gap:** agents often download with `curl -o file` and then grep the file. The hook leaves downloads alone, so in those runs it never engaged (`curl-numpy`).

So the hook's defaults follow these numbers: it mediates **HTML, scanned PDFs and Office files** (which `Read` can't open at all). Text PDFs and images are opt-in with `LEANTEXT_HOOK_KINDS=all`.

Every row reruns with `python bench/agent_bench.py`. Per-run answers, token breakdowns and hook decisions are in `bench/agent_results.json` and `bench/agent_results_logs/`. `--keep-transcripts` also saves each session transcript there; those contain your account IDs, so they're git-ignored.

## Routing

| Input | Route | Fallback |
|---|---|---|
| HTML / blog | drop content hidden by default (`hidden`, `aria-hidden`, `display:none`, unselected ARIA tab panels), then trafilatura, then code blocks restored verbatim with language tags | markdownify over `<body>` minus chrome |
| Bot challenge / JS-only shell | fail with exit 2 and say so, instead of returning "please enable JavaScript" as content | — |
| PDF with a text layer | first engine whose output isn't glued together: pdftotext (poppler) → pypdf → MarkItDown | — |
| Scanned PDF (no text layer) | render each page, then image route | — |
| Office (docx, pptx, xlsx, epub) | MarkItDown | pandoc |
| Image | Tesseract OCR: crop transparent window shadows, invert dark backgrounds, upscale only small images | vision model if OCR is empty, mean confidence < 70, or > 25% of words are low-confidence |
| Anything else | passthrough | — |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Markdown on stdout |
| 1 | Input error (missing file, fetch failed, unsupported input) |
| 2 | Extraction failed; nothing usable produced |
| 3 | Extraction suspicious (only with `--strict`; otherwise a warning on stderr) |

**"Suspicious"** means the output may not represent the source. leantext names the reason, so the agent (or you) can re-run with `--raw` or `--keep-hidden`:

- code blocks, tables or headings in the source are missing from the output (tags inside `<script>` don't count)
- output is under 10% of the main-content text
- the page is mostly JavaScript with little visible text, so the content probably loads at runtime
- PDF text has glued-together words (a two-column layout read badly)
- PDF pages are mostly graphics, so the text may not describe them

## Known limits (found on real pages)

- **Client-rendered sites** (React/Ember apps, many communities and dashboards) ship an empty shell. A plain fetch can't see their content, and leantext flags it rather than pretending. Live-tab mode (CDP/Playwright) is what will fix this.
- **Bot walls** (Cloudflare, PyPI's challenge) fail with exit 2. Save the page from your browser and pass the `.html` file.
- **OCR flattens code indentation** and can move right-aligned columns to the end. Fine for errors, logs and tables; for code where indentation matters, prefer the source file or vision.
- **Graphics-heavy PDF pages** are flagged; their text may not describe the figure.

## Token counting

`--report` always says which counter it used:

1. Anthropic `count_tokens` API, when `ANTHROPIC_API_KEY` is set (exact for Claude)
2. tiktoken `cl100k_base` (a close proxy)
3. chars / 4, labelled `estimate`

Force one with `LEANTEXT_TOKENIZER=anthropic|tiktoken|estimate`. Image baselines use Claude's documented `width × height / 750` after resizing to a 1568 px long edge. When the text output is *not* cheaper than the image, the report says so.

## Benchmark

```bash
python bench/run.py                                    # token-only, offline
ANTHROPIC_API_KEY=... python bench/run.py              # exact tokens + correctness
python bench/run.py --baseline raw                     # compare against raw HTML instead
```

Add inputs to `bench/inputs.json`. Each one is a source (path or URL), a question, and regexes the answer must match. The harness asks the same question twice, once with the agent's default view of the input and once with leantext's output, then writes `bench/results.md` and `bench/results.json`.

## Caching

Results are cached by content hash in `~/.cache/leantext`, so repeat reads skip extraction and OCR. Disable with `--no-cache` or `LEANTEXT_NO_CACHE=1`, and move the cache with `LEANTEXT_CACHE_DIR`.

## Failure cases become tests

Every bad conversion should become a fixture in `tests/fixtures/` plus an assertion in `tests/test_leantext.py`. OCR confusions get a narrow rule in `extractors/image.py::_FIXES`, never a broad one.

```bash
python tests/make_fixtures.py   # regenerate fixtures
pytest -q
```

## Roadmap

- [x] CLI + router for blog URL, PDF, screenshot
- [x] Token counter + savings report
- [x] OCR to vision fallback
- [x] Hidden-content removal, bot-wall detection, PDF engine scoring (from real-page testing)
- [ ] MCP server wrapping the CLI
- [x] Claude Code hook that mediates the agent's own Read and curl/wget, with an end-to-end on/off benchmark
- [ ] Live browser tabs via CDP/Playwright (separate `--user-data-dir`)

## License

Apache-2.0
