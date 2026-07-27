from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import PortalEngine


_REGISTRY: dict[str, type[PortalEngine]] = {}
managed_portals: dict[str, list[str]] = {}
_INFRASTRUCTURE_MODULES = {"base", "registry", "utils"}


def register_portal_class(cls: type[PortalEngine]) -> None:
    from .base import PortalEngine

    key = cls.portal_key.strip().lower()
    patterns = tuple(
        dict.fromkeys(
            pattern.strip() for pattern in cls.url_patterns if pattern.strip()
        )
    )
    if not key:
        raise ValueError(f"{cls.__name__} must declare a nonempty portal_key")
    if not patterns:
        raise ValueError(f"{cls.__name__} must declare at least one URL pattern")
    if cls.login is PortalEngine.login and cls.login_config is None:
        raise ValueError(f"{cls.__name__} must configure or override login()")
    if cls.fetch_grades is PortalEngine.fetch_grades and cls.grade_table_config is None:
        raise ValueError(f"{cls.__name__} must configure or override fetch_grades()")
    existing = _REGISTRY.get(key)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"Duplicate portal key '{key}' declared by {existing.__name__} and {cls.__name__}"
        )

    cls.portal_key = key
    cls.url_patterns = patterns
    _REGISTRY[key] = cls
    managed_portals[key] = list(patterns)


def discover_portals(package_name: str, package_paths: Iterable[str]) -> None:
    modules = sorted(
        module.name
        for module in pkgutil.iter_modules(package_paths)
        if module.name not in _INFRASTRUCTURE_MODULES
        and not module.name.startswith("_")
    )
    for module_name in modules:
        _ = importlib.import_module(f"{package_name}.{module_name}")


def get_portal(key: object) -> type[PortalEngine]:
    if not key or not isinstance(key, str):
        raise ValueError(f"Invalid or missing portal key: {key!r}")
    try:
        return _REGISTRY[key.strip().lower()]
    except KeyError:
        raise ValueError(f"No portal engine registered for '{key}'") from None


def get_portal_key_from_url(url: str) -> str | None:
    if not url:
        return None
    normalized_url = url.casefold()
    matches = (
        (len(pattern), key)
        for key, patterns in managed_portals.items()
        for pattern in patterns
        if pattern.casefold() in normalized_url
    )
    try:
        _, key = max(matches, key=lambda match: (match[0], match[1]))
    except ValueError:
        return None
    return key
