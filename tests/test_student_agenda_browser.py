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


def test_grade_card_exposes_sync_issue_details_to_pointer_and_keyboard_users(
    browser_page: Page,
    preview_url: str,
) -> None:
    page = browser_page
    page.goto(preview_url, wait_until="networkidle")

    message = "The portal rejected the student's username or password."
    trigger = page.get_by_label(f"Grades: Issue. {message}")

    expect(trigger).to_have_count(1)
    expect(trigger).to_have_attribute("title", message)
    expect(trigger).to_contain_text("Issue")
    expect(trigger.locator("xpath=../..")).to_contain_text("Last checked")

    trigger.focus()
    expect(trigger).to_be_focused()
    trigger.click()
    expect(trigger.locator("xpath=..").get_by_role("note")).to_contain_text(message)


def test_grade_values_use_continuous_translucent_backgrounds(
    browser_page: Page,
    preview_url: str,
) -> None:
    page = browser_page
    page.goto(preview_url, wait_until="networkidle")

    grade_values = page.locator(".tc-grade-card .tc-grade-value")
    expect(grade_values).to_have_count(15)
    styles = grade_values.evaluate_all(
        """elements => elements.map(element => ({
            background: getComputedStyle(element).backgroundColor,
            color: getComputedStyle(element).color,
            text: element.textContent.trim(),
        }))"""
    )
    graded = [style for style in styles if style["text"] != "No grade"]
    ungraded = [style for style in styles if style["text"] == "No grade"]
    assert len(graded) == 14
    assert len(ungraded) == 1
    assert ungraded[0]["background"] == "rgba(0, 0, 0, 0)"
    assert all(style["background"].endswith(", 0.22)") for style in graded)
    assert all(style["color"] == "rgb(15, 23, 42)" for style in styles)
    assert len({style["background"] for style in styles}) > 4
    assert all(style["text"] for style in styles)
    movement_offsets = page.locator(
        '.tc-grade-value [aria-label="Grade increased"], '
        '.tc-grade-value [aria-label="Grade decreased"]'
    ).evaluate_all(
        """elements => elements.map(element => {
            const marker = element.getBoundingClientRect();
            const grade = element.parentElement.getBoundingClientRect();
            return Math.abs(
                (marker.top + marker.height / 2) - (grade.top + grade.height / 2)
            );
        })"""
    )
    assert movement_offsets
    assert max(movement_offsets) <= 0.5


def test_student_report_embeds_primary_agenda_and_keeps_secondary_card(
    browser_page: Page,
    preview_url: str,
) -> None:
    page = browser_page
    page.goto(preview_url, wait_until="networkidle")

    expect(page.locator(".tc-grade-agenda")).to_have_count(8)
    ungraded = page.locator(".tc-grade-agenda").filter(has_text="Design Thinking Seminar")
    expect(ungraded.locator("summary")).to_contain_text("No grade")
    ungraded.locator("summary").click()
    expect(ungraded.locator(".tc-agenda-assignment").first).to_be_visible()
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


@pytest.mark.parametrize("width", [360, 768, 1440])
def test_assignment_metadata_is_visible_inline_in_both_placements(browser_page, preview_url, width):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 1100})
    page.goto(preview_url, wait_until="networkidle")
    embedded = page.locator(".tc-grade-agenda").first
    embedded.locator("summary").click()
    row = embedded.locator(".tc-agenda-assignment").first
    expect(row.get_by_label("Score: 7/10 · 70%", exact=True)).to_be_visible()
    category = row.get_by_text("Formative", exact=True)
    if width == 360:
        expect(category).to_be_hidden()
    else:
        expect(category).to_be_visible()
    expect(row.get_by_label("Low-grade assignment")).to_be_visible()
    expect(row.locator("time")).to_have_text("Aug 25")
    unscored = embedded.locator(".tc-agenda-assignment").filter(has_text="Graph transformations")
    expect(unscored.get_by_label("Score unavailable")).to_have_text("—")
    zero = embedded.locator(".tc-agenda-assignment").filter(has_text="Function comparison")
    expect(zero.get_by_label("Score: 0/10 · 0%", exact=True)).to_be_visible()
    standalone = page.locator(".tc-agenda-card .tc-agenda-class").first
    standalone.locator("summary").click()
    standalone_row = standalone.locator(".tc-agenda-assignment").first
    expect(standalone_row.get_by_label("Score: 79.5%", exact=True)).to_be_visible()
    standalone_category = standalone_row.get_by_text("Summative", exact=True)
    if width == 360:
        expect(standalone_category).to_be_hidden()
    else:
        expect(standalone_category).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for assignment in (row, unscored, standalone_row):
        box = assignment.bounding_box()
        selectors = [".tc-agenda-score", "time"]
        if width != 360:
            selectors.insert(1, ".tc-agenda-category")
        cells = [assignment.locator(selector).bounding_box() for selector in selectors]
        assert box and all(cells)
        for cell in cells:
            assert cell["x"] >= box["x"]
            assert cell["x"] + cell["width"] <= box["x"] + box["width"] + 1
            assert abs((cell["y"] + cell["height"] / 2) - (box["y"] + box["height"] / 2)) < 2
        for left, right in zip(cells, cells[1:]):
            assert left["x"] + left["width"] <= right["x"]
    output = ROOT / "output" / "assignment-metadata"
    output.mkdir(parents=True, exist_ok=True)
    page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
    page.screenshot(path=str(output / f"student-{width}.png"), full_page=True)


def test_embedded_agenda_rows_fit_the_mobile_card_without_horizontal_scrolling(browser_page, preview_url):
    page = browser_page
    page.set_viewport_size({"width": 360, "height": 1000})
    page.goto(preview_url, wait_until="networkidle")
    page.locator(".tc-grade-agenda").evaluate_all("els => els.forEach(el => el.open = true)")
    bounds = page.locator('[aria-label="Current grades and assignments"]').evaluate("""el => {
        const right = el.getBoundingClientRect().right;
        return {
            client: el.clientWidth,
            scroll: el.scrollWidth,
            clipped: Array.from(el.querySelectorAll('.tc-grade-agenda, .tc-agenda-assignment'))
                .filter(row => row.getBoundingClientRect().right > right + 1).length
        };
    }""")
    assert bounds['scroll'] <= bounds['client']
    assert bounds['clipped'] == 0


def test_long_assignment_metadata_keeps_full_accessible_values_without_overflow(browser_page, preview_url):
    page = browser_page
    page.set_viewport_size({"width": 360, "height": 1100})
    page.goto(preview_url, wait_until="networkidle")
    course = page.locator(".tc-agenda-card .tc-agenda-class").filter(has_text="Design Thinking Seminar With")
    course.locator("summary").click()
    title = "A deliberately long fictional prototype evaluation assignment title for overflow inspection"
    row = course.locator(".tc-agenda-assignment").filter(has_text=title)
    expect(row.locator(".tc-agenda-title")).to_have_attribute("title", title)
    expect(row.get_by_label("Score: 0.1234567890123456789/10 · 1.2%", exact=True)).to_be_visible()
    expect(row.get_by_text("Summative", exact=True)).to_be_hidden()
    expect(row.locator("time")).to_have_text("Aug 23 · 18:00")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
