#!/usr/bin/env python
"""
update_sheets.py · PlaywrightScraper/work_flows

Build 4 tabs per franchise:
  • LoginMaster  – flat login control (one row per student)
  • HS           – grade blocks for HS/College students (PasswordGood=1)
  • MS           – grade blocks for MS students (PasswordGood=1)
  • Error        – grade blocks for students with PasswordGood=0

Notes
–––––
• Reuses the BMaster-style layout for HS/MS/Error: legend rows + meta → spacer → subject rows with week columns.
• Weeks are computed per franchise (union of that franchise’s student weeks) so the columns line up across HS/MS/Error.
• Writes local Excel (one file per franchise) with four sheets. Google Sheets upload is included but commented out.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
import sqlite3
from typing import Dict, List

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

import time
from functools import wraps
from gspread.exceptions import APIError

# ────────────────────────────────────────────────────────────────────────────────
# Paths & constants
ROOT = pathlib.Path(__file__).resolve().parents[2]  # repo root
DB_PATH = ROOT / "config" / "students.db"
KEY_PATH = ROOT / "sheet_mod_grades.json"  # change if your key has a different name
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

# HS/MS detection helpers
HS_TOKENS = {"9", "9th", "10", "10th", "11", "11th", "12", "12th", "college"}
HS_WORDS = {"freshman", "sophomore", "junior", "senior"}

# tune as needed
PER_TAB_SLEEP_SEC = 0.75          # small pause between tab writes
MAX_RETRIES = 6                   # total attempts per call
BACKOFF_BASE_SEC = 1.0            # starting backoff
BACKOFF_MULTIPLIER = 1.8          # growth factor

# ────────────────────────────────────────────────────────────────────────────────
# Helpers

def _retry_on_rate_limit(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        delay = BACKOFF_BASE_SEC
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except APIError as e:
                # Only back off on 429s
                if "429" in str(e):
                    if attempt == MAX_RETRIES:
                        raise
                    time.sleep(delay)
                    delay *= BACKOFF_MULTIPLIER
                else:
                    raise
    return wrapper

def _connect_db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _load_sheet_map(conn: sqlite3.Connection) -> Dict[int, str]:
    """Return {FranchiseID: <spreadsheet-id>} extracted from URL."""
    cur = conn.cursor()
    cur.execute("SELECT FranchiseID, spreadsheet FROM Spreadsheets")
    mapping: Dict[int, str] = {}
    for fid, url in cur.fetchall():
        m = re.search(r"/d/([A-Za-z0-9_-]+)", url or "")
        if m:
            mapping[fid] = m.group(1)
    return mapping


def _query_students(conn: sqlite3.Connection) -> pd.DataFrame:
    """Return a DataFrame with student rows + WeeklyData used for grade blocks."""
    sql = """
        SELECT
            ID,
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
        FROM Student
        ORDER BY FirstName ASC, Grade ASC
    """
    return pd.read_sql_query(sql, conn)


def _query_login_master(conn: sqlite3.Connection) -> pd.DataFrame:
    """Raw login control data for LoginMaster tab."""
    sql = """
        SELECT
            ID,
            FranchiseID,
            FirstName,
            LastName,
            Grade,
            Portal1,
            P1Username,
            P1Password,
            Portal2,
            P2Username,
            P2Password,
            PasswordGood
        FROM Student
        ORDER BY LastName ASC, FirstName ASC
    """
    return pd.read_sql_query(sql, conn)


def _is_hs_grade(grade: str | None) -> bool:
    if grade is None:
        return False
    s = str(grade).strip().lower()
    if any(w in s for w in HS_WORDS) or "college" in s:
        return True
    m = re.search(r"\b(\d{1,2})\b", s)
    if m:
        n = int(m.group(1))
        return n >= 9  # 9–12 treated as HS
    if any(tok in s for tok in HS_TOKENS):
        return True
    return False


def _safe_json_loads(s: str | None) -> dict:
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _coerce_str(x) -> str:
    return "" if x is None or (isinstance(x, float) and math.isnan(x)) else str(x)


def _extract_subjects(wdata: dict) -> List[str]:
    """Union of subject names across weeks for ONE student."""
    names = set()
    for week_payload in wdata.values():
        if isinstance(week_payload, dict):
            names.update(week_payload.keys())
    return sorted(names)

def _load_known_franchise_ids(conn: sqlite3.Connection) -> set[int]:
    """
    Only allow FranchiseIDs that are present in Spreadsheets.
    (Optionally require a non-empty spreadsheet URL.)
    """
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT DISTINCT FranchiseID
            FROM Spreadsheets
            WHERE FranchiseID IS NOT NULL
              AND COALESCE(spreadsheet, '') <> ''   -- comment out this line if URL shouldn't be required
        """)
        return {int(r[0]) for r in cur.fetchall() if r[0] is not None}
    except sqlite3.Error as e:
        print(f"[WARN] Could not load FranchiseIDs from Spreadsheets: {e}")
        return set()
    
