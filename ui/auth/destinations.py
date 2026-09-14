"""Local post-authentication destinations, independent of request input/hosts."""

from __future__ import annotations

import re


_DESTINATION = re.compile(
    r"/franchise/(?P<franchise>[1-9][0-9]*)(?:/student/(?P<student>[1-9][0-9]*))?"
)


def validate_return_path(value: object) -> str:
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("invalid return path")
    if value == "/":
        return value
    match = _DESTINATION.fullmatch(value)
    if (
        match is None
        or int(match["franchise"]) > 2_147_483_647
        or (match["student"] is not None and int(match["student"]) > 9_223_372_036_854_775_807)
    ):
        raise ValueError("invalid return path")
    return value


def resolve_landing_path(
    return_path: str,
    *,
    franchise_id: int,
    crm_role: str,
    permissions: tuple[str, ...],
) -> str:
    path = validate_return_path(return_path)
    if path == "/":
        return f"/franchise/{franchise_id}" if crm_role == "3" else "/"
    match = _DESTINATION.fullmatch(path)
    if match is None or int(match["franchise"]) != franchise_id or "students.read" not in permissions:
        raise ValueError("unauthorized return path")
    return path
