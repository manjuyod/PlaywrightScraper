from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeout
from tenacity import wait_none

from scraper.portals import utils as portal_utils
from scraper.portals.gps import GPS


class FakeLoginControl:
    def __init__(self, visible: bool) -> None:
        self.visible = visible

    async def is_visible(self) -> bool:
        return self.visible


class FakeLoginPage:
    def __init__(
        self, *, username_visible: bool, password_visible: bool
    ) -> None:
        self.url = "https://gpsportal.example/login"
        self.username_visible = username_visible
        self.password_visible = password_visible

    def locator(self, selector: str) -> FakeLoginControl:
        visibility = {
            "input#identification": self.username_visible,
            "input#ember535": self.password_visible,
        }
        return FakeLoginControl(visibility[selector])


def _engine(page: FakeLoginPage) -> GPS:
    return GPS(
        page,  # type: ignore[arg-type]
        "gps-user",
        "gps-password",
        "https://gpsportal.example/login",
        auth_images=["first", "second", "third"],
    )


def _configure_login_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[str], list[str]]:
    login_calls: list[str] = []
    auth_calls: list[str] = []

    async def fake_universal_login_flow(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        login_calls.append("submit")

    async def fake_gps_auth(self: GPS) -> None:
        auth_calls.append("pictographs")

    monkeypatch.setattr(portal_utils, "universal_login_flow", fake_universal_login_flow)
    monkeypatch.setattr(GPS, "do_gps_auth", fake_gps_auth)
    return login_calls, auth_calls


@pytest.mark.parametrize(
    ("username_visible", "password_visible"),
    [(True, False), (False, True)],
    ids=("username-only", "password-only"),
)
def test_login_rejects_a_lingering_gps_form_without_retrying_or_entering_auth(
    monkeypatch: pytest.MonkeyPatch,
    username_visible: bool,
    password_visible: bool,
) -> None:
    page = FakeLoginPage(
        username_visible=username_visible,
        password_visible=password_visible,
    )
    login_calls, auth_calls = _configure_login_dependencies(monkeypatch)

    with pytest.raises(GPS.LoginError, match="^portal login rejected$"):
        asyncio.run(_engine(page).login())

    assert login_calls == ["submit"]
    assert auth_calls == []


def test_login_continues_to_gps_auth_after_the_login_form_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakeLoginPage(username_visible=False, password_visible=False)
    login_calls, auth_calls = _configure_login_dependencies(monkeypatch)

    asyncio.run(_engine(page).login())

    assert login_calls == ["submit"]
    assert auth_calls == ["pictographs"]


def test_login_does_not_resubmit_credentials_when_pictograph_readiness_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a post-submit GPS timeout re-enters the whole login flow."""
    page = FakeLoginPage(username_visible=False, password_visible=False)
    login_calls, _ = _configure_login_dependencies(monkeypatch)

    async def fail_pictograph_readiness(_self: GPS) -> None:
        raise PlaywrightTimeout("sensitive post-submit detail")

    monkeypatch.setattr(GPS, "do_gps_auth", fail_pictograph_readiness)
    engine = _engine(page)

    with pytest.raises(Exception) as raised:
        asyncio.run(
            type(engine).login.retry_with(wait=wait_none())(engine)
        )

    assert login_calls == ["submit"]
    assert type(raised.value) is GPS.LoginError
    assert str(raised.value) == "portal login rejected"
