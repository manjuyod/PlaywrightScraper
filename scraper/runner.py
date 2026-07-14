from __future__ import annotations

import argparse
import asyncio
import os
import random
from dataclasses import dataclass

from dotenv import load_dotenv
from playwright.async_api import Browser, async_playwright

from scraper import api_client as worker_api
from scraper.diagnostics import sensitive_tracing_context
from scraper.portals import LoginError, get_portal
from scraper.portals.utils import get_portal_key_from_url
from scraper.safe_logging import suppress_portal_output


load_dotenv()


class ReportedBadLogin(LoginError):
    """A controlled authentication-failure result was sent for this student."""


@dataclass
class WorkerLeaseRenewal:
    stop_event: asyncio.Event
    task: asyncio.Task
    errors: list[Exception]


def start_worker_lease_renewal(
    *,
    job_id: str,
    lease_token: str,
    lease_expires_at: str,
    kind: str,
    progress: dict[str, int],
) -> WorkerLeaseRenewal:
    interval_seconds = worker_api.lease_renewal_interval(lease_expires_at)
    stop_event = asyncio.Event()
    errors: list[Exception] = []

    async def renew_until_stopped() -> None:
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                return
            except asyncio.TimeoutError:
                try:
                    await asyncio.to_thread(
                        worker_api.heartbeat,
                        job_id,
                        lease_token,
                        kind=kind,
                        total=progress["total"],
                        attempted=progress["attempted"],
                        success=progress["success"],
                        errors=progress["errors"],
                    )
                except Exception as exc:
                    errors.append(exc)
                    return

    return WorkerLeaseRenewal(
        stop_event=stop_event,
        task=asyncio.create_task(renew_until_stopped()),
        errors=errors,
    )


async def stop_worker_lease_renewal(renewal: WorkerLeaseRenewal) -> bool:
    renewal.stop_event.set()
    await renewal.task
    return bool(renewal.errors)


def students_from_worker_context(
    context: dict, job_id: str | None = None, lease_token: str | None = None
) -> list[dict]:
    api_students = context.get("students", [])
    if not isinstance(api_students, list):
        return []

    students_list: list[dict] = []
    for row in api_students:
        if not isinstance(row, dict):
            continue
        login_url = row.get("portal1")
        portal_key = (row.get("portal") or "").strip().lower()
        if not portal_key:
            portal_key = get_portal_key_from_url(login_url or "") or ""
        mapped = {
            "db_id": row.get("crmstudentid"),
            "student_name": row.get("firstname") or "",
            "login_url": login_url,
            "id": row.get("p1username"),
            "password": row.get("p1password"),
            "alt_login_url": row.get("portal2"),
            "alt_id": row.get("p2username"),
            "alt_password": row.get("p2password"),
            "portal": portal_key,
            "track_agenda": row.get("track_agenda"),
        }
        if job_id is not None:
            mapped["job_id"] = job_id
        if lease_token is not None:
            mapped["lease_token"] = lease_token
        students_list.append(mapped)
    return students_list


def clear_worker_context_secrets(context: dict | None, students: list[dict]) -> None:
    sensitive = {
        "p1username",
        "p1password",
        "p2username",
        "p2password",
        "id",
        "password",
        "alt_id",
        "alt_password",
    }
    candidate_rows = context.get("students", []) if isinstance(context, dict) else []
    raw_rows = candidate_rows if isinstance(candidate_rows, list) else []
    for row in [*raw_rows, *students]:
        if isinstance(row, dict):
            for key in sensitive:
                row.pop(key, None)
            row.clear()
    raw_rows.clear()
    if isinstance(context, dict):
        context.clear()
    students.clear()


def mark_bad_login(student: dict) -> None:
    job_id = student.get("job_id")
    lease_token = student.get("lease_token")
    if not job_id or not isinstance(lease_token, str):
        raise ValueError("Authentication failures require a leased worker job")
    worker_api.result(
        str(job_id),
        lease_token,
        crmstudentid=int(student["db_id"]),
        status="bad_login",
        passwordgood=False,
    )


def default_worker_id() -> str:
    if os.getenv("WORKER_ID"):
        return os.environ["WORKER_ID"]
    if hasattr(os, "uname"):
        return os.uname().nodename
    return os.getenv("COMPUTERNAME") or "grade-worker"


