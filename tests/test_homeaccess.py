from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scraper.portals.utils import get_portal_key_from_url


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "homeaccess_classwork.html"


class FakeTracing:
    async def stop(self) -> None:
        return None


class FakeContext:
    def __init__(self) -> None:
        self.tracing = FakeTracing()


class FakeTextLocator:
    def __init__(self, visible: bool) -> None:
        self.visible = visible


class FakeFrame:
    def __init__(self, html: str) -> None:
        self._html = html

    async def content(self) -> str:
        return self._html


class FakePage:
    def __init__(self, *, frame_html: str = "") -> None:
        self.url = "https://homeaccess.example.org/HomeAccess/Account/LogOn"
        self.context = FakeContext()
        self.goto_calls: list[str] = []
        self.frame_calls: list[dict[str, object]] = []
        self.waited = False
        self.error_visible = False
        self._frame = FakeFrame(frame_html)

    async def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None) -> None:
        self.goto_calls.append(url)
        self.url = url

    async def wait_for_timeout(self, timeout: int) -> None:
        self.waited = True

    def get_by_text(self, text: str, exact: bool = False) -> FakeTextLocator:
        return FakeTextLocator(self.error_visible)

    def frame(self, *, name=None, url=None):
        self.frame_calls.append({"name": name, "url": url})
        if name == "sg-legacy-iframe":
            return self._frame
        if callable(url) and url("https://homeaccess.example.org/HomeAccess/Content/Student/Assignments.aspx"):
            return self._frame
        return None

    async def content(self) -> str:
        raise AssertionError("fetch_grades() should read iframe HTML, not top-level page HTML")


def test_get_portal_key_from_url_detects_homeaccess() -> None:
    assert (
        get_portal_key_from_url(
            "https://homeaccess.hboe.org/HomeAccess/Account/LogOn?ReturnUrl=%2fhomeaccess%2f"
        )
        == "homeaccess"
    )


def test_parse_classwork_html_extracts_current_percentages() -> None:
    from scraper.portals.homeaccess import HomeAccess

    html = FIXTURE_PATH.read_text(encoding="utf-8")

    parsed = HomeAccess.parse_classwork_html(html)

    assert parsed == {
        "LANGUAGE ARTS 7": 92.5,
        "ACCELERATED MATH 7": 91.55,
        "CODING & COLLABORATION": 80.19,
    }


def test_login_uses_shared_selectors_and_fetch_grades_reads_iframe(monkeypatch: pytest.MonkeyPatch) -> None:
    from scraper.portals import homeaccess as homeaccess_module
    from scraper.portals.homeaccess import HomeAccess

    page = FakePage(frame_html=FIXTURE_PATH.read_text(encoding="utf-8"))
    calls: dict[str, tuple[str, str] | tuple[str, str] | list[dict[str, object]] | str] = {}

    async def fake_universal_login_flow(
        page_obj,
        login_url,
        sid,
        pw,
        username_selector,
        password_selector,
        **kwargs,
    ) -> None:
        calls["selectors"] = (username_selector, password_selector)
        calls["login_url"] = login_url
        calls["credentials"] = (sid, pw)
        page_obj.url = "https://homeaccess.example.org/HomeAccess/Grades/ReportCard"

    async def fake_wait_after_nav(*args, **kwargs) -> None:
        wait_calls = calls.setdefault("wait_after_nav", [])
        assert isinstance(wait_calls, list)
        wait_calls.append(kwargs)

    async def fake_exists(locator, timeout: int = 1000) -> bool:
        return getattr(locator, "visible", False)

    monkeypatch.setattr(homeaccess_module, "universal_login_flow", fake_universal_login_flow)
    monkeypatch.setattr(homeaccess_module, "wait_after_nav", fake_wait_after_nav)
    monkeypatch.setattr(homeaccess_module, "exists", fake_exists)

    engine = HomeAccess(
        page=page,
        student_id="910407",
        password="secret",
        login_url="https://homeaccess.example.org/HomeAccess/Account/LogOn?ReturnUrl=%2fhomeaccess%2f",
    )

    asyncio.run(engine.login())
    grades = asyncio.run(engine.fetch_grades())

    assert calls["selectors"] == ("#LogOnDetails_UserName", "#LogOnDetails_Password")
    assert page.goto_calls == ["https://homeaccess.example.org/HomeAccess/Classes/Classwork"]
    assert page.frame_calls
    assert grades["parsed_grades"]["LANGUAGE ARTS 7"] == 92.5
