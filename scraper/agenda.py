import asyncio
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Playwright, BrowserContext

from scraper.portals.utils import get_portal_key_from_url
from scraper.runner import get_students_from_db, filter_students, get_portal

load_dotenv()
async def fetch_agenda(ctx: BrowserContext, student: dict) -> dict:
    page = await ctx.new_page()
    grade_portal = student.get("portal")
    if grade_portal == 'canvas':
        login_url = student.get("login_url")
        sid = student.get("id")
        password = student.get("password")
    else:
        login_url = student.get("alt_login_url")
        sid = student.get("alt_id")
        password = student.get("alt_password")

    portal = get_portal_key_from_url(login_url)

    Engine = get_portal(portal)
    scraper = Engine(
        page,
        sid,
        password,
        alt_student_id=student["alt_id"],
        alt_password=student["alt_password"],
        login_url=login_url, # use the alternate as this should be where class info resides
        alt_portal_url=student.get("alt_login_url"),
        student_name=student.get("student_name"),
    )

    # Only GPS uses pictograph answers
    if student.get("auth_images") and student["portal"] == "gps":
        setattr(scraper, "auth_images", student["auth_images"])
    agenda = {}
    try:
        print(f"Starting login for {student['id']}", flush=True)
        try:
            await scraper.login(first_name=student.get("student_name"))
            agenda = await scraper.get_agenda()
        except:
            print(f"[RUNNER] Invalid credentials for ID={student['db_id']};")

        print(f"Login successful for {student['id']}, collecting agenda…", flush=True)
    finally:
        await page.close()
        return agenda
    

async def main(franchise_id: int | None, student_id: int | None):
    if student_id is not None:
        _students = get_students_from_db(student_id=student_id)
    elif franchise_id is not None:
        _students = get_students_from_db(franchise_id=franchise_id)
    else:
        _students = get_students_from_db()

    students = filter_students(_students, 'track_agenda', True)

    async with async_playwright() as pw:
        browser_args = [
            "--disable-blink-features=AutomationControlled",
        ]
        browser = await pw.chromium.launch(headless=False, args=browser_args)
        context = await browser.new_context()
        context.set_default_timeout(5_000)
        context.set_default_navigation_timeout(5_000)

        tasks = [fetch_agenda(context, student) for student in students]
        results = await asyncio.gather(*tasks)

        for (student, agenda) in zip(students, results):
            agendas[student.get("student_name")] = agenda


import argparse
if __name__ == '__main__':
    # For manual testing i.e. `python -m scraper.agenda -f 57 -s 442`
    parser = argparse.ArgumentParser(description="Collect student agendas.")
    parser.add_argument(
        "-f", "--franchise-id",
        type=int,
        help="Franchise for which to gather agendas."
    )
    parser.add_argument(
        "-s", "--student",
        type=int,
        help="Student for which to fetch agenda."
    )
    args = parser.parse_args()

    agendas = {}
    asyncio.run(main(args.franchise_id, args.student))

    print(agendas)
