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

Model: Claude Code default, 3 repeats per arm, fresh headless session per run, every file kind enabled. A warm-up run primes the prompt cache first. Input tokens are everything the model processed across all turns, including Claude Code's own system prompt (mostly cache reads). Cost is the CLI's `total_cost_usd`. The pdf-paper rubric accepts any reported speedup of 10x or more (re-graded identically for both arms).
