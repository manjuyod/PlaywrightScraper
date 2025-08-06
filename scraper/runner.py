# -*- coding: utf-8 -*-
"""
Updated runner script for scraping Infinite Campus and other portals.

This file is based on ``scraper/runner.py`` from the PlaywrightScraper
repository.  The modifications introduce longer default timeouts for
Playwright pages and add comments to explain the scraping flow.  Use this
version in place of the original runner when testing the updated
Infinite Campus engine.
"""

import asyncio
import argparse
import json
import pathlib
import random
import sqlite3
import sys
from traceback import format_exception_only

from playwright.async_api import async_playwright  # type: ignore
from scraper.portals import get_portal  # type: ignore

# Path to the SQLite database storing student credentials and metadata
DB_PATH = pathlib.Path(__file__).parent.parent / "config" / "students.db"


def get_students_from_db(franchise_id: int | None = None):
    """Return a list of student dicts that should be scraped.

    Students are selected if they are active on the current date.  Each
    returned dict contains the database primary key (db_id), first name,
    login username (id), password and the portal engine key.
    """
    students_list = []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

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
            if franchise_id is not None:
                conditions.append("FranchiseID = ?")
                params.append(franchise_id)
            query = base + " WHERE " + " AND ".join(conditions)
            cur.execute(query, params)
            for row in cur.fetchall():
                students_list.append(
                    {
                        "db_id": row["ID"],
                        "student_name": row["FirstName"],
                        "id": row["P1Username"],
                        "password": row["P1Password"],
                        "portal": row["portal"],
                    }
                )
    except sqlite3.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)
    return students_list


def students(franchise_id: int | None = None):
    return get_students_from_db(franchise_id)


async def scrape_one(pw, student: dict):
    """Scrape a single student using the appropriate portal engine.

    A small random delay is introduced before each scrape to avoid
    triggering rate limits on the target site.  The Playwright context
    is configured with extended timeouts to accommodate slow page loads.
    """
    # Tiny jitter so requests don't align perfectly across multiple students
    await asyncio.sleep(random.uniform(0, 1.0))
    browser_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-web-security",
        "--disable-features=VizDisplayCompositor",
    ]
    browser = await pw.chromium.launch(headless=False, args=browser_args)
    context = await browser.new_context()
    page = await context.new_page()
    # Increase default timeouts to handle slow network/UI transitions
    page.set_default_timeout(15_000)  # 15 seconds for element operations
    page.set_default_navigation_timeout(15_000)  # 15 seconds for navigations
    Engine = get_portal(student["portal"])
    scraper = Engine(
        page,
        student["id"],
        student["password"],
        student_name=student.get("student_name"),
    )
    try:
        print(f"Starting login for {student['id']}")
        await scraper.login(first_name=student.get("student_name"))
        print(f"Login successful for {student['id']}, fetching grades…")
        grades = await scraper.fetch_grades()
        # Engines may return dict or list
        if isinstance(grades, dict) and "raw_html" in grades:
            out_dir = pathlib.Path("output/phase1totuples")
            out_dir.mkdir(parents=True, exist_ok=True)
            html_file = out_dir / f"{student['id']}_grades.html"
            html_file.write_text(grades["raw_html"], encoding="utf-8")
            grades["file"] = str(html_file)
        return {"db_id": student["db_id"], "id": student["id"], "parsed_grades": grades}
    finally:
        await browser.close()


async def main(franchise_id: int | None = None):
    """Entry point for running the scraper over multiple students."""
    out_dir = pathlib.Path("output/phase1totuples")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "grades.jsonl"
    success_count = 0
    error_count = 0
    student_list = students(franchise_id)
    print(f"Found {len(student_list)} students to scrape.")
    with open(out_file, "w", encoding="utf-8") as f:
        async with async_playwright() as p:
            for student in student_list:
                try:
                    result = await scrape_one(p, student)
                    f.write(json.dumps(result) + "\n")
                    success_count += 1
                    print(f"SUCCESS: {student['id']}")
                except Exception as e:
                    error_result = {
                        "student_id": student["id"],
                        "error": f"{type(e).__name__}: {e}",
                        "traceback": format_exception_only(type(e), e),
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
        "-f",
        "--franchise-id",
        type=int,
        help="Only scrape students for a specific FranchiseID.",
    )
    args = parser.parse_args()
    asyncio.run(main(franchise_id=args.franchise_id))