# -*- coding: utf-8 -*-
import asyncio
import argparse
import json
import re
import pathlib
import random
import sqlite3
import sys
from traceback import format_exception_only
from playwright.async_api import async_playwright
from scraper.portals import get_portal, LoginError
from typing import Dict, List

DB_PATH = pathlib.Path(__file__).parent.parent / "config" / "students.db"

def _load_student_auth_map(conn: sqlite3.Connection) -> Dict[int, dict]:
    """
    Returns {StudentID -> {"type": AuthType, "answers": list[str]}}
    from student_auth where each row stores JSON in Answers.
    """
    cur = conn.cursor()
    cur.execute("SELECT StudentID, AuthType, Answers FROM student_auth")  # one row per student
    out: Dict[int, dict] = {}
    for sid, auth_type, answers_raw in cur.fetchall():
        try:
            answers = json.loads(answers_raw)
            if not isinstance(answers, list):
                raise ValueError("Answers JSON must be a list")
        except Exception:
            # fallback if someone stored CSV
            answers = [p.strip() for p in str(answers_raw).split(",") if p.strip()]
        out[sid] = {"type": auth_type, "answers": answers}
    return out

def get_students_from_db(franchise_id: int | None = None, student_id: int | None = None):
    """Return a list of student dicts to scrape.

    If student_id is provided, it takes precedence over franchise_id (and returns at most one row).
    """
    students_list = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # load the picture map once per connection
            student_auth_map = _load_student_auth_map(conn)

            base = """
                SELECT ID, FirstName, P1Username, P1Password, portal, YearStart, YearEnd
                FROM Student
            """
            conditions = [
                "PasswordGood = 1",
                "(YearStart IS NULL OR date(YearStart) <= date('now'))",
                "(YearEnd   IS NULL OR date('now') <= date(YearEnd))",
            ]
            params: list = []

            if student_id is not None:
                conditions.append("ID = ?")
                params.append(student_id)
            elif franchise_id is not None:
                conditions.append("FranchiseID = ?")
                params.append(franchise_id)

            query = base + " WHERE " + " AND ".join(conditions)
            cur.execute(query, params)

            for row in cur.fetchall():
                # look up auth by StudentID (if present)
                auth = student_auth_map.get(row["ID"])  # -> {"type": "...", "answers": [...] } or None
                auth_images = auth["answers"] if auth and auth["type"] == "gps_pictograph" else None

                students_list.append(
                    {
                        "db_id": row["ID"],
                        "student_name": row["FirstName"],
                        "id": row["P1Username"],
                        "password": row["P1Password"],
                        "portal": row["portal"],
                        "auth_images": auth_images,  # only set when gps pictograph
                    }
                )

    except sqlite3.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)

    return students_list

def students(franchise_id: int | None = None, student_id: int | None = None):
    return get_students_from_db(franchise_id=franchise_id, student_id=student_id)


async def scrape_one(pw, student: dict):
    """Scrape a single student using the appropriate portal engine."""
    await asyncio.sleep(random.uniform(0, 1.0))
    browser_args = [
    #    "--no-sandbox",
    #    "--disable-dev-shm-usage",
        "--disable-gpu",
    #    "--disable-web-security",
    #    "--disable-features=VizDisplayCompositor",
    ]
    browser = await pw.chromium.launch(headless=False, args=browser_args)
    context = await browser.new_context()
    page = await context.new_page()
    page.set_default_timeout(15_000)
    page.set_default_navigation_timeout(15_000)

    Engine = get_portal(student["portal"])
    scraper = Engine(
        page,
        student["id"],
        student["password"],
        student_name=student.get("student_name"),
    )

    # Only GPS uses pictograph answers
    if student.get("auth_images") and student["portal"] == "gps":
        # safer than passing via __init__
        setattr(scraper, "auth_images", student["auth_images"])

    try:
        print(f"Starting login for {student['id']}")
        try:
            await scraper.login(first_name=student.get("student_name"))
        except Exception as e:
            raise LoginError(e)
        print(f"Login successful for {student['id']}, fetching grades…")
        grades = await scraper.fetch_grades()

        # Normalize payload so we always write top-level "parsed_grades"
        if isinstance(grades, dict) and "parsed_grades" in grades:
            parsed = grades["parsed_grades"]
        else:
            parsed = grades  # already a dict or list per engine

        # Optional raw_html handling if an engine returns it
        if isinstance(grades, dict) and "raw_html" in grades:
            out_dir = pathlib.Path("output/phase1totuples")
            out_dir.mkdir(parents=True, exist_ok=True)
            html_file = out_dir / f"{student['id']}_grades.html"
            html_file.write_text(grades["raw_html"], encoding="utf-8")
            # not included in final payload by design, but keeps debug artifact
        return {"db_id": student["db_id"], "id": student["id"], "parsed_grades": parsed}

    finally:
        await browser.close()


async def main(franchise_id: int | None = None, student_id: int | None = None):
    """Entry point for running the scraper over multiple students."""
    out_dir = pathlib.Path("output/phase1totuples")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "grades.jsonl"

    student_list = students(franchise_id=franchise_id, student_id=student_id)
    if not student_list:
        if student_id is not None:
            print(f"No active student found with ID = {student_id}.")
        else:
            print("No active students found for the given filters.")
        return

    label = f"student_id={student_id}" if student_id is not None else f"franchise_id={franchise_id}" if franchise_id is not None else "all active"
    print(f"Found {len(student_list)} students to scrape ({label}).")

    success_count = 0
    error_count = 0
    with open(out_file, "w", encoding="utf-8") as f:
        async with async_playwright() as p:
            for student in student_list:
                try:
                    result = await scrape_one(p, student)
                    f.write(json.dumps(result) + "\n")
                    success_count += 1
                    print(f"SUCCESS: {student['id']}")
                except Exception as e:
                    # mark this student’s password as bad
                    if isinstance(e, LoginError):
                        with sqlite3.connect(DB_PATH) as conn:
                            cur = conn.cursor()
                            cur.execute(
                                "UPDATE Student SET PasswordGood = 0 WHERE ID = ?",
                                (student["db_id"],)
                            )
                            conn.commit()
                        print(f"[RUNNER] Invalid credentials for ID={student['db_id']}; PasswordGood set to 0")
                    # record the error in the JSONL for auditing
                    error_result = {
                        "student_id": student["id"],
                        "error": f"{type(e).__name__}: {e}",
                        "traceback": format_exception_only(type(e), e)
                    }
                    f.write(json.dumps(error_result) + "\n")
                    error_count += 1
                    print(f"ERROR: {student['id']} (details in grades.jsonl)")
    print(f"\nScraping complete! Results saved to {out_file}")
    print(f"Successfully processed {success_count} students")
    print(f"Errors encountered: {error_count}")
    print("Script finished.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape student grades.")
    parser.add_argument(
        "-f", "--franchise-id",
        type=int,
        help="Only scrape students for a specific FranchiseID."
    )
    parser.add_argument(
        "-s", "--student-id",
        type=int,
        help="Scrape a single student by database ID. Takes precedence over --franchise-id."
    )
    args = parser.parse_args()
    asyncio.run(main(franchise_id=args.franchise_id, student_id=args.student_id))
