from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gspread
import pytest
from google.oauth2.service_account import Credentials
from scraper.runner import db_conn


ROOT = Path(__file__).resolve().parents[1]
KEY_PATH = ROOT / "sheet_mod_grades.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

LOGIN_MASTER_TITLE = "LoginMaster"

FIELDS = [
    "firstname",
    "lastname",
    "grade",
    "portal1",
    "p1username",
    "p1password",
    "portal2",
    "p2username",
    "p2password",
    "passwordgood",
]


def _norm_space(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _norm_name_key(value: Any) -> str:
    return _norm_space(value).lower()


def _norm_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        return int(str(value).strip() or 0)
    except Exception:
        return 0


def _cell_str(value: Any) -> str:
    return "" if value is None else str(value)


def _extract_sheet_id(url: str) -> str:
    # Preferred: https://docs.google.com/spreadsheets/d/<ID>/edit...
    m = re.search(r"/d/([A-Za-z0-9_-]+)", url or "")
    if m:
        return m.group(1)

    # Fallback: last path segment (only works if the URL ends with the ID)
    candidate = (url or "").rstrip("/").split("/")[-1]
    # Strip query/fragment if present.
    candidate = candidate.split("?", 1)[0].split("#", 1)[0]
    return candidate


def _get_sheet_id_for_franchise(fid: int) -> str:
    with db_conn() as conn:
        row = (
            conn.exec_driver_sql(
                """
                SELECT spreadsheet
                FROM Spreadsheets
                WHERE franchiseid = %s
                  AND COALESCE(spreadsheet, '') <> ''
                ORDER BY id DESC
                LIMIT 1
                """,
                (fid,),
            )
            .mappings()
            .fetchone()
        )
    if not row or not row.get("spreadsheet"):
        raise AssertionError(f"No spreadsheet URL found in Spreadsheets table for FranchiseID={fid}")
    return _extract_sheet_id(str(row["spreadsheet"]))


def _gc_client() -> gspread.Client:
    if not KEY_PATH.exists():
        raise AssertionError(f"Missing service account key file: {KEY_PATH}")

    creds = Credentials.from_service_account_file(str(KEY_PATH), scopes=SCOPES)
    return gspread.authorize(creds)


def _read_login_master(sheet_id: str) -> list[dict[str, str]]:
    gc = _gc_client()
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(LOGIN_MASTER_TITLE)

    values = ws.get_all_values(value_render_option="FORMATTED_VALUE")
    if not values:
        return []

    headers = [h.strip() for h in values[0]]
    header_lut = {h.lower(): h for h in headers if h}
    missing_headers = [h for h in FIELDS if h not in header_lut]
    if missing_headers:
        raise AssertionError(
            f"{LOGIN_MASTER_TITLE} is missing required headers: {missing_headers}. "
            f"Found headers: {headers}"
        )

    out: list[dict[str, str]] = []
    for raw_row in values[1:]:
        padded = list(raw_row) + [""] * max(0, len(headers) - len(raw_row))
        row = dict(zip(headers, padded))

        rec: dict[str, str] = {f: row.get(header_lut[f], "") for f in FIELDS}

        if _norm_space(rec["firstname"]) or _norm_space(rec["lastname"]):
            out.append(rec)
    return out


@dataclass(frozen=True)
class _Mismatch:
    key: tuple[int, str, str]
    field: str
    sheet_value: str
    db_value: str


def _fmt_key(key: tuple[int, str, str]) -> str:
    _, first, last = key
    return f"{last}, {first}".strip(", ").strip()


@pytest.mark.integration
def test_franchise_19_loginmaster_matches_db_exact_cells() -> None:
    fid = int(os.getenv("TEST_FRANCHISE_ID", "19"))
    sheet_id = _get_sheet_id_for_franchise(fid)

    sheet_rows = _read_login_master(sheet_id)
    assert sheet_rows, f"Sheet {sheet_id} LoginMaster has 0 student rows"

    # Build key -> row (detect duplicates)
    sheet_by_key: dict[tuple[int, str, str], dict[str, str]] = {}
    sheet_dupes: dict[tuple[int, str, str], int] = {}
    for r in sheet_rows:
        key = (fid, _norm_name_key(r["firstname"]), _norm_name_key(r["lastname"]))
        if key in sheet_by_key:
            sheet_dupes[key] = sheet_dupes.get(key, 1) + 1
            continue
        sheet_by_key[key] = r

    assert not sheet_dupes, (
        "Duplicate (firstname, lastname) rows in sheet LoginMaster for FranchiseID="
        f"{fid}: " + ", ".join(f"{_fmt_key(k)} x{n}" for k, n in list(sheet_dupes.items())[:20])
    )

    with db_conn() as conn:
        db_rows = (
            conn.exec_driver_sql(
                """
                SELECT
                    firstname,
                    lastname,
                    grade,
                    portal1,
                    p1username,
                    p1password,
                    portal2,
                    p2username,
                    p2password,
                    passwordgood
                FROM Student
                WHERE franchiseid = %s
                """,
                (fid,),
            )
            .mappings()
            .all()
        )

    assert db_rows, f"DB has 0 Student rows for FranchiseID={fid}"

    db_by_key: dict[tuple[int, str, str], dict[str, Any]] = {}
    db_dupes: dict[tuple[int, str, str], int] = {}
    for r in db_rows:
        key = (fid, _norm_name_key(r.get("firstname")), _norm_name_key(r.get("lastname")))
        if key in db_by_key:
            db_dupes[key] = db_dupes.get(key, 1) + 1
            continue
        db_by_key[key] = r

    assert not db_dupes, (
        "Duplicate (firstname, lastname) rows in DB Student for FranchiseID="
        f"{fid}: " + ", ".join(f"{_fmt_key(k)} x{n}" for k, n in list(db_dupes.items())[:20])
    )

    sheet_keys = set(sheet_by_key.keys())
    db_keys = set(db_by_key.keys())

    missing_in_db = sorted(sheet_keys - db_keys, key=_fmt_key)
    extra_in_db = sorted(db_keys - sheet_keys, key=_fmt_key)

    assert not missing_in_db, (
        f"Students present in sheet but missing in DB (FranchiseID={fid}): "
        + ", ".join(_fmt_key(k) for k in missing_in_db[:25])
        + ("" if len(missing_in_db) <= 25 else f" ... (+{len(missing_in_db) - 25} more)")
    )
    assert not extra_in_db, (
        f"Students present in DB but missing in sheet (FranchiseID={fid}): "
        + ", ".join(_fmt_key(k) for k in extra_in_db[:25])
        + ("" if len(extra_in_db) <= 25 else f" ... (+{len(extra_in_db) - 25} more)")
    )

    mismatches: list[_Mismatch] = []
    for key in sorted(sheet_keys & db_keys, key=_fmt_key):
        srow = sheet_by_key[key]
        drow = db_by_key[key]

        # Compare exact cell strings (passwordgood compared as int).
        for f in FIELDS:
            if f == "passwordgood":
                sheet_v = _norm_int(srow.get(f))
                db_v = _norm_int(drow.get(f))
                if sheet_v != db_v:
                    mismatches.append(
                        _Mismatch(
                            key=key,
                            field=f,
                            sheet_value=str(srow.get(f, "")),
                            db_value=str(drow.get(f, "")),
                        )
                    )
            else:
                sheet_v = _cell_str(srow.get(f))
                db_v = _cell_str(drow.get(f))
                if sheet_v != db_v:
                    mismatches.append(
                        _Mismatch(
                            key=key,
                            field=f,
                            sheet_value=str(srow.get(f, "")),
                            db_value=str(drow.get(f, "")),
                        )
                    )

    assert not mismatches, (
        f"Found {len(mismatches)} field mismatches between sheet LoginMaster and DB Student "
        f"for FranchiseID={fid} (showing up to 30):\n"
        + "\n".join(
            f"- {_fmt_key(m.key)} · {m.field}: sheet={m.sheet_value!r} db={m.db_value!r}"
            for m in mismatches[:30]
        )
    )
