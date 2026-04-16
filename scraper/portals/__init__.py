from typing import Callable, Dict, Type
import importlib
from .base import PortalEngine

# ---------------------------
# Public API (define FIRST so engines can import safely)
# ---------------------------

class LoginError(Exception):
    """Raised when a portal login fails in a recognized way."""
    pass


_REGISTRY: Dict[str, Type[PortalEngine]] = {}


def register_portal(key: str) -> Callable[[Type[PortalEngine]], Type[PortalEngine]]:
    """
    Class decorator to auto-register a portal engine under a simple key.
    Example in an engine file:
        @register_portal("canvas")
        class CanvasEngine(PortalEngine): ...
    """
    def decorator(cls: Type[PortalEngine]) -> Type[PortalEngine]:
        _REGISTRY[key.lower()] = cls
        return cls
    return decorator


def get_portal(key: str) -> Type[PortalEngine]:
    """Return the engine class previously registered under `key`."""
    if not key or not isinstance(key, str):
        raise ValueError(f"Invalid or missing portal key: {key!r}")
    try:
        return _REGISTRY[key.lower()]
    except KeyError:
        raise ValueError(f"No portal engine registered for '{key}'") from None


# ---------------------------
# Dynamic module loading
# ---------------------------

# Map of portal keys -> list of URL substrings commonly found in their login URLs.
# (Useful for auto-detect; runner can use detect_portal_from_url when DB portal is NULL.)
managed_portals: Dict[str, list[str]] = {
    "classlink": ["classlink"],
    "gps": ["gpsportal"],
    "microsoft_benjamin_franklin": ["benjaminfranklincs"],
    "parentvue": ["parentvue", "Login_Parent", "Login_Student"],
    "powerschool": ["powerschool"],
    "blackbaud": ["myschoolapp, blackbaud"],
    "aeries": ["aeries", "LoginParent.aspx, Dashboard.aspx"],
    "infinite_campus": ["campus/portal", "infinitecampus"],
    "student_connection": ["studentconnect"],
    "schoology": ["schoology"],
    "howsschoolgoing": ["howsschoolgoing"],
    "canvas": ["instructure.com", "canvas"],
    "k12": ["login.k12"],
    "google_classroom": ["classroom.google", "accounts.google"],
    "schooltool": ["schooltool"],
    "asuprep": ["global.asuprep"],
   }
# Import engines so they register. NOTE: The managed portal should match the .py file name that manages it
for portal in managed_portals.keys():
    importlib.import_module(f".{portal}", __name__)


# ---------------------------
# Optional helper: detect portal key from a login URL
# ---------------------------



__all__ = [
    "LoginError",
    "register_portal",
    "get_portal",
    "managed_portals",
    # "get_portal_key_from_url",
]
