from __future__ import annotations

from .base import (
    GradeMap,
    GradeTableConfig,
    LoginError,
    PortalEngine,
    UniversalLoginConfig,
)
from .registry import (
    discover_portals,
    get_portal,
    get_portal_key_from_url,
    managed_portals,
)

discover_portals(__name__, __path__)

__all__ = [
    "GradeMap",
    "GradeTableConfig",
    "LoginError",
    "PortalEngine",
    "UniversalLoginConfig",
    "get_portal",
    "get_portal_key_from_url",
    "managed_portals",
]
