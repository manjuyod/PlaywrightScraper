import asyncio
import queue
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Playwright, BrowserContext

from scraper.portals.utils import get_portal_key_from_url
from scraper.runner import get_students_from_db, get_portal, db_conn
from db import filter_group
import json

load_dotenv()
async def fetch_agenda(ctx: BrowserContext, student: dict) -> dict:
    # logins for canvas and google classroom are stored in the alternate fields, unless the students main login is canvas
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
        login_url=login_url,
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
            print(f"Login successful for {student['id']}, collecting agenda…", flush=True)
            return await scraper.get_agenda() # this method should only exist on Canvas and Google Classroom
        except:
            return {}
    finally:
        await page.close()


async def main(
    franchise_id: int | None,
    student_id: int | None,
    job_id: str | None = None,
    state_q: queue.Queue | None = None,
):
    _students = get_students_from_db(student_id=student_id, franchise_id=franchise_id)
    students = filter_group(_students, 'track_agenda', True)

    if job_id and state_q:
        from ui.ext_jobs import JobState
        state = JobState(total=len(students), steps=len(students) + 2)
        state.next_step()
        state_q.put((job_id, state))
    else:
        state = None

    async with async_playwright() as pw:
        browser_args = [
            "--disable-blink-features=AutomationControlled",
        ]
        browser = await pw.chromium.launch(headless=False, args=browser_args)
        context = await browser.new_context()
        context.set_default_timeout(5_000)
        context.set_default_navigation_timeout(5_000)

        tasks = {
            asyncio.create_task(fetch_agenda(context, student)): student
            for student in students
        }

        for task in asyncio.as_completed(tasks):
            student = tasks[task]
            try:
                agenda = await task
            except Exception:
                agenda = {}
            print(f"Agenda collected for student {student['student_name']}: {agenda}")
            # add the agenda to the student in the database
            with db_conn() as conn:
                cur = conn.cursor()
                cur.execute("UPDATE Student SET weekly_agenda = %s WHERE ID = %s", (json.dumps(agenda), student["db_id"]))
            print(f"Agenda saved for student {student['student_name']}")

            if state:
                state.next_step()
                state_q.put((job_id, state))

        if state:
            state.next_step()
            state_q.put((job_id, state))


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
    asyncio.run(main(args.franchise_id, args.student))
