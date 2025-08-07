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
    except KeyError:  # nicer error than raw KeyError
        raise ValueError(f"No portal engine registered for '{key}'") from None

# Import engines so they register.
from . import infinite_campus_student_ccsd, infinite_campus_parent_ccsd, infinite_campus_parent_gilbert, infinite_campus_parent_alac, parentvue_husd, studentvue_husd