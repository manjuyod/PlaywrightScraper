# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import random
import sys
import textwrap
from time import time
from typing import Any, Mapping

from dotenv import load_dotenv
from playwright.async_api import Browser, async_playwright

from scraper.db_cli import (
    GradeDbClient,
    GradeDbError,
    GradeDbLeaseExpired,
    GradeDbUnavailable,
)
from scraper.notif import Severity, send_notification_to_slack
from scraper.portals import LoginError, get_portal
from scraper.portals.utils import get_portal_key_from_url

load_dotenv()

HEARTBEAT_INTERVAL_SECONDS = 60.0


def _debug_env() -> None:
    print("[runner] CWD:", os.getcwd(), flush=True)
    print("[runner] Python:", sys.executable, sys.version, flush=True)
    print("[runner] ENV (Prod/Dev):", os.getenv("PYTHON_ENV"), flush=True)
    print(
        "[runner] GRADE_DB_CLI_PATH set:",
        bool(os.getenv("GRADE_DB_CLI_PATH")),
        flush=True,
    )


def _student_from_context(context: Mapping[str, Any]) -> dict[str, Any]:
    portal = str(context.get("portal") or "").strip().lower()
    if not portal:
        portal = get_portal_key_from_url(context.get("portal1") or "")
    return {
        "db_id": int(context["crmstudentid"]),
        "student_name": str(context.get("firstname") or ""),
        "login_url": context.get("portal1"),
        "id": context.get("p1username"),
        "password": context.get("p1password"),
        "alt_login_url": context.get("portal2"),
        "alt_id": context.get("p2username"),
        "alt_password": context.get("p2password"),
        "portal": portal,
        "auth_images": list(context.get("auth_images") or []),
        "auth_type": context.get("auth_type"),
        "track_agenda": bool(context.get("track_agenda")),
        "status": context.get("status"),
        "passwordgood": context.get("passwordgood"),
        "franchise_id": context.get("franchiseid"),
        "grade": context.get("grade"),
    }


def _filter_contexts(
    contexts: list[dict[str, Any]],
    *,
    portal: str | None,
    status: str | None,
) -> list[dict[str, Any]]:
    wanted_portal = (portal or "").strip().lower()
    wanted_status = (status or "").strip().lower()
    return [
        context
        for context in contexts
        if (not wanted_portal or context.get("portal") == wanted_portal)
        and (
            not wanted_status
            or str(context.get("status") or "").strip().lower() == wanted_status
        )
    ]
    
async def scrape_one(browser: Browser, student: dict[str, Any]):
    """Collect one student's grades without reading or writing a database."""
    await asyncio.sleep(random.uniform(0, 1.0))
    context = await browser.new_context()
    page = await context.new_page()
    page.set_default_timeout(15_000)
    page.set_default_navigation_timeout(15_000)

    try:
        engine = get_portal(student["portal"])
        scraper = engine(
            page,
            student["id"],
            student["password"],
            student_name=student.get("student_name"),
            login_url=student["login_url"],
            alt_portal_url=student.get("alt_login_url"),
            alt_student_id=student.get("alt_id"),
            alt_password=student.get("alt_password"),
        )
        if student.get("auth_images") and student["portal"] == "gps":
            setattr(scraper, "auth_images", student["auth_images"])

        try:
            if not scraper.sid or not scraper.pw:
                raise LoginError("portal login rejected")
            await scraper.login(first_name=student.get("student_name"))
        except (LoginError, scraper.LoginError):
            raise LoginError("portal login rejected") from None
        except Exception:
            raise RuntimeError("portal login failed") from None

        grades = await scraper.fetch_grades()
        parsed = grades.get("parsed_grades") if isinstance(grades, dict) else grades
        return {
            "db_id": student["db_id"],
            "id": student["id"],
            "parsed_grades": parsed,
        }
    finally:
        await page.close()
        await context.close()


def project_root() -> pathlib.Path:
    reference_file = ".python-version"
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / reference_file).exists():
            return parent
    return pathlib.Path.cwd()


def _new_progress(total: int) -> dict[str, int]:
    return {"total": total, "attempted": 0, "success": 0, "errors": 0}


def _advance_progress(progress: dict[str, int], *, success: bool) -> None:
    progress["attempted"] += 1
    if success:
        progress["success"] += 1
    else:
        progress["errors"] += 1


