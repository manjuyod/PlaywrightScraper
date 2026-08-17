from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeout

from scraper.portals import canvas
from scraper.portals.canvas import CanvasEngine


class RouteRequest:
    def __init__(self, url: str, frame: object, *, navigation: bool = True) -> None:
        self.url = url
        self.frame = frame
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


class RoutePage:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url
        self.main_frame = self
        self._listeners: dict[str, list[object]] = {}
        self.goto_calls = 0
        self.fill_calls = 0
        self.press_calls = 0

    def on(self, event: str, callback: object) -> None:
        self._listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event: str, callback: object) -> None:
        listeners = self._listeners.get(event, [])
        if callback in listeners:
            listeners.remove(callback)

    def request_navigation(
        self,
        url: str,
        *,
        frame: object | None = None,
        navigation: bool = True,
    ) -> None:
        request = RouteRequest(
            url,
            self.main_frame if frame is None else frame,
            navigation=navigation,
        )
        for callback in list(self._listeners.get("request", [])):
            callback(request)

    def navigate(self, url: str) -> None:
        self.request_navigation(url)
        self.url = url
        for callback in list(self._listeners.get("framenavigated", [])):
            callback(self)

    async def goto(self, url: str, **_kwargs: object) -> None:
        self.goto_calls += 1
        if self.goto_calls < 3:
            raise PlaywrightTimeout("pre-submit navigation timed out")
        self.navigate(url)

    async def wait_for_timeout(self, _timeout: int) -> None:
        pass

    async def fill(self, *_args: object, **_kwargs: object) -> None:
        self.fill_calls += 1

    async def press(self, *_args: object, **_kwargs: object) -> None:
        self.press_calls += 1


@pytest.mark.parametrize(
    "entry_url",
    [
        "http://husd.instructure.com/login",
        "https://student@husd.instructure.com/login",
        "https://husd.instructure.com:444/login",
        "https://husd.instructure.com.evil.example/login",
        "https://not-husd.instructure.com/login",
    ],
)
def test_canvas_route_rejects_untrusted_configured_entry(entry_url: str) -> None:
    """Would fail if credentials could start from anything except the approved HUSD tenant."""
    with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
        canvas._CanvasAuthRoute(entry_url)


@pytest.mark.parametrize(
    ("prefix", "untrusted_url"),
    [
        ([], "https://other.instructure.com/login"),
        ([], "https://af4a8e81-f8b1-4434-88f6-0c8c0a166c9e.iad.login.instructure.com.evil.example/login"),
        ([], "https://unknown-sso.example/login"),
        ([], "https://sso.canvaslms.com.evil.example/login"),
        (["https://sso.canvaslms.com/login"], "https://login.microsoftonline.com.evil.example/common/oauth2"),
        (["https://sso.canvaslms.com/login"], "http://login.microsoftonline.com/common/oauth2"),
        (["https://sso.canvaslms.com/login"], "https://user@login.microsoftonline.com/common/oauth2"),
        (["https://sso.canvaslms.com/login"], "https://login.microsoftonline.com:444/common/oauth2"),
        (
            [
                "https://sso.canvaslms.com/login",
                "https://login.microsoftonline.com/common/oauth2",
            ],
            "https://login.live.com.evil.example/continue",
        ),
    ],
)
def test_canvas_route_rejects_untrusted_authentication_hops(
    prefix: list[str], untrusted_url: str
) -> None:
    """Would fail if a lookalike or unapproved transit could receive credentials."""
    route = canvas._CanvasAuthRoute("https://husd.instructure.com/login/canvas")
    for url in prefix:
        route.observe(url)

    with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
        route.observe(untrusted_url)


def test_canvas_route_accepts_only_the_approved_sso_sequence() -> None:
    """Would fail if the HUSD Canvas-to-Microsoft route or approved Canvas return is blocked."""
    route = canvas._CanvasAuthRoute("https://husd.instructure.com/login/canvas")

    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe("https://login.microsoftonline.com/common/oauth2/authorize")
    route.require_microsoft_credentials()
    route.mark_password_submitted()
    route.observe("https://iad.login.instructure.com/login/saml")

    assert route.verified_canvas_origin("https://iad.login.instructure.com/dashboard") == (
        "https://iad.login.instructure.com"
    )


