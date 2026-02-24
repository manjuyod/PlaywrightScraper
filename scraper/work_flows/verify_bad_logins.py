import asyncio
from dataclasses import asdict
from scraper.runner import bad_login
from db import get_students, filter_group, db_conn
from scraper.portals import get_portal, LoginError

from playwright.async_api import Browser, async_playwright


async def verify_login(browser: Browser, student: dict):
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
            login_url=student.get("portal1"),
            alt_portal_url=student.get("portal2")
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
async def main():
    students: list[dict] = filter_group(filter_group(get_students(bare=True), key='passwordgood', value=0), key='status', value='error')
    # students = filter_group(students, key='portal', value='canvas') # temp filter on canvas students
    print(f"[verify_bad_logins] Found {len(students)} students to verify.")
    async with async_playwright() as p:
        browser_args = [
            "--disable-blink-features=AutomationControlled",
        ]
        browser = await p.chromium.launch(headless=False, args=browser_args)
        for student in students:
            student = dict(student)
            student['db_id'] = student.pop('id')
            print(f"[verify_bad_logins] Verifying student ID={student['db_id']} PasswordGood={student['passwordgood']}")
            try:
                await verify_login(browser, student)
            except Exception as e:
                print(f"[verify_bad_logins] Error logging in student ID={student['db_id']}: {e}")
                continue
            good_login(int(student['db_id']))

if __name__ == "__main__":
    asyncio.run(main())