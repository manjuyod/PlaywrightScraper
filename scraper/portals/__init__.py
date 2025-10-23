# scraper/portals/__init__.py
from typing import Callable, Dict, Type
from .base import PortalEngine

_REGISTRY: Dict[str, Type[PortalEngine]] = {}

def register_portal(key: str) -> Callable[[Type[PortalEngine]], Type[PortalEngine]]:
    """Class decorator to auto-register a portal engine."""
    def decorator(cls: Type[PortalEngine]) -> Type[PortalEngine]:
        _REGISTRY[key.lower()] = cls
        return cls
    return decorator

def get_portal(key: str) -> Type[PortalEngine]:
    try:
        return _REGISTRY[key.lower()]
    except (KeyError, AttributeError):  # nicer error than raw KeyError
        raise ValueError(f"No portal engine registered for '{key}'") from None


import importlib
# add portals that should be imported here as strings, along with the general substrings usually contained in their urls
managed_portals = {
    "classlink": ["classlink"],
    "gps": ["gpsportal"],
    "microsoft_benjamin_franklin": ["benjaminfranklincs"],
    "parentvue": ["parentvue", "Login_Parent", "Login_Student"],
    "powerschool": ["powerschool"],
    "bghs_blackbaud": ["bishopgorman"],
    "aeries": ["aeries", "LoginParent.aspx"],
    "infinite_campus": ["campus/portal", "infinitecampus"],
    "student_connection": ["studentconnect", "k12.ca.us"],
    "schoology": ["schoology"]
   }
# Import engines so they register.
for portal in managed_portals.keys():
    importlib.import_module(f".{portal}", __name__)
