from __future__ import annotations

from urllib.parse import parse_qs, urlsplit


_CLEVER_ORIGIN = "https://clever.com"
_CLEVER_CALLBACK = "https://clever.com/in/auth_callback"


def https_origin(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return None
    return f"https://{parsed.hostname.casefold()}"


def is_clever_entry(url: str) -> bool:
    if https_origin(url) != _CLEVER_ORIGIN:
        return False
    parsed = urlsplit(url)
    if parsed.path != "/oauth/authorize":
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    required = {
        "channel": ["clever"],
        "redirect_uri": [_CLEVER_CALLBACK],
        "response_type": ["code"],
    }
    return (
        all(query.get(key) == value for key, value in required.items())
        and len(query.get("client_id", [])) == 1
        and bool(query["client_id"][0])
        and len(query.get("district_id", [])) == 1
        and bool(query["district_id"][0])
    )
