import argparse
import asyncio
from scraper.runner import _load_student_auth_map
from db import filter_group, db_conn
import db

from scraper.portals import get_portal

# IMPORTANT:
# Import runner so the same environment/bootstrap side effects happen
# as in the workflows that already work.
import scraper.runner  # noqa: F401
_ = scraper.runner  # silence unused import warning
from playwright.async_api import Browser, async_playwright

async def test_login(browser: Browser, student: dict):
    student = dict(student)
    print(f"[verify_bad_logins] Verifying student ID={student['db_id']} PasswordGood={student['passwordgood']}")
    try:
        await verify_login(browser, student)
    except Exception as e:
        print(f"[verify_bad_logins] Error logging in student ID={student['id']}: {e}")
        return
    good_login(int(student['db_id']))

async def verify_login(browser: Browser, student: dict) -> bool:
    context = await browser.new_context()
    page = await context.new_page()
    page.set_default_timeout(15_000)
    page.set_default_navigation_timeout(15_000)

    try:
        Engine = get_portal(student["portal"])
        scraper = Engine(
            page,
            student["p1username"],
            student["p1password"],
            student_name=student.get("firstname"),
            login_url=student["portal1"],
            alt_portal_url=student.get("portal2"),
        )

        # Only GPS uses pictograph answers
        if student.get("auth_images") and student["portal"] == "gps":
            setattr(scraper, "auth_images", student["auth_images"])

        await scraper.login(scraper.student_name)
        return True

    except Exception as e:
        print(
            f"[verify_bad_logins] Error logging in student ID={student['db_id']}: {type(e)}: {e.args}",
            flush=True,
        )
        return False

    finally:
        await page.close()
        await context.close()


def good_login(student_id: int):
    """Set PasswordGood=1 for a student in the database."""
    print(
        f"[verify_bad_logins] good_login(): setting PasswordGood=1 for student ID={student_id}",
        flush=True,
    )
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE Student SET PasswordGood = 1 WHERE ID = %s",
            (student_id,),
        )
        conn.commit()


def parse_args():
    parser = argparse.ArgumentParser(description="Verify bad student logins.")
    parser.add_argument(
        "--franchise-id",
        type=int,
        required=False,
        default=None,
        help="Franchise ID to filter students by.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=True,
        help="Run browser in headed mode.",
    )
    return parser.parse_args()


async def main(debug: bool, franchise_id: int | None = None):
    print(
        f"[verify_bad_logins] main(): franchise_id={franchise_id}, debug={debug}",
        flush=True,
    )
    
    if franchise_id is not None:
        students = db.get_students(franchise_id, raw=True)
    else: students = db.get_students(raw=True)
    
    students = filter_group(students, key="passwordgood", value=0)
    students = filter_group(students, key="status", value="error")

    print(f"[verify_bad_logins] Found {len(students)} students to verify.", flush=True)
    auth_map = None
    if len(filter_group(students, key="portal", value="gps")) > 0:
        with db_conn() as conn:
            auth_map = _load_student_auth_map(conn)
    
    async with async_playwright() as p:
        browser_args = [
            "--disable-blink-features=AutomationControlled",
        ]
        browser = await p.chromium.launch(
            headless=not debug,
            args=browser_args,
        )

        try:
            for student in students:
                student = dict(student)
                if student["portal"] == "gps":
                    assert auth_map is not None, "Auth map is required for GPS students but was not loaded."
                    student["auth_images"] = auth_map[student["id"]]["answers"]
                    
                student["db_id"] = student.pop("id")
                print(
                    f"[verify_bad_logins] Verifying student ID={student['db_id']} "
                    f"PasswordGood={student['passwordgood']}",
                    flush=True,
                )

                success = await verify_login(browser, student)

                if success:
                    print(
                        f"[verify_bad_logins] Successful login for student ID={student['db_id']}",
                        flush=True,
                    )
                    good_login(int(student["db_id"]))
                    student["passwordgood"] = 1
        finally:
            await browser.close()

if __name__ == "__main__":
    args = parse_args()
    print(
        f"[verify_bad_logins] Parsed args: franchise_id={args.franchise_id}, debug={args.debug}",
        flush=True,
    )
    asyncio.run(main(franchise_id=args.franchise_id, debug=args.debug))
