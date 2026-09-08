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

    async def wait_for(self, *, state: str, timeout: int) -> None:
        assert state == "hidden"
        assert timeout == 5_000
        if self.visible:
            raise PlaywrightTimeout("login form remained visible")


class FakeLoginPage:
    def __init__(self, *, username_visible: bool, password_visible: bool) -> None:
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

    async def fake_wait_for_auth_screen(self: GPS) -> None:
        page = self.page
        username = page.locator("input#identification")
        password = page.locator("input#ember535")
        if isinstance(password, TransitioningLoginControl):
            await password.wait_for(state="hidden", timeout=5_000)
        if await username.is_visible() or await password.is_visible():
            raise self.LoginError("portal login rejected")

    monkeypatch.setattr(portal_utils, "universal_login_flow", fake_universal_login_flow)
    monkeypatch.setattr(GPS, "_wait_for_auth_screen", fake_wait_for_auth_screen)
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


class TransitioningLoginControl(FakeLoginControl):
    async def wait_for(self, *, state: str, timeout: int) -> None:
        assert state == "hidden"
        assert timeout == 5_000
        self.visible = False


class TransitioningPasswordPage(FakeLoginPage):
    def __init__(self) -> None:
        super().__init__(username_visible=False, password_visible=True)
        self.username_control = FakeLoginControl(False)
        self.password_control = TransitioningLoginControl(True)

    def locator(self, selector: str) -> FakeLoginControl:
        controls = {
            "input#identification": self.username_control,
            "input#ember535": self.password_control,
        }
        return controls[selector]


def test_login_waits_for_transitioning_password_form_before_pictograph_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if GPS validates during the post-submit form transition."""
    page = TransitioningPasswordPage()
    login_calls, auth_calls = _configure_login_dependencies(monkeypatch)

    asyncio.run(_engine(page).login())

    assert login_calls == ["submit"]
    assert auth_calls == ["pictographs"]


def test_login_does_not_resubmit_credentials_when_pictograph_readiness_times_out(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Would fail if a post-submit GPS timeout re-enters the whole login flow."""
    page = FakeLoginPage(username_visible=False, password_visible=False)
    login_calls, _ = _configure_login_dependencies(monkeypatch)

    async def fail_pictograph_readiness(_self: GPS) -> None:
        raise PlaywrightTimeout("sensitive post-submit detail")

    monkeypatch.setattr(GPS, "do_gps_auth", fail_pictograph_readiness)
    engine = _engine(page)

    with pytest.raises(Exception) as raised:
        asyncio.run(type(engine).login.retry_with(wait=wait_none())(engine))

    assert login_calls == ["submit"]
    assert type(raised.value) is GPS.LoginError
    assert str(raised.value) == "portal login rejected"
    assert "portal.login.auth_challenge_timed_out" in caplog.messages
    for sentinel in (
        "gps-user",
        "gps-password",
        "first",
        "second",
        "third",
        "sensitive post-submit detail",
    ):
        assert sentinel not in caplog.text


class FakePictographLocator:
    def __init__(self, page: FakePictographPage, selector: str) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self) -> FakePictographLocator:
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        assert state in {"visible", "hidden"}
        if state == "hidden":
            assert self.selector == ".pictograph-list"
        assert timeout == 45_000

    async def click(self) -> None:
        self.page.clicked_selectors.append(self.selector)


class FakePictographPage:
    def __init__(self) -> None:
        self.url = "https://gpsportal.example/pictographs"
        self.clicked_selectors: list[str] = []
        self.challenge_index = 0

    def locator(self, selector: str) -> FakePictographLocator:
        return FakePictographLocator(self, selector)

    async def wait_for_load_state(self, state: str) -> None:
        assert state == "networkidle"
        raise PlaywrightTimeout("background requests never became idle")

    async def eval_on_selector_all(self, selector: str, expression: str) -> list[str]:
        assert selector == ".pictograph-list img.tile-icon"
        assert expression == "imgs => imgs.map(img => img.alt)"
        answer = ["first", "second", "third"][self.challenge_index]
        self.challenge_index += 1
        return ["distractor", answer]

    async def wait_for_timeout(self, timeout: int) -> None:
        assert timeout == 1000


def test_pictograph_auth_succeeds_without_global_network_idle() -> None:
    """Would fail if GPS readiness depends on unrelated background traffic."""
    page = FakePictographPage()

    asyncio.run(_engine(page).do_gps_auth())  # type: ignore[arg-type]

    assert page.clicked_selectors == [
        ".pictograph-list img.tile-icon[alt='first']",
        ".pictograph-list img.tile-icon[alt='second']",
        ".pictograph-list img.tile-icon[alt='third']",
    ]


class FakeDelayedPictographLocator(FakePictographLocator):
    async def wait_for(self, *, state: str, timeout: int) -> None:
        if state == "hidden":
            await super().wait_for(state=state, timeout=timeout)
            return
        assert state == "visible"
        assert timeout == 45_000
        if timeout < self.page.challenge_ready_after_ms:
            raise PlaywrightTimeout("challenge was still loading")


class FakeDelayedPictographPage(FakePictographPage):
    def __init__(self, *, challenge_ready_after_ms: int) -> None:
        super().__init__()
        self.challenge_ready_after_ms = challenge_ready_after_ms

    def locator(self, selector: str) -> FakePictographLocator:
        return FakeDelayedPictographLocator(self, selector)


def test_pictograph_auth_allows_a_delayed_challenge() -> None:
    """Would fail if readiness retained the previous 15-second deadline."""
    page = FakeDelayedPictographPage(challenge_ready_after_ms=44_000)

    asyncio.run(_engine(page).do_gps_auth())  # type: ignore[arg-type]

    assert len(page.clicked_selectors) == 3


class FakeAuthControl:
    def __init__(self, page: "FakeAuthPage", selector: str) -> None:
        self.page = page
        self.selector = selector
        self.first = self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        self.page.waits.append((self.selector, state, timeout))

    async def click(self) -> None:
        self.page.challenge += 1


class FakeAuthPage:
    def __init__(self) -> None:
        self.url = "https://gpsportal.example/auth"
        self.challenge = 0
        self.waits: list[tuple[str, str, int]] = []

    def locator(self, selector: str) -> FakeAuthControl:
        return FakeAuthControl(self, selector)

    async def eval_on_selector_all(
        self, _selector: str, _expression: str
    ) -> list[str]:
        return [["first"], ["second"], ["third"]][self.challenge]

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        pass


def test_auth_uses_visible_pictographs_without_network_idle() -> None:
    page = FakeAuthPage()
    engine = GPS(
        page,  # type: ignore[arg-type]
        "gps-user",
        "gps-password",
        "https://gpsportal.example/login",
        auth_images=["first", "second", "third"],
    )

    asyncio.run(engine._wait_for_auth_screen())
    asyncio.run(engine.do_gps_auth())

    assert page.challenge == 3
    assert page.waits == [
        (".pictograph-list", "visible", 45_000),
        (".pictograph-list img.tile-icon", "visible", 45_000),
        (".pictograph-list img.tile-icon", "visible", 45_000),
        (".pictograph-list", "hidden", 45_000),
    ]
