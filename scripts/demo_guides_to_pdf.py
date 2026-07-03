"""Render the demo walkthrough Markdown files to PDF.

Converts each PHASE*-walkthrough.md to styled HTML, loads it in Chromium with a
file:// base so relative screenshot paths resolve, and prints to PDF.

Run:  uv run python scripts/demo_guides_to_pdf.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent / "demo-screenshots"
GUIDES = ["PHASE02-SAP-walkthrough.md"]

CSS = """
<style>
  @page { size: A4; margin: 16mm 14mm; }
  body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: #1a1f29; line-height: 1.5; font-size: 12px; }
  h1 { font-size: 22px; border-bottom: 3px solid #4a7fd9; padding-bottom: 6px; color: #16233a; }
  h2 { font-size: 16px; margin-top: 22px; color: #1f3a63;
       border-left: 4px solid #4a7fd9; padding-left: 8px; }
  h3 { font-size: 13px; color: #2f3a4a; }
  blockquote { border-left: 3px solid #c9d4e6; background: #f4f7fc; margin: 10px 0;
               padding: 6px 12px; color: #34465f; }
  code { background: #eef1f6; padding: 1px 4px; border-radius: 3px;
         font-family: Consolas, Monaco, monospace; font-size: 11px; }
  pre { background: #0f1115; color: #e6e6e6; padding: 12px; border-radius: 6px;
        overflow-x: auto; font-size: 10.5px; line-height: 1.4; }
  pre code { background: none; color: inherit; padding: 0; }
  img { max-width: 100%; border: 1px solid #d4dae6; border-radius: 6px;
        margin: 8px 0; box-shadow: 0 1px 4px rgba(0,0,0,.08); display: block; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 11px; }
  th, td { border: 1px solid #cdd5e2; padding: 5px 9px; text-align: left; }
  th { background: #eef2f8; }
  a { color: #2a5cad; text-decoration: none; }
  details { margin: 8px 0; }
  summary { cursor: pointer; color: #2a5cad; font-weight: 600; }
  h2 { page-break-after: avoid; }
  img { page-break-inside: avoid; }
</style>
"""


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for name in GUIDES:
            md_path = ROOT / name
            if not md_path.exists():
                print(f"skip {name} (missing)")
                continue
            html_body = markdown.markdown(
                md_path.read_text(encoding="utf-8"),
                extensions=["tables", "fenced_code", "sane_lists"],
            )
            html = f"<!doctype html><html><head><meta charset='utf-8'>{CSS}</head><body>{html_body}</body></html>"
            html_path = ROOT / (md_path.stem + ".html")
            html_path.write_text(html, encoding="utf-8")
            # open the expander content in the PDF
            page.goto(html_path.as_uri(), wait_until="networkidle")
            page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
            page.wait_for_timeout(400)
            pdf_path = ROOT / (md_path.stem + ".pdf")
            page.pdf(path=str(pdf_path), format="A4", print_background=True,
                     margin={"top": "16mm", "bottom": "16mm", "left": "14mm", "right": "14mm"})
            html_path.unlink(missing_ok=True)
            print(f"wrote {pdf_path.name} ({pdf_path.stat().st_size // 1024} KB)")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
