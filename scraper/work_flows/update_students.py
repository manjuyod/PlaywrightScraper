#!/usr/bin/env python
"""
update_students.py · PlaywrightScraper/work_flows

Synchronize Student table from each franchise's LoginMaster sheet (source of truth).

Actions per franchise:
  • INSERT  — if (FranchiseID, firstname, lastname) exists in sheet but not DB
  • UPDATE  — if exists in both and any tracked field differs
  • SKIP    — if exists in both and nothing differs
  • DELETE  — if in DB but not in sheet (guarded to avoid accidental wipes)

Requirements:
  - Google service account JSON at repo root (default: sheet_mod_grades.json)
  - Spreadsheets table: FranchiseID, spreadsheet (Google Sheet URL)
  - LoginMaster tab with headers:
      firstname, lastname, grade,
      portal1, p1username, p1password,
      portal2, p2username, p2password, passwordgood
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time
import random
from urllib.parse import urlsplit
from scraper.runner import db_conn, connection, DictCursor
from typing import Dict, List

import gspread
from gspread.utils import ValueRenderOption
from gspread.cell import Cell
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

# ────────────────────────────────────────────────────────────────────────────────
# Paths & constants

ROOT = pathlib.Path(__file__).resolve().parents[2]  # repo root
KEY_PATH = ROOT / "sheet_mod_grades.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

LOGIN_MASTER_TITLE = "LoginMaster"
MIN_ROWS_FOR_DELETE = 3  # safety: require at least this many parsed rows to run deletes

TRACKED_FIELDS = [
    "grade",
    "portal1", "p1username", "p1password",
    "portal2", "p2username", "p2password",
    "passwordgood",
]

TRIM_SHEET_FIELDS = [
    # Only trim left/right whitespace (human entry tends to add trailing spaces).
    "firstname",
    "lastname",
    "portal1",
    "p1username",
    "portal2",
    "p2username",
]

SENSITIVE_FIELDS = {"p1password", "p2password"}

# ────────────────────────────────────────────────────────────────────────────────
def _load_sheet_map(conn: connection) -> Dict[int, str]:
    """
    Return {FranchiseID: <sheet_id>} for non-empty spreadsheet URLs.
    """
    cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute("""
        SELECT franchiseid, spreadsheet
        FROM Spreadsheets
        WHERE franchiseid IS NOT NULL
          AND COALESCE(spreadsheet, '') <> ''
        order by id desc
    """)
    mapping: Dict[int, str] = {}
    for fid, url in cur.fetchall():
        # Accept either full Google Sheets URL or a raw spreadsheet ID.
        # Prefer extracting the /d/<id> token when present.
        m = re.search(r"/d/([A-Za-z0-9_-]+)", url or "")
        if m:
            sheet_id = m.group(1)
        else:
            # Fallback: last path segment, without query/fragment.
            sheet_id = (url or "").rstrip("/").split("/")[-1]
            sheet_id = sheet_id.split("?", 1)[0].split("#", 1)[0]
        mapping[int(fid)] = sheet_id
    return mapping

def _gc_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(str(KEY_PATH), scopes=SCOPES)
    return gspread.Client(auth=creds)

def _norm_space(s: str | None) -> str:
    if s is None:
        return ""
    return " ".join(str(s).strip().split())

def _norm_name_key(s: str | None) -> str:
    # For matching keys: case-insensitive + whitespace-normalized
    return _norm_space(s).lower()

def _norm_int(x) -> int:
    try:
        return int(x)
    except Exception:
        return 0

def _env_flag(name: str) -> bool:
    v = os.getenv(name)
    if v is None:
        return False
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}
     
def _open_by_key_retry(gc, sheet_id, retries=5):
    for attempt in range(1, retries + 1):
        try:
            print('open by key, ', sheet_id)
            return gc.open_by_key(sheet_id)
        except APIError as e:
            print(e)
            msg = str(e)
            if any(code in msg for code in ("503", "500", "502", "429")) and attempt < retries:
                sleep = (2 ** attempt) + random.random()
                print(f"[WARN] transient Sheets error ({msg.split(':',1)[0]}) — retry {attempt}/{retries} in {sleep:.1f}s...")
                time.sleep(sleep)
                continue
            raise

def _update_cells_retry(ws: gspread.Worksheet, cells: list[Cell], retries: int = 5, chunk_size: int = 500) -> None:
    for start in range(0, len(cells), chunk_size):
        chunk = cells[start:start + chunk_size]
        for attempt in range(1, retries + 1):
            try:
                ws.update_cells(chunk)
                break
            except APIError as e:
                msg = str(e)
                if any(code in msg for code in ("503", "500", "502", "429")) and attempt < retries:
                    sleep = (2 ** attempt) + random.random()
                    print(
                        f"[WARN] transient Sheets error ({msg.split(':',1)[0]}) — "
                        f"retry {attempt}/{retries} in {sleep:.1f}s..."
                    )
                    time.sleep(sleep)
                    continue
                raise

def _read_login_master(gc: gspread.Client, sheet_id: str) -> List[dict]:
    """
    Read LoginMaster rows from a Google Sheet.
    Returns a list of dicts with normalized values.
    """
    print('trying to open sheet')
    sh = _open_by_key_retry(gc, sheet_id)
    print('opened, try to get worksheet')
    assert sh is not None, f"Failed to open sheet with ID {sheet_id}"
    try:
        ws = sh.worksheet(LOGIN_MASTER_TITLE)
    except gspread.WorksheetNotFound:
        print(f"[WARN] Sheet has no tab named '{LOGIN_MASTER_TITLE}' (id={sheet_id}); parsed 0 rows.")
        return []
    print("collected worksheet")
    # get_all_records() reads the header row and returns list[dict]
    # rows = ws.get_all_records()  # empty cells -> ''
    values = ws.get_all_values(value_render_option=ValueRenderOption.formatted) # this is necessary so that strings are not interpreted as numbers, resulting in the truncation of zeros
    if not values:
        print(f"[WARN] Sheet tab '{LOGIN_MASTER_TITLE}' is empty (id={sheet_id}); parsed 0 rows.")
        return []

    headers = [h.strip() for h in values[0]]
    col_by_field = {h.lower(): (i + 1) for i, h in enumerate(headers) if h}

    required = {"firstname", "lastname", "grade", "portal1", "p1username", "p1password", "portal2", "p2username", "p2password", "passwordgood"}
    missing = sorted(required - set(col_by_field.keys()))
    if missing:
        print(f"[WARN] '{LOGIN_MASTER_TITLE}' missing required headers {missing} (id={sheet_id}); parsed 0 rows.")
        return []

    # Pre-trim specific columns in the SHEET itself (ltrim/rtrim only).
    # This keeps the sheet as the source-of-truth and avoids "phantom diffs" caused by trailing spaces.
    cells_to_trim: list[Cell] = []
    for row_num, row_vals in enumerate(values[1:], start=2):  # 1-based row index in Sheets; row 1 is headers
        for field in TRIM_SHEET_FIELDS:
            col = col_by_field.get(field)
            if not col:
                continue
            raw = row_vals[col - 1] if (col - 1) < len(row_vals) else ""
            trimmed = str(raw).strip()
            if raw != trimmed:
                cells_to_trim.append(Cell(row_num, col, trimmed))

    if cells_to_trim:
        print(f"[INFO] Trimming {len(cells_to_trim)} cells in '{LOGIN_MASTER_TITLE}' (id={sheet_id})...")
        _update_cells_retry(ws, cells_to_trim)
        # Keep local copy in sync for parsing/logging.
        for c in cells_to_trim:
            try:
                values[c.row - 1][c.col - 1] = c.value
            except Exception:
                pass

    print("worksheet parsed")
    out: List[dict] = []
    for row_vals in values[1:]:
        def _get(field: str) -> str:
            col = col_by_field.get(field)
            if not col:
                return ""
            return row_vals[col - 1] if (col - 1) < len(row_vals) else ""

        rec = {
            "firstname":   _norm_space(_get("firstname")),
            "lastname":    _norm_space(_get("lastname")),
            "grade":       _norm_space(_get("grade")),
            "portal1":     _norm_space(_get("portal1")),
            "p1username":  _norm_space(_get("p1username")),
            "p1password":  _norm_space(_get("p1password")),
            "portal2":     _norm_space(_get("portal2")),
            "p2username":  _norm_space(_get("p2username")),
            "p2password":  _norm_space(_get("p2password")),
            "passwordgood": _norm_int(_get("passwordgood")),
        }
        # Must have non-empty names to be considered valid
        if rec["firstname"] or rec["lastname"]:
            out.append(rec)
    return out

def _load_db_keys_for_franchise(conn: connection, fid: int) -> Dict[tuple, int]:
    """
    Return { (fid, norm(first), norm(last)): student_id } for the franchise.
    """
    cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute("""
        SELECT id, firstname, lastname
        FROM Student
        WHERE franchiseid = %s
    """, (fid,))
    mapping: Dict[tuple, int] = {}
    for row in cur.fetchall():
        key = (fid, _norm_name_key(row["firstname"]), _norm_name_key(row["lastname"]))
        mapping[key] = int(row["id"])
    return mapping

def _fetch_db_row(conn: connection, student_id: int) -> dict:
    cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute("""
        SELECT grade, portal1, p1Username, p1password,
               portal2, p2username, p2password, passwordgood, portal
        FROM Student
        WHERE id = %s
    """, (student_id,))
    row = cur.fetchone()
    if not row:
        return {}
    return dict(row)

def _differs(db_row: dict, sheet_row: dict) -> bool:
    """
    Compare only TRACKED_FIELDS with normalization.
    """
    if not db_row:
        return True
    for f in TRACKED_FIELDS:
        a = db_row.get(f.lower())
        b = sheet_row.get(f)
        if f == "passwordgood":
            if _norm_int(a) != _norm_int(b):
                return True
        else:
            if _norm_space(a) != _norm_space(b):
                return True
    return False

def _safe_preview(field: str, value) -> str:
    if field in {"p1password", "p2password"}:
        s = _norm_space(value)
        return "" if not s else f"[REDACTED len={len(s)}]"
    if field in {"p1username", "p2username"}:
        s = _norm_space(value)
        return "" if not s else f"[REDACTED_USER len={len(s)}]"
    if field in {"portal1", "portal2"}:
        s = _norm_space(value)
        if not s:
            return ""
        try:
            parts = urlsplit(s)
            if parts.scheme and parts.netloc:
                # Drop query/fragment (these sometimes contain tokens).
                return f"{parts.scheme}://{parts.netloc}{parts.path}"
        except Exception:
            pass
        return "[REDACTED_URL]"
    if field == "passwordgood":
        return str(_norm_int(value))
    return _norm_space(value)

def _diff_detail(db_row: dict, sheet_row: dict, *, portal_new: str | None = None) -> list[tuple[str, str, str]]:
    """
    Return [(field, old, new), ...] using the same normalization semantics as sync logic,
    but with sensitive fields redacted for logging.
    """
    out: list[tuple[str, str, str]] = []
    if not db_row:
        for f in TRACKED_FIELDS:
            out.append((f, "", _safe_preview(f, sheet_row.get(f))))
        if portal_new is not None:
            out.append(("portal", "", str(portal_new)))
        return out

    for f in TRACKED_FIELDS:
        old = db_row.get(f.lower())
        new = sheet_row.get(f)
        if f == "passwordgood":
            if _norm_int(old) != _norm_int(new):
                out.append((f, _safe_preview(f, old), _safe_preview(f, new)))
        else:
            if _norm_space(old) != _norm_space(new):
                out.append((f, _safe_preview(f, old), _safe_preview(f, new)))

    if portal_new is not None:
        portal_old = db_row.get("portal")
        if (portal_old or None) != (portal_new or None):
            out.append(("portal", str(portal_old or ""), str(portal_new or "")))
    return out

from scraper.portals.utils import get_portal_key_from_url


# ────────────────────────────────────────────────────────────────────────────────
# Main sync

def sync_students(target_fid: int | None = None, *, debug: bool = False) -> None:
    gc = _gc_client()
    with db_conn() as conn:
        sheet_map = _load_sheet_map(conn)

    total_ins = total_upd = total_del = total_skp = 0

    for fid, sheet_id in sheet_map.items():
        if target_fid and fid != target_fid:
            continue
        print(f"\n--- Franchise {fid} ---")
        if debug:
            print(f"[DEBUG] detailed row logging enabled (FID={fid})")
        sheet_rows = _read_login_master(gc, sheet_id)
        parsed_count = len(sheet_rows)
        print(f"[INFO] Parsed LoginMaster rows: {parsed_count}")

        # Guard: never mutate if nothing parsed (prevents accidental wipes)
        if parsed_count == 0:
            print(f"[WARN] FID={fid}: 0 rows parsed — skipping inserts/updates/deletes for safety.")
            continue

        # Build target map from SHEET (source of truth)
        target_keys = []
        target_map = {}  # key -> sheet_row
        for r in sheet_rows:
            key = (fid, _norm_name_key(r["firstname"]), _norm_name_key(r["lastname"]))
            if not (key[1] or key[2]):  # skip empty names
                continue
            target_keys.append(key)
            target_map[key] = r

        # DB keys (for comparison only)
        with db_conn() as conn:
            db_key_to_id = _load_db_keys_for_franchise(conn, fid)

        inserts = updates = deletes = skips = 0

        with db_conn() as conn:
            cur = conn.cursor(cursor_factory=DictCursor)
            # Transaction per franchise
            cur.execute("BEGIN")
            try:
                # INSERT / UPDATE / SKIP: iterate LoginMaster (sheet) and check DB
                for key in target_keys:
                    sheet_rec = target_map[key]
                    sid = db_key_to_id.get(key)
                    if sid is None:
                        # INSERT
                        weeklydata = {"2025-08-04":{},"2025-08-11":{},"2025-08-18":{},"2025-08-25":{},"2025-09-01":{},"2025-09-08":{},"2025-09-15":{},"2025-09-22":{},"2025-09-29":{},"2025-10-06":{},"2025-10-13":{},"2025-10-20":{},"2025-10-27":{},"2025-11-03":{},"2025-11-10":{},"2025-11-17":{},"2025-11-24":{},"2025-12-01":{},"2025-12-08":{},"2025-12-15":{},"2025-12-22":{},"2025-12-29":{},"2026-01-05":{},"2026-01-12":{},"2026-01-19":{},"2026-01-26":{},"2026-02-02":{},"2026-02-09":{},"2026-02-16":{},"2026-02-23":{},"2026-03-02":{},"2026-03-09":{},"2026-03-16":{},"2026-03-23":{},"2026-03-30":{},"2026-04-06":{},"2026-04-13":{},"2026-04-20":{},"2026-04-27":{},"2026-05-04":{},"2026-05-11":{},"2026-05-18":{},"2026-05-25":{},"2026-06-01":{},"2026-06-08":{},"2026-06-15":{},"2026-06-22":{},"2026-06-29":{}}
                        portal = get_portal_key_from_url(sheet_rec['portal1'])
                        if debug:
                            print(
                                "[INSERT] "
                                f"FID={fid} name={sheet_rec['lastname']}, {sheet_rec['firstname']} "
                                f"grade={_safe_preview('grade', sheet_rec.get('grade'))!r} "
                                f"passwordgood={_safe_preview('passwordgood', sheet_rec.get('passwordgood'))!r} "
                                f"portal={portal!r}"
                            )
                        cur.execute("""
                            INSERT INTO Student
                              (franchiseid, firstname, lastname, grade,
                                portal1, p1username, p1password,
                                portal2, p2username, p2password, passwordgood, portal, weeklydata)
                            VALUES
                              (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            fid,
                            _norm_space(sheet_rec["firstname"]),
                            _norm_space(sheet_rec["lastname"]),
                            _norm_space(sheet_rec["grade"]),
                            _norm_space(sheet_rec["portal1"]),
                            _norm_space(sheet_rec["p1username"]),
                            _norm_space(sheet_rec["p1password"]),
                            _norm_space(sheet_rec["portal2"]),
                            _norm_space(sheet_rec["p2username"]),
                            _norm_space(sheet_rec["p2password"]),
                            _norm_int(sheet_rec["passwordgood"]),
                            portal,
                            json.dumps(weeklydata)
                        ))
                        inserts += 1
                        continue

                    # UPDATE vs SKIP
                    db_row = _fetch_db_row(conn, sid)
                    needs_update = _differs(db_row, sheet_rec)
                    portal_missing = db_row.get("portal") is None or len(db_row["portal"]) == 0
                    if needs_update or portal_missing:
                        portal = get_portal_key_from_url(sheet_rec['portal1'])
                        if debug:
                            diffs = _diff_detail(db_row, sheet_rec, portal_new=portal)
                            diff_str = ", ".join(f"{f}:{old!r}->{new!r}" for f, old, new in diffs) or "(no field diffs)"
                            reason_parts = []
                            if needs_update:
                                reason_parts.append("fields")
                            if portal_missing:
                                reason_parts.append("portal_missing")
                            reason = "+".join(reason_parts) if reason_parts else "unknown"
                            print(
                                f"[UPDATE] FID={fid} id={sid} name={sheet_rec['lastname']}, {sheet_rec['firstname']} "
                                f"reason={reason} diffs={diff_str}"
                            )
                        cur.execute("""
                            UPDATE Student
                            SET grade = %s,
                                portal1 = %s, p1username = %s, p1password = %s,
                                portal2 = %s, p2username = %s, p2password = %s, 
                                passwordgood = %s,
                                portal = %s
                            WHERE id = %s
                        """, (
                            _norm_space(sheet_rec["grade"]),
                            _norm_space(sheet_rec["portal1"]),
                            _norm_space(sheet_rec["p1username"]),
                            _norm_space(sheet_rec["p1password"]),
                            _norm_space(sheet_rec["portal2"]),
                            _norm_space(sheet_rec["p2username"]),
                            _norm_space(sheet_rec["p2password"]),
                            _norm_int(sheet_rec["passwordgood"]),
                            portal,
                            sid,
                        ))
                        updates += 1
                    else:
                        skips += 1

                # DELETE: DB keys that are not in SHEET (with guard)
                if parsed_count >= MIN_ROWS_FOR_DELETE:
                    sheet_key_set = set(target_keys)
                    db_key_set = set(db_key_to_id.keys())
                    to_delete = db_key_set - sheet_key_set
                    for dkey in to_delete:
                        if debug:
                            sid = db_key_to_id.get(dkey)
                            _, first, last = dkey
                            print(f"[DELETE] FID={fid} id={sid} name={last}, {first}")
                        cur.execute("""
                            DELETE FROM Student
                            WHERE franchiseid = %s
                              AND LOWER(TRIM(firstname)) = %s
                              AND LOWER(TRIM(lastname))  = %s
                        """, (dkey[0], dkey[1], dkey[2]))
                        deletes += 1
                else:
                    print(f"[WARN] FID={fid}: Only {parsed_count} rows parsed; skipping DELETE phase.")

                conn.commit()

            except Exception as e:
                conn.rollback()
                print(f"[ERROR] FID={fid}: rolled back due to error: {e}")
                continue

            total_ins += inserts
            total_upd += updates
            total_del += deletes
            total_skp += skips
        print(f"[SUMMARY] FID={fid} inserts={inserts} updates={updates} skips={skips} deletes={deletes}")

    print("\n=== GRAND TOTALS ===")
    print(f"inserts={total_ins} updates={total_upd} skips={total_skp} deletes={total_del}")

def _parse_args():
    import argparse
    p = argparse.ArgumentParser(description="Pull grade/login tabs from Google Sheets to DB per franchise.")
    p.add_argument("--franchise-id", "--fid", type=int, default=None,
                   help="Only process this FranchiseID. If omitted, process all known franchises.")
    p.add_argument("--debug", action="store_true", default=_env_flag("UPDATE_STUDENTS_DEBUG"),
                   help="Print detailed row-level INSERT/UPDATE/DELETE logs.")
    return p.parse_args()

def main() -> None:
    args = _parse_args()
    target_fid: int | None = args.franchise_id or None
    sync_students(target_fid, debug=bool(getattr(args, "debug", False)))

if __name__ == "__main__":
    main()
