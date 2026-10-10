"""Recognize the observed CCSD Clever entry without replaying OAuth sessions."""
from __future__ import annotations

import base64
import binascii
import xml.etree.ElementTree as ET
import zlib
from urllib.parse import parse_qs, urlsplit


_CCSD_CLEVER_PARAMETERS = {
    "channel": "clever",
    "client_id": "4c63c1cf623dce82caac",
    "redirect_uri": "https://clever.com/in/auth_callback",
    "response_type": "code",
    "district_id": "51e5622080da6210550053a4",
}

CAPISTRANO_CANVAS_ORIGIN = "https://capousd.instructure.com"
CAPISTRANO_MICROSOFT_PATH = "/66f6efc9-9d17-492a-b91f-0d1f2f76092c/saml2"
_CAPISTRANO_MICROSOFT_URL = "https://login.microsoftonline.com" + CAPISTRANO_MICROSOFT_PATH


def is_capistrano_saml_entry(url: str) -> bool:
    """Recognize saved Canvas requests only to restart from the fixed tenant.

    This is a routing hint, not SAML signature validation. Never replay the
    request or navigate to a destination obtained from its untrusted XML.
    """
    try:
        if len(url) > 32768:
            return False
        parsed = urlsplit(url)
        if (parsed.scheme.lower() != "https"
                or parsed.hostname != "login.microsoftonline.com"
                or parsed.port not in (None, 443)
                or parsed.username is not None or parsed.password is not None
                or parsed.path.lower() != CAPISTRANO_MICROSOFT_PATH):
            return False
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=30)
        requests = query.get("SAMLRequest", [])
        if len(requests) != 1:
            return False
        compressed = base64.b64decode(requests[0], validate=True)
        decoder = zlib.decompressobj(-15)
        xml = decoder.decompress(compressed, 16385)
        if (len(xml) > 16384 or not decoder.eof or decoder.unused_data or b"\x00" in xml
                or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper()):
            return False
        root = ET.fromstring(xml)
        issuers = root.findall("{urn:oasis:names:tc:SAML:2.0:assertion}Issuer")
        return (
            root.tag == "{urn:oasis:names:tc:SAML:2.0:protocol}AuthnRequest"
            and root.get("Destination") == _CAPISTRANO_MICROSOFT_URL
            and root.get("AssertionConsumerServiceURL") == CAPISTRANO_CANVAS_ORIGIN + "/login/saml"
            and len(issuers) == 1
            and issuers[0].text == "http://capousd.instructure.com/saml2"
        )
    except (TypeError, ValueError, binascii.Error, zlib.error, ET.ParseError):
        return False


def is_ccsd_clever_entry(url: str) -> bool:
    """Accept only the district/app entry; state is deliberately not consumed."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname != "clever.com"
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/oauth/authorize"
        ):
            return False
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=30)
    except (TypeError, ValueError):
        return False
    return all(query.get(key) == [value] for key, value in _CCSD_CLEVER_PARAMETERS.items())
