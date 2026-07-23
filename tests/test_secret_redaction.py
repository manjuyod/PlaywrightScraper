from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from scraper.portals.base import PortalEngine


ROOT = Path(__file__).resolve().parents[1]


class _Page:
    pass


class _Engine(PortalEngine):
    async def login(self, first_name=None):
        return None

    async def fetch_grades(self):
        return {}


def test_shared_login_exception_does_not_contain_credentials_or_portal_url() -> None:
    engine = _Engine(
        _Page(),
        "credential-user",
        "credential-password",
        "https://secret-portal.example/login",
    )

    with pytest.raises(Exception) as raised:
        asyncio.run(engine.raise_login_error_if(True, "unsafe diagnostic"))

    message = str(raised.value)
    for secret in (
        "credential-user",
        "credential-password",
        "secret-portal.example",
        "unsafe diagnostic",
    ):
        assert secret not in message


def test_runner_paths_have_no_jsonl_or_direct_sql() -> None:
    source = "\n".join(
        (ROOT / path).read_text(encoding="utf-8").lower()
        for path in ("scraper/runner.py", "scraper/agenda.py")
    )

    assert "jsonl" not in source
    assert "exec_driver_sql" not in source
    assert "update student" not in source


def test_portal_code_does_not_capture_login_traces_or_log_gps_answers() -> None:
    portal_root = ROOT / "scraper" / "portals"
    source = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in portal_root.glob("*.py")
    )

    assert "tracing.start(" not in source
    assert "print(self.auth_images" not in source
    assert "for {self.auth_images}" not in source
    portal_test = (ROOT / "scraper" / "workflows" / "test_portal.py").read_text(
        encoding="utf-8"
    )
    assert "print(student)" not in portal_test
