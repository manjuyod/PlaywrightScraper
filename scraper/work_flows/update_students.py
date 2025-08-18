#!/usr/bin/env python
"""
update_students.py · PlaywrightScraper/work_flows

Synchronize Student table from each franchise's LoginMaster sheet (source of truth).

Actions per franchise:
  • INSERT  — if (FranchiseID, FirstName, LastName) exists in sheet but not DB
  • UPDATE  — if exists in both and any tracked field differs
  • SKIP    — if exists in both and nothing differs
  • DELETE  — if in DB but not in sheet (guarded to avoid accidental wipes)

Requirements:
  - Google service account JSON at repo root (default: sheet_mod_grades.json)
  - Spreadsheets table: FranchiseID, spreadsheet (Google Sheet URL)
  - LoginMaster tab with headers:
      FirstName, LastName, Grade,
      Portal1, P1Username, P1Password,
      Portal2, P2Username, P2Password, PasswordGood
"""

from __future__ import annotations

import pathlib
import re
import sqlite3
from typing import Dict, List

import gspread
from google.oauth2.service_account import Credentials

# ────────────────────────────────────────────────────────────────────────────────
# Paths & constants

ROOT = pathlib.Path(__file__).resolve().parents[2]  # repo root
DB_PATH = ROOT / "config" / "students.db"
KEY_PATH = ROOT / "sheet_mod_grades.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

LOGIN_MASTER_TITLE = "LoginMaster"
MIN_ROWS_FOR_DELETE = 3  # safety: require at least this many parsed rows to run deletes

TRACKED_FIELDS = [
    "Grade",
    "Portal1", "P1Username", "P1Password",
    "Portal2", "P2Username", "P2Password",
    "PasswordGood",
]

# ────────────────────────────────────────────────────────────────────────────────
# Helpers

def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _load_sheet_map(conn: sqlite3.Connection) -> Dict[int, str]:
    """
    Return {FranchiseID: <sheet_id>} for non-empty spreadsheet URLs.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT FranchiseID, spreadsheet
        FROM Spreadsheets
        WHERE FranchiseID IS NOT NULL
          AND COALESCE(spreadsheet, '') <> ''
    """)
    mapping: Dict[int, str] = {}
    for fid, url in cur.fetchall():
        m = re.search(r"/d/([A-Za-z0-9_-]+)", url or "")
        if m:
            mapping[int(fid)] = m.group(1)
    return mapping

def _gc_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
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

def _read_login_master(gc: gspread.Client, sheet_id: str) -> List[dict]:
    """
    Read LoginMaster rows from a Google Sheet.
    Returns a list of dicts with normalized values.
    """
    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(LOGIN_MASTER_TITLE)
    except gspread.WorksheetNotFound:
        print(f"[WARN] Sheet has no tab named '{LOGIN_MASTER_TITLE}' (id={sheet_id}); parsed 0 rows.")
        return []

    # get_all_records() reads the header row and returns list[dict]
    rows = ws.get_all_records()  # empty cells -> ''
    out: List[dict] = []

    for r in rows:
        rec = {
            "FirstName":   _norm_space(r.get("FirstName")),
            "LastName":    _norm_space(r.get("LastName")),
            "Grade":       _norm_space(r.get("Grade")),
            "Portal1":     _norm_space(r.get("Portal1")),
            "P1Username":  _norm_space(r.get("P1Username")),
            "P1Password":  _norm_space(r.get("P1Password")),
            "Portal2":     _norm_space(r.get("Portal2")),
            "P2Username":  _norm_space(r.get("P2Username")),
            "P2Password":  _norm_space(r.get("P2Password")),
            "PasswordGood": _norm_int(r.get("PasswordGood")),
        }
        # Must have non-empty names to be considered valid
        if rec["FirstName"] or rec["LastName"]:
            out.append(rec)

    return out

def _load_db_keys_for_franchise(conn: sqlite3.Connection, fid: int) -> Dict[tuple, int]:
    """
    Return { (fid, norm(first), norm(last)): student_id } for the franchise.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT ID, FirstName, LastName
        FROM Student
        WHERE FranchiseID = ?
    """, (fid,))
    mapping: Dict[tuple, int] = {}
    for row in cur.fetchall():
        key = (fid, _norm_name_key(row["FirstName"]), _norm_name_key(row["LastName"]))
        mapping[key] = int(row["ID"])
    return mapping

