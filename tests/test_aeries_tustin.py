from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import async_playwright

from scraper.portals.aeries import Aeries
from scraper.portals.aeries_agenda import collect_aeries_agenda, parse_aeries_gradebook


LEGACY_LOGIN = "https://parentnet.tustin.k12.ca.us/ParentPortal/LoginParent.aspx"
ORIGIN = "https://tustinusd.aeries.net"
TODAY = date(2026, 9, 22)


def summary_row(number, title, grade, status=""):
    prefix = f"ctl00_MainContent_subGBS_DataDetails_ctl{number:02}_"
    return f"""<tr id="{prefix}trGBKItem">
      <td></td><td><a id="{prefix}lbtnCourseTitle">{title}</a></td>
      <td id="{prefix}cellTablePoint"><span>{grade}</span></td>
      <td>{status}</td><td></td>
    </tr>"""


SUMMARY = '<table id="ctl00_MainContent_subGBS_tblEverything">' + "".join([
    summary_row(1, "English", "98.25"),
    summary_row(2, "Math *", "0.00"),
    summary_row(3, "English", "0.00", "Dropped 08/13/2026"),
    summary_row(4, "No marks yet", ""),
]) + "</table>"


def assignment(*, score="", completed="0 / 10", graded=False, due="09/23/2026"):
    return f"""<div id="test_assignmentsView"><div class="Card">
      <div id="test_scoreData">{score}</div>
      <div id="test_completeData">Complete {completed}</div>
      <span id="test_spTransfer">0.00%</span>
      <div class="TextHeading">1 - Practice</div>
      <div>Due Date: {due}</div><div>Grading Complete: {graded}</div>
    </div></div>"""


@pytest.mark.parametrize("score,graded,due,expected", [
    ("9 / 10", False, "09/01/2026", None),
    ("7 / 10", False, "09/01/2026", "low_score"),
    ("/ 10", False, "09/23/2026", "due"),
    ("/ 10", False, "09/01/2026", "due"),
    ("/ 10", True, "09/01/2026", "missing"),
    ("0 / 10", True, "09/01/2026", "missing"),
    ("NA / 0", True, "09/01/2026", None),
])
def test_agenda_uses_actual_scores_and_teacher_grading_state(score, graded, due, expected):
    records = parse_aeries_gradebook(
        assignment(score=score, graded=graded, due=due),
        course="2- Math- Fall  8/13/2026 - 12/18/2026*",
        reference=TODAY,
    )
    assert [record["status"] for record in records] == ([] if expected is None else [expected])
    if records:
        assert records[0]["course"] == "Math"


def test_tustin_summary_uses_current_percentages_and_excludes_dropped_duplicate():
    from scraper.portals.aeries_gradebook import parse_tustin_grade_summary

    assert parse_tustin_grade_summary(SUMMARY) == {"ENGLISH": 98.25, "MATH": 0.0}


def test_tustin_summary_card_layout_and_empty_shape_validation():
    from scraper.portals.aeries_gradebook import parse_tustin_grade_summary

    html = """<table id="ctl00_MainContent_subGBS_tblEverything"><tr><td>
      <div class="Card"><a id="test_lbtnCardCourseTitle">Science *</a>
      <span id="test_cellCardAll">(82.34)</span></div>
      <div class="Card"><a id="old_lbtnCardCourseTitle">History</a>
      <span id="old_cellCardAll">(0.00)</span><div>Dropped 08/13/2026</div></div>
      </td></tr></table>"""
    assert parse_tustin_grade_summary(html) == {"SCIENCE": 82.34}
    assert parse_tustin_grade_summary('<table id="ctl00_MainContent_subGBS_tblEverything"></table>') == {}
    with pytest.raises(RuntimeError, match="summary_missing"):
        parse_tustin_grade_summary("<html>Login</html>")


class Locator:
    async def wait_for(self, **kwargs):
        pass


class Page:
    def __init__(self, url=ORIGIN + "/student/Dashboard.aspx", final_url=None):
        self.url = url
        self.final_url = final_url
        self.gotos = []

    async def goto(self, url, **kwargs):
        self.gotos.append(url)
        self.url = self.final_url or url

    def locator(self, selector):
        return Locator()

    async def content(self):
        return SUMMARY

    async def evaluate(self, script):
        return [{"course": "1- Math- Fall 8/13/2026 - 12/18/2026", "html": assignment(score="/ 10")}]


def test_grade_collector_uses_summary_through_legacy_tustin_url():
    async def run():
        page = Page()
        engine = Aeries(page, "student", "password", LEGACY_LOGIN)
        assert await engine.fetch_grades() == {"ENGLISH": 98.25, "MATH": 0.0}
        assert page.gotos == [ORIGIN + "/student/GradebookSummary.aspx"]
    asyncio.run(run())


