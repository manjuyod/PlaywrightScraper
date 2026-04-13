# -*- coding: utf-8 -*-
# builtins
import argparse
import asyncio
import json
import os
import pathlib
import pprint
import queue
import random
import sys
import textwrap
from time import time
from traceback import format_exception_only
from typing import Dict

# db
import psycopg2 as pg
from dotenv import load_dotenv

# external
from playwright.async_api import Browser, Playwright, async_playwright
from psycopg2.extensions import connection
from psycopg2.extras import DictCursor

from scraper.notif import Severity, send_notification_to_slack
from scraper.portals import LoginError, get_portal, managed_portals
from scraper.portals.utils import get_portal_key_from_url

load_dotenv()
print("[runner] module import OK", flush=True)


def _debug_env():
    print("[runner] CWD:", os.getcwd(), flush=True)
    import sys as _sys

    print("[runner] Python:", _sys.executable, _sys.version, flush=True)
    # Show key DB envs (mask password)
    print("[runner] ENV (Prod/Dev):", os.getenv("PYTHON_ENV"), flush=True)
    print("[runner] ENV PGHOST:", os.getenv("PGHOST"), flush=True)
    print("[runner] ENV PGDATABASE:", os.getenv("PGDATABASE"), flush=True)
    print("[runner] ENV PGUSER:", os.getenv("PGUSER"), flush=True)
    print("[runner] ENV PGPORT:", os.getenv("PGPORT"), flush=True)
    print("[runner] ENV (PGPASSWORD set?):", bool(os.getenv("PGPASSWORD")), flush=True)


def db_conn() -> connection:
    print("[runner] db_conn(): creating connection...", flush=True)
    return pg.connect(
        host=os.getenv("PGHOST"),
        database=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
        port=os.getenv("PGPORT"),
        sslmode="require",
    )


def _load_student_auth_map(conn: connection) -> Dict[int, dict]:
    """
    Returns {StudentID -> {"type": AuthType, "answers": list[str]}}
    from student_auth where each row stores JSON or CSV-like in answers.
    """
    cur = conn.cursor()
    cur.execute("SELECT studentid, authtype, answers FROM student_auth")
    out: Dict[int, dict] = {}
    for sid, auth_type, answers_raw in cur.fetchall():
        answers_raw = (answers_raw or "").strip("{}")
        answers = [a.strip('" ').strip() for a in answers_raw.split(",") if a.strip()]
        out[sid] = {"type": auth_type, "answers": answers}
    return out


