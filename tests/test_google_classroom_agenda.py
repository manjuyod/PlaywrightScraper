from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from scraper.portals import google_classroom
from scraper.portals.google_classroom import GoogleClassroom


ASSIGNED_HTML = """
<ul>
  <li><a href="/c/123/a/stream-10/details">Details</a>
    <div data-course-id="123" data-stream-item-id="stream-10">
      <div class="y9bEQb"><p>Practice set</p><p class="tWeh6">Algebra II</p></div>
      <p class="pOf0gc">Due Aug 18</p>
    </div>
  </li>
</ul>
"""

MISSING_HTML = """
<ul>
  <li><a href="/c/456/a/stream-9/details">Details</a>
    <div data-course-id="456" data-stream-item-id="stream-9">
      <div class="y9bEQb"><p>Reading response</p><p class="tWeh6">English 11</p></div>
      <p class="pOf0gc">Due Aug 16, 11:59 PM</p>
    </div>
  </li>
</ul>
"""

MIXED_DUE_HTML = """
<ul>
  <li><a href="/c/123/a/stream-10/details">Details</a>
    <div data-course-id="123" data-stream-item-id="stream-10">
      <div class="y9bEQb"><p>Practice set</p><p class="tWeh6">Algebra II</p></div>
      <p class="pOf0gc">Due Aug 18</p>
    </div>
  </li>
  <li><a href="/c/123/a/stream-11/details">Details</a>
    <div data-course-id="123" data-stream-item-id="stream-11">
      <div class="y9bEQb"><p>Lab notes</p><p class="tWeh6">Algebra II</p></div>
      <p class="pOf0gc">Due sometime soon</p>
    </div>
  </li>
</ul>
"""


class FakeControl:
    def __init__(self, page: "FakePage", name: str) -> None:
        self.page = page
        self.name = name

    async def click(self) -> None:
        self.page.clicks.append(self.name)
        if self.name in ("Assigned", "Missing"):
            self.page.current_tab = self.name

    async def count(self) -> int:
        return 1


class FakePage:
    def __init__(self) -> None:
        self.clicks: list[str] = []
        self.current_tab = "Assigned"

    async def wait_for_selector(self, _: str, timeout: int) -> None:
        _ = timeout

    def get_by_role(self, role: str, name: str) -> FakeControl:
        _ = role
        return FakeControl(self, name)

    def locator(self, _: str) -> FakeControl:
        return FakeControl(self, "Main Menu")

    async def content(self) -> str:
        return ASSIGNED_HTML if self.current_tab == "Assigned" else MISSING_HTML


class LoginControl:
    def __init__(self, visible: bool) -> None:
        self.visible = visible


class LoginPage:
    def __init__(self, url: str, *, main_menu_visible: bool = False) -> None:
        self.url = url
        self.main_menu_visible = main_menu_visible
        self.goto_urls: list[str] = []

    async def goto(self, url: str, *, wait_until: str) -> None:
        _ = wait_until
        self.goto_urls.append(url)

    def locator(self, selector: str) -> LoginControl:
        assert selector == 'button[aria-label="Main Menu"]'
        return LoginControl(self.main_menu_visible)


def _login_engine(page: LoginPage, **kwargs: object) -> GoogleClassroom:
    return GoogleClassroom(
        page,
        "google-user",
        "google-password",
        "https://classroom.google.com",
        **kwargs,
    )


def _configure_google_login(
    monkeypatch: pytest.MonkeyPatch, *, navigation_times_out: bool = False
) -> list[GoogleClassroom]:
    calls: list[GoogleClassroom] = []

    async def google_login(_: GoogleClassroom) -> None:
        calls.append(_)
        return None

    async def wait_for_navigation(*_: object, **__: object) -> None:
        if navigation_times_out:
            raise google_classroom.PlaywrightTimeout("redirect pending")

    async def control_exists(control: LoginControl, timeout: int = 1000) -> bool:
        _ = timeout
        return control.visible

    monkeypatch.setattr(GoogleClassroom, "google_login", google_login)
    monkeypatch.setattr(google_classroom, "wait_after_nav", wait_for_navigation)
    monkeypatch.setattr(google_classroom, "exists", control_exists)
    return calls


