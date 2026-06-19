"""Phase 01 / E2 — Evidence pipeline: protocol (E2a) + search strategy (E2b) + RoB (E2d).

Each sub-demo runs in its own thread. Handoffs (E2c) are narrated in the guide.
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from demo_lib import out_dir, new_context, open_app, new_thread, send, wait_card, card_shot, click_btn  # noqa: E402

OUT = out_dir("phase01-evidence")


def trigger(page, text, first_kind):
    new_thread(page)
    ta = page.locator("textarea.composer-input"); ta.click(); ta.fill(text)
    page.wait_for_timeout(200)
    page.locator(".composer button.btn-primary").click()
    wait_card(page, first_kind)


def main() -> int:
    with sync_playwright() as p:
        browser, ctx = new_context(p)
        page = ctx.new_page()
        page.on("pageerror", lambda e: print(f"  PAGEERROR: {e}", flush=True))
        open_app(page)

        # ---- E2a: PRISMA-P protocol ----
        try:
            print("[e2a] /protocol", flush=True)
            trigger(page, "/protocol SGLT2 inhibitors for HF prevention in T2DM", "protocol_methods")
            card_shot(page, "protocol_methods", OUT / "e2a-01-protocol-methods.png")
            click_btn(page, r"Methods confirmed")
            wait_card(page, "protocol_document")
            card_shot(page, "protocol_document", OUT / "e2a-02-protocol-draft.png")
            click_btn(page, r"Finalize protocol")
            wait_card(page, "protocol_document")
            page.wait_for_timeout(800)
            card_shot(page, "protocol_document", OUT / "e2a-03-protocol-final.png")
        except Exception as e:  # noqa: BLE001
            print(f"[e2a] ERROR {e}", flush=True)

        # ---- E2b: search strategy ----
        try:
            print("[e2b] /search", flush=True)
            trigger(page, "/search proton pump inhibitor + DAPT in post-PCI ACS", "query_blocks")
            card_shot(page, "query_blocks", OUT / "e2b-01-query-blocks.png")
            click_btn(page, r"Confirm terms")
            wait_card(page, "strategy_result")
            page.wait_for_timeout(600)
            card_shot(page, "strategy_result", OUT / "e2b-02-strategy-result.png")
        except Exception as e:  # noqa: BLE001
            print(f"[e2b] ERROR {e}", flush=True)

        # ---- E2d: risk of bias ----
        try:
            print("[e2d] /rob", flush=True)
            trigger(page, "/rob Run RoB 2.0 on PMID 20925534, PMID 30873575, PMID 31091374",
                    "rob_assessments")
            card_shot(page, "rob_assessments", OUT / "e2d-01-rob-assessments.png")
            click_btn(page, r"RoB confirmed")
            wait_card(page, "rob_summary")
            page.wait_for_timeout(800)
            card_shot(page, "rob_summary", OUT / "e2d-02-rob-summary.png")
        except Exception as e:  # noqa: BLE001
            print(f"[e2d] ERROR {e}", flush=True)

        ctx.close(); browser.close()
    print("[e2] DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