def test_canvas_route_accepts_the_exact_live_redirect_chain() -> None:
    """Would fail if the approved pre-auth redirects cannot reach Microsoft safely."""
    route = canvas._CanvasAuthRoute("https://husd.instructure.com/login/canvas")

    for url in (
        "https://husd.instructure.com/login/canvas",
        "https://iad.login.instructure.com/",
        "https://af4a8e81-f8b1-4434-88f6-0c8c0a166c9e.iad.login.instructure.com/sso",
        "https://sso.canvaslms.com/login/saml",
        "https://husd.instructure.com/login/saml",
        "https://login.microsoftonline.com/common/oauth2/authorize",
    ):
        route.observe(url)

    route.require_microsoft_credentials()
    route.mark_password_submitted()
    route.observe("https://login.live.com/continue")
    route.observe("https://iad.login.instructure.com/dashboard")
    assert route.verified_canvas_origin("https://iad.login.instructure.com/dashboard") == (
        "https://iad.login.instructure.com"
    )


def test_canvas_route_accepts_exact_preauth_broker_after_microsoft_submission() -> None:
    """Would fail when the live post-submit broker blocks an authenticated return."""
    route = canvas._CanvasAuthRoute("https://husd.instructure.com/login/canvas")

    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe("https://login.microsoftonline.com/common/oauth2/authorize")
    route.mark_password_submitted()
    route.observe(
        "https://af4a8e81-f8b1-4434-88f6-0c8c0a166c9e.iad.login.instructure.com/sso"
    )
    route.observe(
        "https://af4a8e81-f8b1-4434-88f6-0c8c0a166c9e.iad.login.instructure.com/sso"
    )
    route.observe("https://husd.instructure.com/")

    assert route.verified_canvas_origin("https://husd.instructure.com/") == (
        "https://husd.instructure.com"
    )


def test_canvas_postauth_broker_can_finish_on_exact_iad_return() -> None:
    """Would fail if an approved return remains trapped in the broker phase."""
    route = canvas._CanvasAuthRoute("https://husd.instructure.com/login/canvas")

    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe("https://login.microsoftonline.com/common/oauth2/authorize")
    route.mark_password_submitted()
    route.observe(
        "https://af4a8e81-f8b1-4434-88f6-0c8c0a166c9e.iad.login.instructure.com/sso"
    )
    route.observe("https://iad.login.instructure.com/dashboard")

    assert route.verified_canvas_origin("https://iad.login.instructure.com/dashboard") == (
        "https://iad.login.instructure.com"
    )


@pytest.mark.parametrize(
    "untrusted_url",
    [
        "https://af4a8e81-f8b1-4434-88f6-0c8c0a166c9e.iad.login.instructure.com.evil.example/sso",
        "https://login.microsoftonline.com/common/oauth2/authorize",
    ],
)
def test_canvas_postauth_broker_rejects_untrusted_or_backward_hops(
    untrusted_url: str,
) -> None:
    """Would fail if the broker phase could broaden or return to credential hosts."""
    route = canvas._CanvasAuthRoute("https://husd.instructure.com/login/canvas")
    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe("https://login.microsoftonline.com/common/oauth2/authorize")
    route.mark_password_submitted()
    route.observe(
        "https://af4a8e81-f8b1-4434-88f6-0c8c0a166c9e.iad.login.instructure.com/sso"
    )

    with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
        route.observe(untrusted_url)


def test_canvas_route_rejects_live_continuation_before_password_submission() -> None:
    """Would fail if a continuation host can receive username/password credentials."""
    route = canvas._CanvasAuthRoute("https://husd.instructure.com/login/canvas")
    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe("https://login.microsoftonline.com/common/oauth2/authorize")

    with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
        route.observe("https://login.live.com/continue")


