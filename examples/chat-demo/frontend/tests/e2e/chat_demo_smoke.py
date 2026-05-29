"""Playwright smoke checks for the chat demo UI.

Run against an already running dev server:

    CHAT_DEMO_URL=http://localhost:5174 python tests/e2e/chat_demo_smoke.py
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright


BASE_URL = os.environ.get("CHAT_DEMO_URL", "http://localhost:5174")
ARTIFACT_DIR = Path(os.environ.get("CHAT_DEMO_SCREENSHOT_DIR", "/tmp/chat-demo-smoke"))


def goto(page: Page, path: str) -> None:
    page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")
    page.wait_for_timeout(800)


def assert_shell_layout(page: Page, *, max_header_height: int) -> None:
    header_box = page.locator("header").bounding_box()
    composer_box = page.get_by_placeholder(re.compile("Message the assistant", re.I)).bounding_box()
    viewport = page.viewport_size or {"width": 0, "height": 0}
    assert header_box is not None
    assert composer_box is not None
    assert header_box["height"] <= max_header_height
    assert composer_box["y"] + composer_box["height"] <= viewport["height"]


def active_label(page: Page) -> str:
    return page.evaluate(
        """() => {
          const el = document.activeElement;
          if (!el) return "";
          return [
            el.getAttribute("aria-label"),
            el.getAttribute("placeholder"),
            el.textContent,
          ].filter(Boolean).join(" ").replace(/\\s+/g, " ").trim();
        }"""
    )


def keyboard_reachability_smoke(page: Page) -> None:
    goto(page, "/sessions/new")
    labels: list[str] = []
    for _ in range(16):
        page.keyboard.press("Tab")
        labels.append(active_label(page))
    joined = "\n".join(labels).lower()
    assert "new session" in joined
    assert "search sessions" in joined
    assert "message the assistant" in joined
    assert "tools" in joined
    assert "toggle theme" in joined


def desktop_smoke(page: Page) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    goto(page, "/sessions/new")
    tools_button = page.get_by_role("button", name=re.compile(r"^Tools", re.I))
    expect(tools_button).to_be_visible()
    expect(page.get_by_role("button", name=re.compile("Select model|qwen|openai", re.I))).to_be_visible()
    expect(page.get_by_placeholder(re.compile("Message the assistant", re.I))).to_be_visible()
    assert_shell_layout(page, max_header_height=88)

    tools_button.click()
    expect(page.get_by_text("Web Search")).to_be_visible()
    expect(page.get_by_text("Web Fetch")).to_be_visible()
    page.keyboard.press("Escape")

    page.get_by_role("button", name=re.compile("Select model|qwen|openai", re.I)).click()
    expect(page.get_by_placeholder(re.compile("Search models", re.I))).to_be_visible()
    page.get_by_placeholder(re.compile("Search models", re.I)).fill("qwen")
    expect(page.get_by_text(re.compile("qwen", re.I)).first).to_be_visible()
    page.keyboard.press("Escape")

    page.get_by_placeholder(re.compile("Search sessions", re.I)).fill("no-session-should-match")
    expect(page.get_by_text("No matching sessions.")).to_be_visible()
    keyboard_reachability_smoke(page)
    page.screenshot(path=str(ARTIFACT_DIR / "desktop.png"), full_page=True)


def mobile_smoke(page: Page) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    goto(page, "/sessions/new")
    assert_shell_layout(page, max_header_height=132)
    page.get_by_role("button", name="Open sidebar").click()
    expect(page.get_by_placeholder(re.compile("Search sessions", re.I))).to_be_visible()
    page.get_by_role("button", name="Close sidebar").first.click()
    expect(page.get_by_placeholder(re.compile("Message the assistant", re.I))).to_be_visible()
    page.screenshot(path=str(ARTIFACT_DIR / "mobile.png"), full_page=True)


def tablet_smoke(page: Page) -> None:
    page.set_viewport_size({"width": 768, "height": 1024})
    goto(page, "/sessions/new")
    assert_shell_layout(page, max_header_height=132)
    page.screenshot(path=str(ARTIFACT_DIR / "tablet.png"), full_page=True)


def wide_smoke(page: Page) -> None:
    page.set_viewport_size({"width": 1920, "height": 1080})
    goto(page, "/sessions/new")
    assert_shell_layout(page, max_header_height=88)
    page.screenshot(path=str(ARTIFACT_DIR / "wide.png"), full_page=True)


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        desktop_smoke(page)
        mobile_smoke(page)
        tablet_smoke(page)
        wide_smoke(page)
        browser.close()


if __name__ == "__main__":
    main()
