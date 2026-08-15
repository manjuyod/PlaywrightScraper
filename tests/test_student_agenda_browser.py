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


def _box(page: Page, selector: str, index: int = 0) -> dict[str, float]:
    box = page.locator(selector).nth(index).bounding_box()
    assert box is not None
    return box


def _assert_aligned_equal(left: dict[str, float], right: dict[str, float]) -> None:
    assert abs(left["y"] - right["y"]) <= 1
    assert abs((left["y"] + left["height"]) - (right["y"] + right["height"])) <= 1
    assert abs(left["height"] - right["height"]) <= 1


def _scroll_metrics(page: Page, selector: str, index: int = 0) -> dict[str, int]:
    return page.locator(selector).nth(index).evaluate(
        "element => ({clientHeight: element.clientHeight, scrollHeight: element.scrollHeight, scrollTop: element.scrollTop})"
    )


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
        ('[aria-label="Grade history"]', 0),
        (".tc-agenda-card .tc-report-card__scroll", 0),
        (".tc-agenda-card .tc-report-card__scroll", 1),
    ]
    for selector, index in selectors:
        outline = _focus_outline(page, selector, index)
        assert outline["style"] == "solid"
        assert outline["width"] >= 3
        assert outline["offset"] >= 2
        assert outline["channels"][:3] == [244, 131, 61]
        assert outline["visibleWithinCard"] is True


def test_student_report_cards_are_equal_height_and_scroll_independently(
    browser_page: Page,
    preview_url: str,
) -> None:
    page = browser_page
    page.goto(preview_url, wait_until="networkidle")
    expect(page.get_by_role("heading", name="Current grades")).to_be_visible()

    grade_cards = page.locator(".tc-grade-card")
    agenda_cards = page.locator(".tc-agenda-card")
    expect(grade_cards).to_have_count(2)
    expect(agenda_cards).to_have_count(2)

    desktop_grade_boxes = [_box(page, ".tc-grade-card", index) for index in range(2)]
    desktop_agenda_boxes = [_box(page, ".tc-agenda-card", index) for index in range(2)]
    _assert_aligned_equal(*desktop_grade_boxes)
    _assert_aligned_equal(*desktop_agenda_boxes)

    history_scroll = page.get_by_label("Grade history")
    history_heading = page.get_by_role("heading", name="Grade history")
    history_heading_y = history_heading.bounding_box()["y"]
    history_metrics = _scroll_metrics(page, '[aria-label="Grade history"]')
    assert history_metrics["scrollHeight"] > history_metrics["clientHeight"]
    history_scroll.evaluate("element => { element.scrollTop = 180; }")
    assert history_scroll.evaluate("element => element.scrollTop") > 0
    assert abs(history_heading.bounding_box()["y"] - history_heading_y) <= 1

    agenda_scrolls = page.locator(".tc-agenda-card .tc-report-card__scroll")
    expect(agenda_scrolls).to_have_count(2)
    for index in range(2):
        metrics = _scroll_metrics(
            page, ".tc-agenda-card .tc-report-card__scroll", index
        )
        assert metrics["scrollHeight"] > metrics["clientHeight"]

    heading_positions = [
        page.get_by_role("heading", name="Agenda 1 · Canvas").bounding_box()["y"],
        page.get_by_role("heading", name="Agenda 2 · ParentVUE").bounding_box()["y"],
    ]
    legend_positions = [
        page.get_by_label("Assignment status legend").nth(index).bounding_box()["y"]
        for index in range(2)
    ]
    agenda_scrolls.nth(0).evaluate("element => { element.scrollTop = 180; }")
    assert agenda_scrolls.nth(0).evaluate("element => element.scrollTop") > 0
    assert agenda_scrolls.nth(1).evaluate("element => element.scrollTop") == 0
    assert page.get_by_role("heading", name="Agenda 1 · Canvas").bounding_box()["y"] == heading_positions[0]
    assert page.get_by_label("Assignment status legend").nth(0).bounding_box()["y"] == legend_positions[0]

    agenda_scrolls.nth(1).evaluate("element => { element.scrollTop = 180; }")
    assert agenda_scrolls.nth(1).evaluate("element => element.scrollTop") > 0
    assert page.get_by_role("heading", name="Agenda 2 · ParentVUE").bounding_box()["y"] == heading_positions[1]
    assert page.get_by_label("Assignment status legend").nth(1).bounding_box()["y"] == legend_positions[1]

    page.set_viewport_size({"width": 720, "height": 1000})
    page.wait_for_timeout(100)
    mobile_boxes = [
        _box(page, ".tc-grade-card", 0),
        _box(page, ".tc-grade-card", 1),
        _box(page, ".tc-agenda-card", 0),
        _box(page, ".tc-agenda-card", 1),
    ]
    assert max(box["x"] for box in mobile_boxes) - min(box["x"] for box in mobile_boxes) <= 1
    assert [box["y"] for box in mobile_boxes] == sorted(box["y"] for box in mobile_boxes)
    assert len({round(box["y"], 2) for box in mobile_boxes}) == 4
    assert abs(mobile_boxes[0]["height"] - desktop_grade_boxes[0]["height"]) <= 1
    assert abs(mobile_boxes[1]["height"] - desktop_grade_boxes[1]["height"]) <= 1
    assert abs(mobile_boxes[2]["height"] - desktop_agenda_boxes[0]["height"]) <= 1
    assert abs(mobile_boxes[3]["height"] - desktop_agenda_boxes[1]["height"]) <= 1


def test_student_report_disclosures_statuses_and_tabs_remain_interactive(
    browser_page: Page,
    preview_url: str,
) -> None:
    page = browser_page
    page.set_viewport_size({"width": 720, "height": 1000})
    page.goto(preview_url, wait_until="networkidle")

    first_details = page.locator(".tc-agenda-class").first
    first_summary = first_details.locator("summary")
    first_summary.click()
    assert first_details.evaluate("element => element.open") is True
    expect(page.get_by_label("Missing assignment").first).to_be_visible()
    expect(page.get_by_label("Upcoming assignment").first).to_be_visible()
    expect(page.get_by_label("Missing assignment").first).to_have_text("M")
    expect(page.get_by_label("Upcoming assignment").first).to_have_text("DUE")

    first_summary.focus()
    first_summary.press("Enter")
    assert first_details.evaluate("element => element.open") is False
    first_summary.press("Enter")
    assert first_details.evaluate("element => element.open") is True

    page.get_by_role("link", name="Heatmap").click()
    expect(page.get_by_role("heading", name="Grade Heatmap")).to_be_visible()
    page.get_by_role("link", name="Report").click()
    expect(page.get_by_role("heading", name="Agenda 1 · Canvas")).to_be_visible()
    mobile_grade_boxes = [_box(page, ".tc-grade-card", index) for index in range(2)]
    mobile_agenda_boxes = [_box(page, ".tc-agenda-card", index) for index in range(2)]
    assert abs(mobile_grade_boxes[0]["height"] - mobile_grade_boxes[1]["height"]) <= 1
    assert abs(mobile_agenda_boxes[0]["height"] - mobile_agenda_boxes[1]["height"]) <= 1