def test_canvas_route_allows_live_continuation_only_after_password_submission() -> None:
    """Would fail if the approved post-submit continuation cannot be distinguished."""
    route = canvas._CanvasAuthRoute("https://husd.instructure.com/login/canvas")
    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe("https://login.microsoftonline.com/common/oauth2/authorize")
    route.mark_password_submitted()

    route.observe("https://login.live.com/continue")


def test_canvas_route_rejects_unrelated_canvas_return_after_microsoft() -> None:
    """Would fail if another Canvas tenant can become the agenda API origin."""
    route = canvas._CanvasAuthRoute("https://husd.instructure.com/login/canvas")
    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe("https://login.microsoftonline.com/common/oauth2/authorize")
    route.mark_password_submitted()

    with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
        route.observe("https://other.instructure.com/dashboard")


def test_canvas_route_final_verification_rejects_unrelated_canvas_tenant() -> None:
    """Would fail if final origin verification broadens a trusted Canvas return."""
    route = canvas._CanvasAuthRoute("https://husd.instructure.com/login/canvas")
    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe("https://login.microsoftonline.com/common/oauth2/authorize")
    route.mark_password_submitted()
    route.observe("https://iad.login.instructure.com/dashboard")

    with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
        route.verified_canvas_origin("https://other.instructure.com/dashboard")


def test_canvas_exact_live_redirect_cannot_receive_password_before_submission() -> None:
    """Would fail if a username-stage live redirect can receive the password."""

    class Page(RoutePage):
        def __init__(self) -> None:
            super().__init__("https://login.microsoftonline.com/common/oauth2/authorize")
            self.password_fills = 0

        async def fill(self, selector: str, _value: str, **_kwargs: object) -> None:
            if selector == "input#username":
                raise PlaywrightTimeout("modern Microsoft form")
            if selector == "input#i0118":
                self.password_fills += 1

        async def click(self, selector: str) -> None:
            if selector == "#idSIButton9":
                self.navigate("https://login.live.com/continue")

    page = Page()
    engine = CanvasEngine(
        page, "student-id", "password", "https://husd.instructure.com/login/canvas"
    )
    route = canvas._CanvasAuthRoute(engine.login_url)
    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe(page.url)
    engine._install_canvas_route_guard(route)
    try:
        with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
            asyncio.run(engine._submit_microsoft_credentials_once(route))
    finally:
        engine._remove_canvas_route_guard()

    assert page.password_fills == 0


def test_canvas_route_guard_observes_main_frame_redirect_requests() -> None:
    """Would fail if server redirects are invisible until their final document commits."""
    page = RoutePage()
    engine = CanvasEngine(
        page, "student-id", "password", "https://husd.instructure.com/login/canvas"
    )
    route = canvas._CanvasAuthRoute(engine.login_url)
    engine._install_canvas_route_guard(route)
    try:
        for url in (
            "https://husd.instructure.com/login/canvas",
            "https://iad.login.instructure.com/",
            "https://af4a8e81-f8b1-4434-88f6-0c8c0a166c9e.iad.login.instructure.com/sso",
            "https://sso.canvaslms.com/login/saml",
            "https://husd.instructure.com/login/saml",
            "https://login.microsoftonline.com/common/oauth2/authorize",
        ):
            page.request_navigation(url)
        engine._raise_canvas_route_error()
        route.require_microsoft_credentials()
    finally:
        engine._remove_canvas_route_guard()

    page.request_navigation("https://unknown-idp.example/after-cleanup")
    engine._raise_canvas_route_error()


def test_canvas_route_guard_ignores_subframe_navigation_requests() -> None:
    """Would fail if an unrelated iframe can poison the main-frame trust route."""
    page = RoutePage()
    engine = CanvasEngine(
        page, "student-id", "password", "https://husd.instructure.com/login/canvas"
    )
    route = canvas._CanvasAuthRoute(engine.login_url)
    engine._install_canvas_route_guard(route)
    try:
        page.request_navigation(
            "https://unknown-idp.example/frame",
            frame=object(),
        )
        engine._raise_canvas_route_error()
    finally:
        engine._remove_canvas_route_guard()