def _fetch_db_row(conn: sqlite3.Connection, student_id: int) -> dict:
    cur = conn.cursor()
    cur.execute("""
        SELECT Grade, Portal1, P1Username, P1Password,
               Portal2, P2Username, P2Password, PasswordGood
        FROM Student
        WHERE ID = ?
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
        a = db_row.get(f)
        b = sheet_row.get(f)
        if f == "PasswordGood":
            if _norm_int(a) != _norm_int(b):
                return True
        else:
            if _norm_space(a) != _norm_space(b):
                return True
    return False

# ────────────────────────────────────────────────────────────────────────────────
# Main sync

def sync_students() -> None:
    conn = _connect_db()
    gc = _gc_client()
    sheet_map = _load_sheet_map(conn)

    cur = conn.cursor()

    total_ins = total_upd = total_del = total_skp = 0

    for fid, sheet_id in sheet_map.items():
        print(f"\n--- Franchise {fid} ---")
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
            key = (fid, _norm_name_key(r["FirstName"]), _norm_name_key(r["LastName"]))
            if not (key[1] or key[2]):  # skip empty names
                continue
            target_keys.append(key)
            target_map[key] = r

        # DB keys (for comparison only)
        db_key_to_id = _load_db_keys_for_franchise(conn, fid)

        inserts = updates = deletes = skips = 0

        # Transaction per franchise
        cur.execute("BEGIN")
        try:
            # INSERT / UPDATE / SKIP: iterate LoginMaster (sheet) and check DB
            for key in target_keys:
                sheet_rec = target_map[key]
                sid = db_key_to_id.get(key)

                if sid is None:
                    # INSERT
                    cur.execute("""
                        INSERT INTO Student
                          (FranchiseID, FirstName, LastName, Grade,
                           Portal1, P1Username, P1Password,
                           Portal2, P2Username, P2Password, PasswordGood)
                        VALUES
                          (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        fid,
                        _norm_space(sheet_rec["FirstName"]),
                        _norm_space(sheet_rec["LastName"]),
                        _norm_space(sheet_rec["Grade"]),
                        _norm_space(sheet_rec["Portal1"]),
                        _norm_space(sheet_rec["P1Username"]),
                        _norm_space(sheet_rec["P1Password"]),
                        _norm_space(sheet_rec["Portal2"]),
                        _norm_space(sheet_rec["P2Username"]),
                        _norm_space(sheet_rec["P2Password"]),
                        _norm_int(sheet_rec["PasswordGood"]),
                    ))
                    inserts += 1
                    continue

                # UPDATE vs SKIP
                db_row = _fetch_db_row(conn, sid)
                if _differs(db_row, sheet_rec):
                    cur.execute("""
                        UPDATE Student
                        SET Grade = ?,
                            Portal1 = ?, P1Username = ?, P1Password = ?,
                            Portal2 = ?, P2Username = ?, P2Password = ?,
                            PasswordGood = ?
                        WHERE ID = ?
                    """, (
                        _norm_space(sheet_rec["Grade"]),
                        _norm_space(sheet_rec["Portal1"]),
                        _norm_space(sheet_rec["P1Username"]),
                        _norm_space(sheet_rec["P1Password"]),
                        _norm_space(sheet_rec["Portal2"]),
                        _norm_space(sheet_rec["P2Username"]),
                        _norm_space(sheet_rec["P2Password"]),
                        _norm_int(sheet_rec["PasswordGood"]),
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
                    cur.execute("""
                        DELETE FROM Student
                        WHERE FranchiseID = ?
                          AND LOWER(TRIM(FirstName)) = ?
                          AND LOWER(TRIM(LastName))  = ?
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

    print(f"\n=== GRAND TOTALS ===")
    print(f"inserts={total_ins} updates={total_upd} skips={total_skp} deletes={total_del}")

def main() -> None:
    sync_students()

if __name__ == "__main__":
    main()
