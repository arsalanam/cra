"""Shared Playwright helpers for the chat-workflow demo captures (Phase 01 etc.).

The served index.html now pins Babel 7, so the SPA mounts without interception.
"""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout

BASE = "http://localhost:8000/"
CARD_WAIT = 180_000  # meta-analysis / NMA / IPD turns include a sandbox run


def out_dir(name: str) -> Path:
    d = Path(__file__).resolve().parent.parent / "demo-screenshots" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_context(p, headless: bool = True):
    browser = p.chromium.launch(headless=headless)
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    return browser, ctx


def open_app(page) -> None:
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector(".new-thread-btn", timeout=30_000)
    page.wait_for_timeout(700)


def new_thread(page) -> None:
    page.locator(".new-thread-btn").click()
    page.wait_for_selector("textarea.composer-input", timeout=15_000)
    page.wait_for_timeout(300)


def send(page, text: str) -> None:
    ta = page.locator("textarea.composer-input")
    ta.click()
    ta.fill(text)
    page.locator(".composer button.btn-primary").click()


def wait_card(page, kind: str, timeout: int = CARD_WAIT) -> None:
    page.wait_for_selector(f".card-tag.{kind}", timeout=timeout)
    try:
        page.wait_for_selector(".spinner", state="detached", timeout=timeout)
    except PWTimeout:
        pass
    page.wait_for_timeout(700)


def card_shot(page, kind: str, path: Path) -> None:
    card = page.locator(".card", has=page.locator(f".card-tag.{kind}")).last
    card.scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    card.screenshot(path=str(path))
    print(f"  saved {path.name}", flush=True)


def shot(page, path: Path) -> None:
    page.screenshot(path=str(path))
    print(f"  saved {path.name}", flush=True)


def click_btn(page, label_re: str) -> None:
    btn = page.get_by_role("button", name=re.compile(label_re))
    btn.last.scroll_into_view_if_needed()
    btn.last.click()