def test_canvas_route_guard_ignores_same_frame_non_navigation_requests() -> None:
    """Would fail if a resource request can poison an authenticated main-frame route."""
    page = RoutePage()
    engine = CanvasEngine(
        page, "student-id", "password", "https://husd.instructure.com/login/canvas"
    )
    route = canvas._CanvasAuthRoute(engine.login_url)
    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe("https://login.microsoftonline.com/common/oauth2/authorize")
    engine._install_canvas_route_guard(route)
    try:
        page.request_navigation(
            "https://unknown-idp.example/resource",
            navigation=False,
        )
        engine._raise_canvas_route_error()
        route.require_microsoft_credentials()
    finally:
        engine._remove_canvas_route_guard()


def test_canvas_login_cleanup_preserves_the_primary_exception() -> None:
    """Would fail if listener cleanup replaces the actual login failure."""

    class Engine(CanvasEngine):
        async def _prepare_login(self, _route: object) -> None:
            raise PlaywrightTimeout("primary login timeout")

    engine = Engine(
        RoutePage(), "student-id", "password", "https://husd.instructure.com/login/canvas"
    )

    with pytest.raises(PlaywrightTimeout, match="primary login timeout"):
        asyncio.run(engine.login())


def test_canvas_login_cleanup_failure_does_not_mask_the_primary_exception() -> None:
    """Would fail if a listener-removal error replaces the actual login failure."""

    class Page(RoutePage):
        def remove_listener(self, _event: str, _callback: object) -> None:
            raise RuntimeError("listener cleanup failed")

    class Engine(CanvasEngine):
        async def _prepare_login(self, _route: object) -> None:
            raise PlaywrightTimeout("primary login timeout")

    engine = Engine(
        Page(), "student-id", "password", "https://husd.instructure.com/login/canvas"
    )

    with pytest.raises(PlaywrightTimeout, match="primary login timeout"):
        asyncio.run(engine.login())


def test_pre_submit_preparation_retries_without_filling_or_pressing() -> None:
    """Would fail if retrying navigation could repeat credential entry or submission."""

    class Engine(CanvasEngine):
        async def _click_sso_entry_if_needed(self) -> None:
            self.page.navigate("https://sso.canvaslms.com/login/saml")
            self.page.navigate("https://login.microsoftonline.com/common/oauth2/authorize")

    page = RoutePage()
    engine = Engine(page, "student-id", "password", "https://husd.instructure.com/login/canvas")
    route = canvas._CanvasAuthRoute(engine.login_url)
    engine._install_canvas_route_guard(route)
    try:
        asyncio.run(engine._prepare_login(route))
    finally:
        engine._remove_canvas_route_guard()

    assert page.goto_calls == 3
    assert page.fill_calls == 0
    assert page.press_calls == 0
    route.require_microsoft_credentials()


def test_post_submit_timeout_submits_once_and_is_not_retried() -> None:
    """Would fail if a timeout after password submission restarts the complete login."""

    class Engine(CanvasEngine):
        def __init__(self, *args: object) -> None:
            super().__init__(*args)
            self.prepare_calls = 0
            self.submit_calls = 0

        async def _prepare_login(self, route: object) -> None:
            self.prepare_calls += 1
            route.observe("https://sso.canvaslms.com/login/saml")
            self.page.navigate("https://login.microsoftonline.com/common/oauth2/authorize")

        async def _submit_microsoft_credentials_once(self, route: object) -> None:
            route.require_microsoft_credentials()
            route.mark_password_submitted()
            self.submit_calls += 1

        async def _wait_for_login_result(self, timeout_ms: int = 12000) -> bool:
            raise PlaywrightTimeout("post-submit readiness timed out")

    page = RoutePage()
    engine = Engine(page, "student-id", "password", "https://husd.instructure.com/login/canvas")

    with pytest.raises(PlaywrightTimeout, match="post-submit readiness timed out"):
        asyncio.run(engine.login())

    assert engine.prepare_calls == 1
    assert engine.submit_calls == 1


