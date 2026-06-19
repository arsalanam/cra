"""Playwright driver — Phase 04 / X2 EDC capture (site coordinator) demo.

Pre-req: X1 has published the Vital Signs form (with a hard sys-BP check
70-250 + a soft >180 warning). This script creates the "Smoke deployment"
via the EDC API (collector.html has no create-deployment UI), then drives
collector.html: add site -> add subject -> open form -> valid save ->
hard-check block (BP 300 -> 422) -> soft-check auto-query (BP 200) ->
query panel. Screenshots into demo-screenshots/phase04-ecrf/.

Run:  uv run python scripts/demo_capture_x2_edc.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
COLLECTOR = f"{BASE}/collector.html"
OUT = Path(__file__).resolve().parent.parent / "demo-screenshots" / "phase04-ecrf"
OUT.mkdir(parents=True, exist_ok=True)

SITE_NAME = "Site 01 — University Hospital"
SUBJECT_CODE = "S01-001"


def log(m: str) -> None:
    print(f"[x2] {m}", flush=True)


def card_shot(page, inner_selector: str, name: str) -> None:
    c = page.locator(".card", has=page.locator(inner_selector)).first
    c.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    c.screenshot(path=str(OUT / name))
    log(f"saved {name}")


def setup_deployment(api):
    studies = api.get(f"{BASE}/api/ecrf/studies").json()
    sid = studies[0]["id"]
    # reuse an existing "Smoke deployment" if present, else create one
    deps = api.get(f"{BASE}/api/edc/deployments").json()
    for d in deps:
        if d["name"] == "Smoke deployment":
            return d["id"]
    r = api.post(
        f"{BASE}/api/edc/deployments",
        data={"name": "Smoke deployment", "research_study_id": sid},
        headers={"Content-Type": "application/json"},
    )
    return r.json()["id"]


def discover_fields(api, deployment_id):
    forms = api.get(f"{BASE}/api/edc/deployments/{deployment_id}/forms").json()
    vs = next(f for f in forms if "vital" in f["title"].lower())
    df = api.get(f"{BASE}/api/edc/deployed-forms/{vs['id']}").json()
    ids = {}
    for sec in df["definition"]["sections"]:
        for it in sec["items"]:
            lbl = it["label"].lower()
            if "systolic" in lbl:
                ids["sys"] = it["id"]
            elif "diastolic" in lbl:
                ids["dia"] = it["id"]
            elif "heart" in lbl or "pulse" in lbl:
                ids["hr"] = it["id"]
    return ids


def fill_field(page, item_id, value):
    el = page.locator(f"#f_{item_id}")
    if el.count():
        el.fill(str(value))


def main() -> int:
    with sync_playwright() as p:
        api = p.request.new_context()
        dep_id = setup_deployment(api)
        fields = discover_fields(api, dep_id)
        log(f"deployment={dep_id} fields={fields}")

        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
        page = ctx.new_page()
        page.on("pageerror", lambda e: log(f"PAGEERROR: {e}"))

        log("loading collector.html")
        page.goto(COLLECTOR, wait_until="networkidle")
        page.wait_for_selector("#deployments", timeout=20_000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "10-collector-initial.png"))

        log("select deployment")
        page.select_option("#deployments", dep_id)
        page.wait_for_timeout(1000)

        log("add site")
        page.locator("#new-site").fill(SITE_NAME)
        page.get_by_role("button", name="+ Site").click()
        page.wait_for_timeout(900)

        log("add subject")
        page.locator("#new-subject").fill(SUBJECT_CODE)
        page.get_by_role("button", name="+ Subject").click()
        page.wait_for_selector(".pick", timeout=10_000)
        page.wait_for_timeout(600)
        card_shot(page, "#subjects", "11-site-subject.png")

        log("select subject")
        page.locator(".pick", has_text=SUBJECT_CODE).first.click()
        page.wait_for_timeout(600)

        log("open Vital Signs form for subject")
        page.get_by_role("button", name="Open form for subject").click()
        page.wait_for_selector("#form-render .item", timeout=15_000)
        page.wait_for_timeout(600)
        card_shot(page, "#form-render", "12-form-opened.png")

        log("enter valid vitals -> save OK")
        fill_field(page, fields["sys"], 120)
        fill_field(page, fields.get("dia", ""), 78)
        fill_field(page, fields.get("hr", ""), 72)
        page.get_by_role("button", name="Save", exact=True).click()
        page.wait_for_selector("#status.ok", timeout=15_000)
        page.wait_for_timeout(500)
        card_shot(page, "#form-render", "13-valid-saved.png")

        log("hard-check violation: systolic BP 300 -> 422 blocked")
        fill_field(page, fields["sys"], 300)
        page.locator("#reason").fill("Re-entry of systolic BP reading.")
        page.get_by_role("button", name="Save", exact=True).click()
        page.wait_for_selector("#status.err", timeout=15_000)
        page.wait_for_timeout(500)
        card_shot(page, "#form-render", "14-hard-check-blocked.png")

        log("soft-check violation: systolic BP 200 -> saves + auto-query")
        fill_field(page, fields["sys"], 200)
        page.locator("#reason").fill("Confirmed elevated systolic reading at visit.")
        page.get_by_role("button", name="Save", exact=True).click()
        page.wait_for_selector("#status.ok", timeout=15_000)
        page.wait_for_timeout(800)
        card_shot(page, "#form-render", "15-soft-check-saved.png")
        card_shot(page, "#queries", "16-auto-query.png")

        ctx.close()
        browser.close()
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