def get_students_from_db(
    franchise_id: int | None = None,
    student_id: int | None = None,
    portal: str | None = None,
    status: str | None = None,
    allow_bad_logins: bool = False,
):
    """Return a list of student dicts to scrape.

    If student_id is provided, it takes precedence over franchise_id.
    If `portal` is provided, we filter in Python (post auto-detection) so rows with portal=NULL aren't dropped.
    """
    print(
        f"[runner] get_students_from_db(): fid={franchise_id} sid={student_id} portal={portal} status={status}",
        flush=True,
    )
    students_list = []
    try:
        with db_conn() as conn:
            print("[runner] Successfully connected to database.", flush=True)
            cur = conn.cursor(cursor_factory=DictCursor)

            student_auth_map = _load_student_auth_map(conn)

            base = """
                SELECT ID, FirstName, P1Username, P1Password, Portal1, p2username, p2password, portal2, portal,
                       YearStart, YearEnd, PasswordGood, FranchiseID, track_agenda, status
                FROM Student
            """
            conditions = [
                # "PasswordGood = 1",
                "(YearStart IS NULL OR YearStart = '' OR date(YearStart) <= CURRENT_DATE)",
                "(YearEnd IS NULL OR YearEnd = '' OR CURRENT_DATE <= date(YearEnd))",
            ]
            params: list = []

            if not allow_bad_logins:
                conditions.append("PasswordGood = 1")

            # IMPORTANT: do NOT filter by portal in SQL; we filter in Python post-detection.
            if student_id is not None:
                conditions.append("ID = %s")
                params.append(student_id)
            if franchise_id is not None:
                conditions.append("FranchiseID = %s")
                params.append(franchise_id)
            if status is not None:
                conditions.append("status = %s")
                params.append(status)

            query = base + " WHERE " + " AND ".join(conditions)
            print("[runner] SQL:", query, flush=True)
            print("[runner] SQL params:", params, flush=True)
            cur.execute(query, params)

            want_portal = (portal or "").strip().lower() or None

            rows = cur.fetchall()
            print(f"[runner] fetched {len(rows)} Student rows", flush=True)

            for row in rows:
                login_url = row["portal1"]
                portal_raw = row["portal"]  # may be NULL/None
                portal_key = (portal_raw or "").strip().lower()
                if not portal_key:
                    portal_key = get_portal_key_from_url(login_url) or ""
                # print(f"[runner] row ID={row['id']} portal_raw={portal_raw!r} → portal_key={portal_key!r}", flush=True)

                # If CLI asked for a specific portal, enforce it now (post-detection)
                if want_portal and portal_key != want_portal:
                    continue

                # look up auth by StudentID (if present)
                auth = student_auth_map.get(row["id"])
                auth_images = (
                    auth["answers"]
                    if auth and auth["type"] == "gps_pictograph"
                    else None
                )

                if not portal_key:
                    print(
                        f"[WARN] Skipping ID={row['id']}: missing portal (login_url={login_url!r})",
                        flush=True,
                    )
                    bad_login(row["id"])
                    continue

                students_list.append(
                    {
                        "db_id": row["id"],
                        "student_name": row["firstname"],
                        "login_url": login_url,
                        "id": row["p1username"],
                        "password": row["p1password"],
                        "alt_login_url": row["portal2"],
                        "alt_id": row["p2username"],
                        "alt_password": row["p2password"],
                        "portal": portal_key,  # guaranteed non-empty here
                        "auth_images": auth_images,
                        "track_agenda": row["track_agenda"],
                        "status": row["status"],
                        "passwordgood": row["passwordgood"],
                    }
                )

    except pg.Error as e:
        print(f"Database error: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    return students_list


def filter_students(
    _students: list[dict[str, str]], key: str, value
) -> list[dict[str, str]]:
    """
    Filters students dictionary by a particular key - value pair.
    Args:
        _students: dictionary of students to be filtered
        key: key to match
        value: value to match
    Returns:
        The filtered dictionary
    """
    return [
        student
        for student in _students
        if key in student.keys() and value in student.values()
    ]


def students(
    franchise_id: int | None = None,
    student_id: int | None = None,
    portal: str | None = None,
    status: str | None = None,
):
    return get_students_from_db(
        franchise_id=franchise_id, student_id=student_id, portal=portal, status=status
    )


def bad_login(student_id: int):
    """Set PasswordGood=0 for a student in the database."""
    print(
        f"[runner] bad_login(): setting PasswordGood=0 for student ID={student_id}",
        flush=True,
    )
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE Student SET PasswordGood = 0 WHERE ID = %s", (student_id,))
        conn.commit()


async def scrape_one(browser: Browser, student: dict):
    """Scrape a single student using the appropriate portal engine."""
    await asyncio.sleep(random.uniform(0, 1.0))
    # new context prevents cookies from leaking between students
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
        login_url=student["login_url"],
        alt_portal_url=student.get("alt_login_url"),
    )

    # Only GPS uses pictograph answers
    if student.get("auth_images") and student["portal"] == "gps":
        print(f"Setting auth_images for student ID={student['db_id']}: {student['auth_images']}", flush=True)
        setattr(scraper, "auth_images", student["auth_images"])

    try:
        print(f"Starting login for {student['id']}", flush=True)
        try:
            if not scraper.sid or not scraper.pw:  # early check for field population
                raise ValueError(
                    f"Invalid login credentials for ID={student['db_id']};\nMissing username or password"
                )
            await scraper.login(first_name=student.get("student_name"))
        except ValueError:  # no username/password
            bad_login(int(student["db_id"]))
            raise
        except (
            Exception
        ) as e:  # any exception while logging in is considered a bad login
            bad_login(int(student['db_id']))
            print(
                f"[RUNNER] Invalid credentials for ID={student['db_id']}; PasswordGood set to 0"
            )
            raise LoginError(
                f"{e}\nLikely bad username/password for student"
            )  # raise once again so we log the error in the output json
        print(f"Login successful for {student['id']}, fetching grades…", flush=True)

        # post-login
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

        return {"db_id": student["db_id"], "id": student["id"], "parsed_grades": parsed}
    finally:
        await page.close()
        await context.close()


def project_root() -> pathlib.Path:
    reference_file = ".python-version"
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / reference_file).exists():
            return parent
    return pathlib.Path.cwd()


out_dir = pathlib.Path("output/phase1totuples")
out_dir.mkdir(parents=True, exist_ok=True)
out_file = project_root() / out_dir / "grades.jsonl"


