from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

from bs4 import BeautifulSoup
from playwright.async_api import Page
import pytest

from scraper.portals.howsschoolgoing import HowsSchoolGoing
from scraper.portals.base import LoginError, PortalEngine


@pytest.mark.parametrize(("grade_html", "expected"), [
    ("A 93.4%*", 93.4),
    ("93.4%*", 93.4),
    ("B+*", 89.0),
    ("<span>A</span><span>93.4%*</span>", 93.4),
    ("A 93.4%", 93.4),
    ("", None),
    ("N/A*", None),
    ("9*3%", None),
])
def test_grade_parser_removes_trailing_star_annotations(monkeypatch, grade_html, expected):
    engine = HowsSchoolGoing(
        cast(Page, SimpleNamespace(url="https://school.howsschoolgoing.com/dashboard/grades")),
        "student", "secret", "https://login.howsschoolgoing.com/central/login",
    )

    async def get_soup():
        return BeautifulSoup(
            '<div class="dataSource_Common_StudentProfile_Grades_GradesTable">'
            f'<table><tr><td>Math</td><td>{grade_html}</td></tr></table></div>',
            "html.parser",
        )

    monkeypatch.setattr(engine, "get_soup", get_soup)
    assert asyncio.run(engine.fetch_grades()) == ({} if expected is None else {"MATH": expected})


@pytest.mark.parametrize(("url", "recover"), [
    ("https://doral.howsschoolgoing.com/dashboard/student-profile/tab/common-student-profile-learner-launchpad", True),
    ("https://login.howsschoolgoing.com/central/login", False),
    ("https://accounts.google.com/signin/challenge/pwd", False),
    ("https://doral.howsschoolgoing.com.attacker.test/dashboard/", False),
    ("http://doral.howsschoolgoing.com/dashboard/", False),
])
def test_login_recovers_only_an_authenticated_portal_dashboard(monkeypatch, url, recover):
    engine = HowsSchoolGoing(cast(Page, SimpleNamespace(url=url)), "student", "secret", "https://login.howsschoolgoing.com/central/login")
    completed = []

    async def rejected(self, first_name=None):
        raise LoginError("SSO flow did not recognize the current page")

    async def after_login(first_name):
        completed.append(first_name)

    monkeypatch.setattr(PortalEngine, "login", rejected)
    monkeypatch.setattr(engine, "after_login", after_login)
    if recover:
        asyncio.run(engine.login("Ada"))
        assert completed == ["Ada"]
    else:
        with pytest.raises(LoginError):
            asyncio.run(engine.login("Ada"))
        assert completed == []


def test_post_login_waits_for_rendered_grade_table():
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.route("https://school.howsschoolgoing.com/**", lambda route: route.fulfill(
                content_type="text/html",
                body='''<div id="data-tab"><button onclick="setTimeout(() => {
                    history.replaceState(null, '', '/dashboard/grades');
                    document.body.insertAdjacentHTML('beforeend', '<div class=&quot;dataSource_Common_StudentProfile_Grades_GradesTable&quot;><table><tr><td>Math</td><td>A 93.4%*</td></tr></table></div>');
                }, 3500)">Grades</button></div>''',
            ))
            await page.goto("https://school.howsschoolgoing.com/dashboard/launchpad")
            try:
                engine = HowsSchoolGoing(page, "student", "secret", "https://login.howsschoolgoing.com/central/login")
                await engine.after_login("Ada")
                assert await page.locator(".dataSource_Common_StudentProfile_Grades_GradesTable").count() == 1
            finally:
                await browser.close()
    asyncio.run(run())


@pytest.mark.parametrize("empty", [False, True])
def test_post_login_waits_for_rows_after_table_shell_is_visible(empty):
    import json
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            rows = '<tr><td colspan="4">No data available in table</td></tr>' if empty else '<tr><td>Math</td><td>A 93.4%*</td></tr>'
            body = '''<div id="data-tab"><button onclick="loadGrades()">Grades</button></div>
                <div class="dataSource_Common_StudentProfile_Grades_GradesTable" style="min-height:100px">
                <table><tbody><tr><td colspan="4">Loading...</td></tr></tbody></table></div>'''
            body += '<script>function loadGrades() {setTimeout(() => document.querySelector("tbody").innerHTML = ' + json.dumps(rows) + ', 500);}</script>'
            await page.route("https://school.howsschoolgoing.com/**", lambda route: route.fulfill(content_type="text/html", body=body))
            await page.goto("https://school.howsschoolgoing.com/dashboard/grades")
            try:
                engine = HowsSchoolGoing(page, "student", "secret", "https://login.howsschoolgoing.com/central/login")
                await engine.after_login("Ada")
                assert await page.get_by_text("Loading...", exact=True).count() == 0
                assert await engine.fetch_grades() == ({} if empty else {"MATH": 93.4})
            finally:
                await browser.close()
    asyncio.run(run())