def test_agenda_accepts_known_tustin_redirect():
    async def run():
        page = Page()
        records = await collect_aeries_agenda(page, login_url=LEGACY_LOGIN)
        assert len(records) == 1
        assert page.gotos == [ORIGIN + "/student/GradebookDetails.aspx"]
    asyncio.run(run())


@pytest.mark.parametrize("url", [
    "https://tustinusd.aeries.net.attacker.test/student/Dashboard.aspx",
    "https://other.aeries.net/student/Dashboard.aspx",
    "http://tustinusd.aeries.net/student/Dashboard.aspx",
    "https://tustinusd.aeries.net:444/student/Dashboard.aspx",
])
def test_agenda_rejects_untrusted_initial_origin(url):
    async def run():
        page = Page(url)
        with pytest.raises(RuntimeError, match="origin_unverified"):
            await collect_aeries_agenda(page, login_url=LEGACY_LOGIN)
        assert page.gotos == []
    asyncio.run(run())


@pytest.mark.parametrize("final", [
    "https://other.aeries.net/student/GradebookDetails.aspx",
    "http://tustinusd.aeries.net/student/GradebookDetails.aspx",
    ORIGIN + "/student/LoginParent.aspx",
])
def test_agenda_checks_origin_and_page_again_after_navigation(final):
    async def run():
        page = Page(final_url=final)
        with pytest.raises(RuntimeError, match="origin_unverified"):
            await collect_aeries_agenda(page, login_url=ORIGIN + "/student/LoginParent.aspx")
    asyncio.run(run())


@pytest.mark.parametrize("url", [
    ORIGIN + "/student/LoginParent.aspx",
    "https://mystudent.fjuhsd.org/Parent/LoginParent.aspx",
    ORIGIN + "/student/Dashboard.aspx",  # URL alone does not prove authentication.
])
def test_login_does_not_succeed_from_url_substrings(url, monkeypatch):
    monkeypatch.setattr("scraper.portals.aeries.exists", AsyncMock(return_value=False))
    assert asyncio.run(Aeries(Page(url), "student", "password", LEGACY_LOGIN)._is_logged_in()) is False


def test_login_page_rejection_is_classified_as_bad_login():
    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page()
                await page.route("**/*", lambda route: route.fulfill(body="""
                    <input id="portalAccountUsername"><input id="portalAccountPassword">
                    <div class="alert">The Username or Password entered are incorrect.</div>
                """, content_type="text/html"))
                await page.goto(ORIGIN + "/student/LoginParent.aspx")
                engine = Aeries(page, "student", "password", LEGACY_LOGIN)
                with pytest.raises(engine.LoginError):
                    await engine.validate_login()
            finally:
                await browser.close()
    asyncio.run(run())


def test_agenda_requests_only_active_gradebooks_and_keeps_courses_separate():
    from urllib.parse import parse_qs

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            requests = []
            try:
                page = await browser.new_page()
                async def serve(route):
                    if route.request.method == "POST":
                        selected = parse_qs(route.request.post_data)["gradebook"][0]
                        requests.append(selected)
                        body = assignment(score="7 / 10", graded=True, due="09/01/2026")
                    else:
                        body = '''<form method="post"><select name="gradebook" id="ctl00_MainContent_subGBS_dlGN">
                            <option value="math">1- Math- Fall 8/13/2026 - 12/18/2026</option>
                            <option value="old">&lt;&lt; DROPPED 2- Math- Fall &gt;&gt;</option>
                            <option value="science">3- Science- Fall 8/13/2026 - 12/18/2026</option>
                            </select></form>'''
                    await route.fulfill(body=body, content_type="text/html")
                await page.route("**/*", serve)
                await page.goto(ORIGIN + "/student/Dashboard.aspx")
                records = await collect_aeries_agenda(page, login_url=LEGACY_LOGIN)
                assert sorted(requests) == ["math", "science"]
                assert {r["course"] for r in records} == {"Math", "Science"}
            finally:
                await browser.close()
    asyncio.run(run())


def test_required_password_change_is_a_terminal_login_failure(monkeypatch):
    monkeypatch.setattr("scraper.portals.aeries.exists", AsyncMock(return_value=False))
    page = Page(ORIGIN + "/student/ChangePassword.aspx")
    page.get_by_role = lambda role: Locator()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    engine = Aeries(page, "student", "password", LEGACY_LOGIN)
    # The account needs human intervention; it must not repeatedly log in
    # until the timeout, or attempt to change the password on the user's behalf.
    assert asyncio.run(engine._wait_for_login_result(timeout_ms=1000)) is False
    page.wait_for_timeout.assert_not_awaited()


def test_required_password_change_cannot_pass_via_navigation_markers(monkeypatch):
    monkeypatch.setattr("scraper.portals.aeries.exists", AsyncMock(return_value=True))
    page = Page(ORIGIN + "/student/ChangePassword.aspx")
    engine = Aeries(page, "student", "password", LEGACY_LOGIN)
    assert asyncio.run(engine._is_logged_in()) is False
