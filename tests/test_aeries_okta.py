from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeout, async_playwright
from tenacity import wait_none

from scraper.portals.aeries import Aeries
from scraper.portals.base import LoginError


PORTAL = "https://my.iusd.org/LoginParent.aspx"
OKTA = "https://iusd.okta.com"
USERNAME = "fixture-student@iusd.org"
PASSWORD = "fixture-password"


async def browser_scenario(outcome="success", *, entry=None, invoke_dispatch=False):
    """Real browser forms and cross-origin redirects; every request stays local."""
    submissions = []
    requests = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        page.set_default_timeout(1000)
        await page.expose_function("recordSubmission", lambda kind, value: submissions.append((kind, value)))

        async def serve(route):
            url = route.request.url
            requests.append(url)
            if url.endswith("/password"):
                next_step = {
                    "success": "location.href='https://www.google.com/a/iusd.org/acs'",
                    "rejected": "document.body.insertAdjacentHTML('beforeend','<div role=alert>Unable to sign in</div>')",
                    "locked": "document.body.insertAdjacentHTML('beforeend','<div role=alert>Unable to sign in. Your account is locked.</div>')",
                    "mfa": "document.body.innerHTML='<h2>Verify with Okta Verify</h2><input type=password name=credentials.passcode><button>Verify</button>'",
                    "recovery": "document.body.innerHTML='<h2>Reset your password</h2><input type=password name=credentials.passcode><button>Verify</button>'",
                    "untrusted": "location.href='https://iusd.okta.com.attacker.test/password'",
                    "stalled": "void 0",
                }[outcome]
                html = f'''<h2>Verify with your password</h2>
                    <input name="credentials.passcode" type="password" autocomplete="current-password">
                    <button onclick="(async()=>{{await recordSubmission('password',document.querySelector('input').value);{next_step}}})()">Verify</button>
                    <button onclick="recordSubmission('recovery','clicked')">Forgot password?</button>'''
            elif url.startswith("https://www.google.com/"):
                html = "<script>location.href='https://accounts.google.com/v3/signin/continue'</script>"
            elif url.startswith("https://accounts.google.com/"):
                html = "<script>location.href='https://accounts.youtube.com/accounts/SetSID'</script>"
            elif url.startswith("https://accounts.youtube.com/"):
                html = "<script>location.href='https://my.iusd.org/Dashboard.aspx'</script>"
            elif url.endswith("/Dashboard.aspx"):
                html = "<div>Loading</div><script>setTimeout(()=>document.body.innerHTML='<div id=NavMainGrades>Grades</div>',150)</script>"
            else:
                html = '''<h2>Sign In</h2><input id="identifier" autocomplete="username">
                    <input type="checkbox" name="rememberMe">
                    <button onclick="(async()=>{await recordSubmission('username',document.querySelector('#identifier').value);await recordSubmission('remember',document.querySelector('[name=rememberMe]').checked);location.href='/password'})()">Next</button>'''
            await route.fulfill(body=html, content_type="text/html")

        await page.route("**/*", serve)
        try:
            await page.goto(entry or OKTA + "/app/google/test/sso/saml")
            error = None
            try:
                if invoke_dispatch:
                    await Aeries(page, USERNAME, PASSWORD, PORTAL).alternate_sso_login()
                else:
                    from scraper.portals.aeries_okta import complete_iusd_okta_login
                    await complete_iusd_okta_login(
                        page, username=USERNAME, password=PASSWORD,
                        login_url=PORTAL, timeout_ms=2500,
                    )
            except Exception as exc:
                error = exc
            final_url = page.url
            authenticated = await page.locator("#NavMainGrades").is_visible()
            # A temporary navigation guard must leave pre-existing routes intact.
            await page.goto("https://after-diagnostic.example/")
            return submissions, requests, final_url, authenticated, error
        finally:
            await browser.close()


