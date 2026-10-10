import asyncio

import pytest

from scraper.portals import canvas
from scraper.portals.base import LoginError


ENTRY = "https://capousd.instructure.com"
MICROSOFT = "https://login.microsoftonline.com/66f6efc9-9d17-492a-b91f-0d1f2f76092c/saml2"


def test_capistrano_accepts_direct_district_microsoft_login_and_exact_return():
    route = canvas._CapistranoCanvasAuthRoute(ENTRY)
    route.observe(MICROSOFT)
    route.require_microsoft_credentials()
    route.mark_password_submitted()
    route.observe(ENTRY + "/login/saml")
    assert route.verified_canvas_origin(ENTRY + "/") == ENTRY
    with pytest.raises(canvas.CanvasTrustError):
        route.require_microsoft_credentials()


@pytest.mark.parametrize("url", [
    "https://capousd.instructure.com.evil.test/",
    "http://capousd.instructure.com/",
    "https://capousd.instructure.com:444/",
    "https://student@capousd.instructure.com/",
    "https://husd.instructure.com/",
])
def test_capistrano_rejects_untrusted_entry(url):
    with pytest.raises(canvas.CanvasTrustError):
        canvas._CapistranoCanvasAuthRoute(url)


@pytest.mark.parametrize("url", [
    "https://login.microsoftonline.com/other-tenant/saml2",
    "https://login.microsoftonline.com.evil.test/66f6efc9-9d17-492a-b91f-0d1f2f76092c/saml2",
    "https://sso.canvaslms.com/login/saml",
    "https://other.instructure.com/",
])
def test_capistrano_rejects_unobserved_auth_routes(url):
    route = canvas._CapistranoCanvasAuthRoute(ENTRY)
    with pytest.raises(canvas.CanvasTrustError):
        route.observe(url)


def test_capistrano_requires_submission_before_return_and_rejects_other_tenants():
    route = canvas._CapistranoCanvasAuthRoute(ENTRY)
    route.observe(MICROSOFT)
    with pytest.raises(canvas.CanvasTrustError):
        route.verified_canvas_origin(ENTRY)
    route.mark_password_submitted()
    with pytest.raises(canvas.CanvasTrustError):
        route.observe("https://husd.instructure.com/")
    with pytest.raises(canvas.CanvasTrustError):
        route.mark_password_submitted()


def test_capistrano_accepts_observed_post_login_pass_through_only_after_canvas_return():
    transit = 'https://sso.canvaslms.com/delegated_auth_pass_through'
    route = canvas._CapistranoCanvasAuthRoute(ENTRY)
    with pytest.raises(canvas.CanvasTrustError):
        route.observe(transit)
    route.observe(MICROSOFT)
    route.mark_password_submitted()
    with pytest.raises(canvas.CanvasTrustError):
        route.observe(transit)
    route.observe(ENTRY + '/login/saml')
    route.observe(transit)
    with pytest.raises(canvas.CanvasTrustError):
        route.verified_canvas_origin(transit)
    assert route.verified_canvas_origin(ENTRY + '/') == ENTRY


def test_capistrano_rejects_unobserved_transit_after_return():
    route = canvas._CapistranoCanvasAuthRoute(ENTRY)
    route.observe(MICROSOFT)
    route.mark_password_submitted()
    route.observe(ENTRY)
    with pytest.raises(canvas.CanvasTrustError):
        route.observe('https://sso.canvaslms.com/login/saml')


def test_route_rejection_does_not_claim_the_password_was_rejected():
    assert not isinstance(canvas.CanvasTrustError(), LoginError)


def test_capistrano_login_dispatch_uses_existing_guarded_microsoft_submission():
    class Page:
        url = ENTRY

    class Engine(canvas.CanvasEngine):
        def _install_canvas_route_guard(self, route):
            self.route = route

        def _remove_canvas_route_guard(self):
            pass

        async def _prepare_login(self, route):
            route.observe(MICROSOFT)

        async def _submit_microsoft_credentials_once(self, route):
            route.mark_password_submitted()
            route.observe(ENTRY)

        async def _wait_for_login_result(self, **kwargs):
            return True

        async def post_login(self):
            pass

        async def _is_canvas_logged_in(self):
            return True

    engine = Engine(Page(), "student", "password", ENTRY)
    asyncio.run(engine.login())
    assert isinstance(engine.route, canvas._CapistranoCanvasAuthRoute)
    assert engine._canvas_origin == ENTRY


@pytest.mark.parametrize('action', [ENTRY + '/login/canvas', 'https://evil.test/login/canvas'])
def test_explicit_native_login_submits_once_to_only_the_configured_canvas_form(action):
    class Form:
        def __init__(self):
            self.fills = []
            self.submits = 0

        def locator(self, selector):
            self.selector = selector
            return self

        async def wait_for(self, **kwargs):
            pass

        async def evaluate(self, script):
            return {'action': action, 'method': 'post'}

        async def fill(self, value):
            self.fills.append(value)

        async def press(self, key):
            self.submits += 1

    class Page:
        url = 'about:blank'

        def __init__(self):
            self.form = Form()

        async def goto(self, url, **kwargs):
            self.url = url

        def locator(self, selector):
            return self.form

    class Engine(canvas.CanvasEngine):
        def _install_canvas_route_guard(self, route):
            self.route = route

        def _remove_canvas_route_guard(self):
            pass

        async def _prepare_login(self, route):
            pytest.fail('native Canvas credentials must not be sent to Microsoft')

        async def _wait_for_login_result(self, **kwargs):
            return True

        async def post_login(self):
            pass

        async def _is_canvas_logged_in(self):
            return True

    page = Page()
    engine = Engine(page, 'student', 'password', ENTRY + '/login/canvas')
    if action.startswith(ENTRY):
        asyncio.run(engine.login())
        assert page.form.fills == ['student', 'password']
        assert page.form.submits == 1
        assert engine._canvas_origin == ENTRY
    else:
        with pytest.raises(canvas.CanvasTrustError):
            asyncio.run(engine.login())
        assert page.form.fills == []
        assert page.form.submits == 0


def test_trust_failure_preserves_password_validity_at_both_result_boundaries(monkeypatch):
    from scraper import agenda, runner

    posts = []

    class Client:
        def post_result(self, **kwargs):
            posts.append(kwargs)
            return {'applied': True}

    async def rejected_route(*args, **kwargs):
        raise canvas.CanvasTrustError()

    student = {'db_id': 1, 'portal': 'canvas', 'login_url': ENTRY,
               'id': 'student', 'password': 'password'}
    monkeypatch.setattr(runner, 'scrape_one', rejected_route)
    asyncio.run(runner._process_grade_students(
        Client(), {'job_id': 'job', 'lease_token': 'lease'}, object(), [student],
        runner._new_progress(1), asyncio.Event(),
    ))
    assert posts[0]['outcome'] == {
        'kind': 'failure', 'channel': 'grade', 'code': 'scrape_failed', 'passwordgood': None,
    }

    monkeypatch.setattr(agenda, '_collect_slot', rejected_route)
    result, _ = asyncio.run(agenda.fetch_agenda(object(), student))
    assert result.failures == {'agenda1': 'scrape_failed'}
