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

def _extract_subjects(wdata: dict) -> List[str]:
    """Union of subject names across weeks (robust to dict or list week payloads)."""
    names = set()
    for _, val in wdata.items():
        if isinstance(val, dict):
            names.update(val.keys())
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    s = item.get("subject") or item.get("name") or item.get("course")
                    if s:
                        names.add(s)
    return sorted(names)
# ────────────────────────────────────────────────────────────────────────────────
# Data‑frame builders

def _build_legend_rows(weeks: List[str]) -> pd.DataFrame:
    """
    Build:
      Row 0: A1 left blank (entire first row blank)
      Row 1: B2: '<= 69% (needs meeting)'
      Row 2: B3: '70-80% (text parents)' and C3..: week keys
      Row 3: B4: '80-90%'
      Row 4: B5: '>90%'
      Row 5: B6: 'Already had Meeting'
    """
    rows = [
        {"Field": "", "Value": ""},  # row index 0 — full row blank keeps A1 empty

        {"Field": "", "Value": "<= 69% (needs meeting)"},
        dict({"Field": "", "Value": "70-80% (text parents)"} | {w: w for w in weeks}),
        {"Field": "", "Value": "80-90%"},
        {"Field": "", "Value": ">90%"},
        {"Field": "", "Value": "Already had Meeting"},
    ]
    # IMPORTANT: columns are positional: A="Field", B="Value", C..="weeks"
    return pd.DataFrame(rows, columns=["Field", "Value", *weeks])

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
        # keep A/B-only rows; no StudentID
        return pd.concat([meta, err], ignore_index=True)


    # 3) pivot rows
    wdata = json.loads(row["WeeklyData"])
    subjects = _extract_subjects(wdata)

    pivots: List[Dict[str, str | float]] = []
    for idx, subj in enumerate(subjects, 1):
        d: Dict[str, str | float] = {"Field": f"Subject{idx}", "Value": subj}
        for week in weeks:
            entry = wdata.get(week, {}).get(subj)
            val = ""
            if isinstance(entry, dict):
                # Prefer numeric percentage if present, else letter_grade, else blank
                pct = entry.get("percentage")
                if pct is not None and not (isinstance(pct, float) and math.isnan(pct)):
                    val = pct
                else:
                    lg = entry.get("letter_grade")
                    val = lg if lg is not None else ""
            elif entry is not None and not (isinstance(entry, float) and math.isnan(entry)):
                # If your JSON sometimes stores a scalar directly
                val = entry
            d[week] = val

        # 👇 This line is currently missing
        pivots.append(d)

    pivot_df = pd.DataFrame(pivots, columns=["Field", "Value", *weeks])

    block = pd.concat([meta, pivot_df, blank], ignore_index=True)
    # No StudentID column in the export — keep positional A/B/C.. layout
    return block


def _build_dataframe_for_franchise(df: pd.DataFrame) -> pd.DataFrame:
    all_weeks: set[str] = set()
    for wd_json in df["WeeklyData"]:
        all_weeks.update(json.loads(wd_json).keys())
    weeks = sorted(all_weeks)  # Monday-anchored keys sort fine as YYYY-MM-DD

    # Prepend top rows:
    #  - A1 blank
    #  - B2..B6 ledger (row index 1..5)
    #  - row index 2 carries C.. week labels
    legend = _build_legend_rows(weeks)

    frames = [_build_student_block(row, weeks) for _, row in df.iterrows()]
    return pd.concat([legend, *frames], ignore_index=True)


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

        # ─────────────── Debug: Local Excel Export ───────────────
        # Uncomment these lines to debug without uploading to Google Sheets
        #excel_path = ROOT / f"debug_BMaster_F{fid}.xlsx"
        #export_df.to_excel(excel_path, index=False, header=False)
        #print(f"  ✓ Exported locally to {excel_path}")
        #continue  # Skip Google Sheets upload during local debugging
        # ─────────────────────────────────────────────────────────

        # ─────────────── Debug Option: Turn off dataframe push ───
        # Comment these lines out While testing local ONLY
        _push_dataframe(sheet_map[fid], export_df)
        print("  ✓ Uploaded")
        # ─────────────────────────────────────────────────────────

    conn.close()



if __name__ == "__main__":
    main()