def test_login_accepts_classroom_origin_only_when_main_menu_is_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a Classroom URL is accepted before its authenticated UI is ready."""
    page = LoginPage("https://classroom.google.com/u/0/h", main_menu_visible=True)
    google_login_calls = _configure_google_login(monkeypatch)

    asyncio.run(_login_engine(page).login())

    assert page.goto_urls == []
    assert google_login_calls == []


@pytest.mark.parametrize(
    ("url", "portal_key"),
    [
        ("https://accounts.google.com/signin/challenge", "google_classroom"),
        ("https://unknown.example/continue", None),
    ],
)
def test_login_rejects_unresolved_or_unknown_redirects(
    monkeypatch: pytest.MonkeyPatch, url: str, portal_key: str | None
) -> None:
    """Would fail if a non-Classroom redirect is treated as an authenticated session."""
    page = LoginPage(url)
    _configure_google_login(monkeypatch, navigation_times_out=True)
    monkeypatch.setattr(
        google_classroom, "get_portal_key_from_url", lambda _: portal_key
    )

    with pytest.raises(GoogleClassroom.LoginError, match="^portal login rejected$"):
        asyncio.run(_login_engine(page).login())


def test_login_delegates_once_with_configured_gps_credentials_and_copied_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if approved GPS delegation uses Google credentials or shares auth images."""
    page = LoginPage("https://gps.example/sso/callback")
    _configure_google_login(monkeypatch, navigation_times_out=True)
    constructed: list[tuple[object, ...]] = []

    class GpsEngine:
        def __init__(self, page: LoginPage) -> None:
            self.page = page

        async def login(self) -> None:
            self.page.url = "https://classroom.google.com/u/0/h"
            self.page.main_menu_visible = True
            return None

    def get_portal_key(url: str) -> str | None:
        return "gps" if "gps.example" in url else "google_classroom"

    def get_portal(portal: str):
        assert portal == "gps"

        def construct(*args: object, **kwargs: object) -> GpsEngine:
            constructed.append((*args, kwargs))
            return GpsEngine(args[0])

        return construct

    monkeypatch.setattr(google_classroom, "get_portal_key_from_url", get_portal_key)
    monkeypatch.setattr(google_classroom, "get_portal", get_portal)
    images = ["circle", "triangle", "star"]

    asyncio.run(
        _login_engine(
            page,
            alt_portal_url="https://gps.example/login",
            alt_student_id="gps-user",
            alt_password="gps-password",
            auth_images=images,
        ).login()
    )

    assert len(constructed) == 1
    (
        page_arg,
        sid,
        password,
    ) = constructed[0][:3]
    kwargs = constructed[0][3]
    assert page_arg is page
    assert (sid, password) == ("gps-user", "gps-password")
    assert kwargs == {
        "login_url": "https://gps.example/sso/callback",
        "student_name": None,
        "auth_images": ["circle", "triangle", "star"],
    }
    assert kwargs["auth_images"] is not images


def test_login_rejects_delegation_that_remains_on_gps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a successful GPS handoff is accepted without Classroom readiness."""
    page = LoginPage("https://gps.example/sso/callback")
    _configure_google_login(monkeypatch, navigation_times_out=True)

    class GpsEngine:
        def __init__(self, *_: object, **__: object) -> None:
            return None

        async def login(self) -> None:
            return None

    monkeypatch.setattr(
        google_classroom,
        "get_portal_key_from_url",
        lambda url: "gps" if "gps.example" in url else "google_classroom",
    )
    monkeypatch.setattr(google_classroom, "get_portal", lambda _: GpsEngine)

    with pytest.raises(GoogleClassroom.LoginError, match="^portal login rejected$"):
        asyncio.run(
            _login_engine(
                page,
                alt_portal_url="https://gps.example/login",
                alt_student_id="gps-user",
                alt_password="gps-password",
            ).login()
        )


def test_login_rejects_gps_redirect_when_origins_do_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a same-key portal on another HTTPS origin receives alternate credentials."""
    page = LoginPage("https://other-gps.example/sso/callback")
    _configure_google_login(monkeypatch, navigation_times_out=True)
    constructed = False

    monkeypatch.setattr(google_classroom, "get_portal_key_from_url", lambda _: "gps")

    def get_portal(_: str):
        nonlocal constructed
        constructed = True
        raise AssertionError("origin-mismatched portal must not be constructed")

    monkeypatch.setattr(google_classroom, "get_portal", get_portal)

    with pytest.raises(GoogleClassroom.LoginError, match="^portal login rejected$"):
        asyncio.run(
            _login_engine(
                page,
                alt_portal_url="https://gps.example/login",
                alt_student_id="gps-user",
                alt_password="gps-password",
            ).login()
        )

    assert constructed is False


