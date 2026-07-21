from __future__ import annotations

import argparse
import asyncio
import queue
from typing import Any, Literal, Mapping

from dotenv import load_dotenv
from playwright.async_api import BrowserContext, async_playwright

from scraper.db_cli import (
    GradeDbClient,
    GradeDbError,
    GradeDbLeaseExpired,
    GradeDbUnavailable,
)
from scraper.portals import get_portal
from scraper.portals.utils import get_portal_key_from_url
from scraper.runner import (
    _advance_progress,
    _heartbeat_loop,
    _new_progress,
    _student_from_context,
)

load_dotenv()


async def fetch_agenda(
    ctx: BrowserContext,
    student: dict[str, Any],
    target: Literal["upcoming", "missing"],
) -> tuple[dict, dict[str, Any]]:
    page = await ctx.new_page()
    try:
        if student["portal"] == "canvas":
            login_url = student["login_url"]
            sid = student["id"]
            password = student["password"]
        else:
            login_url = student["alt_login_url"]
            sid = student["alt_id"]
            password = student["alt_password"]

        portal = get_portal_key_from_url(login_url)
        if portal not in ("canvas", "google_classroom"):
            raise RuntimeError("agenda portal is unsupported")
        engine = get_portal(portal)
        scraper = engine(
            page,
            sid,
            password,
            alt_student_id=student.get("alt_id"),
            alt_password=student.get("alt_password"),
            login_url=login_url,
            alt_portal_url=student.get("alt_login_url"),
            student_name=student.get("student_name"),
        )
        if student.get("auth_images") and student["portal"] == "gps":
            setattr(scraper, "auth_images", student["auth_images"])
        await scraper.login(first_name=student.get("student_name"))
        agenda = await scraper.get_agenda(get=target)
        return agenda, student
    finally:
        await page.close()


async def _cancel_tasks(tasks: set[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _collect_and_post_agendas(
    client: GradeDbClient,
    session: Mapping[str, Any],
    context: BrowserContext,
    students: list[dict[str, Any]],
    target: Literal["upcoming", "missing"],
    progress: dict[str, int],
    lease_failed: asyncio.Event,
    on_progress=None,
) -> str | None:
    if lease_failed.is_set():
        return "lease_renewal_failed"
    tasks = {
        asyncio.create_task(fetch_agenda(context, student, target)): student
        for student in students
    }
    pending = set(tasks)
    try:
        while pending:
            if lease_failed.is_set():
                await _cancel_tasks(pending)
                return "lease_renewal_failed"
            done, pending = await asyncio.wait(
                pending, timeout=0.25, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                student = tasks[task]
                try:
                    weekly_agenda, _ = task.result()
                    if isinstance(weekly_agenda, dict) and weekly_agenda:
                        outcome = {
                            "kind": "agenda_success",
                            "weekly_agenda": weekly_agenda,
                        }
                        collection_succeeded = True
                    else:
                        outcome = {
                            "kind": "failure",
                            "code": "agenda_empty",
                            "passwordgood": None,
                        }
                        collection_succeeded = False
                except Exception:
                    outcome = {
                        "kind": "failure",
                        "code": "agenda_failed",
                        "passwordgood": None,
                    }
                    collection_succeeded = False

                if lease_failed.is_set():
                    await _cancel_tasks(pending)
                    return "lease_renewal_failed"
                try:
                    response = await asyncio.to_thread(
                        client.post_result,
                        job_id=session["job_id"],
                        lease_token=session["lease_token"],
                        crmstudentid=student["db_id"],
                        outcome=outcome,
                    )
                except GradeDbLeaseExpired:
                    await _cancel_tasks(pending)
                    return "lease_expired"
                except GradeDbUnavailable:
                    await _cancel_tasks(pending)
                    return "neon_unavailable"
                except GradeDbError:
                    await _cancel_tasks(pending)
                    return "result_post_failed"

                applied_success = collection_succeeded and bool(response.get("applied"))
                _advance_progress(progress, success=applied_success)
                if on_progress is not None:
                    on_progress()
        return None
    finally:
        await _cancel_tasks(pending)


async def main(
    franchise_id: int | None,
    student_id: int | None,
    job_id: str | None = None,
    state_q: queue.Queue | None = None,
    target: Literal["upcoming", "missing"] = "upcoming",
):
    if target not in ("upcoming", "missing"):
        raise ValueError("agenda target is invalid")
    client = GradeDbClient()
    session = await asyncio.to_thread(
        client.start_job,
        kind="agenda",
        franchise_id=franchise_id,
        student_id=student_id,
    )
    students = [_student_from_context(row) for row in session.get("students", [])]
    progress = _new_progress(len(students))

    if not students:
        await asyncio.to_thread(
            client.complete_job,
            job_id=session["job_id"],
            lease_token=session["lease_token"],
            progress=progress,
        )
        return progress

    stop_heartbeat = asyncio.Event()
    lease_failed = asyncio.Event()
    heartbeat = asyncio.create_task(
        _heartbeat_loop(client, session, progress, stop_heartbeat, lease_failed)
    )
    failure_code: str | None = None
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context()
            context.set_default_timeout(5_000)
            context.set_default_navigation_timeout(5_000)
            try:
                failure_code = await _collect_and_post_agendas(
                    client,
                    session,
                    context,
                    students,
                    target,
                    progress,
                    lease_failed,
                )
            finally:
                await context.close()
                await browser.close()
    except Exception:
        failure_code = failure_code or "agenda_runner_failed"
    finally:
        stop_heartbeat.set()
        await heartbeat

    if lease_failed.is_set():
        failure_code = failure_code or "lease_renewal_failed"
    if failure_code:
        try:
            await asyncio.to_thread(
                client.fail_job,
                job_id=session["job_id"],
                lease_token=session["lease_token"],
                code=failure_code,
            )
        except GradeDbError:
            pass
        raise RuntimeError(f"agenda job failed: {failure_code}")

    await asyncio.to_thread(
        client.complete_job,
        job_id=session["job_id"],
        lease_token=session["lease_token"],
        progress=progress,
    )
    return progress


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect student agendas.")
    parser.add_argument("-f", "--franchise-id", type=int)
    parser.add_argument("-s", "--student", type=int)
    parser.add_argument("--target", choices=("upcoming", "missing"), default="upcoming")
    args = parser.parse_args()
    asyncio.run(main(args.franchise_id, args.student, target=args.target))
