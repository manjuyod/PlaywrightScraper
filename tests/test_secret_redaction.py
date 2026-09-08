from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path

import pytest

from scraper.portals.base import PortalEngine
from scraper import agenda
from scraper.config.logging import (
    ContextFilter,
    JsonFormatter,
    bind_log_context,
    reset_log_context,
)


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


def test_agenda_slot_diagnostics_allowlist_and_redact_collection_data(
    monkeypatch
) -> None:
    """Diagnostics must not turn a failed portal collection into a data leak."""

    sentinels = (
        "credential-user",
        "credential-password",
        "https://secret.instructure.com/login?token=secret-token",
        "auth-image-secret",
        "<html>secret-page</html>",
        "Sensitive Assignment",
        "unknown-extra-secret",
    )

    class Page:
        async def close(self) -> None:
            return None

    class Context:
        def set_default_timeout(self, _timeout: int) -> None:
            return None

        def set_default_navigation_timeout(self, _timeout: int) -> None:
            return None

        async def new_page(self) -> Page:
            return Page()

        async def close(self) -> None:
            return None

    class Browser:
        async def new_context(self) -> Context:
            return Context()

    class FailingEngine:
        agenda_capable = True

        def __init__(self, *_args, **_kwargs) -> None:
            return None

        async def login(self, first_name=None) -> None:
            raise RuntimeError(" ".join(sentinels))

        async def get_agenda(self):
            return []

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("scraper.agenda")
    original_level = logger.level
    original_propagate = logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    monkeypatch.setattr(agenda, "get_portal", lambda _portal: FailingEngine)
    student = {
        "login_url": sentinels[2],
        "id": sentinels[0],
        "password": sentinels[1],
        "alt_login_url": None,
        "alt_id": None,
        "alt_password": None,
        "auth_images": [sentinels[3]],
        "student_name": sentinels[5],
        "unknown_extra": sentinels[6],
    }
    outer_context_token = bind_log_context(
        password=sentinels[1],
        html=sentinels[4],
        url=sentinels[2],
        assignment=sentinels[5],
        unknown_extra=sentinels[6],
    )
    try:
        result, _ = asyncio.run(agenda.fetch_agenda(Browser(), student))
        assert result.failures == {"agenda1": "scrape_failed"}
    finally:
        reset_log_context(outer_context_token)
        logger.removeHandler(handler)
        logger.setLevel(original_level)
        logger.propagate = original_propagate

    payloads = [json.loads(line) for line in stream.getvalue().splitlines()]
    allowed_keys = {
        "timestamp",
        "level",
        "logger",
        "event",
        "phase",
        "portal",
        "slot",
        "exception_kind",
        "worker_count",
        "page_cleanup",
        "context_cleanup",
    }
    assert payloads
    assert all(set(payload) <= allowed_keys for payload in payloads)
    assert [payload["event"] for payload in payloads] == [
        "agenda.fetch.prepared",
        "agenda.slot.collection.failed",
    ]
    assert payloads[0]["phase"] == "agenda_fetch"
    assert payloads[0]["worker_count"] == 1
    assert payloads[1] | {
        "event": "agenda.slot.collection.failed",
        "phase": "slot_collection",
        "portal": "canvas",
        "slot": 1,
        "exception_kind": "unexpected",
        "page_cleanup": "closed",
        "context_cleanup": "closed",
    } == payloads[1]
    serialized = stream.getvalue()
    assert all(sentinel not in serialized for sentinel in sentinels)

    next_record = logging.makeLogRecord({})
    ContextFilter().filter(next_record)
    assert not hasattr(next_record, "slot")
    assert not hasattr(next_record, "portal")
