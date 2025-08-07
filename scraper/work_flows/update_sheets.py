#!/usr/bin/env python
"""
update_sheets.py · PlaywrightScraper/work_flows

Build a franchise‑specific “BMaster” worksheet each week:
  1. Read every student from config/students.db.
  2. For each franchise, assemble a DataFrame with:
       • meta rows (Name → P2Password)
       • two blank rows
       • one row per subject (Subject1…SubjectN) with grades across all Monday‑anchor weeks
       • if PasswordGood = 0 → replace pivot with a single error row
  3. Look up the franchise’s Google Sheet from the Spreadsheets table.
  4. Overwrite (or create) the tab named "BMaster" with the DataFrame, no formatting.

Requirements
–––––––––––––
• gspread ≥ 6, google‑auth
• service_account.json (or your renamed JSON key) at repo root.
"""
from __future__ import annotations

import itertools
import json
import math
import pathlib
import re
import sqlite3
from typing import Dict, List

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

# ────────────────────────────────────────────────────────────────────────────────
# Paths & constants
ROOT = pathlib.Path(__file__).resolve().parents[2]  # repo root
DB_PATH = ROOT / "config" / "students.db"
KEY_PATH = ROOT / "sheet_mod_grades.json"  # ← change if your key has a different name
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Column / field order for the meta rows
META_FIELDS = [
    ("Name", "StudentName"),
    ("Grade", "Grade"),
    ("Portal1", "Portal1"),
    ("P1Username", "P1Username"),
    ("P1Password", "P1Password"),
    ("Portal2", "Portal2"),
    ("P2Username", "P2Username"),
    ("P2Password", "P2Password"),
]

# ────────────────────────────────────────────────────────────────────────────────
# Helpers

def _connect_db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _load_sheet_map(conn: sqlite3.Connection) -> Dict[int, str]:
    """Return {FranchiseID: <spreadsheet‑id>} extracted from URL."""
    cur = conn.cursor()
    cur.execute("SELECT FranchiseID, spreadsheet FROM Spreadsheets")
    mapping: Dict[int, str] = {}
    for fid, url in cur.fetchall():
        m = re.search(r"/d/([A-Za-z0-9_-]+)", url)
        if m:
            mapping[fid] = m.group(1)
    return mapping


def _query_students(conn: sqlite3.Connection) -> pd.DataFrame:
    """Return a DataFrame of all students, ordered for stable output."""
    sql = """
        SELECT ID,
               FranchiseID,
               FirstName || ' ' || LastName AS StudentName,
               Grade,
               Portal1,
               P1Username,
               P1Password,
               Portal2,
               P2Username,
               P2Password,
               WeeklyData,
               PasswordGood
        FROM   Student
        ORDER  BY FirstName ASC, Grade ASC
    """
    return pd.read_sql_query(sql, conn)


# ────────────────────────────────────────────────────────────────────────────────
# Data‑frame builders

def _build_student_block(row: pd.Series, weeks: List[str]) -> pd.DataFrame:
    """Return one student’s block (meta + blank + pivot)."""
    good_pw = row["PasswordGood"] == 1

    # 1) meta rows
    meta = pd.DataFrame(
        {
            "Field": [lbl for lbl, _ in META_FIELDS],
            "Value": [row[src] for _, src in META_FIELDS],
        }
    )

    # 2) two blank spacer rows
    blank = pd.DataFrame([{"Field": "", "Value": ""}] * 2)

    if not good_pw:
        err = pd.DataFrame(
            [
                {"Field": "Error", "Value": "invalid entry, check credentials"},
                {"Field": "", "Value": ""},
                {"Field": "", "Value": ""},
            ]
        )
        block = pd.concat([meta, err], ignore_index=True)
        block.insert(0, "StudentID", row["ID"])
        return block

    # 3) pivot rows
    wdata = json.loads(row["WeeklyData"])
    subjects = sorted(set(itertools.chain.from_iterable(wdata[w].keys() for w in wdata)))

    pivots: List[Dict[str, str | float]] = []
    for idx, subj in enumerate(subjects, 1):
        d: Dict[str, str | float] = {"Field": f"Subject{idx}", "Value": subj}
        for week in weeks:
            raw = wdata.get(week, {}).get(subj)
            if isinstance(raw, dict):
                raw = raw.get("percentage") or raw.get("letter_grade") or ""
            if raw is None or (isinstance(raw, float) and math.isnan(raw)):
                raw = ""
            d[week] = raw
        pivots.append(d)

    pivot_df = pd.DataFrame(pivots, columns=["Field", "Value", *weeks])

    block = pd.concat([meta, blank, pivot_df], ignore_index=True)
    block.insert(0, "StudentID", row["ID"])
    return block


def _build_dataframe_for_franchise(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate blocks for every student in one franchise."""
    all_weeks: set[str] = set()
    for wd_json in df["WeeklyData"]:
        all_weeks.update(json.loads(wd_json).keys())
    weeks = sorted(all_weeks)

    frames = [_build_student_block(row, weeks) for _, row in df.iterrows()]
    return pd.concat(frames, ignore_index=True)


# ────────────────────────────────────────────────────────────────────────────────
# Google Sheets uploader

def _push_dataframe(sheet_id: str, df: pd.DataFrame, tab: str = "BMaster") -> None:
    creds = Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
    gc = gspread.Client(auth=creds)

    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows="100", cols="30")

    ws.clear()
    clean_df = df.where(pd.notna(df), "")
    ws.update(clean_df.values.tolist(), "A1", value_input_option="USER_ENTERED")


# ────────────────────────────────────────────────────────────────────────────────
# Main entry point

def main() -> None:
    conn = _connect_db()
    sheet_map = _load_sheet_map(conn)
    df_all = _query_students(conn)

    for fid, grp in df_all.groupby("FranchiseID"):
        if fid not in sheet_map:
            print(f"[SKIP] No sheet configured for FranchiseID {fid}")
            continue

        print(f"Processing FranchiseID {fid} (students: {len(grp)})")
        export_df = _build_dataframe_for_franchise(grp)
        _push_dataframe(sheet_map[fid], export_df)
        print("  ✓ Uploaded")

    conn.close()


if __name__ == "__main__":
    main()