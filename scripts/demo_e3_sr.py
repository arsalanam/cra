"""Phase 01 / E3 — SR title/abstract screening UI (sr.html).

Single-user capture: create project -> ingest from PubMed -> AI-assist ->
screening view -> PRISMA diagram. Dual-review/adjudication is narrated.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "demo-screenshots" / "phase01-evidence"
OUT.mkdir(parents=True, exist_ok=True)
SR = "http://localhost:8000/sr.html"

NAME = "PPI + DAPT upper-GI-bleeding screening (smoke review)"
PICO = ("population: adults post-PCI for ACS on dual antiplatelet therapy\n"
        "intervention: PPI added to DAPT\ncomparator: DAPT alone or DAPT + placebo\n"
        "outcome: upper GI bleeding events")
QUERY = '("proton pump inhibitors"[MeSH Terms] OR PPI) AND ("dual antiplatelet"[tiab] OR DAPT) AND (bleeding OR hemorrhage OR haemorrhage)'
INCLUDE = "Adults >=18\nDual antiplatelet therapy\nReports upper GI bleeding"
EXCLUDE = "Preclinical / animal\nReview articles\nNon-English"


def log(m): print(f"[e3] {m}", flush=True)
def shot(page, name): page.screenshot(path=str(OUT / name)); log(f"saved {name}")


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 1280, "height": 1100}, device_scale_factor=2)
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())
        page.on("pageerror", lambda e: log(f"PAGEERROR: {e}"))

        log("open sr.html")
        page.goto(SR, wait_until="networkidle")
        page.wait_for_selector("button:has-text('New project')", timeout=30_000)
        page.wait_for_timeout(600)

        log("new project form")
        page.get_by_role("button", name=re.compile("New project")).click()
        page.get_by_placeholder(re.compile("PPI . DAPT meta-analysis")).fill(NAME)
        page.get_by_placeholder(re.compile("population:")).fill(PICO)
        page.get_by_placeholder(re.compile("proton pump inhibitors")).fill(QUERY)
        page.get_by_placeholder(re.compile("Adults")).fill(INCLUDE)
        page.get_by_placeholder(re.compile("Preclinical")).fill(EXCLUDE)
        page.wait_for_timeout(300)
        shot(page, "e3-01-new-project.png")

        log("create project")
        page.get_by_role("button", name=re.compile("^Create project$")).click()
        page.wait_for_selector("text=Ingest from search", timeout=20_000)
        page.wait_for_timeout(800)

        log("ingest from PubMed")
        page.get_by_role("button", name=re.compile("Ingest from search")).click()
        page.wait_for_timeout(8000)  # PubMed fan-out + reload

        log("AI-assist (Bedrock)")
        try:
            page.get_by_role("button", name=re.compile("AI-assist")).click()
            page.wait_for_timeout(35000)  # Bedrock classifies up to 25 abstracts
        except Exception as e:  # noqa: BLE001
            log(f"ai-assist click issue: {e}")
        page.reload(wait_until="networkidle")
        page.wait_for_selector("text=Ingest from search", timeout=20_000)
        page.wait_for_timeout(800)
        shot(page, "e3-02-overview.png")

        log("screening view")
        try:
            page.get_by_role("link", name=re.compile("Screen . abstract")).first.click()
            page.wait_for_timeout(2500)
            shot(page, "e3-03-screening.png")
        except Exception as e:  # noqa: BLE001
            log(f"screen view issue: {e}")

        log("PRISMA diagram")
        try:
            page.get_by_role("link", name=re.compile("PRISMA")).first.click()
            page.wait_for_timeout(2500)
            shot(page, "e3-04-prisma.png")
        except Exception as e:  # noqa: BLE001
            log(f"prisma issue: {e}")

        ctx.close(); b.close()
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
