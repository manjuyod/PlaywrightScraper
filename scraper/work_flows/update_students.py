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
import pathlib
import re
import time
import random
from scraper.runner import db_conn, connection, DictCursor
from typing import Dict, List

import gspread
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
        sheet_id = url.split("/")[-1]
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
    
def _open_by_key_retry(gc, sheet_id, retries=5):
    for attempt in range(1, retries + 1):
        try:
            return gc.open_by_key(sheet_id)
        except APIError as e:
            msg = str(e)
            if any(code in msg for code in ("503", "500", "502", "429")) and attempt < retries:
                sleep = (2 ** attempt) + random.random()
                print(f"[WARN] transient Sheets error ({msg.split(':',1)[0]}) — retry {attempt}/{retries} in {sleep:.1f}s...")
                time.sleep(sleep)
                continue
            raise

def _read_login_master(gc: gspread.Client, sheet_id: str) -> List[dict]:
    """
    Read LoginMaster rows from a Google Sheet.
    Returns a list of dicts with normalized values.
    """
    sh = _open_by_key_retry(gc, sheet_id) # this hangs on franchise 19 because of the number of sheets within the document
    try:
        ws = sh.worksheet(LOGIN_MASTER_TITLE)
    except gspread.WorksheetNotFound:
        print(f"[WARN] Sheet has no tab named '{LOGIN_MASTER_TITLE}' (id={sheet_id}); parsed 0 rows.")
        return []
    # print("worksheet parsed")
    # get_all_records() reads the header row and returns list[dict]
    rows = ws.get_all_records()  # empty cells -> ''
    # print("worksheet rows parsed", rows)
    out: List[dict] = []
    for r in rows:
        rec = {
            "firstname":   _norm_space(r.get("firstname")),
            "lastname":    _norm_space(r.get("lastname")),
            "grade":       _norm_space(r.get("grade")),
            "portal1":     _norm_space(r.get("portal1")),
            "p1username":  _norm_space(r.get("p1username")),
            "p1password":  _norm_space(r.get("p1password")),
            "portal2":     _norm_space(r.get("portal2")),
            "p2username":  _norm_space(r.get("p2username")),
            "p2password":  _norm_space(r.get("p2password")),
            "passwordgood": _norm_int(r.get("passwordgood")),
        }
        # Must have non-empty names to be considered valid
        if rec["firstname"] or rec["lastname"]:
            out.append(rec)
            # print(f"Portal: {rec['']}")

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

from scraper.portals import managed_portals
def get_portal_from_record(record: dict) -> str | None:
    """Sorts portal links into 'buckets' defined from portals that we currently manage"""
    portal_link = record["portal1"]
    # print(f'\n{portal_link}')
    for portal, rules in managed_portals.items():
        # print(portal)
        for rule in rules:
            # print(f'\t {rule}')
            # print(rule in portal_link)
            if rule in portal_link:
                # print(f'found {portal} for {portal_link}')
                return portal
    print(f"No portal found for {portal_link}")
    return None

# ────────────────────────────────────────────────────────────────────────────────
# Main sync

def sync_students(target_fid: int | None = None) -> None:
    conn = db_conn()
    gc = _gc_client()
    sheet_map = _load_sheet_map(conn)
    cur = conn.cursor(cursor_factory=DictCursor)

    total_ins = total_upd = total_del = total_skp = 0

    for fid, sheet_id in sheet_map.items():
        if target_fid and fid != target_fid:
            continue
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
            key = (fid, _norm_name_key(r["firstname"]), _norm_name_key(r["lastname"]))
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
                    weeklydata = {"2025-08-04":{},"2025-08-11":{},"2025-08-18":{},"2025-08-25":{},"2025-09-01":{},"2025-09-08":{},"2025-09-15":{},"2025-09-22":{},"2025-09-29":{},"2025-10-06":{},"2025-10-13":{},"2025-10-20":{},"2025-10-27":{},"2025-11-03":{},"2025-11-10":{},"2025-11-17":{},"2025-11-24":{},"2025-12-01":{},"2025-12-08":{},"2025-12-15":{},"2025-12-22":{},"2025-12-29":{},"2026-01-05":{},"2026-01-12":{},"2026-01-19":{},"2026-01-26":{},"2026-02-02":{},"2026-02-09":{},"2026-02-16":{},"2026-02-23":{},"2026-03-02":{},"2026-03-09":{},"2026-03-16":{},"2026-03-23":{},"2026-03-30":{},"2026-04-06":{},"2026-04-13":{},"2026-04-20":{},"2026-04-27":{},"2026-05-04":{},"2026-05-11":{},"2026-05-18":{},"2026-05-25":{},"2026-06-01":{},"2026-06-08":{},"2026-06-15":{},"2026-06-22":{},"2026-06-29":{}}
                    portal = get_portal_from_record(sheet_rec)
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
                if _differs(db_row, sheet_rec) or db_row['portal'] is None:
                    portal = get_portal_from_record(sheet_rec)
                    cur.execute("""
                        UPDATE Student
                        SET grade = %s,
                            portal1 = %s, p1username = %s, p1password = %s,
                            portal2 = %s, p2username = %s, p2password = %s,
                            passwordgood = %s, portal = %s
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

    print(f"\n=== GRAND TOTALS ===")
    print(f"inserts={total_ins} updates={total_upd} skips={total_skp} deletes={total_del}")

def _parse_args():
    import argparse
    p = argparse.ArgumentParser(description="Pull grade/login tabs from Google Sheets to DB per franchise.")
    p.add_argument("--franchise-id", "--fid", type=int, default=None,
                   help="Only process this FranchiseID. If omitted, process all known franchises.")
    return p.parse_args()

def main() -> None:
    args = _parse_args()
    target_fid: int | None = args.franchise_id or None
    sync_students(target_fid)

if __name__ == "__main__":
    main()
