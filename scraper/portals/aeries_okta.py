"""Irvine's observed Aeries → Google → Okta login, without account changes."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError, Page, Route

from .base import LoginError


_TRANSIT_HOSTS = frozenset({
    "my.iusd.org", "iusd.okta.com", "www.google.com",
    "accounts.google.com", "accounts.youtube.com",
})


class IusdOktaError(RuntimeError):
    """A controlled stop that does not trigger the generic login timeout retry."""

    def __init__(self, code: str) -> None:
        self.code = code
        # Never include page text, credentials, or SAML/OAuth URLs in errors.
        super().__init__(code)


def _https_host(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.hostname
                and parsed.port in (None, 443)
                and parsed.username is None and parsed.password is None):
            return parsed.hostname
    except ValueError:
        pass
    return None


def is_iusd_portal(login_url: str) -> bool:
    return _https_host(login_url) == "my.iusd.org"


_SCREEN = r"""() => {
    const visible = el => !!el && el.getClientRects().length > 0
        && getComputedStyle(el).visibility !== 'hidden';
    const text = selector => Array.from(document.querySelectorAll(selector))
        .filter(visible).map(el => el.innerText || '').join(' ').toLowerCase();
    const heading = text('h1, h2, .okta-form-title');
    const errors = text('[role="alert"], .infobox-error, .o-form-error-container');
    return {
        url: location.href,
        authenticated: Array.from(document.querySelectorAll(
            '#NavMainGrades, #StudentNameDropDown, #divClass')).some(visible),
        locked: /account.*locked|too many.*attempts/.test(heading + ' ' + errors),
        rejected: /unable to sign in|invalid credentials|incorrect password|authentication failed/.test(errors),
        interaction: /okta verify|security method|security question|authenticator|verification code|enter.*code|reset.*password|change.*password|set up|enroll|account.*locked/.test(heading),
        username: visible(document.querySelector('input#identifier')) && /sign in/.test(heading),
        password: visible(document.querySelector('input[name="credentials.passcode"][type="password"]'))
            && /verify with your password/.test(heading)
    };
}"""


async def complete_iusd_okta_login(
    page: Page, *, username: str, password: str, login_url: str,
    timeout_ms: int = 60000,
) -> None:
    """Submit each observed Okta step once; allow only the observed return chain.

    The shared Google login hands off here when federation replaces Google's
    password screen. MFA, recovery and unfamiliar pages are left untouched.
    """
    if not is_iusd_portal(login_url):
        raise IusdOktaError("iusd_okta_untrusted_origin")

    blocked = False
    username_submitted = False
    password_submitted = False

    async def guard_navigation(route: Route) -> None:
        nonlocal blocked
        request = route.request
        if (request.is_navigation_request() and request.frame == page.main_frame
                and _https_host(request.url) not in _TRANSIT_HOSTS):
            blocked = True
            await route.abort()
        else:
            await route.fallback()

    await page.route("**/*", guard_navigation)
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            while True:
                if blocked or _https_host(page.url) not in _TRANSIT_HOSTS:
                    raise IusdOktaError("iusd_okta_untrusted_origin")
                try:
                    state = await page.evaluate(_SCREEN)
                except PlaywrightError as exc:
                    # Automatic SAML redirects can replace the execution context.
                    if "Execution context was destroyed" not in str(exc):
                        raise
                    await asyncio.sleep(0.1)
                    continue
                host = _https_host(state["url"])
                if blocked or host not in _TRANSIT_HOSTS:
                    raise IusdOktaError("iusd_okta_untrusted_origin")
                path = urlsplit(state["url"]).path.lower()
                if host == "my.iusd.org":
                    if path.endswith("/dashboard.aspx") and state["authenticated"]:
                        return
                    if path.endswith("/changepassword.aspx"):
                        raise IusdOktaError("iusd_okta_interaction_required")
                elif host == "iusd.okta.com":
                    if state["locked"]:
                        raise IusdOktaError("iusd_okta_interaction_required")
                    if state["rejected"]:
                        raise LoginError("Irvine Okta rejected the login")
                    if state["interaction"] or re.search(r"/(recovery|reset|enroll)(/|$)", path):
                        raise IusdOktaError("iusd_okta_interaction_required")
                    if state["username"] and not username_submitted and not password_submitted:
                        username_submitted = True
                        await page.locator("input#identifier").fill(username)
                        await page.get_by_role("button", name="Next", exact=True).click()
                    elif state["password"] and not password_submitted:
                        password_submitted = True
                        await page.locator('input[name="credentials.passcode"][type="password"]').fill(password)
                        await page.get_by_role("button", name="Verify", exact=True).click()
                await asyncio.sleep(0.1)
    except TimeoutError:
        raise IusdOktaError("iusd_okta_timeout") from None
    except PlaywrightError:
        code = "iusd_okta_untrusted_origin" if blocked else "iusd_okta_page_failed"
        raise IusdOktaError(code) from None
    finally:
        await page.unroute("**/*", guard_navigation)
