"""Recognize the observed CCSD Clever entry without replaying OAuth sessions."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit


_CCSD_CLEVER_PARAMETERS = {
    "channel": "clever",
    "client_id": "4c63c1cf623dce82caac",
    "redirect_uri": "https://clever.com/in/auth_callback",
    "response_type": "code",
    "district_id": "51e5622080da6210550053a4",
}


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
