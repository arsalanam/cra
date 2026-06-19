"""Generic Markdown -> PDF renderer (Chromium print-to-PDF).

Renders GitHub-flavoured Markdown (tables, fenced code) plus raw HTML blocks
(e.g. the CSS bar chart + number strip in feature-guide.md) to a polished,
branded PDF. Used for the executive feature guide and the demo walkthroughs.

Usage:
    uv run python scripts/md_to_pdf.py <input.md> <output.pdf> [base_dir]

`base_dir` (optional) is the directory relative image paths resolve against;
defaults to the input file's directory.
"""
from __future__ import annotations

import sys
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

CSS = """
<style>
  @page { size: A4; margin: 14mm 13mm; }
  body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: #1a2233; line-height: 1.45; font-size: 11.5px; }
  h1 { font-size: 21px; color: #0e2a4d; margin: 0 0 4px;
       border-bottom: 3px solid #1f7a8c; padding-bottom: 7px; }
  h2 { font-size: 15px; color: #0e2a4d; margin: 18px 0 7px;
       border-left: 4px solid #1f7a8c; padding-left: 8px; page-break-after: avoid; }
  h3 { font-size: 12.5px; color: #24405f; margin: 12px 0 4px; }
  p { margin: 6px 0; }
  blockquote { border-left: 3px solid #1f7a8c; background: #eef6f8; margin: 10px 0;
               padding: 7px 13px; color: #20384f; border-radius: 0 4px 4px 0; }
  code { background: #eef1f6; padding: 1px 4px; border-radius: 3px;
         font-family: Consolas, Monaco, monospace; font-size: 10.5px; }
  table { border-collapse: collapse; width: 100%; margin: 9px 0; font-size: 10.6px;
          page-break-inside: avoid; }
  th, td { border: 1px solid #cfd8e6; padding: 5px 9px; text-align: left; vertical-align: top; }
  th { background: #0e2a4d; color: #fff; font-weight: 600; }
  tr:nth-child(even) td { background: #f5f8fc; }
  td strong, th { white-space: normal; }
  a { color: #1f7a8c; text-decoration: none; }
  hr { border: 0; border-top: 1px solid #dde4ee; margin: 14px 0; }

  img { max-width: 100%; max-height: 200mm; object-fit: contain;
        border: 1px solid #d4dae6; border-radius: 6px; margin: 8px 0; display: block; }

  .meta-strip { background: #f0f4fa; border: 1px solid #d7e0ee; border-radius: 6px;
                padding: 7px 12px; font-size: 10.5px; color: #2a435f; margin: 10px 0; }

  /* horizontal bar chart */
  .chart { margin: 10px 0 14px; }
  .bar-row { display: flex; align-items: center; margin: 4px 0; }
  .bar-label { width: 170px; font-size: 10.5px; color: #24405f; flex: none; }
  .bar { flex: 1; background: #eef1f6; border-radius: 4px; height: 18px; overflow: hidden; }
  .fill { display: flex; align-items: center; justify-content: flex-end; height: 18px;
          background: linear-gradient(90deg, #1f7a8c, #2e9fb3); color: #fff;
          font-size: 9.5px; font-weight: 600; padding-right: 7px; border-radius: 4px; min-width: 22px; }

  /* number strip */
  .numbers { display: flex; gap: 10px; margin: 12px 0; }
  .num { flex: 1; background: #0e2a4d; color: #fff; border-radius: 7px;
         padding: 10px 8px; text-align: center; }
  .num b { display: block; font-size: 20px; color: #6fd0e0; line-height: 1.1; }
  .num span { font-size: 9.5px; color: #cfe0ee; }
</style>
"""


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: md_to_pdf.py <input.md> <output.pdf> [base_dir]")
        return 2
    src = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    base = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else src.parent

    body = markdown.markdown(
        src.read_text(encoding="utf-8"),
        extensions=["tables", "fenced_code", "sane_lists", "md_in_html"],
    )
    html = f"<!doctype html><html><head><meta charset='utf-8'><base href='{base.as_uri()}/'>{CSS}</head><body>{body}</body></html>"
    tmp = src.with_suffix(".render.html")
    tmp.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(tmp.as_uri(), wait_until="networkidle")
        page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
        page.wait_for_timeout(400)
        page.pdf(path=str(out), format="A4", print_background=True,
                 margin={"top": "14mm", "bottom": "14mm", "left": "13mm", "right": "13mm"})
        browser.close()
    tmp.unlink(missing_ok=True)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
