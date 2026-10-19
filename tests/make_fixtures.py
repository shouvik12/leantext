"""Generate the offline test fixtures (run once; outputs are committed)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

F = Path(__file__).parent / "fixtures"
F.mkdir(exist_ok=True)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

# ---- 1. A realistic, bloated blog page ------------------------------------------------
nav = "".join(f'<li><a href="/c/{i}" class="nav-link px-3 py-2 text-sm hover:bg-gray-100">Category {i}</a></li>' for i in range(40))
side = "".join(f'<div class="card"><a href="/p/{i}"><h4>Related post number {i}: ten things about caching</h4></a><p>Short teaser text for related post {i} that nobody reads.</p></div>' for i in range(25))
foot = "".join(f'<a href="/f/{i}">Footer link {i}</a> ' for i in range(60))
tracking = "".join(f"<script>window.dataLayer=window.dataLayer||[];dataLayer.push({{event:'e{i}',v:'{'x' * 120}'}});</script>" for i in range(30))
css = "<style>" + " ".join(f".u{i}{{margin:{i}px;padding:{i}px;color:#{i:06x}}}" for i in range(400)) + "</style>"
article = """
<article>
<h1>Retrying HTTP calls without melting your API</h1>
<p class="byline">By Sam Rivera, March 2026</p>
<p>Naive retries turn a brief outage into a thundering herd. The fix is exponential backoff with jitter,
plus a budget so retries can never exceed a fixed share of traffic.</p>
<h2>Backoff with full jitter</h2>
<p>Each attempt sleeps a random duration between zero and an exponentially growing cap:</p>
<pre><code class="language-python">import random, time

def backoff(attempt, base=0.1, cap=10.0):
    return random.uniform(0, min(cap, base * 2 ** attempt))

for attempt in range(5):
    try:
        return call()
    except TransientError:
        time.sleep(backoff(attempt))
</code></pre>
<h2>Choosing a strategy</h2>
<table>
<thead><tr><th>Strategy</th><th>Load on recovery</th><th>Median latency</th></tr></thead>
<tbody>
<tr><td>No jitter</td><td>Spiky</td><td>Low</td></tr>
<tr><td>Equal jitter</td><td>Moderate</td><td>Medium</td></tr>
<tr><td>Full jitter</td><td>Smooth</td><td>Medium</td></tr>
</tbody></table>
<h2>Retry budgets</h2>
<p>Cap retries at 10% of requests over a sliding window. When the budget is spent, fail fast and let
the circuit breaker take over. This bounds the extra load to 1.1x even during a full outage.</p>
<pre><code class="language-go">if budget.Allow() {
    return retry(req)
}
return errBudgetExhausted
</code></pre>
<p>The short version: jitter spreads the load, budgets cap it.</p>
</article>"""
html = f"""<!doctype html><html><head><title>Retrying HTTP calls | DevBlog</title>{css}{tracking}</head><body>
<div id="cookie-banner" class="fixed bottom-0">We use cookies to improve your experience. <button>Accept all</button> <button>Manage preferences</button></div>
<header><nav><ul>{nav}</ul></nav><form><input placeholder="Search"></form></header>
<main>{article}</main>
<aside><h3>Related</h3>{side}<div class="newsletter">Subscribe to our newsletter for weekly tips!</div></aside>
<footer>{foot}<p>© 2026 DevBlog Inc. All rights reserved.</p></footer>
</body></html>"""
(F / "blog.html").write_text(html)

# ---- 2. A text PDF (two pages, with a table-like layout) ------------------------------
c = canvas.Canvas(str(F / "report.pdf"), pagesize=A4)
para = ("Quarterly infrastructure report. Median API latency fell from 182 ms to 141 ms after the "
        "connection-pool change. Error rate held at 0.12 percent. Storage costs rose 8 percent due to "
        "log retention; we recommend reducing retention from 90 to 30 days for debug logs.")
for page in range(2):
    y = 800
    c.setFont("Helvetica-Bold", 16); c.drawString(60, y, f"Infra Report Q3 - Page {page + 1}"); y -= 30
    c.setFont("Helvetica", 11)
    for _ in range(6):
        for chunk in [para[i:i + 95] for i in range(0, len(para), 95)]:
            c.drawString(60, y, chunk); y -= 15
        y -= 10
    c.showPage()
c.save()

# ---- 3. A screenshot of a terminal/error dialog ---------------------------------------
img = Image.new("RGB", (1000, 420), "#1e1e1e")
d = ImageDraw.Draw(img)
mono = ImageFont.truetype(MONO, 22)
lines = [
    "$ npm run build",
    "",
    "> devtv@1.4.0 build",
    "> vite build",
    "",
    "error TS2339: Property 'channelId' does not exist on type 'Spotlight'.",
    "  src/channels/spotlight.ts:42:17",
    "",
    "Found 1 error. Build failed with exit code 2.",
]
for i, line in enumerate(lines):
    d.text((24, 24 + i * 38), line, fill="#e6e6e6" if "error" not in line else "#ff6b6b", font=mono)
img.save(F / "screenshot.png")

# ---- 4. A scanned PDF (image-only page, no text layer) --------------------------------
page = Image.new("RGB", (1240, 1754), "white")
d = ImageDraw.Draw(page)
f = ImageFont.truetype(FONT, 30)
scan_lines = ["INVOICE 2026-0417", "", "Bill to: Example Studio Ltd.", "Service: API integration, 12 hours",
              "Rate: 95.00 per hour", "Total due: 1,140.00", "", "Payment within 30 days of issue."]
for i, line in enumerate(scan_lines):
    d.text((120, 160 + i * 60), line, fill="black", font=f)
page.save(F / "scanned.pdf", "PDF", resolution=150)
print("fixtures written to", F)
