#!/usr/bin/env python
"""
update_bmaster_sheets.py  ·  PlaywrightScraper/work_flows

Pulls data from students.db ➜ builds the weekly‑grade dataframe exactly as
specified by Brandon (meta rows, blank rows, pivot rows) ➜ overwrites the
"BMaster" tab of every franchise’s Google Sheet (looked‑up from the
Spreadsheets table).

• Requires gspread>=6 & google‑auth
• Expects service_account.json in repo root (same folder as pyproject.toml)
• No cell formatting is applied (you can add gspread‑formatting later)
"""
from __future__ import annotations

import itertools
import json
import pathlib
import re
import sqlite3
from typing import Dict, List

import pandas as pd
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
    """Return {FranchiseID: spreadsheet_id}. Extracts the id from the URL."""
    cur = conn.cursor()
    cur.execute("SELECT FranchiseID, spreadsheet FROM Spreadsheets")
    mapping: Dict[int, str] = {}
    for fid, url in cur.fetchall():
        m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
        if m:
            mapping[fid] = m.group(1)
    return mapping


def _query_students(conn: sqlite3.Connection) -> pd.DataFrame:
    query = """
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
    return pd.read_sql_query(query, conn)


def _build_student_block(row: pd.Series, weeks: List[str]) -> pd.DataFrame:
    """Return a DataFrame representing one student’s block."""
    password_good = row["PasswordGood"] == 1

    # META rows (column0=Field, column1=Value)
    meta_rows = {
        "Field": [label for label, _ in META_FIELDS],
        "Value": [row[src] for _, src in META_FIELDS],
    }
    df_meta = pd.DataFrame(meta_rows)

    # Two blank rows
    df_blank = pd.DataFrame([{"Field": "", "Value": ""}, {"Field": "", "Value": ""}])

    if not password_good:
        invalid = pd.DataFrame([
            {"Field": "Error", "Value": "invalid entry, check credentials"},
            {"Field": "", "Value": ""},  # preserve spacing
            {"Field": "", "Value": ""},
        ])
        block = pd.concat([df_meta, invalid], ignore_index=True)
        block.insert(0, "StudentID", row["ID"])
        return block

    # Parse weekly JSON once
    weekly_data = json.loads(row["WeeklyData"])
    subjects = sorted(
        set(itertools.chain.from_iterable(week_dict.keys() for week_dict in weekly_data.values()))
    )

    pivot_rows = []
    for idx, subj in enumerate(subjects, start=1):
        d: Dict[str, str | float] = {
            "Field": f"Subject{idx}",
            "Value": subj,
        }
        for week in weeks:
            d[week] = weekly_data.get(week, {}).get(subj, "")
        pivot_rows.append(d)
    df_pivot = pd.DataFrame(pivot_rows, columns=["Field", "Value", *weeks])

    block = pd.concat([df_meta, df_blank, df_pivot], ignore_index=True)
    block.insert(0, "StudentID", row["ID"])
    return block


def _build_dataframe_for_franchise(df_students: pd.DataFrame) -> pd.DataFrame:
    # Collect full list of Monday‑anchor weeks across these students (sorted so cols are stable)
    all_weeks = set()
    for wd_json in df_students["WeeklyData"]:
        all_weeks.update(json.loads(wd_json).keys())
    weeks = sorted(all_weeks)

    blocks = [
        _build_student_block(row, weeks)
        for _, row in df_students.iterrows()
    ]
    return pd.concat(blocks, ignore_index=True)


def _push_dataframe(sheet_id: str, dataframe: pd.DataFrame, worksheet_name: str = "BMaster") -> None:
    creds = Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
    gc = gspread.Client(auth=creds)

    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet_name, rows="100", cols="30")

    # Clean NaN / None so JSON serialization succeeds
    cleaned = dataframe.where(pd.notna(dataframe), "")

    ws.clear()
    # gspread 6: values first, then range_name
    ws.update(cleaned.values.tolist(), "A1", value_input_option="USER_ENTERED")


# ────────────────────────────────────────────────────────────────────────────────
# Main entry

def main() -> None:
    conn = _connect_db()
    sheet_map = _load_sheet_map(conn)

    df_all = _query_students(conn)

    # Partition by FranchiseID
    for fid, grp in df_all.groupby("FranchiseID"):
        if fid not in sheet_map:
            print(f"[SKIP] No sheet configured for FranchiseID {fid}")
            continue

        print(f"Processing FranchiseID {fid}  (students: {len(grp)})")
        df_export = _build_dataframe_for_franchise(grp)
        _push_dataframe(sheet_map[fid], df_export)
        print(f"  ✓ Uploaded to sheet {sheet_map[fid]}")

    conn.close()


if __name__ == "__main__":
    main()
