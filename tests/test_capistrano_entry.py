import asyncio
import base64
import zlib
from urllib.parse import urlencode

import pytest

from scraper.agenda import is_agenda_eligible, resolve_agenda_slots
from scraper.portals import get_portal_key_from_url
from scraper.portals import canvas
from scraper.runner import student_from_context


MICROSOFT = 'https://login.microsoftonline.com/66f6efc9-9d17-492a-b91f-0d1f2f76092c/saml2'
XML = f'''<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
    Destination="{MICROSOFT}" AssertionConsumerServiceURL="https://capousd.instructure.com/login/saml">
    <saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">http://capousd.instructure.com/saml2</saml:Issuer>
    </samlp:AuthnRequest>'''


def saved_entry(xml=XML):
    compressor = zlib.compressobj(wbits=-15)
    payload = compressor.compress(xml.encode()) + compressor.flush()
    return MICROSOFT + '?' + urlencode({'SAMLRequest': base64.b64encode(payload).decode(), 'Signature': 'expired'})


def test_saved_capistrano_microsoft_entry_is_runnable_for_grades_and_agendas():
    url = saved_entry()
    assert get_portal_key_from_url(url) == 'canvas'
    student = student_from_context({'crmstudentid': 1, 'portal': 'unknown', 'portal1': url,
                                    'p1username': 'student', 'p1password': 'password'})
    assert student['portal'] == 'canvas'
    assert resolve_agenda_slots(student)[0].portal == 'canvas'
    assert is_agenda_eligible(student)


@pytest.mark.parametrize('url', [
    MICROSOFT,
    MICROSOFT + '?SAMLRequest=invalid',
    saved_entry().replace('login.microsoftonline.com', 'login.microsoftonline.com.evil.test'),
    saved_entry().replace('https://login.microsoftonline', 'http://login.microsoftonline'),
    saved_entry().replace('login.microsoftonline.com/', 'user@login.microsoftonline.com/'),
    saved_entry().replace('login.microsoftonline.com/', 'login.microsoftonline.com:444/'),
    saved_entry().replace('/66f6efc9-9d17-492a-b91f-0d1f2f76092c/', '/other-tenant/'),
    saved_entry(XML.replace('capousd.instructure.com', 'other.instructure.com')),
    saved_entry(XML.replace('https://capousd.instructure.com/login/saml', 'https://evil.test/login/saml')),
    saved_entry(XML.replace('http://capousd.instructure.com/saml2', 'http://other.instructure.com/saml2')),
    saved_entry(XML.replace('AuthnRequest', 'Response')),
    saved_entry(' ' * 100000 + XML),
    saved_entry() + '&SAMLRequest=second',
    saved_entry('<!DOCTYPE x [<!ENTITY a "test">]>' + XML),
])
def test_ambiguous_or_untrusted_microsoft_links_remain_unsupported(url):
    assert get_portal_key_from_url(url) is None
    with pytest.raises(canvas.CanvasTrustError):
        asyncio.run(canvas.CanvasEngine(None, 'student', 'password', url).login())


def test_saved_saml_link_starts_fresh_from_canvas_without_replaying_signature():
    class StopAtNavigation(Exception):
        pass

    class Page:
        main_frame = None

        def on(self, *args):
            pass

        def remove_listener(self, *args):
            pass

        async def goto(self, url, **kwargs):
            self.goto_url = url
            raise StopAtNavigation()

    page = Page()
    engine = canvas.CanvasEngine(page, 'student', 'password', saved_entry())
    with pytest.raises(Exception):
        asyncio.run(engine.login())
    assert page.goto_url == 'https://capousd.instructure.com'


def test_alternate_xml_encoding_cannot_bypass_doctype_rejection():
    xml = ('<!DOCTYPE x [<!ENTITY a "test">]>' + XML).encode('utf-16')
    compressor = zlib.compressobj(wbits=-15)
    payload = compressor.compress(xml) + compressor.flush()
    url = MICROSOFT + '?' + urlencode({'SAMLRequest': base64.b64encode(payload).decode()})
    assert get_portal_key_from_url(url) is None
