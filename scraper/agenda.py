from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable
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
from scraper.config.logging import (
    bind_log_context,
    reset_log_context,
    suspend_log_context,
)
from scraper.portals import LoginError, get_portal
from scraper.portals.utils import get_portal_key_from_url
from scraper.runner import (
    _advance_progress,
    _heartbeat_loop,
    _new_progress,
    student_from_context,
)

load_dotenv()

MAX_CONCURRENT_AGENDA_WORKERS = 6
logger = logging.getLogger("scraper.agenda")

AgendaExceptionKind = Literal["cancelled", "configuration", "timeout", "unexpected"]
CleanupStatus = Literal["not_started", "closed", "failed"]
AgendaSlotKey = Literal["agenda1", "agenda2"]
AgendaSlotResultHandler = Callable[
    [AgendaSlotKey, Mapping[str, Any], str | None], Awaitable[None]
]


@dataclass(frozen=True, repr=False)
class AgendaSlot:
    number: Literal[1, 2]
    key: AgendaSlotKey
    portal: str | None
    login_url: str | None
    username: str | None
    password: str | None


class AgendaSlotCollectionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AgendaFetchResult(dict[str, Any]):
    def __init__(
        self,
        bundle: AgendaBundle,
        attempted_slots: tuple[AgendaSlotKey, ...],
        failures: Mapping[AgendaSlotKey, str],
    ) -> None:
        super().__init__(bundle)
        self.attempted_slots = attempted_slots
        self.failures = dict(failures)

    @property
    def bundle(self) -> AgendaBundle:
        return self  # type: ignore[return-value]


def _agenda_exception_kind(exc: BaseException) -> AgendaExceptionKind:
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, AgendaSlotCollectionError):
        return "configuration"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "unexpected"


def _log_agenda_slot_diagnostic(
    *,
    event: Literal["agenda.slot.collection.completed", "agenda.slot.collection.failed"],
    slot: AgendaSlot,
    exception_kind: AgendaExceptionKind | None,
    page_cleanup: CleanupStatus,
    context_cleanup: CleanupStatus,
) -> None:
    extra: dict[str, object] = {
        "phase": "slot_collection",
        "portal": slot.portal,
        "slot": slot.number,
        "page_cleanup": page_cleanup,
        "context_cleanup": context_cleanup,
    }
    if exception_kind is not None:
        extra["exception_kind"] = exception_kind
    _emit_agenda_diagnostic(event, extra)


def _emit_agenda_diagnostic(event: str, extra: dict[str, object]) -> None:
    context_token = suspend_log_context()
    try:
        logger.info(event, extra=extra)
    except Exception:
        pass
    finally:
        reset_log_context(context_token)


def _log_agenda_fetch_prepared(worker_count: int) -> None:
    _emit_agenda_diagnostic(
        "agenda.fetch.prepared",
        {
            "phase": "agenda_fetch",
            "worker_count": min(max(worker_count, 0), MAX_CONCURRENT_AGENDA_WORKERS),
        },
    )


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
    context_token = bind_log_context(portal=slot.portal, slot=slot.number)
    try:
        context = None
        page = None
        page_cleanup: CleanupStatus = "not_started"
        context_cleanup: CleanupStatus = "not_started"
        exception_kind: AgendaExceptionKind | None = None
        primary_exception: BaseException | None = None
        body_succeeded = False
        try:
            if (
                not slot.portal
                or not slot.login_url
                or not slot.username
                or not slot.password
            ):
                raise AgendaSlotCollectionError(f"{slot.key}_configuration_missing")
            first_slot, second_slot = resolve_agenda_slots(student)
            alternate_slot = second_slot if slot.number == 1 else first_slot
            context = await browser.new_context()
            context.set_default_timeout(15_000)
            context.set_default_navigation_timeout(15_000)
            page = await context.new_page()
            engine = get_portal(slot.portal)
            engine_kwargs: dict[str, object] = {
                "alt_portal_url": alternate_slot.login_url,
                "alt_student_id": alternate_slot.username,
                "alt_password": alternate_slot.password,
                "student_name": _optional_string(student.get("student_name")),
            }
            if slot.portal == "google_classroom":
                raw_auth_images = student.get("auth_images")
                if isinstance(raw_auth_images, list):
                    engine_kwargs["auth_images"] = [
                        image for image in raw_auth_images[:3] if isinstance(image, str)
                    ]
            scraper = engine(
                page,
                slot.username,
                slot.password,
                slot.login_url,
                **engine_kwargs,
            )
            await scraper.login(first_name=_optional_string(student.get("student_name")))
            records = await scraper.get_agenda()
            weeks = normalize_agenda(
                records,
                known_course_titles=student.get("known_course_titles", ()),
            )
            body_succeeded = True
            return weeks
        except BaseException as exc:
            primary_exception = exc
            exception_kind = _agenda_exception_kind(exc)
            raise
        finally:
            cleanup_exception: BaseException | None = None
            if page is not None:
                try:
                    await page.close()
                    page_cleanup = "closed"
                except BaseException as exc:
                    page_cleanup = "failed"
                    cleanup_exception = exc
            if context is not None:
                try:
                    await context.close()
                    context_cleanup = "closed"
                except BaseException as exc:
                    context_cleanup = "failed"
                    cleanup_exception = cleanup_exception or exc
            _log_agenda_slot_diagnostic(
                event=(
                    "agenda.slot.collection.completed"
                    if exception_kind is None
                    else "agenda.slot.collection.failed"
                ),
                slot=slot,
                exception_kind=exception_kind,
                page_cleanup=page_cleanup,
                context_cleanup=context_cleanup,
            )
            if (
                cleanup_exception is not None
                and primary_exception is None
                and not body_succeeded
            ):
                raise cleanup_exception
    finally:
        reset_log_context(context_token)


