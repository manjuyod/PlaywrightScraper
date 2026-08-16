from __future__ import annotations

import asyncio

import pytest

from scraper.portals import utils as portal_utils
from scraper.portals.gps import GPS


class FakeLoginControl:
    def __init__(self, visible: bool) -> None:
        self.visible = visible

    async def is_visible(self) -> bool:
        return self.visible


class FakeLoginPage:
    def __init__(self, *, login_form_visible: bool) -> None:
        self.url = "https://gpsportal.example/login"
        self.login_form_visible = login_form_visible

    def locator(self, selector: str) -> FakeLoginControl:
        assert selector in {
            "input#identification",
            "input#ember535",
        }
        return FakeLoginControl(self.login_form_visible)


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


def test_login_rejects_a_lingering_gps_form_without_retrying_or_entering_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakeLoginPage(login_form_visible=True)
    login_calls, auth_calls = _configure_login_dependencies(monkeypatch)

    with pytest.raises(GPS.LoginError, match="^portal login rejected$"):
        asyncio.run(_engine(page).login())

    assert login_calls == ["submit"]
    assert auth_calls == []


def test_login_continues_to_gps_auth_after_the_login_form_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakeLoginPage(login_form_visible=False)
    login_calls, auth_calls = _configure_login_dependencies(monkeypatch)

    asyncio.run(_engine(page).login())

    assert login_calls == ["submit"]
    assert auth_calls == ["pictographs"]