def test_aeries_dispatch_completes_the_observed_okta_and_google_redirect_chain():
    submissions, requests, url, authenticated, error = asyncio.run(browser_scenario(invoke_dispatch=True))
    assert error is None
    assert submissions == [("username", USERNAME), ("remember", False), ("password", PASSWORD)]
    assert url == "https://my.iusd.org/Dashboard.aspx"
    assert authenticated
    assert any(url.startswith("https://accounts.youtube.com/") for url in requests)


@pytest.mark.parametrize("outcome,expected", [
    ("rejected", "bad_login"),
    ("locked", "iusd_okta_interaction_required"),
    ("mfa", "iusd_okta_interaction_required"),
    ("recovery", "iusd_okta_interaction_required"),
    ("untrusted", "iusd_okta_untrusted_origin"),
    ("stalled", "iusd_okta_timeout"),
])
def test_okta_stops_without_repeated_submissions_or_account_changes(outcome, expected):
    submissions, requests, _, authenticated, error = asyncio.run(browser_scenario(outcome))
    assert error is not None
    assert not authenticated
    if expected == "bad_login":
        assert isinstance(error, LoginError)
    else:
        assert getattr(error, "code", None) == expected
    assert [kind for kind, _ in submissions].count("password") == 1
    assert not any(kind == "recovery" for kind, _ in submissions)
    assert not any(url.startswith("https://iusd.okta.com.attacker.test/") for url in requests)
    assert USERNAME not in str(error) and PASSWORD not in str(error)


@pytest.mark.parametrize("entry", [
    "http://iusd.okta.com/app/google/test/sso/saml",
    "https://iusd.okta.com.attacker.test/app/google/test/sso/saml",
    "https://another-district.okta.com/app/google/test/sso/saml",
    "https://iusd.okta.com:444/app/google/test/sso/saml",
])
def test_untrusted_okta_pages_never_receive_credentials(entry):
    submissions, _, _, _, error = asyncio.run(browser_scenario(entry=entry))
    assert getattr(error, "code", None) == "iusd_okta_untrusted_origin"
    assert submissions == []


def test_existing_non_irvine_alternate_login_remains_unchanged(monkeypatch):
    legacy = AsyncMock()
    monkeypatch.setattr("scraper.portals.aeries.universal_login_flow", legacy)
    page = type("Page", (), {"url":"https://other-district.okta.com/login"})()
    engine = Aeries(page, USERNAME, PASSWORD, "https://other.aeries.net/LoginParent.aspx")
    asyncio.run(engine.alternate_sso_login())
    legacy.assert_awaited_once_with(page, page.url, USERNAME, PASSWORD, "#input28", "#input62")


def test_post_sso_timeout_does_not_repeat_okta_submission(monkeypatch):
    from scraper.portals.aeries_okta import IusdOktaError

    helper = AsyncMock()
    monkeypatch.setattr("scraper.portals.aeries.complete_iusd_okta_login", helper)

    async def handoff(*args, alt_sso_callback, **kwargs):
        await alt_sso_callback()

    monkeypatch.setattr("scraper.portals.utils.universal_login_flow", handoff)
    monkeypatch.setattr(Aeries, "validate_login", AsyncMock(side_effect=PlaywrightTimeout("fixture timeout")))
    engine = Aeries(object(), USERNAME, PASSWORD, PORTAL)

    async def scenario():
        with pytest.raises(IusdOktaError, match="iusd_okta_already_attempted"):
            await Aeries.login.retry_with(wait=wait_none())(engine)

    asyncio.run(scenario())
    helper.assert_awaited_once()


@pytest.mark.parametrize("url,expected", [
    (PORTAL, True),
    ("https://my.iusd.org:443/Dashboard.aspx", True),
    ("https://my.iusd.org.attacker.test/LoginParent.aspx", False),
    ("https://other.aeries.net/LoginParent.aspx", False),
    ("http://my.iusd.org/LoginParent.aspx", False),
    ("https://my.iusd.org:444/LoginParent.aspx", False),
    ("https://user@my.iusd.org/LoginParent.aspx", False),
])
def test_irvine_dispatch_requires_the_exact_configured_https_origin(url, expected):
    from scraper.portals.aeries_okta import is_iusd_portal
    assert is_iusd_portal(url) is expected
