"""Playwright driver — Phase 04 / X1 eCRF design (Form Builder) demo.

Drives http://localhost:8000/ecrf.html: create study -> AI-draft CRFs from a
protocol -> load a draft -> save -> publish -> export ODM-XML. One screenshot
per stage into demo-screenshots/phase04-ecrf/.

Run:  uv run python scripts/demo_capture_x1_ecrf.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ECRF = "http://localhost:8000/ecrf.html"
OUT = Path(__file__).resolve().parent.parent / "demo-screenshots" / "phase04-ecrf"
OUT.mkdir(parents=True, exist_ok=True)

STUDY_NAME = "SMOKE-T2DM-001 — Phase 2 SGLT2i in adults with T2DM (smoke study)"

PROTOCOL = """Phase 2, single-arm, open-label study of an SGLT2 inhibitor in adults with type 2 diabetes mellitus.

Eligibility:
- Adults aged 18-75 years.
- HbA1c 7.0-10.0% at screening.
- On stable metformin >=3 months prior to enrolment.
- BMI 22-40 kg/m2.

Schedule of assessments:
- Visit 1 (Baseline, Week 0): consent, demographics, vital signs, fasting glucose, HbA1c, body weight, eligibility verification, study-drug dispensing.
- Visit 2 (Week 4): vital signs, fasting glucose, AE review.
- Visit 3 (Week 12): vital signs, fasting glucose, HbA1c, body weight, AE review, lab safety panel.
- Visit 4 (Week 24, primary endpoint): vital signs, fasting glucose, HbA1c, body weight, AE review, lab safety panel.
- Visit 5 (EOS, Week 28): final vital signs, AE review, treatment-emergent serious AE inventory.

Primary outcome: change in HbA1c from baseline to Week 24.
Secondary outcomes: fasting glucose, body weight, proportion with HbA1c <7.0% at Week 24.
Safety outcomes: vital signs, lab safety panel, treatment-emergent adverse events (graded per CTCAE v5).

Patient-reported outcomes (ePRO, weekly):
- Diabetes Treatment Satisfaction Questionnaire (DTSQ, 8 items).
- Self-reported hypoglycaemia events (frequency + severity).
"""

WAIT = 120_000  # AI draft turn can take 10-40s


def log(m: str) -> None:
    print(f"[x1] {m}", flush=True)


def shot(page, name: str) -> None:
    page.screenshot(path=str(OUT / name))  # viewport
    log(f"saved {name}")


def card_of(page, inner_selector: str):
    """The .card ancestor that contains the given inner selector."""
    return page.locator(".card", has=page.locator(inner_selector)).first


def card_shot(page, inner_selector: str, name: str) -> None:
    c = card_of(page, inner_selector)
    c.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    c.screenshot(path=str(OUT / name))
    log(f"saved {name} (card of {inner_selector})")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
        page = ctx.new_page()
        page.on("pageerror", lambda e: log(f"PAGEERROR: {e}"))

        log("loading ecrf.html")
        page.goto(ECRF, wait_until="networkidle")
        page.wait_for_selector("#new-study", timeout=20_000)
        page.wait_for_timeout(500)
        shot(page, "00-ecrf-initial.png")

        log("create study")
        page.locator("#new-study").fill(STUDY_NAME)
        page.get_by_role("button", name="Create study").click()
        page.wait_for_selector(".study", timeout=15_000)
        page.locator(".study", has_text="SMOKE-T2DM-001").first.click()
        page.wait_for_timeout(800)
        card_shot(page, "#studies", "01-study-created.png")

        log("paste protocol")
        page.locator("#protocol").fill(PROTOCOL)
        page.wait_for_timeout(300)
        card_shot(page, "#protocol", "02-protocol-pasted.png")

        log("draft CRFs (Bedrock)")
        page.get_by_role("button", name="Draft CRFs").click()
        page.wait_for_selector("#draft-forms .form-row", timeout=WAIT)
        page.wait_for_timeout(800)
        card_shot(page, "#draft-forms", "03-drafted-forms.png")

        # prefer a Vital Signs draft if present, else the first
        rows = page.locator("#draft-forms .form-row")
        target = rows.first
        n = rows.count()
        for i in range(n):
            t = rows.nth(i).inner_text().lower()
            if "vital" in t:
                target = rows.nth(i)
                break
        log("load a draft form into editor")
        target.click()
        page.wait_for_timeout(600)
        card_shot(page, "#editor", "04-draft-loaded.png")

        # Human-in-the-loop review: the designer hardens the systolic-BP range
        # check (soft -> hard, blocks save) and adds a soft >180 warning that
        # auto-raises a query. Demonstrates X1 step 4 (designer edits checks).
        log("designer edits sbp edit-checks (add hard block + soft warning)")
        edited = page.evaluate(
            """() => {
                const ta = document.getElementById('editor');
                const def = JSON.parse(ta.value);
                let hit = null;
                for (const sec of def.sections) for (const it of sec.items) {
                    if ((it.label||'').toLowerCase().includes('systolic')) {
                        const v = it.id;
                        it.edit_checks = [
                            {id:v+'_range_hard', severity:'hard',
                             expression:`is_blank(${v}) or (${v} >= 70 and ${v} <= 250)`,
                             message:'Systolic BP must be between 70 and 250 mmHg (hard limit).'},
                            {id:v+'_high_soft', severity:'soft',
                             expression:`is_blank(${v}) or (${v} <= 180)`,
                             message:'Systolic BP above 180 mmHg — verify reading (possible hypertensive value).'}
                        ];
                        hit = v;
                    }
                }
                ta.value = JSON.stringify(def, null, 2);
                return hit;
            }"""
        )
        log(f"hardened systolic-BP item id = {edited}")
        page.wait_for_timeout(300)
        card_shot(page, "#editor", "05-designer-edited-checks.png")

        log("save draft")
        page.get_by_role("button", name="Save draft").click()
        page.wait_for_selector("#status.ok", timeout=20_000)
        page.wait_for_timeout(500)
        card_shot(page, "#editor", "06-saved-draft.png")

        log("publish")
        page.get_by_role("button", name=re.compile(r"^Publish$")).click()
        page.wait_for_function(
            "document.querySelector('#status') && /Published/i.test(document.querySelector('#status').textContent)",
            timeout=20_000,
        )
        page.wait_for_timeout(500)
        card_shot(page, "#editor", "07-published-editor.png")
        card_shot(page, "#forms", "08-published-forms-list.png")

        log("export ODM-XML")
        try:
            with page.expect_download(timeout=20_000) as dl:
                page.get_by_role("button", name="Export ODM").click()
            dl.value.save_as(str(OUT / "form.odm.xml"))
            log("saved form.odm.xml")
        except Exception as e:  # noqa: BLE001
            log(f"ODM export skipped: {e}")

        ctx.close()
        browser.close()
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
