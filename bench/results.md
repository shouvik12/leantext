| Input | Type | Route | Tokens without | Tokens with | Saved | Answer in text | Correct without | Correct with |
|---|---|---|---:|---:|---:|:---:|:---:|:---:|
| blog-retries | html | trafilatura | 1,676 | 284 | 83% | ✅ | — | — |
| pdf-infra | pdf | pdftotext | 4,636 | 811 | 83% | ✅ | — | — |
| pdf-scanned-invoice | pdf | pdf-scanned->ocr | 2,318 | 49 | 98% | ✅ | — | — |
| screenshot-build | image | ocr(conf=90, low=3%) | 560 | 50 | 91% | ✅ | — | — |
| **Total** | | | **9,190** | **1,194** | **87%** | **4/4** | — | — |

Token-only run (no API key): baselines are estimates (`estimate` for text; width×height/750 for images and rendered PDF pages). Set ANTHROPIC_API_KEY for exact counts and the correctness columns. "Answer in text" checks the expected answer appears in leantext's output: necessary for a correct answer, not proof of one.
