# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import asyncio
import logging
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
from scraper.config.notifications import Severity, send_notification_to_slack
from scraper.config.logging import bind_log_context, configure_logging, reset_log_context
from scraper.portals import LoginError, get_portal
from scraper.portals.utils import get_portal_key_from_url

load_dotenv()

HEARTBEAT_INTERVAL_SECONDS = 60.0
logger = logging.getLogger("scraper.runner")
StudentContext = dict[str, Any]
RawStudentContext = Mapping[str, Any]

class RunnerFatalError(RuntimeError):
    code: str
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"grade job failed: {code}")

async def _send_slack_notification(severity: Severity, message: str) -> None:
    if not os.getenv("PYTHON_ENV") != "dev" or os.getenv("SLACK_NOTIFY_IN_DEV") == "1":
        return
    try:
        _ = await asyncio.to_thread(send_notification_to_slack, severity, message)
    except Exception as exc:
        logger.error(
            "runner.notification.failed",
            extra={"exception_type": type(exc).__name__},
        )


def _debug_env() -> None:
    logger.debug(
        "runner.environment",
        extra={
            "cwd": os.getcwd(),
            "python_executable": sys.executable,
            "python_version": sys.version,
            "environment": os.getenv("PYTHON_ENV"),
            "grade_db_cli_configured": bool(os.getenv("GRADE_DB_CLI_PATH")),
        },
    )


def student_from_context(context: RawStudentContext) -> StudentContext:
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


# Compatibility for internal callers written before this became a shared boundary helper.
_student_from_context = student_from_context


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
    
async def scrape_one(
    browser: Browser,
    student: dict[str, Any],
    *,
    login_only: bool = False,
):
    """Log in and optionally collect grades without database reads or writes."""
    await asyncio.sleep(random.uniform(0, 1.0))
    context = await browser.new_context()
    page = await context.new_page()
    page.set_default_timeout(15_000)
    page.set_default_navigation_timeout(15_000)
    log_context = {
        "portal": str(student["portal"]),
        "student_record_id": int(student["db_id"]),
    }
    context_token = bind_log_context(**log_context)
    logger.info("portal.scrape.started", extra=log_context)

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
            logger.warning("portal.login.rejected", extra=log_context)
            raise LoginError("portal login rejected") from None
        except Exception as exc:
            logger.error(
                "portal.login.failed",
                extra={**log_context, "exception_type": type(exc).__name__},
            )
            raise RuntimeError("portal login failed") from None

        logger.info("portal.login.succeeded", extra=log_context)
        if login_only:
            return {
                "db_id": student["db_id"],
                "id": student["id"],
                "parsed_grades": None,
            }

        grades = await scraper.fetch_grades()
        parsed = grades.get("parsed_grades") if isinstance(grades, dict) else grades
        result = {
            "db_id": student["db_id"],
            "id": student["id"],
            "parsed_grades": parsed,
        }
        logger.info(
            "portal.scrape.completed",
            extra={
                **log_context,
                "course_count": len(parsed) if isinstance(parsed, dict) else 0,
            },
        )
        return result
    finally:
        try:
            await page.close()
            await context.close()
        finally:
            reset_log_context(context_token)


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
        except Exception as exc:
            logger.error(
                "portal.scrape.failed",
                extra={
                    "portal": student["portal"],
                    "student_record_id": student["db_id"],
                    "exception_type": type(exc).__name__,
                },
            )
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


async def _run_grade_job(
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
    contexts = [student_from_context(row) for row in session.get("students", [])]
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
        raise RunnerFatalError(failure_code)

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
    severity = Severity.Crit if progress["errors"] else Severity.Info
    await _send_slack_notification(severity, summary)
    logger.info(
        "runner.completed",
        extra={
            "attempted": progress["attempted"],
            "success": progress["success"],
            "errors": progress["errors"],
            "elapsed_seconds": elapsed,
        },
    )
    return progress


async def main(
    franchise_id: int | None = None,
    student_id: int | None = None,
    portal: str | None = None,
    status: str | None = None,
):
    configure_logging()
    try:
        return await _run_grade_job(
            franchise_id=franchise_id,
            student_id=student_id,
            portal=portal,
            status=status,
        )
    except Exception as exc:
        failure_code = (
            exc.code if isinstance(exc, RunnerFatalError) else "unhandled_exception"
        )
        logger.critical(
            "runner.fatal",
            extra={
                "failure_code": failure_code,
                "exception_type": type(exc).__name__,
            },
            exc_info=os.getenv("LOG_INCLUDE_TRACEBACKS") == "1",
        )
        await _send_slack_notification(
            Severity.Crit,
            textwrap.dedent(
                f"""
                Grade scraping stopped because of a fatal error.
                Failure code: {failure_code}
                Exception type: {type(exc).__name__}
                """
            ).strip(),
        )
        raise


if __name__ == "__main__":
    configure_logging()
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
