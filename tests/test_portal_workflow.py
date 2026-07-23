from __future__ import annotations

import asyncio
import json
import os
import random
from pathlib import Path
from typing import cast

import pytest
from playwright.async_api import Browser

from scraper.workflows import test_portal
from scraper.workflows.test_portal import load_students, select_students, stress_test


def _students(portal: str, count: int) -> list[dict[str, object]]:
    return [
        {"db_id": index, "portal": portal, "id": "secret", "password": "secret"}
        for index in range(count)
    ]


def _write_accounts(path: Path, accounts: list[dict[str, object]]) -> None:
    path.write_text(json.dumps(accounts), encoding="utf-8")
    path.chmod(0o600)


def test_account_manifest_loads_only_explicit_accounts(tmp_path: Path) -> None:
    path = tmp_path / "students.portal-test.json"
    _write_accounts(
        path,
        [
            {
                "test_id": 7,
                "portal": "canvas",
                "login_url": "https://portal.example/login",
                "id": "test-user",
                "password": "test-password",
            }
        ],
    )

    students = load_students(path)

    assert len(students) == 1
    assert students[0]["db_id"] == 7
    assert students[0]["portal"] == "canvas"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions are not used on Windows")
def test_account_manifest_rejects_group_or_world_access(tmp_path: Path) -> None:
    path = tmp_path / "students.portal-test.json"
    _write_accounts(path, [])
    path.chmod(0o644)

    with pytest.raises(RuntimeError, match="chmod 600"):
        load_students(path)


def test_stress_selection_uses_five_random_students_when_available() -> None:
    students = _students("canvas", 8)

    selected = select_students(
        students, "canvas", limit=5, rng=random.Random(19)
    )

    assert len(selected) == 5
    assert len({student["db_id"] for student in selected}) == 5


def test_stress_selection_uses_every_student_when_fewer_than_five() -> None:
    students = _students("canvas", 3)

    selected = select_students(
        students, "canvas", limit=5, rng=random.Random(19)
    )

    assert {student["db_id"] for student in selected} == {0, 1, 2}


def test_empty_portal_is_reported_as_skipped() -> None:
    results = asyncio.run(
        stress_test(
            cast(Browser, object()),
            [],
            "canvas",
            sample_size=5,
            rng=random.Random(19),
        )
    )

    assert len(results) == 1
    assert results[0].status == "skipped"
    assert results[0].student_record_id is None


def test_portal_defaults_to_login_only(monkeypatch) -> None:
    observed: list[bool] = []

    async def fake_scrape_one(_browser, _student, *, login_only=False):
        observed.append(login_only)
        return {"parsed_grades": None}

    monkeypatch.setattr(test_portal, "scrape_one", fake_scrape_one)
    result = asyncio.run(
        test_portal.test_portal(
            cast(Browser, object()), "canvas", _students("canvas", 1)[0]
        )
    )

    assert result.status == "passed"
    assert observed == [True]


def test_portal_full_flow_is_explicit(monkeypatch) -> None:
    observed: list[bool] = []

    async def fake_scrape_one(_browser, _student, *, login_only=False):
        observed.append(login_only)
        return {"parsed_grades": {}}

    monkeypatch.setattr(test_portal, "scrape_one", fake_scrape_one)
    result = asyncio.run(
        test_portal.test_portal(
            cast(Browser, object()),
            "canvas",
            _students("canvas", 1)[0],
            grades=True,
        )
    )

    assert result.status == "passed"
    assert observed == [False]