def test_staged_microsoft_login_checks_redirect_before_filling_password() -> None:
    """Would fail if an untrusted username redirect can receive the password."""

    class CountLocator:
        async def count(self) -> int:
            return 0

    class Page(RoutePage):
        def __init__(self) -> None:
            super().__init__("https://login.microsoftonline.com/common/oauth2/authorize")
            self.password_fills = 0

        async def fill(self, selector: str, _value: str, **_kwargs: object) -> None:
            if selector == "input#username":
                raise PlaywrightTimeout("modern Microsoft form")
            if selector == "input#i0118":
                self.password_fills += 1

        async def click(self, selector: str) -> None:
            if selector == "#idSIButton9":
                self.navigate("https://unknown-idp.example/continue")

        async def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
            pass

        def get_by_text(self, _text: str) -> CountLocator:
            return CountLocator()

    page = Page()
    engine = CanvasEngine(
        page, "student-id", "password", "https://husd.instructure.com/login/canvas"
    )
    route = canvas._CanvasAuthRoute(engine.login_url)
    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe(page.url)
    engine._install_canvas_route_guard(route)
    try:
        with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
            asyncio.run(engine._submit_microsoft_credentials_once(route))
    finally:
        engine._remove_canvas_route_guard()

    assert page.password_fills == 0


def test_microsoft_fallback_checks_timed_out_username_hop_before_refill() -> None:
    """Would fail if a timed-out legacy probe can refill username after an untrusted hop."""

    class Page(RoutePage):
        def __init__(self) -> None:
            super().__init__("https://login.microsoftonline.com/common/oauth2/authorize")
            self.fallback_username_fills = 0

        async def fill(self, selector: str, _value: str, **_kwargs: object) -> None:
            if selector == "input#username":
                self.navigate("https://unknown-idp.example/continue")
                raise PlaywrightTimeout("legacy username probe timed out")
            if selector == "input#i0116":
                self.fallback_username_fills += 1

    page = Page()
    engine = CanvasEngine(
        page, "student-id", "password", "https://husd.instructure.com/login/canvas"
    )
    route = canvas._CanvasAuthRoute(engine.login_url)
    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe("https://login.microsoftonline.com/common/oauth2/authorize")
    engine._install_canvas_route_guard(route)
    try:
        with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
            asyncio.run(engine._submit_microsoft_credentials_once(route))
    finally:
        engine._remove_canvas_route_guard()

    assert page.fallback_username_fills == 0


def test_microsoft_post_submit_load_timeout_propagates() -> None:
    """Would fail if a timeout after password submission is mistaken for successful submit."""

    class CountLocator:
        async def count(self) -> int:
            return 0

    class Page(RoutePage):
        def __init__(self) -> None:
            super().__init__("https://login.microsoftonline.com/common/oauth2/authorize")
            self.submit_clicks = 0

        async def fill(self, selector: str, _value: str, **_kwargs: object) -> None:
            if selector == "input#username":
                raise PlaywrightTimeout("modern Microsoft form")

        async def click(self, selector: str) -> None:
            if selector != "#idSIButton9":
                return
            self.submit_clicks += 1
            if self.submit_clicks == 2:
                self.navigate("https://iad.login.instructure.com/dashboard")

        async def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
            raise PlaywrightTimeout("post-submit load timed out")

        def get_by_text(self, _text: str) -> CountLocator:
            return CountLocator()

    page = Page()
    engine = CanvasEngine(
        page, "student-id", "password", "https://husd.instructure.com/login/canvas"
    )
    route = canvas._CanvasAuthRoute(engine.login_url)
    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe(page.url)
    engine._install_canvas_route_guard(route)
    try:
        with pytest.raises(PlaywrightTimeout, match="post-submit load timed out"):
            asyncio.run(engine._submit_microsoft_credentials_once(route))
    finally:
        engine._remove_canvas_route_guard()

    assert page.submit_clicks == 2


