from __future__ import annotations

import asyncio
from urllib.parse import urlencode

import pytest

from scraper.portals import get_portal_key_from_url
from scraper.portals.canvas import CanvasEngine, CanvasTrustError


PARAMETERS = {
    "channel": "clever",
    "client_id": "4c63c1cf623dce82caac",
    "confirmed": "true",
    "redirect_uri": "https://clever.com/in/auth_callback",
    "response_type": "code",
    "state": "expired-session-state",
    "district_id": "51e5622080da6210550053a4",
}
ENTRY = "https://clever.com/oauth/authorize"


@pytest.mark.parametrize("state", [None, "expired-session-state", "another-session"])
def test_recognizes_ccsd_clever_portal_without_relying_on_saved_state(state):
    parameters = dict(reversed(list(PARAMETERS.items())))
    if state is None:
        parameters.pop("state")
    else:
        parameters["state"] = state
    assert get_portal_key_from_url(f"{ENTRY}?{urlencode(parameters)}") == "canvas"


@pytest.mark.parametrize("entry", [
    "http://clever.com/oauth/authorize",
    "https://clever.com.attacker.example/oauth/authorize",
    "https://user@clever.com/oauth/authorize",
    "https://clever.com:444/oauth/authorize",
    "https://clever.com/oauth/google/login",
])
def test_other_origins_and_entry_routes_are_not_ccsd_canvas(entry):
    url = f"{entry}?{urlencode(PARAMETERS)}"
    assert get_portal_key_from_url(url) is None
    with pytest.raises(CanvasTrustError):
        asyncio.run(CanvasEngine(None, "student", "password", url).login())


@pytest.mark.parametrize("key, value, detected_portal", [
    ("district_id", "another-district", "clever"),
    ("client_id", "another-app", "clever"),
    ("redirect_uri", "https://attacker.example/callback", None),
    ("response_type", "token", None),
    ("channel", "google", None),
    ("district_id", None, None),
    ("district_id", ["51e5622080da6210550053a4", "another-district"], None),
    ("redirect_uri", ["https://clever.com/in/auth_callback", "https://attacker.example"], None),
])
def test_other_valid_clever_entries_are_generic_and_ambiguous_queries_are_unsupported(
    key, value, detected_portal
):
    parameters = dict(PARAMETERS)
    if value is None:
        parameters.pop(key)
    else:
        parameters[key] = value
    url = f"{ENTRY}?{urlencode(parameters, doseq=True)}"
    assert get_portal_key_from_url(url) == detected_portal
    with pytest.raises(CanvasTrustError):
        asyncio.run(CanvasEngine(None, "student", "password", url).login())