async def fetch_agenda(
    browser: Browser,
    student: dict[str, Any],
    *,
    worker_semaphore: asyncio.Semaphore | None = None,
    on_slot_result: AgendaSlotResultHandler | None = None,
) -> tuple[AgendaFetchResult, dict[str, Any]]:
    if worker_semaphore is None:
        worker_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENDA_WORKERS)

    async def collect_slot(slot: AgendaSlot) -> AgendaWeeks:
        async with worker_semaphore:
            return await _collect_slot(browser, student, slot)

    slots = resolve_agenda_slots(student)
    bundle = empty_agenda_bundle([slot.portal for slot in slots])
    attempted_slots: list[AgendaSlotKey] = []
    failures: dict[AgendaSlotKey, str] = {}
    workers: dict[asyncio.Task[AgendaWeeks], AgendaSlot] = {}

    async def report(slot: AgendaSlot, failure_code: str | None) -> None:
        if on_slot_result is not None:
            await on_slot_result(slot.key, bundle[slot.key], failure_code)

    for slot in slots:
        configured_values = (slot.login_url, slot.username, slot.password)
        if not any(configured_values):
            attempted_slots.append(slot.key)
            await report(slot, None)
            continue
        attempted_slots.append(slot.key)
        if not all(configured_values):
            failures[slot.key] = "configuration_missing"
            await report(slot, failures[slot.key])
            continue
        if not slot.portal:
            failures[slot.key] = "unsupported_portal"
            await report(slot, failures[slot.key])
            continue
        try:
            engine = get_portal(slot.portal)
        except ValueError:
            failures[slot.key] = "unsupported_portal"
            await report(slot, failures[slot.key])
            continue
        if not engine.agenda_capable:
            failures[slot.key] = "unsupported_portal"
            await report(slot, failures[slot.key])
            continue
        workers[asyncio.create_task(collect_slot(slot))] = slot

    _log_agenda_fetch_prepared(len(workers))

    pending = set(workers)
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                slot = workers[task]
                try:
                    bundle[slot.key]["weeks"] = task.result()
                    failure_code = None
                except LoginError:
                    failure_code = "bad_login"
                    failures[slot.key] = failure_code
                except BaseException:
                    failure_code = "scrape_failed"
                    failures[slot.key] = failure_code
                await report(slot, failure_code)
    finally:
        await _cancel_tasks(pending)

    return (
        AgendaFetchResult(
            bundle=bundle,
            attempted_slots=tuple(attempted_slots),
            failures=failures,
        ),
        student,
    )


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
    post_lock = asyncio.Lock()

    async def collect_student(
        student: dict[str, Any],
    ) -> tuple[AgendaFetchResult, bool]:
        reported_slots: set[AgendaSlotKey] = set()
        all_applied = True

        async def post_slot_result(
            slot_key: AgendaSlotKey,
            snapshot: Mapping[str, Any],
            failure_code: str | None,
        ) -> None:
            nonlocal all_applied
            channel = (
                "primary_agenda" if slot_key == "agenda1" else "secondary_agenda"
            )
            outcome = (
                {
                    "kind": "failure",
                    "channel": channel,
                    "code": failure_code,
                }
                if failure_code is not None
                else {
                    "kind": f"{channel}_success",
                    "agenda": dict(snapshot),
                }
            )
            async with post_lock:
                response = await asyncio.to_thread(
                    client.post_result,
                    job_id=session["job_id"],
                    lease_token=session["lease_token"],
                    crmstudentid=student["db_id"],
                    outcome=outcome,
                )
            all_applied = all_applied and bool(response.get("applied"))
            reported_slots.add(slot_key)

        fetch_result, _ = await fetch_agenda(
            browser,
            student,
            worker_semaphore=worker_semaphore,
            on_slot_result=post_slot_result,
        )
        for slot_key in fetch_result.attempted_slots:
            if slot_key not in reported_slots:
                await post_slot_result(
                    slot_key,
                    fetch_result.bundle[slot_key],
                    fetch_result.failures.get(slot_key),
                )
        return fetch_result, all_applied

    tasks = {
        asyncio.create_task(collect_student(student)): student
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
                    fetch_result, all_applied = task.result()
                except GradeDbLeaseExpired:
                    await _cancel_tasks(pending)
                    return "lease_expired"
                except GradeDbUnavailable:
                    await _cancel_tasks(pending)
                    return "neon_unavailable"
                except GradeDbError:
                    await _cancel_tasks(pending)
                    return "result_post_failed"
                except Exception:
                    slots = resolve_agenda_slots(student)
                    attempted_slots = tuple(
                        slot.key
                        for slot in slots
                        if any((slot.login_url, slot.username, slot.password))
                    )
                    fetch_result = AgendaFetchResult(
                        bundle=empty_agenda_bundle([slot.portal for slot in slots]),
                        attempted_slots=attempted_slots,
                        failures={key: "scrape_failed" for key in attempted_slots},
                    )
                    all_applied = True

                if lease_failed.is_set():
                    await _cancel_tasks(pending)
                    return "lease_renewal_failed"
                for slot_key in fetch_result.attempted_slots:
                    if slot_key in fetch_result.failures:
                        all_applied = False

                collection_succeeded = not fetch_result.failures and all_applied
                _advance_progress(progress, success=collection_succeeded)
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
    collection_finished = False
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
                collection_finished = True
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass
    except Exception:
        if not collection_finished:
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
