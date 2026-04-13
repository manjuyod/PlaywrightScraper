import asyncio
from dataclasses import asdict
from scraper.runner import bad_login, _load_student_auth_map, get_students_from_db
from db import get_students, filter_group, db_conn
from scraper.portals import get_portal, LoginError

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

async def verify_login(browser: Browser, student: dict):
    context = await browser.new_context()

    page = await context.new_page()
    page.set_default_timeout(15_000)
    page.set_default_navigation_timeout(15_000)
    try:
        Engine = get_portal(student["portal"])
        scraper = Engine(
            page,
            student["id"],
            student["password"],
            student_name=student.get("name"),
            login_url=student["login_url"],
            alt_portal_url=student.get("alt_login_url")
        )

        # Only GPS uses pictograph answers
        if student.get("auth_images") and student["portal"] == "gps":
            setattr(scraper, "auth_images", student["auth_images"])

        await scraper.login(scraper.student_name)
    finally:
        await page.close()

def good_login(student_id: int):
    """Set PasswordGood=1 for a student in the database."""

    print(f"[verify_bad_logins] good_login(): setting PasswordGood=1 for student ID={student_id}", flush=True)
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE Student SET PasswordGood = 1 WHERE ID = %s",
            (student_id,)
        )
        conn.commit()

import pprint
async def main(sid: int | None = None, portal: str | None = None):
    students: list[dict] = filter_group(filter_group(get_students_from_db(allow_bad_logins=True), key='passwordgood', value=0), key='status', value='error')
    if sid is not None:
        students = [student for student in students if student['db_id'] == sid]
    if portal is not None:
        students = [student for student in students if student['portal'] == portal]
    # students = filter_group(students, key='portal', value='canvas') # temp filter on canvas students
    print(f"[verify_bad_logins] Found {len(students)} students to verify.")
    async with async_playwright() as p:
        browser_args = [
            "--disable-blink-features=AutomationControlled",
        ]
        browser = await p.chromium.launch(headless=False, args=browser_args)
        i = 0
        while i < len(students):
            print(f"[verify_bad_logins] Processing student {i} of {len(students)} (ID={students[i]['id']})...")
            await test_login(browser, students[i])
            i += 1

            # student_batch = students[i:i+5] # process 5 students at a time
            # print(f"[verify_bad_logins] Processing students {i} to {i+len(student_batch)-1}...")
            # i += 5
            # tasks = [test_login(browser, student) for student in student_batch]
            # await asyncio.gather(*tasks)

argparse = __import__("argparse")
if __name__ == "__main__":
    # Run with optional filters: e.g. `python verify_bad_logins.py --sid 123` or `python verify_bad_logins.py --portal canvas`


    parser = argparse.ArgumentParser(description="Verify bad logins for students in the database.")
    parser.add_argument("--sid", "-s", type=int, help="Filter by student ID")
    parser.add_argument("--portal", "-p", type=str, help="Filter by portal name")
    args = parser.parse_args()
    asyncio.run(main(sid=args.sid, portal=args.portal))
