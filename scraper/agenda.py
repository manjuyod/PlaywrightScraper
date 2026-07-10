from __future__ import annotations

import asyncio
import os
from typing import Literal

from dotenv import load_dotenv
from playwright.async_api import BrowserContext, async_playwright

from scraper import api_client as worker_api
from scraper.portals import LoginError, get_portal
from scraper.portals.utils import get_portal_key_from_url
from scraper.runner import ReportedBadLogin, mark_bad_login
from scraper.safe_logging import suppress_portal_output


load_dotenv()


def _headless_worker() -> bool:
    return os.getenv("WORKER_HEADLESS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _agenda_credentials(student: dict) -> tuple[str, str | None, str | None]:
    if student.get("portal") == "canvas":
        return (
            str(student.get("login_url") or ""),
            student.get("id"),
            student.get("password"),
        )
    return (
        str(student.get("alt_login_url") or ""),
        student.get("alt_id"),
        student.get("alt_password"),
    )


async def fetch_agenda(
    ctx: BrowserContext,
    student: dict,
    target: Literal["upcoming", "missing"],
) -> tuple[dict, dict]:
    login_url, student_login, password = _agenda_credentials(student)
    portal = get_portal_key_from_url(login_url)
    if portal not in {"canvas", "google_classroom"}:
        raise ValueError("Student has no supported agenda portal")
    if not student_login or not password:
        mark_bad_login(student)
        raise ReportedBadLogin()

    page = await ctx.new_page()
    scraper = get_portal(portal)(
        page,
        student_login,
        password,
        alt_student_id=student.get("alt_id"),
        alt_password=student.get("alt_password"),
        login_url=login_url,
        alt_portal_url=student.get("alt_login_url"),
        student_name=student.get("student_name"),
    )
    if student.get("auth_images") and portal == "gps":
        setattr(scraper, "auth_images", student["auth_images"])

    try:
        try:
            await scraper.login(first_name=student.get("student_name"))
        except LoginError as exc:
            mark_bad_login(student)
            raise ReportedBadLogin() from exc
        agenda = await scraper.get_agenda(get=target)
        if not isinstance(agenda, dict):
            raise ValueError("Agenda portal returned an invalid result")
        return agenda, student
    finally:
        await page.close()


def save_agenda_result(
    student: dict,
    agenda: dict,
    job_id: str | None = None,
    lease_token: str | None = None,
) -> None:
    if not job_id or not lease_token:
        raise ValueError("Agenda results require a leased worker job")
    worker_api.result(
        job_id,
        lease_token,
        crmstudentid=int(student["db_id"]),
        status="agenda_synced",
        weekly_agenda=agenda,
    )


async def collect_agendas(
    students: list[dict],
    job_id: str | None = None,
    lease_token: str | None = None,
    target: Literal["upcoming", "missing"] = "upcoming",
) -> dict[str, int]:
    if not job_id or not lease_token:
        raise ValueError("Agenda collection requires a leased worker job")
    tracked_students = [student for student in students if student.get("track_agenda")]
    summary = {"attempted": 0, "success": 0, "errors": 0}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=_headless_worker(),
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context()
        context.set_default_timeout(5_000)
        context.set_default_navigation_timeout(5_000)

        async def collect_one(student: dict) -> bool:
            worker_api.event(
                job_id,
                lease_token,
                "student_started",
                crmstudentid=int(student["db_id"]),
            )
            try:
                agenda, fetched_student = await fetch_agenda(context, student, target)
                save_agenda_result(
                    fetched_student,
                    agenda,
                    job_id=job_id,
                    lease_token=lease_token,
                )
                return True
            except worker_api.ResultDeliveryAmbiguous:
                raise
            except ReportedBadLogin:
                return False
            except Exception:
                worker_api.result(
                    job_id,
                    lease_token,
                    crmstudentid=int(student["db_id"]),
                    status="failed",
                    failure_code="portal_failure",
                )
                return False

        try:
            with suppress_portal_output():
                tasks = [
                    asyncio.create_task(collect_one(student))
                    for student in tracked_students
                ]
                try:
                    for completed in asyncio.as_completed(tasks):
                        succeeded = await completed
                        summary["attempted"] += 1
                        if succeeded:
                            summary["success"] += 1
                        else:
                            summary["errors"] += 1
                except worker_api.ResultDeliveryAmbiguous:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
        finally:
            await context.close()
            await browser.close()

    return summary


if __name__ == "__main__":
    from scraper.runner import drain_worker

    asyncio.run(drain_worker())