# ────────────────────────────────────────────────────────────────────────────────
# Data-frame builders (legend + per-student blocks)

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
    return pd.DataFrame(rows, columns=["Field", "Value", *weeks])


def _build_student_block(row: pd.Series, weeks_all: List[str]) -> pd.DataFrame:
    """
    Return one student’s block:
     meta rows (A/B only) → two blanks → subject rows with week columns.
    We align each student’s block to the franchise-wide weeks after computing per-student data.
    """
    good_pw = row["PasswordGood"] == 1

    # 1) meta rows (explicit strings, no NaN bleed)
    meta_rows = [{"Field": lbl, "Value": _coerce_str(row[src])} for lbl, src in META_FIELDS]
    meta = pd.DataFrame(meta_rows, columns=["Field", "Value"])

    # 2) two blank spacer rows
    blank = pd.DataFrame([{"Field": "", "Value": ""}] * 2, columns=["Field", "Value"])

    if not good_pw:
        err = pd.DataFrame(
            [
                {"Field": "Error", "Value": "invalid entry, check credentials"},
                {"Field": "", "Value": ""},
                {"Field": "", "Value": ""},
            ],
            columns=["Field", "Value"],
        )
        return pd.concat([meta, err], ignore_index=True)

    # 3) subject → grades for this student only
    wdata = _safe_json_loads(row["WeeklyData"])
    subjects = _extract_subjects(wdata)
    weeks_for_student = sorted([w for w in wdata.keys() if w in weeks_all])

    pivots: List[dict] = []
    for idx, subj in enumerate(subjects, start=1):
        d: dict[str, str | float] = {"Field": f"Subject{idx}", "Value": subj}
        for w in weeks_for_student:
            entry = wdata.get(w, {}).get(subj)
            val: str | float = ""
            if isinstance(entry, dict):
                pct = entry.get("percentage")
                if pct is not None and not (isinstance(pct, float) and math.isnan(pct)):
                    val = pct
                else:
                    lg = entry.get("letter_grade")
                    val = _coerce_str(lg) if lg is not None else ""
            elif entry is not None and not (isinstance(entry, float) and math.isnan(entry)):
                val = entry  # scalar fallback
            d[w] = val
        pivots.append(d)

    pivot_df = pd.DataFrame(pivots) if pivots else pd.DataFrame(columns=["Field", "Value"])
    if pivot_df.empty:
        pivot_df = pd.DataFrame(columns=["Field", "Value"])
    if "Field" not in pivot_df.columns:
        pivot_df["Field"] = []
    if "Value" not in pivot_df.columns:
        pivot_df["Value"] = []

    # Reindex to franchise-wide weeks so columns line up across students
    pivot_df = pivot_df.reindex(columns=["Field", "Value", *weeks_all], fill_value="")

    # Final block for this student
    return pd.concat([meta, pivot_df, blank], ignore_index=True)


def _collect_weeks(df: pd.DataFrame) -> List[str]:
    weeks: set[str] = set()
    for wd_json in df["WeeklyData"]:
        try:
            weeks.update(json.loads(wd_json).keys())
        except Exception:
            pass
    return sorted(weeks)


def _build_dataframe_for_group(df_sub: pd.DataFrame, weeks: List[str]) -> pd.DataFrame:
    """Legend + all student blocks in df_sub, aligned to the provided weeks."""
    legend = _build_legend_rows(weeks)
    if df_sub.empty:
        note = pd.DataFrame([{"Field": "", "Value": "(no students)"}])
        return pd.concat([legend, note], ignore_index=True)
    frames = [_build_student_block(row, weeks) for _, row in df_sub.iterrows()]
    return pd.concat([legend, *frames], ignore_index=True)


# ────────────────────────────────────────────────────────────────────────────────
# Exporters

