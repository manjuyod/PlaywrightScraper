from __future__ import annotations

import asyncio

import pytest

from scraper.portals import infinite_campus as infinite_campus_module
from scraper.portals import utils as portal_utils
from scraper.portals.infinite_campus import InfiniteCampus


class FakeLoginTextLocator:
    def __init__(self, visible: bool = False) -> None:
        self.visible = visible


class FakeLoginPage:
    def __init__(self) -> None:
        self.url = "https://ic.example/campus/portal"
        self.load_state_calls: list[str | None] = []

    async def wait_for_load_state(self, state: str | None = None) -> None:
        self.load_state_calls.append(state)

    def frame(self, name: str | None = None):
        del name
        return None

    def get_by_text(self, _text: str, exact: bool = False) -> FakeLoginTextLocator:
        del _text, exact
        return FakeLoginTextLocator(False)


def test_infinite_campus_login_uses_5000ms_prefill_wait_and_runs_real_hooks_once() -> None:
    page = FakeLoginPage()
    calls: dict[str, object] = {"exists": 0}

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

    async def fake_exists(*_args: object, **_kwargs: object) -> bool:
        calls["exists"] = int(calls["exists"]) + 1
        return False

    def _run() -> None:
        asyncio.run(
            InfiniteCampus(
                page,
                "student",
                "password",
                "https://ic.example/campus/portal",
            ).login()
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
    assert page.load_state_calls == ["networkidle"]
