"""Live smoke and stress tests for the registered portal engines.

This is an explicit operational diagnostic, not a pytest test. It reads eligible
student contexts through the read-only grade-db boundary and never posts results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Literal, cast

from playwright.async_api import Browser, async_playwright

from scraper.config.logging import configure_logging
from scraper.portals import managed_portals
from scraper.runner import StudentContext, scrape_one


logger = logging.getLogger("scraper.workflows.test_portal")
TestStatus = Literal["passed", "failed", "skipped"]
_MAX_ACCOUNT_FILE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class PortalTestResult:
    portal: str
    student_record_id: int | None
    status: TestStatus
    exception_type: str | None = None


@dataclass(frozen=True)
class PortalTestArgs:
    students_file: Path
    portal: str | None
    sample_size: int
    seed: int | None
    headless: bool
    grades: bool


def _required_string(row: Mapping[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"portal test account requires nonempty {field}")
    return value.strip()


def _optional_string(row: Mapping[str, object], field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"portal test account {field} must be a string")
    return value.strip() or None


def _normalize_student(row: Mapping[str, object]) -> StudentContext:
    record_id = row.get("test_id")
    if not isinstance(record_id, int) or isinstance(record_id, bool) or record_id <= 0:
        raise ValueError("portal test account requires a positive integer test_id")
    portal = _required_string(row, "portal").lower()
    if portal not in managed_portals:
        raise ValueError(f"portal test account has unknown portal key: {portal}")

    raw_auth_images = row.get("auth_images", [])
    if not isinstance(raw_auth_images, list) or not all(
        isinstance(value, str) for value in raw_auth_images
    ):
        raise ValueError("portal test account auth_images must be a list of strings")

    return {
        "db_id": record_id,
        "portal": portal,
        "login_url": _required_string(row, "login_url"),
        "id": _required_string(row, "id"),
        "password": _required_string(row, "password"),
        "student_name": _optional_string(row, "student_name"),
        "alt_login_url": _optional_string(row, "alt_login_url"),
        "alt_id": _optional_string(row, "alt_id"),
        "alt_password": _optional_string(row, "alt_password"),
        "auth_images": list(cast(list[str], raw_auth_images)),
    }


def load_students(path: Path) -> list[StudentContext]:
    """Load explicitly provisioned test accounts from a protected local file."""
    resolved = path.expanduser().resolve()
    try:
        file_stat = resolved.stat()
    except OSError as exc:
        raise RuntimeError("portal test account file is unavailable") from exc
    if not resolved.is_file():
        raise RuntimeError("portal test account path is not a file")
    if file_stat.st_size > _MAX_ACCOUNT_FILE_BYTES:
        raise RuntimeError("portal test account file exceeds the 1 MiB limit")
    if os.name != "nt" and stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise RuntimeError("portal test account file must be owner-only; run chmod 600")

    try:
        payload: object = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("portal test account file is invalid") from exc
    if not isinstance(payload, list):
        raise ValueError("portal test account file must contain a JSON list")

    students: list[StudentContext] = []
    seen_record_ids: set[int] = set()
    for raw_row in cast(list[object], payload):
        row = raw_row
        if isinstance(row, Mapping):
            student = _normalize_student(cast(Mapping[str, object], row))
            record_id = cast(int, student["db_id"])
            if record_id in seen_record_ids:
                raise ValueError("portal test account test_id values must be unique")
            seen_record_ids.add(record_id)
            students.append(student)
        else:
            raise ValueError("each portal test account must be a JSON object")
    if not students:
        raise ValueError("portal test account file contains no accounts")
    return students


def select_students(
    students: Sequence[StudentContext],
    portal: str,
    *,
    limit: int,
    rng: random.Random,
) -> list[StudentContext]:
    """Choose at most ``limit`` students for a portal without mutating input."""
    if limit < 1:
        raise ValueError("sample size must be positive")
    candidates = [student for student in students if student.get("portal") == portal]
    return rng.sample(candidates, min(limit, len(candidates)))


async def test_portal(
    browser: Browser,
    portal: str,
    student: StudentContext,
    *,
    grades: bool = False,
) -> PortalTestResult:
    """Exercise login, optionally followed by grade fetching, without persistence."""
    record_id_value = student.get("db_id")
    if not isinstance(record_id_value, int):
        raise RuntimeError("student context is missing its CRM record ID")
    record_id = record_id_value
    logger.info(
        "portal.test.started",
        extra={"portal": portal, "student_record_id": record_id},
    )
    try:
        _ = await scrape_one(browser, student, login_only=not grades)
    except Exception as exc:
        exception_type = type(exc).__name__
        logger.error(
            "portal.test.failed",
            extra={
                "portal": portal,
                "student_record_id": record_id,
                "exception_type": exception_type,
            },
        )
        return PortalTestResult(portal, record_id, "failed", exception_type)

    logger.info(
        "portal.test.passed",
        extra={"portal": portal, "student_record_id": record_id},
    )
    return PortalTestResult(portal, record_id, "passed")


async def stress_test(
    browser: Browser,
    students: Sequence[StudentContext],
    portal: str,
    *,
    sample_size: int,
    rng: random.Random,
    grades: bool = False,
) -> list[PortalTestResult]:
    selected = select_students(students, portal, limit=sample_size, rng=rng)
    if not selected:
        logger.warning("portal.test.skipped", extra={"portal": portal, "reason": "no_students"})
        return [PortalTestResult(portal, None, "skipped")]
    return list(
        await asyncio.gather(
            *(
                test_portal(browser, portal, student, grades=grades)
                for student in selected
            )
        )
    )


async def full_test(
    browser: Browser,
    students: Sequence[StudentContext],
    *,
    rng: random.Random,
    grades: bool = False,
) -> list[PortalTestResult]:
    selected: list[tuple[str, StudentContext]] = []
    results: list[PortalTestResult] = []
    for portal in managed_portals:
        sample = select_students(students, portal, limit=1, rng=rng)
        if sample:
            selected.append((portal, sample[0]))
        else:
            logger.warning(
                "portal.test.skipped",
                extra={"portal": portal, "reason": "no_students"},
            )
            results.append(PortalTestResult(portal, None, "skipped"))

    tested = await asyncio.gather(
        *(
            test_portal(browser, portal, student, grades=grades)
            for portal, student in selected
        )
    )
    results.extend(tested)
    return results


def _log_summary(results: Sequence[PortalTestResult], elapsed_seconds: int) -> None:
    counts = {
        status: sum(result.status == status for result in results)
        for status in ("passed", "failed", "skipped")
    }
    logger.info(
        "portal.test.completed",
        extra={**counts, "elapsed_seconds": elapsed_seconds},
    )


async def main(args: PortalTestArgs) -> int:
    students = load_students(args.students_file)
    rng = random.Random(args.seed)
    started = monotonic()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            if args.portal:
                portal = args.portal.strip().lower()
                if portal not in managed_portals:
                    raise ValueError(f"unknown portal key: {portal}")
                results = await stress_test(
                    browser,
                    students,
                    portal,
                    sample_size=args.sample_size,
                    rng=rng,
                    grades=args.grades,
                )
            else:
                results = await full_test(
                    browser, students, rng=rng, grades=args.grades
                )
        finally:
            await browser.close()

    _log_summary(results, int(monotonic() - started))
    return 1 if any(result.status == "failed" for result in results) else 0


def _parse_args() -> PortalTestArgs:
    parser = argparse.ArgumentParser(
        description=(
            "Test login for one random student per portal, or stress one portal "
            "with up to five random students. No results are persisted."
        )
    )
    configured_file = os.getenv("PORTAL_TEST_ACCOUNTS_FILE", "").strip()
    _ = parser.add_argument(
        "--students-file",
        type=Path,
        default=Path(configured_file) if configured_file else None,
        required=not configured_file,
        help=(
            "Protected JSON account manifest; alternatively set "
            "PORTAL_TEST_ACCOUNTS_FILE."
        ),
    )
    _ = parser.add_argument(
        "-p",
        "--portal",
        help="Stress-test this portal instead of testing one student per portal.",
    )
    _ = parser.add_argument(
        "--sample-size",
        type=int,
        default=5,
        help="Maximum students for a single-portal stress test (default: 5).",
    )
    _ = parser.add_argument(
        "--seed", type=int, help="Optional seed for repeatable random selection."
    )
    _ = parser.add_argument(
        "--headless", action="store_true", help="Run Chromium without a visible window."
    )
    _ = parser.add_argument(
        "--grades",
        action="store_true",
        help="Fetch grades after login; by default the test stops after login succeeds.",
    )
    namespace = parser.parse_args()
    return PortalTestArgs(
        students_file=cast(Path, namespace.students_file),
        portal=cast(str | None, namespace.portal),
        sample_size=cast(int, namespace.sample_size),
        seed=cast(int | None, namespace.seed),
        headless=cast(bool, namespace.headless),
        grades=cast(bool, namespace.grades),
    )


if __name__ == "__main__":
    configure_logging(log_name="portal-tests")
    raise SystemExit(asyncio.run(main(_parse_args())))
