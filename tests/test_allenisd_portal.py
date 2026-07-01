from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scraper.portals.utils import get_portal_key_from_url


FIXTURE_PATH = Path(__file__).with_name("fixtures") / "allenisd_skyward_gradebook.html"


class FakeLocator:
    def __init__(self, calls: list[tuple], label: str, text: str = "") -> None:
        self.calls = calls
        self.label = label
        self.text = text
        self.first = self

    async def click(self) -> None:
        self.calls.append(("click", self.label))

    async def press_sequentially(self, value: str, delay: int | None = None) -> None:
        self.calls.append(("press_sequentially", self.label, value, delay))

    async def press(self, key: str) -> None:
        self.calls.append(("press", self.label, key))

    async def inner_text(self, timeout: int | None = None) -> str:
        self.calls.append(("inner_text", self.label, timeout))
        return self.text


class FakePopupInfo:
    def __init__(self, popup_page: "FakePage") -> None:
        self.value = AwaitablePage(popup_page)

    async def __aenter__(self) -> "FakePopupInfo":
        return self

    async def __aexit__(self, *_args) -> None:
        return None


class AwaitablePage:
    def __init__(self, page: "FakePage") -> None:
        self.page = page

    def __await__(self):
        async def get_page():
            return self.page

        return get_page().__await__()


class FakeKeyboard:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    async def press(self, key: str) -> None:
        self.calls.append(("keyboard_press", key))


class FakePage:
    def __init__(
        self,
        *,
        name: str = "page",
        popup_page: "FakePage | None" = None,
        html: str = "",
        body_text: str = "",
    ) -> None:
        self.name = name
        self.calls: list[tuple] = []
        self.popup_page = popup_page
        self.html = html
        self.body_text = body_text
        self.url = f"https://example.test/{name}"
        self.keyboard = FakeKeyboard(self.calls)
        self.active_element_ids: list[str] = []

    async def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None) -> None:
        self.calls.append(("goto", url, wait_until, timeout))
        self.url = url

    async def wait_for_timeout(self, timeout: int) -> None:
        self.calls.append(("wait_for_timeout", timeout))

    async def wait_for_load_state(self, state: str) -> None:
        self.calls.append(("wait_for_load_state", state))

    async def wait_for_url(self, pattern, timeout: int | None = None, wait_until: str | None = None) -> None:
        self.calls.append(("wait_for_url", pattern, timeout, wait_until))
        self.url = "https://aisd-tx.us001-rapididentity.com/p/portal"

    def get_by_role(self, role: str, name: str):
        self.calls.append(("get_by_role", role, name))
        return FakeLocator(self.calls, f"{role}:{name}")

    def get_by_text(self, text: str, exact: bool = False):
        self.calls.append(("get_by_text", text, exact))
        return FakeLocator(self.calls, f"text:{text}")

    def locator(self, selector: str):
        self.calls.append(("locator", selector))
        return FakeLocator(self.calls, selector, self.body_text)

    async def evaluate(self, expression: str):
        self.calls.append(("evaluate", expression))
        if self.active_element_ids:
            return self.active_element_ids.pop(0)
        return ""

    def expect_popup(self) -> FakePopupInfo:
        assert self.popup_page is not None
        return FakePopupInfo(self.popup_page)

    async def content(self) -> str:
        self.calls.append(("content",))
        return self.html


def test_get_portal_key_from_url_detects_allenisd() -> None:
    assert get_portal_key_from_url("https://portal.allenisd.org/") == "allenisd"


def test_parse_gradebook_html_extracts_current_course_grades() -> None:
    from scraper.portals.allenisd import AllenISD

    parsed = AllenISD.parse_gradebook_html(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert parsed == {
        "ENGLISH II": 92.5,
        "GEOMETRY": 88.0,
        "BIOLOGY": 94.2,
    }


def test_login_uses_rapididentity_skyward_popup_flow() -> None:
    from scraper.portals.allenisd import AllenISD

    popup = FakePage(name="skyward")
    portal = FakePage(name="portal", popup_page=popup)
    portal.active_element_ids = ["show-password-button", "", "authn-go-button"]
    engine = AllenISD(
        page=portal,
        student_id="runtime-user",
        password="runtime-password",
        login_url="https://portal.allenisd.org",
    )

    asyncio.run(engine.login())

    assert engine.page is popup
    assert ("goto", "https://portal.allenisd.org/", "domcontentloaded", 30_000) in portal.calls
    assert ("press_sequentially", "textbox:Username", "runtime-user", 85) in portal.calls
    assert ("press_sequentially", "textbox:Password", "runtime-password", 95) in portal.calls
    assert ("press", "textbox:Password", "Tab") in portal.calls
    assert portal.calls.count(("keyboard_press", "Tab")) == 2
    assert ("keyboard_press", "Enter") in portal.calls
    assert ("get_by_text", "Skyward Student", True) in portal.calls
    assert ("click", "text:Skyward Student") in portal.calls


def test_fetch_grades_opens_view_all_grades_and_parses_page() -> None:
    from scraper.portals.allenisd import AllenISD

    html = FIXTURE_PATH.read_text(encoding="utf-8")
    page = FakePage(name="skyward", html=html)
    engine = AllenISD(
        page=page,
        student_id="runtime-user",
        password="runtime-password",
        login_url="https://portal.allenisd.org/",
    )

    result = asyncio.run(engine.fetch_grades())

    assert ("get_by_role", "menuitem", "Gradebook") in page.calls
    assert ("get_by_role", "link", "Display Options") in page.calls
    assert ("get_by_role", "link", "View All Grades") in page.calls
    assert result["parsed_grades"]["ENGLISH II"] == 92.5


def test_login_raises_sanitized_error_on_auth_blocker() -> None:
    from scraper.portals import LoginError
    from scraper.portals.allenisd import AllenISD

    page = FakePage(body_text="Verification code required")
    engine = AllenISD(
        page=page,
        student_id="runtime-user",
        password="runtime-password",
        login_url="https://portal.allenisd.org/",
    )

    with pytest.raises(LoginError, match="mfa"):
        asyncio.run(engine.login())
