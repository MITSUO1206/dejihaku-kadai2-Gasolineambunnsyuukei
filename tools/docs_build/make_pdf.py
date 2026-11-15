"""資料/システム解説書.html → 資料/システム解説書.pdf（Edgeで印刷）。確認用に各ページのPNGも出す"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
html = ROOT / "資料" / "システム解説書.html"
pdf = ROOT / "資料" / "システム解説書.pdf"

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge")
    pg = b.new_page()
    pg.goto(html.as_uri(), wait_until="networkidle")
    pg.pdf(path=str(pdf), format="A4", print_background=True, prefer_css_page_size=True)
    b.close()
print("PDF:", pdf)

if "--preview" in sys.argv:
    import pdfplumber
    out = Path(sys.argv[sys.argv.index("--preview") + 1])
    out.mkdir(parents=True, exist_ok=True)
    with pdfplumber.open(pdf) as d:
        print("pages:", len(d.pages))
        for i, page in enumerate(d.pages, 1):
            page.to_image(resolution=70).save(out / f"p{i}.png")
