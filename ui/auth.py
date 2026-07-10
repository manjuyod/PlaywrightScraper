from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ui import api_client


@dataclass(frozen=True)
class CrmLoginResult:
    authenticated: bool
    role: int | None = None
    franchise_id: int | None = None
    display_name: str | None = None


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_candidate(row: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    for candidate in candidates:
        if row.get(candidate) is not None:
            return row[candidate]
    return None


def _result_from_fields(
    role: int | None,
    franchise_id: int | None,
    display_name: str | None = None,
) -> CrmLoginResult:
    if role in (2, 3) and franchise_id and franchise_id > 0:
        return CrmLoginResult(
            authenticated=True,
            role=role,
            franchise_id=franchise_id,
            display_name=display_name,
        )
    return CrmLoginResult(authenticated=False)


def _login_result_from_api(payload: dict[str, Any]) -> CrmLoginResult:
    if not payload.get("authenticated"):
        return CrmLoginResult(authenticated=False)
    role = _coerce_int(_extract_candidate(payload, ("role",)))
    franchise_id = _coerce_int(
        _extract_candidate(payload, ("franchise_id", "franchiseid", "id"))
    )
    display_name = _extract_candidate(payload, ("display_name", "name"))
    return _result_from_fields(
        role,
        franchise_id,
        str(display_name) if display_name is not None else None,
    )


def crm_login(username: str, password: str) -> CrmLoginResult:
    try:
        payload = api_client.login(username, password)
    except api_client.ApiClientError:
        return CrmLoginResult(authenticated=False)
    if not isinstance(payload, dict):
        return CrmLoginResult(authenticated=False)
    return _login_result_from_api(payload)
