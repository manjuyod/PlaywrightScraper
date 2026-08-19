from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import Page, expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "tests" / "support" / "student_agenda_preview.py"


def _unused_local_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture(scope="module")
def preview_url() -> Iterator[str]:
    port = _unused_local_port()
    url = f"http://127.0.0.1:{port}/"
    process = subprocess.Popen(
        [sys.executable, str(PREVIEW), "--port", str(port)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with urlopen(url, timeout=0.5) as response:  # noqa: S310
                    if response.status == 200:
                        break
            except URLError:
                time.sleep(0.1)
        else:
            raise AssertionError("Synthetic agenda preview did not start")
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture()
def browser_page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        yield page
        page.close()
        browser.close()


def _focus_outline(page: Page, selector: str, index: int = 0) -> dict[str, object]:
    region = page.locator(selector).nth(index)
    region.focus()
    return region.evaluate(
        r"""element => {
            const style = getComputedStyle(element);
            const channels = Array.from(style.outlineColor.matchAll(/[\d.]+/g), match => Number(match[0]));
            const box = element.getBoundingClientRect();
            const parentBox = element.parentElement.getBoundingClientRect();
            return {
                style: style.outlineStyle,
                width: Number.parseFloat(style.outlineWidth),
                offset: Number.parseFloat(style.outlineOffset),
                channels,
                visibleWithinCard: box.left - 5 >= parentBox.left && box.right + 5 <= parentBox.right,
            };
        }"""
    )


def test_report_scroll_regions_use_the_existing_visible_focus_treatment(
    browser_page: Page,
    preview_url: str,
) -> None:
    page = browser_page
    page.goto(preview_url, wait_until="networkidle")

    selectors = [
        ('[aria-label="Current grades and assignments"]', 0),
        ('[aria-label="Grade history"]', 0),
        (".tc-agenda-card .tc-report-card__scroll", 0),
    ]
    for selector, index in selectors:
        outline = _focus_outline(page, selector, index)
        assert outline["style"] == "solid"
        assert outline["width"] >= 3
        assert outline["offset"] >= 2
        assert outline["channels"][:3] == [244, 131, 61]
        assert outline["visibleWithinCard"] is True


def test_student_report_embeds_primary_agenda_and_keeps_secondary_card(
    browser_page: Page,
    preview_url: str,
) -> None:
    page = browser_page
    page.goto(preview_url, wait_until="networkidle")

    expect(page.locator(".tc-grade-agenda")).to_have_count(7)
    expect(page.locator(".tc-agenda-card")).to_have_count(1)
    expect(page.get_by_role("heading", name="Agenda · ParentVUE")).to_be_visible()
    expect(page.get_by_role("heading", name="Agenda · Canvas")).to_have_count(0)

    first_course = page.locator(".tc-grade-agenda").first
    expect(page.get_by_label("Missing assignment").first).not_to_be_visible()
    first_course.locator("summary").click()
    expect(page.get_by_label("Missing assignment").first).to_be_visible()
    expect(page.get_by_label("Upcoming assignment").first).to_be_visible()
    expect(first_course.get_by_text("Week of", exact=False)).to_have_count(0)
    expect(first_course.locator(".tc-agenda-assignment").first).to_contain_text("Systems practice")

    page.get_by_role("link", name="Heatmap").click()
    expect(page.get_by_role("heading", name="Grade Heatmap")).to_be_visible()
    page.get_by_role("link", name="Report").click()
    expect(page.get_by_role("heading", name="Agenda · ParentVUE")).to_be_visible()