@pytest.mark.parametrize(
    "failing_action",
    ["modern_username_submit", "modern_password_submit", "legacy_password_submit"],
)
def test_microsoft_navigation_timeout_preserves_pending_trust_error(
    failing_action: str,
) -> None:
    """Would fail if an action timeout masks the untrusted navigation it committed."""

    class CountLocator:
        async def count(self) -> int:
            return 0

    class PasswordLocator:
        def __init__(self, page: Page) -> None:
            self.page = page

        async def press(self, _key: str) -> None:
            self.page.navigate("https://unknown-idp.example/continue")
            raise PlaywrightTimeout("legacy password submit timed out")

    class Page(RoutePage):
        def __init__(self) -> None:
            super().__init__("https://login.microsoftonline.com/common/oauth2/authorize")
            self.microsoft_clicks = 0

        async def fill(self, selector: str, _value: str, **_kwargs: object) -> None:
            if selector == "input#username" and failing_action.startswith("modern"):
                raise PlaywrightTimeout("modern Microsoft form")

        async def click(self, selector: str) -> None:
            if selector != "#idSIButton9":
                return
            self.microsoft_clicks += 1
            should_fail = (
                failing_action == "modern_username_submit" and self.microsoft_clicks == 1
            ) or (
                failing_action == "modern_password_submit" and self.microsoft_clicks == 2
            )
            if should_fail:
                self.navigate("https://unknown-idp.example/continue")
                raise PlaywrightTimeout("modern submit timed out")

        def locator(self, _selector: str) -> PasswordLocator:
            return PasswordLocator(self)

        def get_by_text(self, _text: str) -> CountLocator:
            return CountLocator()

    page = Page()
    engine = CanvasEngine(
        page, "student-id", "password", "https://husd.instructure.com/login/canvas"
    )
    route = canvas._CanvasAuthRoute(engine.login_url)
    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe(page.url)
    engine._install_canvas_route_guard(route)
    try:
        with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
            asyncio.run(engine._submit_microsoft_credentials_once(route))
    finally:
        engine._remove_canvas_route_guard()


def test_login_result_poll_preserves_trust_error_over_rejection_marker() -> None:
    """Would fail if an untrusted redirect is sanitized as an ordinary credential rejection."""

    class Page(RoutePage):
        async def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
            self.navigate("https://unknown-idp.example/rejected")

    class Engine(CanvasEngine):
        async def _is_canvas_logged_in(self) -> bool:
            return False

        async def _has_canvas_login_error(self) -> bool:
            return True

    page = Page("https://login.microsoftonline.com/common/oauth2/authorize")
    engine = Engine(page, "student-id", "password", "https://husd.instructure.com/login/canvas")
    route = canvas._CanvasAuthRoute(engine.login_url)
    route.observe("https://sso.canvaslms.com/login/saml")
    route.observe(page.url)
    engine._install_canvas_route_guard(route)
    try:
        with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
            asyncio.run(engine._wait_for_login_result(timeout_ms=100))
    finally:
        engine._remove_canvas_route_guard()


@pytest.mark.parametrize(
    ("url", "markers", "expected"),
    [
        ("https://husd.instructure.com/dashboard", {"[aria-label='Global Navigation']"}, True),
        ("https://husd.instructure.com/dashboard", set(), False),
        ("https://arbitrary.example/dashboard", {"[aria-label='Global Navigation']"}, False),
        ("https://husd.instructure.com.evil.example/dashboard", {"#menu"}, False),
    ],
)
def test_canvas_authenticated_state_requires_approved_host_and_positive_marker(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    markers: set[str],
    expected: bool,
) -> None:
    """Would fail if URL shape alone or untrusted DOM content were accepted as authenticated."""

    class Locator:
        def __init__(self, selector: str) -> None:
            self.selector = selector

    class Page:
        def __init__(self) -> None:
            self.url = url

        def locator(self, selector: str) -> Locator:
            return Locator(selector)

    async def no_login_field(_locator: object, timeout: int = 1000) -> bool:
        return False

    class Engine(CanvasEngine):
        async def _exists(self, selector: str, *, timeout: int = 3000) -> bool:
            return selector in markers

    monkeypatch.setattr(canvas, "exists", no_login_field)
    engine = Engine(Page(), "student-id", "password", "https://husd.instructure.com/login/canvas")

    assert asyncio.run(engine._is_canvas_logged_in()) is expected


