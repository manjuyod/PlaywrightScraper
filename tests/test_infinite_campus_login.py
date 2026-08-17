from __future__ import annotations

import asyncio

import pytest

from scraper.portals import infinite_campus as infinite_campus_module
from scraper.portals import utils as portal_utils
from scraper.portals.base import PortalEngine
from scraper.portals.infinite_campus import InfiniteCampus


class FakeLoginTextLocator:
    def __init__(self, text: str, visible: bool = False) -> None:
        self.text = text
        self.visible = visible


class FakeStudentLinkLocator:
    def __init__(self, calls: dict[str, object], name: str) -> None:
        self.calls = calls
        self.name = name

    async def click(self, timeout: int) -> None:
        self.calls["selection_clicks"] = int(self.calls.get("selection_clicks", 0)) + 1
        self.calls["selection_name"] = self.name
        self.calls["selection_timeout"] = timeout


class FakeLoginFrame:
    def __init__(self, calls: dict[str, object]) -> None:
        self.calls = calls

    def get_by_role(
        self, role: str, name: str, exact: bool = False
    ) -> FakeStudentLinkLocator:
        assert role == "link"
        assert exact is False
        return FakeStudentLinkLocator(self.calls, name)


class FakeLoginPage:
    def __init__(self, calls: dict[str, object]) -> None:
        self.url = "https://ic.example/campus/portal"
        self.load_state_calls: list[str | None] = []
        self.frame_locator = FakeLoginFrame(calls)

    async def wait_for_load_state(self, state: str | None = None) -> None:
        self.load_state_calls.append(state)

    def frame(self, name: str | None = None):
        assert name == "main-workspace"
        return self.frame_locator

    def get_by_text(self, text: str, exact: bool = False) -> FakeLoginTextLocator:
        assert exact is False
        return FakeLoginTextLocator(text, visible=False)


def test_infinite_campus_login_uses_5000ms_prefill_wait_and_runs_real_hooks_once() -> None:
    calls: dict[str, object] = {"exists": 0}
    page = FakeLoginPage(calls)
    first_name = "Avery"

    async def fake_universal_login_flow(
        page_obj,  # noqa: ARG001
        login_url: str,
        sid: str,
        pw: str,
        username_selector: str,
        password_selector: str,
        **kwargs: object,
    ) -> None:
        del page_obj
        calls["login_url"] = login_url
        calls["username"] = sid
        calls["password"] = pw
        calls["username_selector"] = username_selector
        calls["password_selector"] = password_selector
        calls["pre_fill_wait"] = int(kwargs["pre_fill_wait"])  # type: ignore[index]
        page.url = "https://ic.example/campus/portal/nav-wrapper"

    async def fake_exists(locator: object, timeout: int = 1000) -> bool:
        # Playwright's expect requires a real Locator; model only this browser-I/O seam.
        assert isinstance(locator, FakeLoginTextLocator)
        assert locator.text == "Incorrect Username and/or Password"
        assert timeout == 1000
        calls["exists"] = int(calls["exists"]) + 1
        calls["invalid_marker"] = locator.text
        return locator.visible

    portal = InfiniteCampus(
        page,
        "student",
        "password",
        "https://ic.example/campus/portal",
    )
    real_validate_login = portal.validate_login
    real_after_login = portal.after_login

    async def wrapped_validate_login() -> None:
        calls["validate_login"] = int(calls.get("validate_login", 0)) + 1
        await real_validate_login()

    async def wrapped_after_login(received_first_name: str | None) -> None:
        calls["after_login"] = int(calls.get("after_login", 0)) + 1
        calls["after_login_first_name"] = received_first_name
        await real_after_login(received_first_name)

    portal.validate_login = wrapped_validate_login  # type: ignore[method-assign]
    portal.after_login = wrapped_after_login  # type: ignore[method-assign]

    def _run() -> None:
        asyncio.run(
            PortalEngine.login.__wrapped__(portal, first_name)  # type: ignore[attr-defined]
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(portal_utils, "universal_login_flow", fake_universal_login_flow)
    monkeypatch.setattr(infinite_campus_module, "exists", fake_exists)
    try:
        _run()
    finally:
        monkeypatch.undo()

    assert calls["username"] == "student"
    assert calls["password"] == "password"
    assert calls["pre_fill_wait"] == 5000
    assert calls["username_selector"] == "#username"
    assert calls["password_selector"] == "#password"
    assert calls["exists"] == 1
    assert calls["invalid_marker"] == "Incorrect Username and/or Password"
    assert calls["validate_login"] == 1
    assert calls["after_login"] == 1
    assert calls["after_login_first_name"] == first_name
    assert calls["selection_clicks"] == 1
    assert calls["selection_name"] == first_name
    assert calls["selection_timeout"] == 2000
    assert page.load_state_calls == ["networkidle"]
