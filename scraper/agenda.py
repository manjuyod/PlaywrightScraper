from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from dotenv import load_dotenv
from playwright.async_api import Browser, async_playwright

from scraper.agenda_contract import (
    AgendaBundle,
    AgendaWeeks,
    empty_agenda_bundle,
    normalize_agenda,
)
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
    student_from_context,
)

load_dotenv()

MAX_CONCURRENT_AGENDA_WORKERS = 2


@dataclass(frozen=True, repr=False)
class AgendaSlot:
    number: Literal[1, 2]
    key: Literal["agenda1", "agenda2"]
    portal: str | None
    login_url: str | None
    username: str | None
    password: str | None


class AgendaSlotCollectionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def resolve_agenda_slots(
    student: Mapping[str, object],
) -> tuple[AgendaSlot, AgendaSlot]:
    primary_url = _optional_string(student.get("login_url"))
    alternate_url = _optional_string(student.get("alt_login_url"))
    return (
        AgendaSlot(
            number=1,
            key="agenda1",
            portal=get_portal_key_from_url(primary_url or ""),
            login_url=primary_url,
            username=_optional_string(student.get("id")),
            password=_optional_string(student.get("password")),
        ),
        AgendaSlot(
            number=2,
            key="agenda2",
            portal=get_portal_key_from_url(alternate_url or ""),
            login_url=alternate_url,
            username=_optional_string(student.get("alt_id")),
            password=_optional_string(student.get("alt_password")),
        ),
    )


async def _collect_slot(
    browser: Browser,
    student: Mapping[str, object],
    slot: AgendaSlot,
) -> AgendaWeeks:
    if not slot.portal or not slot.login_url or not slot.username or not slot.password:
        raise AgendaSlotCollectionError(f"{slot.key}_configuration_missing")
    context = await browser.new_context()
    page = None
    try:
        context.set_default_timeout(5_000)
        context.set_default_navigation_timeout(5_000)
        page = await context.new_page()
        engine = get_portal(slot.portal)
        scraper = engine(
            page,
            slot.username,
            slot.password,
            slot.login_url,
            student_name=_optional_string(student.get("student_name")),
        )
        await scraper.login(first_name=_optional_string(student.get("student_name")))
        records = await scraper.get_agenda()
        return normalize_agenda(records)
    finally:
        try:
            if page is not None:
                await page.close()
        finally:
            await context.close()


async def fetch_agenda(
    browser: Browser,
    student: dict[str, Any],
    *,
    worker_semaphore: asyncio.Semaphore | None = None,
) -> tuple[AgendaBundle, dict[str, Any]]:
    if worker_semaphore is None:
        worker_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENDA_WORKERS)

    async def collect_slot(slot: AgendaSlot) -> AgendaWeeks:
        async with worker_semaphore:
            return await _collect_slot(browser, student, slot)

    slots = resolve_agenda_slots(student)
    bundle = empty_agenda_bundle([slot.portal for slot in slots])
    workers: list[tuple[AgendaSlot, asyncio.Task[AgendaWeeks]]] = []
    for slot in slots:
        if (
            not slot.portal
            or not slot.login_url
            or not slot.username
            or not slot.password
        ):
            continue
        engine = get_portal(slot.portal)
        if not engine.agenda_capable:
            continue
        workers.append((slot, asyncio.create_task(collect_slot(slot))))

    results = await asyncio.gather(
        *(task for _, task in workers),
        return_exceptions=True,
    )
    for (slot, _task), result in zip(workers, results, strict=True):
        if isinstance(result, BaseException):
            raise AgendaSlotCollectionError(
                f"{slot.key}_{slot.portal}_failed"
            ) from None
        bundle[slot.key]["weeks"] = result
    return bundle, student


async def _cancel_tasks(tasks: set[asyncio.Task[Any]]) -> None:
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _collect_and_post_agendas(
    client: GradeDbClient,
    session: Mapping[str, Any],
    browser: Browser,
    students: list[dict[str, Any]],
    progress: dict[str, int],
    lease_failed: asyncio.Event,
    on_progress=None,
) -> str | None:
    if lease_failed.is_set():
        return "lease_renewal_failed"
    worker_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENDA_WORKERS)
    tasks = {
        asyncio.create_task(
            fetch_agenda(
                browser,
                student,
                worker_semaphore=worker_semaphore,
            )
        ): student
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
                    outcome = {
                        "kind": "agenda_success",
                        "weekly_agenda": weekly_agenda,
                    }
                    collection_succeeded = True
                except AgendaSlotCollectionError as exc:
                    outcome = {
                        "kind": "failure",
                        "code": exc.code,
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
):
    client = GradeDbClient()
    session = await asyncio.to_thread(
        client.start_job,
        kind="agenda",
        franchise_id=franchise_id,
        student_id=student_id,
    )
    students = [student_from_context(row) for row in session.get("students", [])]
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
            try:
                failure_code = await _collect_and_post_agendas(
                    client,
                    session,
                    browser,
                    students,
                    progress,
                    lease_failed,
                )
            finally:
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
    args = parser.parse_args()
    asyncio.run(main(args.franchise_id, args.student))