def test_successful_login_freezes_the_verified_canvas_return_origin() -> None:
    """Would fail if agenda requests could derive their origin from the configured login URL."""

    class Engine(CanvasEngine):
        async def _prepare_login(self, route: object) -> None:
            route.observe("https://sso.canvaslms.com/login/saml")
            self.page.navigate("https://login.microsoftonline.com/common/oauth2/authorize")

        async def _submit_microsoft_credentials_once(self, route: object) -> None:
            route.require_microsoft_credentials()
            route.mark_password_submitted()

        async def _wait_for_login_result(self, timeout_ms: int = 12000) -> bool:
            self.page.navigate("https://iad.login.instructure.com/dashboard")
            return True

        async def _is_canvas_logged_in(self) -> bool:
            return True

        async def post_login(self) -> None:
            pass

    engine = Engine(
        RoutePage(), "student-id", "password", "https://husd.instructure.com/login/canvas"
    )

    asyncio.run(engine.login())

    assert engine._canvas_origin == "https://iad.login.instructure.com"


def test_untrusted_navigation_during_post_login_precedes_cleanup_error() -> None:
    """Would fail if post-login cleanup can mask a pending Canvas trust violation."""

    class Engine(CanvasEngine):
        async def _prepare_login(self, route: object) -> None:
            route.observe("https://sso.canvaslms.com/login/saml")
            self.page.navigate("https://login.microsoftonline.com/common/oauth2/authorize")

        async def _submit_microsoft_credentials_once(self, route: object) -> None:
            route.require_microsoft_credentials()

        async def _wait_for_login_result(self, timeout_ms: int = 12000) -> bool:
            self.page.navigate("https://iad.login.instructure.com/dashboard")
            return True

        async def post_login(self) -> None:
            self.page.navigate("https://attacker.example/after-login")
            raise RuntimeError("cleanup failed")

    engine = Engine(
        RoutePage(), "student-id", "password", "https://husd.instructure.com/login/canvas"
    )

    with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
        asyncio.run(engine.login())


@pytest.mark.parametrize("probe_timeout", [False, True])
def test_untrusted_navigation_during_final_auth_probe_preserves_trust_error(
    probe_timeout: bool,
) -> None:
    """Would fail if final verification turns a pending trust error into rejection/timeout."""

    class Engine(CanvasEngine):
        async def _prepare_login(self, route: object) -> None:
            route.observe("https://sso.canvaslms.com/login/saml")
            self.page.navigate("https://login.microsoftonline.com/common/oauth2/authorize")

        async def _submit_microsoft_credentials_once(self, route: object) -> None:
            route.require_microsoft_credentials()

        async def _wait_for_login_result(self, timeout_ms: int = 12000) -> bool:
            self.page.navigate("https://iad.login.instructure.com/dashboard")
            return True

        async def post_login(self) -> None:
            pass

        async def _is_canvas_logged_in(self) -> bool:
            self.page.navigate("https://attacker.example/final-probe")
            if probe_timeout:
                raise PlaywrightTimeout("final auth probe timed out")
            return False

    engine = Engine(
        RoutePage(), "student-id", "password", "https://husd.instructure.com/login/canvas"
    )

    with pytest.raises(canvas.CanvasTrustError, match="^canvas_auth_route_untrusted$"):
        asyncio.run(engine.login())