async def _process_grade_students(
    client: GradeDbClient,
    session: Mapping[str, Any],
    browser: Browser,
    student_list: list[dict[str, Any]],
    progress: dict[str, int],
    lease_failed: asyncio.Event,
) -> str | None:
    for student in student_list:
        if lease_failed.is_set():
            return "lease_renewal_failed"
        try:
            result = await scrape_one(browser, student)
            parsed_grades = result.get("parsed_grades")
            if isinstance(parsed_grades, dict) and parsed_grades:
                outcome = {"kind": "grade_success", "parsed_grades": parsed_grades}
                scrape_succeeded = True
            else:
                outcome = {
                    "kind": "failure",
                    "code": "no_grades",
                    "passwordgood": None,
                }
                scrape_succeeded = False
        except LoginError:
            outcome = {
                "kind": "failure",
                "code": "bad_login",
                "passwordgood": False,
            }
            scrape_succeeded = False
        except Exception:
            outcome = {
                "kind": "failure",
                "code": "scrape_failed",
                "passwordgood": None,
            }
            scrape_succeeded = False

        if lease_failed.is_set():
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
            return "lease_expired"
        except GradeDbUnavailable:
            return "neon_unavailable"
        except GradeDbError:
            return "result_post_failed"

        applied_success = scrape_succeeded and bool(response.get("applied"))
        _advance_progress(progress, success=applied_success)
    return None


async def _heartbeat_loop(
    client: GradeDbClient,
    session: Mapping[str, Any],
    progress: dict[str, int],
    stop: asyncio.Event,
    lease_failed: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            return
        except asyncio.TimeoutError:
            pass
        try:
            await asyncio.to_thread(
                client.heartbeat,
                job_id=session["job_id"],
                lease_token=session["lease_token"],
                progress=progress.copy(),
            )
        except GradeDbError:
            lease_failed.set()
            return


async def main(
    franchise_id: int | None = None,
    student_id: int | None = None,
    portal: str | None = None,
    status: str | None = None,
):
    client = GradeDbClient()
    session = await asyncio.to_thread(
        client.start_job,
        kind="grade",
        franchise_id=franchise_id,
        student_id=student_id,
    )
    contexts = [_student_from_context(row) for row in session.get("students", [])]
    student_list = _filter_contexts(contexts, portal=portal, status=status)
    progress = _new_progress(len(student_list))

    if not student_list:
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
    begin_time = time()
    failure_code: str | None = None
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                failure_code = await _process_grade_students(
                    client,
                    session,
                    browser,
                    student_list,
                    progress,
                    lease_failed,
                )
            finally:
                await browser.close()
    except Exception:
        failure_code = failure_code or "runner_failed"
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
        raise RuntimeError(f"grade job failed: {failure_code}")

    await asyncio.to_thread(
        client.complete_job,
        job_id=session["job_id"],
        lease_token=session["lease_token"],
        progress=progress,
    )

    elapsed = int(time() - begin_time)
    summary = textwrap.dedent(
        f"""
        Grade scraping complete.
        Successfully processed {progress["success"]} / {progress["attempted"]} students
        in {elapsed // 60} minutes {elapsed % 60} seconds.
        Errors encountered: {progress["errors"]}
        """
    ).strip()
    if os.getenv("PYTHON_ENV") != "dev" or os.getenv("SLACK_NOTIFY_IN_DEV") == "1":
        severity = Severity.Crit if progress["errors"] else Severity.Info
        try:
            send_notification_to_slack(severity, summary)
        except Exception:
            print("[runner] Slack notification failed", flush=True)
    print(summary, flush=True)
    return progress


if __name__ == "__main__":
    _debug_env()
    parser = argparse.ArgumentParser(description="Scrape student grades.")
    parser.add_argument("-f", "--franchise-id", type=int)
    parser.add_argument("-s", "--student-id", type=int)
    parser.add_argument("-p", "--portal", type=str)
    parser.add_argument("-stat", "--status", type=str)
    args = parser.parse_args()
    asyncio.run(
        main(
            franchise_id=args.franchise_id,
            student_id=args.student_id,
            portal=args.portal,
            status=args.status,
        )
    )
