"""Phase 01 / E1 — Meta-analysis on synthetic data (forest plot via sandbox)."""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from demo_lib import out_dir, new_context, open_app, new_thread, send, wait_card, card_shot, click_btn  # noqa: E402

OUT = out_dir("phase01-evidence")

QUESTION = ("Does adding a proton pump inhibitor to dual antiplatelet therapy reduce upper GI "
            "bleeding in patients who recently underwent PCI for acute coronary syndrome?")

PICO = """| Element | Value |
|---|---|
| Population | Adults post-PCI for ACS, on dual antiplatelet therapy (aspirin + P2Y12 inhibitor) |
| Intervention | PPI added to DAPT (any PPI, any dose, >=30 days) |
| Comparator | DAPT alone or DAPT + placebo |
| Outcome (primary) | Upper GI bleeding events during follow-up |
| Study design | Randomized controlled trials |"""

EXTRACTION = """Here is the extracted data for these RCTs — proceed to the extraction step:
| Study | Year | Design | PPI events | PPI total | Control events | Control total | Follow-up (mo) |
|---|---|---|---|---|---|---|---|
| Anderson et al. | 2014 | Multicenter RCT | 12 | 1200 | 28 | 1198 | 12 |
| Bekele et al. | 2016 | Single-center RCT | 4 | 320 | 9 | 318 | 6 |
| Chen et al. | 2018 | Multicenter RCT | 18 | 2200 | 41 | 2180 | 12 |
| Diaz et al. | 2019 | Pragmatic RCT | 7 | 850 | 14 | 845 | 9 |
| Eriksen et al. | 2020 | RCT | 5 | 560 | 7 | 562 | 6 |
| Faruq et al. | 2021 | Multicenter RCT | 10 | 1400 | 25 | 1395 | 12 |
| Garcia et al. | 2022 | RCT | 3 | 480 | 8 | 478 | 6 |
| Huang et al. | 2023 | Multicenter RCT | 14 | 1800 | 33 | 1810 | 12 |"""


def main() -> int:
    with sync_playwright() as p:
        browser, ctx = new_context(p)
        page = ctx.new_page()
        page.on("pageerror", lambda e: print(f"  PAGEERROR: {e}", flush=True))

        print("[e1] open + new thread", flush=True)
        open_app(page)
        new_thread(page)

        print("[e1] research question -> pico", flush=True)
        ta = page.locator("textarea.composer-input"); ta.click(); ta.fill(QUESTION)
        page.wait_for_timeout(200)
        page.locator(".composer button.btn-primary").click()
        wait_card(page, "pico")
        card_shot(page, "pico", OUT / "e1-01-pico.png")

        print("[e1] PICO table -> search_results", flush=True)
        send(page, PICO)
        wait_card(page, "search_results")
        card_shot(page, "search_results", OUT / "e1-02-search-results.png")

        print("[e1] extraction table -> data_extraction", flush=True)
        send(page, EXTRACTION)
        wait_card(page, "data_extraction")
        card_shot(page, "data_extraction", OUT / "e1-03-data-extraction.png")

        print("[e1] Run meta-analysis -> forest plot (sandbox)", flush=True)
        click_btn(page, r"Run meta-analysis")
        wait_card(page, "meta_analysis")
        page.wait_for_selector(".forest-plot img", timeout=180_000)
        page.wait_for_timeout(1200)
        card_shot(page, "meta_analysis", OUT / "e1-04-forest-plot.png")

        print("[e1] download PDF", flush=True)
        try:
            with page.expect_download(timeout=30_000) as dl:
                page.get_by_role("button", name="Download PDF").last.click()
            dl.value.save_as(str(OUT / "e1-meta-analysis.pdf"))
            print("  saved e1-meta-analysis.pdf", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  PDF skipped: {e}", flush=True)

        ctx.close(); browser.close()
    print("[e1] DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
