from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import pyodbc


@dataclass(frozen=True)
class CrmLoginResult:
    authenticated: bool
    role: int | None = None
    franchise_id: int | None = None
    display_name: str | None = None


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_to_map(cursor: Any, row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return {str(key).lower(): val for key, val in row.items()}

    if hasattr(row, "_asdict"):
        return {str(key).lower(): val for key, val in row._asdict().items()}  # type: ignore[attr-defined]

    description = getattr(cursor, "description", None) or []
    if not description:
        return {}

    row_map: dict[str, Any] = {}
    for idx, column in enumerate(description):
        if not column:
            continue
        key = column[0]
        if key is None:
            continue
        row_map[str(key).lower()] = row[idx] if idx < len(row) else None

    return row_map


def _extract_candidate(row: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    for candidate in candidates:
        if candidate in row:
            if row[candidate] is not None:
                return row[candidate]
        title_case = candidate.upper()
        if title_case in row:
            if row[title_case] is not None:
                return row[title_case]
        camel = candidate[:1].lower() + candidate[1:]
        if camel in row:
            if row[camel] is not None:
                return row[camel]
    return None


_DRIVER_NAME = "ODBC Driver 17 for SQL Server"
_DRIVER_PATH = "/home/runner/odbc/lib/libmsodbcsql-17.10.so.6.1"


def _resolve_driver() -> str:
    if _DRIVER_NAME in pyodbc.drivers():
        return "{" + _DRIVER_NAME + "}"
    return _DRIVER_PATH


def _connect_string() -> str:
    database = os.getenv("CRMSrvDb", "").strip()
    if not database:
        database = os.getenv("CRMSrvDbQA", "").strip()
    if not database:
        raise ValueError("CRMSrvDb or CRMSrvDbQA must be set.")

    trust_server_certificate = (
        os.getenv("CRM_TRUST_SERVER_CERTIFICATE", "").strip().lower()
        in {"1", "true", "yes"}
    )
    return (
        f"DRIVER={_resolve_driver()};"
        f"SERVER={os.getenv('CRMSrvAddress', '')};"
        f"DATABASE={database};"
        f"UID={os.getenv('CRMSrvUs', '')};"
        f"PWD={os.getenv('CRMSrvPs', '')};"
        "Encrypt=yes;"
        f"TrustServerCertificate={'yes' if trust_server_certificate else 'no'};"
        "ApplicationIntent=ReadOnly;"
    )


def _extract_login_fields(cursor: Any, row: Any) -> tuple[int | None, int | None, str | None]:
    row_map = _row_to_map(cursor, row)
    role = _coerce_int(_extract_candidate(row_map, ("role",)))
    franchise_id = _coerce_int(
        _extract_candidate(row_map, ("franchiseid", "franchise_id", "id"))
    )
    display_name = _extract_candidate(row_map, ("name",))
    display_name = str(display_name) if display_name is not None else None
    return role, franchise_id, display_name


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


def crm_login(username: str, password: str) -> CrmLoginResult:
    cursor: Any = None
    connection: Any = None
    role: int | None = None
    franchise_id: int | None = None
    display_name: str | None = None
    try:
        connection = pyodbc.connect(_connect_string())
        cursor = connection.cursor()
        cursor.execute(
            "EXEC dbo.usp_login ?, ?",
            username,
            password,
        )

        while True:
            row = cursor.fetchone()
            while row is not None:
                row_role, row_franchise_id, row_display_name = _extract_login_fields(
                    cursor, row
                )
                if row_role is not None:
                    role = row_role
                if row_franchise_id is not None:
                    franchise_id = row_franchise_id
                if row_display_name:
                    display_name = row_display_name
                result = _result_from_fields(role, franchise_id, display_name)
                if result.authenticated:
                    return result
                row = cursor.fetchone()

            has_next = cursor.nextset()
            if not has_next:
                break

        return CrmLoginResult(authenticated=False)
    except ValueError:
        return CrmLoginResult(authenticated=False)
    except getattr(pyodbc, "Error", Exception):
        return CrmLoginResult(authenticated=False)
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
