"""Playwright driver — Phase 02 / D1 Sample-size + SAP drafter demo.

Drives the live app at http://localhost:8000 through the /sap workflow and
captures one screenshot per stage into demo-screenshots/phase02-sap/.

Run:  uv run python scripts/demo_capture_d1_sap.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = "http://localhost:8000/"
OUT = Path(__file__).resolve().parent.parent / "demo-screenshots" / "phase02-sap"
OUT.mkdir(parents=True, exist_ok=True)

# CDN drift fix: @babel/standalone "latest" now resolves to v8, whose preset-react
# defaults to the automatic JSX runtime and emits `import ...react/jsx-runtime`,
# which breaks the in-browser <script type="text/babel"> (classic) and the app
# never mounts. Pin Babel 7 via request interception so the SPA renders.
BABEL_V7 = "https://unpkg.com/@babel/standalone@7.26.4/babel.min.js"


def pin_babel7(ctx, p) -> None:
    api = p.request.new_context()
    body = api.get(BABEL_V7).body()

    def handler(route):
        route.fulfill(status=200, body=body, content_type="application/javascript")

    ctx.route("**/@babel/standalone**babel.min.js", handler)

SAP_TRIGGER = "/sap two-arm RCT of CBT vs SSRI for depression, primary outcome HAM-D at 12 weeks"
ASSUMPTIONS = (
    "PICOT confirmed. For STEP 2 use a two-sample t-test: control HAM-D mean 18, "
    "intervention mean 14 (mean difference 4), common SD 7, alpha 0.05, power 0.90, "
    "allocation 1:1, 15% dropout."
)

CARD_WAIT = 90_000  # Bedrock turns can take 20-40s


def log(msg: str) -> None:
    print(f"[d1] {msg}", flush=True)


def shot(page, name: str) -> None:
    page.screenshot(path=str(OUT / name))
    log(f"saved {name}")


def card_shot(page, kind: str, name: str) -> None:
    """Screenshot the .card element that contains the given card-tag kind (last one)."""
    card = page.locator(".card", has=page.locator(f".card-tag.{kind}")).last
    card.scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    card.screenshot(path=str(OUT / name))
    log(f"saved {name} (card .{kind})")


def wait_card(page, kind: str) -> None:
    page.wait_for_selector(f".card-tag.{kind}", timeout=CARD_WAIT)
    # let the running spinner clear so buttons are enabled
    try:
        page.wait_for_selector(".spinner", state="detached", timeout=CARD_WAIT)
    except PWTimeout:
        pass
    page.wait_for_timeout(600)


def send(page, text: str) -> None:
    ta = page.locator("textarea.composer-input")
    ta.click()
    ta.fill(text)
    page.locator(".composer button.btn-primary").click()


def click_btn(page, label_re: str) -> None:
    btn = page.get_by_role("button", name=re.compile(label_re))
    btn.last.scroll_into_view_if_needed()
    btn.last.click()


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        pin_babel7(ctx, p)
        page = ctx.new_page()

        log("loading app")
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_selector(".new-thread-btn", timeout=30_000)
        page.wait_for_timeout(800)
        shot(page, "00-welcome.png")

        log("new thread")
        page.locator(".new-thread-btn").click()
        page.wait_for_selector("textarea.composer-input", timeout=15_000)

        log("typing /sap trigger")
        ta = page.locator("textarea.composer-input")
        ta.click()
        ta.fill(SAP_TRIGGER)
        page.wait_for_timeout(300)
        shot(page, "01-sap-typed.png")
        page.locator(".composer button.btn-primary").click()

        log("waiting for PICOT card")
        wait_card(page, "picot")
        card_shot(page, "picot", "02-picot-card.png")

        log("sending assumptions -> sample_size")
        send(page, ASSUMPTIONS)
        wait_card(page, "sample_size")
        card_shot(page, "sample_size", "03-sample-size-card.png")

        log("confirm sample size -> analysis_plan")
        click_btn(page, r"Sample size confirmed")
        wait_card(page, "analysis_plan")
        card_shot(page, "analysis_plan", "04-analysis-plan-card.png")

        log("confirm analysis plan -> sap_document draft")
        click_btn(page, r"Analysis plan confirmed")
        wait_card(page, "sap_document")
        card_shot(page, "sap_document", "05-sap-draft-card.png")

        log("finalize SAP")
        click_btn(page, r"Finalize SAP")
        # final card: wait until a Download button shows up
        page.wait_for_selector("text=Download PDF", timeout=CARD_WAIT)
        try:
            page.wait_for_selector(".spinner", state="detached", timeout=CARD_WAIT)
        except PWTimeout:
            pass
        page.wait_for_timeout(800)
        card_shot(page, "sap_document", "06-sap-final-card.png")

        # capture the PDF download
        log("downloading SAP PDF")
        try:
            with page.expect_download(timeout=30_000) as dl:
                page.get_by_role("button", name=re.compile("Download PDF")).last.click()
            dl.value.save_as(str(OUT / "sap-report.pdf"))
            log("saved sap-report.pdf")
        except Exception as e:  # noqa: BLE001
            log(f"PDF download skipped: {e}")

        # a final full-window shot for context
        page.wait_for_timeout(500)
        shot(page, "07-final-window.png")

        ctx.close()
        browser.close()
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