def test_login_stops_a_delegation_loop_after_one_approved_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a redirected Google engine can repeatedly hand off the same page."""
    page = LoginPage("https://gps.example/sso/callback")
    _configure_google_login(monkeypatch, navigation_times_out=True)
    constructed = 0

    monkeypatch.setattr(google_classroom, "get_portal_key_from_url", lambda _: "gps")

    def get_portal(_: str):
        nonlocal constructed
        constructed += 1
        return GoogleClassroom

    monkeypatch.setattr(google_classroom, "get_portal", get_portal)

    with pytest.raises(GoogleClassroom.LoginError, match="^portal login rejected$"):
        asyncio.run(
            _login_engine(
                page,
                alt_portal_url="https://gps.example/login",
                alt_student_id="gps-user",
                alt_password="gps-password",
            ).login()
        )

    assert constructed == 1


def test_parser_normalizes_sanitized_assigned_and_missing_records() -> None:
    """Would fail if source IDs, due dates/times, or statuses stop being normalized."""
    reference = datetime(2026, 8, 13, 12, 0)

    records = google_classroom._parse_classroom_agenda(
        MISSING_HTML, "missing", reference=reference
    ) + google_classroom._parse_classroom_agenda(ASSIGNED_HTML, "due", reference=reference)

    assert records == [
        {
            "sourceId": "google_classroom:stream-9",
            "course": "English 11",
            "title": "Reading response",
            "dueDate": "2026-08-16",
            "dueTime": "23:59",
            "status": "missing",
        },
        {
            "sourceId": "google_classroom:stream-10",
            "course": "Algebra II",
            "title": "Practice set",
            "dueDate": "2026-08-18",
            "dueTime": None,
            "status": "due",
        },
    ]


def test_parser_omits_only_card_with_unusable_due_text() -> None:
    """Would fail if one row-level date error aborts the complete document parse."""
    records = google_classroom._parse_classroom_agenda(
        MIXED_DUE_HTML, "due", reference=datetime(2026, 8, 13, 12, 0)
    )

    assert records == [
        {
            "sourceId": "google_classroom:stream-10",
            "course": "Algebra II",
            "title": "Practice set",
            "dueDate": "2026-08-18",
            "dueTime": None,
            "status": "due",
        }
    ]


def test_get_agenda_collects_assigned_then_missing_tabs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Would fail if either To-do tab is skipped or missing cannot override due centrally."""
    page = FakePage()
    waits: list[str] = []

    async def wait_for_navigation(_: object, *, pattern: str, **__: object) -> None:
        waits.append(pattern)

    readiness_checks: list[tuple[str, int]] = []

    async def control_exists(control: object, timeout: int = 1000) -> bool:
        readiness_checks.append((getattr(control, "name"), timeout))
        return True

    monkeypatch.setattr(google_classroom, "wait_after_nav", wait_for_navigation)
    monkeypatch.setattr(google_classroom, "exists", control_exists)

    records = asyncio.run(
        GoogleClassroom(page, "student", "password", "https://classroom.google.com").get_agenda()
    )

    assert GoogleClassroom.agenda_capable is True
    assert page.clicks == ["To-do", "Assigned", "Missing"]
    assert waits == ["**/a/not-turned-in/**", "**/a/not-turned-in/**", "**/a/missing/**"]
    assert readiness_checks == [
        ("To-do", 10_000),
        ("Assigned", 10_000),
        ("Missing", 10_000),
    ]
    assert [record["sourceId"] for record in records] == [
        "google_classroom:stream-10",
        "google_classroom:stream-9",
    ]


def test_get_agenda_raises_safe_code_when_navigation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Would fail if a navigation error returns a silent partial agenda."""

    async def navigation_failure(*_: object, **__: object) -> None:
        raise RuntimeError("sensitive navigation details")

    async def control_exists(_: object) -> bool:
        return True

    monkeypatch.setattr(google_classroom, "wait_after_nav", navigation_failure)
    monkeypatch.setattr(google_classroom, "exists", control_exists)

    error_type = getattr(google_classroom, "GoogleClassroomAgendaError")
    with pytest.raises(error_type, match="^google_classroom_agenda_failed$"):
        asyncio.run(
            GoogleClassroom(FakePage(), "student", "password", "https://classroom.google.com").get_agenda()
        )


def test_get_agenda_raises_safe_code_when_parser_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Would fail if a parser error leaks content or returns an incomplete result."""

    async def wait_for_navigation(*_: object, **__: object) -> None:
        return None

    async def control_exists(_: object) -> bool:
        return True

    def parser_failure(*_: object, **__: object) -> list[object]:
        raise ValueError("raw page contents")

    monkeypatch.setattr(google_classroom, "wait_after_nav", wait_for_navigation)
    monkeypatch.setattr(google_classroom, "exists", control_exists)
    monkeypatch.setattr(google_classroom, "_parse_classroom_agenda", parser_failure)

    error_type = getattr(google_classroom, "GoogleClassroomAgendaError")
    with pytest.raises(error_type, match="^google_classroom_agenda_failed$") as error:
        asyncio.run(
            GoogleClassroom(FakePage(), "student", "password", "https://classroom.google.com").get_agenda()
        )

    assert "raw page contents" not in str(error.value)