async def main(
    franchise_id: int | None = None,
    student_id: int | None = None,
    portal: str | None = None,
    status: str | None = None,
    job_id: str | None = None,
    state_q: queue.Queue | None = None,
):
    print(
        f"[runner] main(): start fid={franchise_id} sid={student_id} portal={portal}",
        flush=True,
    )

    student_list = students(
        franchise_id=franchise_id, student_id=student_id, portal=portal, status=status
    )
    print(f"[runner] main(): fetched {len(student_list)} students", flush=True)

    if not student_list:
        if student_id is not None:
            print(f"No active student found with ID = {student_id}.", flush=True)
        else:
            print("No active students found for the given filters.", flush=True)
        return

    label = (
        f"student_id={student_id}"
        if student_id is not None
        else f"franchise_id={franchise_id}"
        if franchise_id is not None
        else "all active"
    )
    if portal is not None:
        label += f", portal={portal}"
    print(f"Found {len(student_list)} students to scrape ({label}).", flush=True)

    # success_count = 0
    # error_count = 0
    portal_attempted = {portal: 0 for portal in managed_portals.keys()}
    portal_success = {portal: 0 for portal in managed_portals.keys()}
    errors = []
    with open(out_file, "w", encoding="utf-8") as f:
        async with async_playwright() as p:
            begin_time = time()
            browser_args = [
                "--disable-blink-features=AutomationControlled",
            ]
            browser = await p.chromium.launch(headless=False, args=browser_args)
            for student in student_list:
                portal_attempted[student.get("portal")] += 1
                try:
                    print(
                        f"Attempting to scrape {student['id']}... [{sum(portal_attempted.values())} / {len(student_list)}]",
                        flush=True,
                    )
                    result = await scrape_one(browser, student)
                    f.write(json.dumps(result) + "\n")
                    portal_success[student.get("portal")] += 1
                    # success_count += 1

                except Exception as e:
                    if "Connection closed while reading from the driver" not in str(e):
                        error_result = {
                            "db_id": student["db_id"],
                            "student_id": student["id"],
                            "error": f"{type(e).__name__}: {e}",
                            "traceback": format_exception_only(type(e), e),
                        }
                        f.write(json.dumps(error_result) + "\n")
                        errors.append(error_result)
                        print(
                            f"ERROR: {student['id']} (details in grades.jsonl)",
                            flush=True,
                        )

    # Compute summary
    end_time = time()
    time_elapsed = int(end_time - begin_time)
    time_per_student = time_elapsed / max(1, len(student_list))

    portal_success_rates = {
        portal: float(success) / attempted * 100
        for portal, success, attempted in zip(
            portal_attempted.keys(), portal_success.values(), portal_attempted.values()
        )
        if attempted != 0
    }
    low_success_rates = {
        portal: success_rate
        for portal, success_rate in portal_success_rates.items()
        if success_rate < 75
    }

    attempted_count = sum(portal_attempted.values())
    success_count = sum(portal_success.values())
    error_count = attempted_count - success_count

    low_success_rates_summary = pprint.pformat(low_success_rates)
    error_summary = pprint.pformat(errors)
    results_log = f"""
    Scraping complete! {f"Franchise ({franchise_id if franchise_id else 'all'})"} {f"Student ({student_id if student_id else 'all'})"}

    Successfully processed {success_count} / {attempted_count} students in {int(time_elapsed / 60)} minutes {time_elapsed % 60} seconds, at {time_per_student:.2f}s per student

    Low success rates
    ==================

    {low_success_rates_summary if len(low_success_rates) > 0 else "No low success rates encountered"}

    Error summary | Encountered {error_count} errors
    ==============

    {error_summary if error_summary else "Nothing to show"}
    """

    results_log = textwrap.dedent(results_log.strip())
    results_log.replace("'", "")
    results_log.replace("\\n", "")

    if os.getenv("PYTHON_ENV") != "dev" or os.getenv("SLACK_NOTIFY_IN_DEV") == "1":
        severity = Severity.Crit if error_count > 0 else Severity.Info
        try:
            send_notification_to_slack(severity, results_log)
        except Exception as e:
            print(f"[runner] Slack notification failed: {e}", flush=True)

    print(f"\nScraping complete! Results saved to {out_file}", flush=True)
    print(
        f"Successfully processed {success_count} students in {int(time_elapsed / 60)} minutes {time_elapsed % 60} seconds, at {time_per_student:.2f}s per student",
        flush=True,
    )
    print(f"Errors encountered: {error_count}", flush=True)
    print("Script finished.", flush=True)

    print(results_log, flush=True)


if __name__ == "__main__":
    print("[runner] __main__ starting", flush=True)
    _debug_env()
    parser = argparse.ArgumentParser(description="Scrape student grades.")
    parser.add_argument(
        "-f",
        "--franchise-id",
        type=int,
        help="Only scrape students for a specific FranchiseID.",
    )
    parser.add_argument(
        "-s",
        "--student-id",
        type=int,
        help="Scrape a single student by database ID. Takes precedence over --franchise-id.",
    )
    parser.add_argument(
        "-p", "--portal", type=str, help="Test a single portal by name."
    )
    parser.add_argument(
        "-stat", "--status", type=str, help="Filter for the status of students."
    )
    args = parser.parse_args()
    print("[runner] CLI args:", args, flush=True)

    try:
        asyncio.run(
            main(
                franchise_id=args.franchise_id,
                student_id=args.student_id,
                portal=args.portal,
                status=args.status,
            )
        )
    except Exception as e:
        import traceback as _tb

        print("[runner] FATAL EXCEPTION:", repr(e), flush=True)
        _tb.print_exc()
        raise