def _export_excel_multi(frames: dict[str, tuple[pd.DataFrame, bool]], path: pathlib.Path) -> None:
    """
    frames: {sheet_name: (df, header_bool)}
      • For grade-block sheets (HS/MS/Error) use header=False.
      • For LoginMaster use header=True.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, (df, header_flag) in frames.items():
            clean = df.where(pd.notna(df), "")
            clean.to_excel(writer, sheet_name=name, index=False, header=header_flag)


def _ensure_ws(sh, title: str, rows: int = 2000, cols: int = 100):
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=str(rows), cols=str(cols))

@_retry_on_rate_limit
def _ws_clear(ws):
    return ws.clear()

@_retry_on_rate_limit
def _ws_update(ws, values):
    return ws.update(values, "A1", value_input_option="USER_ENTERED")

def _push_multi_sheets(sheet_id: str, frames: dict[str, tuple[pd.DataFrame, bool]]) -> None:
    creds = Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
    gc = gspread.Client(auth=creds)
    sh = gc.open_by_key(sheet_id)

    for idx, (title, (df, header_flag)) in enumerate(frames.items(), start=1):
        ws = _ensure_ws(sh, title)

        # Convert DF once
        clean = df.where(pd.notna(df), "")
        values = clean.values.tolist()
        if header_flag:
            values = [list(clean.columns)] + values

        # Instead of full clear+full update, you can do ranged update;
        # but if you want to keep clear(), keep it with backoff:
        _ws_clear(ws)
        _ws_update(ws, values)

        # small pacing between tabs to avoid per-minute spikes
        time.sleep(PER_TAB_SLEEP_SEC)

def _parse_args():
    import argparse
    p = argparse.ArgumentParser(description="Push grade/login tabs to Google Sheets per franchise.")
    p.add_argument("--franchise-id", "--fid", type=int, default=None,
                   help="Only process this FranchiseID. If omitted, process all known franchises.")
    return p.parse_args()

# ────────────────────────────────────────────────────────────────────────────────
# Main entry point

def main() -> None:
    #ArgParse First
    args = _parse_args()
    
    conn = _connect_db()
    sheet_map = _load_sheet_map(conn)
    df_all = _query_students(conn)       # has WeeklyData for grade blocks
    df_login_all = _query_login_master(conn)  # flat login rows
    known_fids = _load_known_franchise_ids(conn)

    # NEW: compute target set (either the single requested ID, or all known)
    if args.franchise_id is not None:
        target_fids = {args.franchise_id} & known_fids
        if not target_fids:
            print(f"[SKIP] FranchiseID {args.franchise_id} not in known set; nothing to do.")
            conn.close()
            return
    else:
        target_fids = known_fids
    
    # hard gate: only keep rows for franchises that exist
    df_all = df_all[df_all["FranchiseID"].isin(target_fids)].copy()
    df_login_all = df_login_all[df_login_all["FranchiseID"].isin(target_fids)].copy()
    
    for fid, grp in df_all.groupby("FranchiseID"):
        if fid not in known_fids:
            print(f"[SKIP] FranchiseID {fid} not found in Franchise table; no workbook/Sheet created.")
            continue

        print(f"Processing FranchiseID {fid} (students: {len(grp)})")

        # Canonical weeks list for this franchise
        weeks = _collect_weeks(grp)

        # ---- LoginMaster (flat)
        login_master = (
            df_login_all[df_login_all["FranchiseID"] == fid]
            .loc[:, [
                "FirstName", "LastName", "Grade",
                "Portal1", "P1Username", "P1Password",
                "Portal2", "P2Username", "P2Password", "PasswordGood"
            ]]
            .sort_values(["LastName", "FirstName"])
            .reset_index(drop=True)
        )

        # Build ID sets for HS / MS / Error from LoginMaster (authoritative for Grade/PasswordGood)
        login_f = df_login_all[df_login_all["FranchiseID"] == fid].copy()
        good = login_f["PasswordGood"] == 1
        is_hs = login_f["Grade"].apply(_is_hs_grade)

        hs_ids  = set(login_f.loc[good & is_hs,  "ID"].tolist())
        ms_ids  = set(login_f.loc[good & ~is_hs, "ID"].tolist())
        err_ids = set(login_f.loc[~good,         "ID"].tolist())

        # Slice the grade-pivot source to those students
        grp_hs  = grp[grp["ID"].isin(hs_ids)]
        grp_ms  = grp[grp["ID"].isin(ms_ids)]
        grp_err = grp[grp["ID"].isin(err_ids)]

        # Build the 3 grade tabs using the SAME weeks list
        hs_df  = _build_dataframe_for_group(grp_hs,  weeks)
        ms_df  = _build_dataframe_for_group(grp_ms,  weeks)
        err_df = _build_dataframe_for_group(grp_err, weeks)

        # Assemble sheets with header flags
        frames = {
            "LoginMaster": (login_master, True),  # header row visible
            "HS":          (hs_df, False),        # BMaster-style (no header)
            "MS":          (ms_df, False),
            "Error":       (err_df, False),
        }

        # ---- Local Excel (debug)
        #out_path = ROOT / f"debug_Franchise_{fid}.xlsx"
        #_export_excel_multi(
        #    {
        #        "LoginMaster": (login_master, True),
        #        "HS": (hs_df, False),
        #        "MS": (ms_df, False),
        #        "Error": (err_df, False),
        #    },
        #    out_path,
        #)
        #print(f"  ✓ Wrote {out_path}")

        # (Optional) push to Google Sheets only if you also have a mapping:
        if fid in sheet_map:
            _push_multi_sheets(sheet_map[fid], {
                "LoginMaster": (login_master, True),
                "HS": (hs_df, False),
                "MS": (ms_df, False),
                "Error": (err_df, False),
            })
            print(f"  ✓ Uploaded to spreadsheet {sheet_map[fid]}")
        else:
            print(f"  [SKIP] No spreadsheet configured for FranchiseID {fid}")

    conn.close()

if __name__ == "__main__":
    main()
