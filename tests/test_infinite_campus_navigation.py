from __future__ import annotations

import asyncio
import json

import pytest
from playwright.async_api import async_playwright

from scraper.portals.infinite_campus import InfiniteCampus
from scraper.portals import infinite_campus_agenda as agenda


def overview(role: str) -> str:
    return f"/campus/apps/portal/{role}/grades"


def classroom(role: str) -> str:
    return f"/campus/apps/portal/{role}/classroom/grades/student-grades"


@pytest.mark.parametrize("already_on_overview", [True, False])
@pytest.mark.parametrize("role", ["student", "parent"])
def test_overview_navigation_waits_for_embedded_overview(already_on_overview, role):
    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            overview_path = overview(role)
            classroom_path = classroom(role)
            outer_overview = f"/campus/nav-wrapper/{role}/portal/{role}/grades"
            initial_frame = overview_path if already_on_overview else classroom_path
            initial_outer = outer_overview if already_on_overview else f"/campus/nav-wrapper/{role}/portal/{role}/classroom/grades/student-grades"
            async def serve(route):
                path = route.request.url.removeprefix("https://ic.example")
                if path == overview_path:
                    body = '<div class="collapsible-card grades__card">Loaded overview</div>'
                elif path == classroom_path:
                    body = '<tl-student-grades>Old course</tl-student-grades>'
                else:
                    body = f'<iframe name="main-workspace" src="{initial_frame}"></iframe>'
                    if not already_on_overview:
                        body += f'''<button id="menu-toggle-button">Menu</button><a href="#" onclick="event.preventDefault(); history.replaceState(null, '', '{outer_overview}'); setTimeout(() => document.querySelector('iframe').src = '{overview_path}', 300)">Grades</a>'''
                await route.fulfill(content_type="text/html", body=body)
            await page.route("https://ic.example/**", serve)
            await page.goto(f"https://ic.example{initial_outer}")
            try:
                engine = InfiniteCampus(page, "student", "password", "https://ic.example")
                await engine.nav_to_grades()
                frame = page.frame("main-workspace")
                assert frame.url.endswith(overview_path)
                assert await frame.locator(".grades__card").count() == 1
            finally:
                await browser.close()
    asyncio.run(run())


@pytest.mark.parametrize("has_assignments", [True, False])
@pytest.mark.parametrize("role", ["student", "parent"])
def test_course_readiness_waits_for_embedded_route_and_loaded_course(has_assignments, role):
    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            # The old overview remains during iframe navigation, then the new
            # route renders a shell before its asynchronous course data arrives.
            row = '<div class="selcat-assignment-row">Loaded work</div>' if has_assignments else ""
            loaded = f'<tl-grading-detail><tl-grading-task-list><div>Loaded course{row}</div></tl-grading-task-list></tl-grading-detail>'
            overview_path = overview(role)
            classroom_path = classroom(role)
            async def serve(route):
                if route.request.url.endswith(overview_path):
                    body = '<tl-grading-task-list>Old overview</tl-grading-task-list>'
                elif route.request.url.endswith(classroom_path):
                    body = '<tl-student-grades id="course"></tl-student-grades>' + f'<script>setTimeout(() => document.querySelector("#course").innerHTML = {json.dumps(loaded)}, 350)</script>'
                else:
                    body = f'<iframe name="main-workspace" src="{overview_path}"></iframe>'
                await route.fulfill(content_type="text/html", body=body)
            await page.route("https://ic.example/**", serve)
            await page.goto("https://ic.example/")
            await page.evaluate("path => setTimeout(() => document.querySelector('iframe').src = path, 300)", classroom_path)
            try:
                frame = await agenda._open_course_grades(await agenda._wait_for_course_page(page))
                assert frame.url.endswith(classroom_path)
                assert await frame.locator("tl-grading-detail").count() == 1
                assert await frame.locator(".selcat-assignment-row").count() == int(has_assignments)
            finally:
                await browser.close()
    asyncio.run(run())


@pytest.mark.parametrize("host, marker, expected", [
    ("coralnv.infinitecampus.org", "Term Q1", "Q1"),
    ("coralnv.infinitecampus.org", "Term Q2", None),
    ("campus.ccsd.net", "Term Q1", None),
    ("coralnv.infinitecampus.org.attacker.test", "Term Q1", None),
])
def test_coral_current_term_heading_is_a_district_specific_marker(host, marker, expected):
    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=f'<tl-app-term-picker><h3><span class="header-text">{marker}</span></h3></tl-app-term-picker>'))
            await page.goto(f"https://{host}{overview('student')}")
            try:
                assert await InfiniteCampus.select_timeframe(page.main_frame, ("QT1", "Q1")) == expected
            finally:
                await browser.close()
    asyncio.run(run())