async def scrape_one(browser: Browser, student: dict) -> dict:
    await asyncio.sleep(random.uniform(0, 1.0))
    context = await browser.new_context()
    page = await context.new_page()
    page.set_default_timeout(15_000)
    page.set_default_navigation_timeout(15_000)
    engine = get_portal(student["portal"])
    scraper = engine(
        page,
        student["id"],
        student["password"],
        student_name=student.get("student_name"),
        login_url=student["login_url"],
        alt_portal_url=student.get("alt_login_url"),
    )
    if student.get("auth_images") and student["portal"] == "gps":
        setattr(scraper, "auth_images", student["auth_images"])
    try:
        if not scraper.sid or not scraper.pw:
            mark_bad_login(student)
            raise ReportedBadLogin()
        with suppress_portal_output():
            async with sensitive_tracing_context(page):
                try:
                    await scraper.login(first_name=student.get("student_name"))
                except LoginError as exc:
                    mark_bad_login(student)
                    raise ReportedBadLogin() from exc
                grades = await scraper.fetch_grades()
        parsed = grades.get("parsed_grades") if isinstance(grades, dict) else grades
        return {"db_id": student["db_id"], "parsed_grades": parsed}
    finally:
        await page.close()
        await context.close()


def _headless_worker() -> bool:
    return os.getenv("WORKER_HEADLESS", "1").strip().lower() not in {"0", "false", "no"}


async def run_worker_once() -> bool:
    job = worker_api.claim_job()
    if not job:
        return False
    job_id = str(job.get("job_id") or "")
    lease_token = job.get("lease_token")
    lease_expires_at = job.get("lease_expires_at")
    if not job_id or not isinstance(lease_token, str) or not isinstance(lease_expires_at, str):
        return False
    kind = "agenda" if job.get("kind") == "agenda" else "grade"
    renewal: WorkerLeaseRenewal | None = None
    context: dict | None = None
    student_list: list[dict] = []

    async def stop_renewal_before_terminal() -> bool:
        nonlocal renewal
        if renewal is None:
            return False
        failed = await stop_worker_lease_renewal(renewal)
        renewal = None
        return failed

    try:
        worker_api.event(job_id, lease_token, "job_started")
        context = worker_api.job_context(job_id, lease_token)
        student_list = students_from_worker_context(
            context, job_id=job_id, lease_token=lease_token
        )
        if kind == "agenda":
            for student in student_list:
                if not student.get("track_agenda"):
                    student.clear()
            student_list[:] = [
                student for student in student_list if student.get("track_agenda")
            ]
        progress = {
            "total": len(student_list),
            "attempted": 0,
            "success": 0,
            "errors": 0,
        }
        worker_api.heartbeat(job_id, lease_token, kind=kind, **progress)
        if not student_list:
            worker_api.complete(job_id, lease_token, kind=kind, **progress)
            return True

        renewal = start_worker_lease_renewal(
            job_id=job_id,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            kind=kind,
            progress=progress,
        )
        if kind == "agenda":
            from scraper.agenda import collect_agendas

            summary = await collect_agendas(
                student_list, job_id=job_id, lease_token=lease_token
            )
            progress.update(summary)
            if renewal.errors or await stop_renewal_before_terminal():
                return False
            worker_api.complete(job_id, lease_token, kind=kind, **progress)
            return True

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=_headless_worker(),
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                for student in student_list:
                    if renewal.errors:
                        return False
                    progress["attempted"] += 1
                    worker_api.event(
                        job_id,
                        lease_token,
                        "student_started",
                        crmstudentid=int(student["db_id"]),
                    )
                    try:
                        result = await scrape_one(browser, student)
                        worker_api.result(
                            job_id,
                            lease_token,
                            crmstudentid=int(result["db_id"]),
                            status="synced",
                            parsed_grades=result.get("parsed_grades"),
                        )
                        progress["success"] += 1
                    except worker_api.ResultDeliveryAmbiguous:
                        return False
                    except ReportedBadLogin:
                        progress["errors"] += 1
                    except Exception:
                        progress["errors"] += 1
                        try:
                            worker_api.result(
                                job_id,
                                lease_token,
                                crmstudentid=int(student["db_id"]),
                                status="failed",
                                failure_code="portal_failure",
                            )
                        except worker_api.ResultDeliveryAmbiguous:
                            return False
                    worker_api.heartbeat(job_id, lease_token, kind=kind, **progress)
            finally:
                await browser.close()

        if await stop_renewal_before_terminal():
            return False
        worker_api.complete(job_id, lease_token, kind=kind, **progress)
        return True
    except worker_api.ResultDeliveryAmbiguous:
        return False
    except Exception:
        try:
            worker_api.fail(job_id, lease_token, "worker_failed")
        finally:
            raise
    finally:
        try:
            if renewal is not None:
                await stop_worker_lease_renewal(renewal)
        finally:
            clear_worker_context_secrets(context, student_list)


async def drain_worker() -> int:
    completed = 0
    while await run_worker_once():
        completed += 1
    return completed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Drain queued private API jobs")
    parser.add_argument(
        "--once", action="store_true", help="Process at most one queued job"
    )
    parser.add_argument(
        "--worker", action="store_true", help="Compatibility alias for --once"
    )
    args = parser.parse_args()
    if args.once or args.worker:
        asyncio.run(run_worker_once())
    else:
        asyncio.run(drain_worker())
