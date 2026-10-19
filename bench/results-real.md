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

Token-only run (no API key): baselines are estimates (`estimate` for text; width×height/750 for images and rendered PDF pages). Set ANTHROPIC_API_KEY for exact counts and the correctness columns. "Answer in text" checks the expected answer appears in leantext's output: necessary for a correct answer, not proof of one.
