# Real-world fixtures

Captured 2026-10-19. Each one exposed a bug in leantext that a test now covers.

| File | Source | License | What it caught |
|---|---|---|---|
| pypi-uv.html | https://pypi.org/project/uv/ | page: PyPI; README: uv (MIT/Apache-2.0) | 75% of output was hidden "Release files" tab panels; blank line inside every code fence |
| pypi-numpy.html | https://pypi.org/project/numpy/ | page: PyPI; README: NumPy (BSD-3) | 90 hidden release panels |
| pypi-challenge.html | https://pypi.org/project/requests/ (served a bot challenge) | PyPI | exited 0 and returned "enable JavaScript" as content |
| devtv-index.html | https://github.com/shouvik12/devtv | Apache-2.0 | false "suspicious" from tags inside JS strings; app shell with runtime-loaded content |
| compressed.tracemonkey-pldi-09.pdf | https://github.com/mozilla/pdf.js (web/) | Apache-2.0 (pdf.js test asset) | MarkItDown glued words and interleaved columns on a two-column paper |
| alphatrans.pdf | https://github.com/mozilla/pdf.js (test/pdfs/) | Apache-2.0 (pdf.js test asset) | page is mostly graphics; text alone misdescribes it |
| rich-table.png | https://github.com/Textualize/rich (imgs/) | MIT | dim/colored text lost by fixed-threshold binarisation; mean confidence hid the failure |
| rich-traceback.png | https://github.com/Textualize/rich (imgs/) | MIT | 0/20 key strings with fixed threshold; transparent window shadow broke Otsu |
| rich-syntax.png, rich-markdown.png | https://github.com/Textualize/rich (imgs/) | MIT | benchmark inputs (OCR flattens code indentation; see README) |
- python-pathlib.html: docs.python.org/3/library/pathlib.html, built from CPython main Doc/ with Sphinx (Oct 2026)
