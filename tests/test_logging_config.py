from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler

from scraper.config.logging import (
    ContextFilter,
    JsonFormatter,
    bind_log_context,
    configure_logging,
    reset_log_context,
)
from scraper.portals.base import PortalLoggerAdapter


def test_json_formatter_emits_structured_event_fields() -> None:
    record = logging.LogRecord(
        "scraper.portals.canvas",
        logging.INFO,
        __file__,
        1,
        "portal.fetch.completed",
        (),
        None,
    )
    record.portal = "canvas"
    record.course_count = 4

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "portal.fetch.completed"
    assert payload["portal"] == "canvas"
    assert payload["course_count"] == 4


def test_portal_logger_adapter_merges_call_fields() -> None:
    adapter = PortalLoggerAdapter(logging.getLogger("test.portal"), {"portal": "canvas"})

    _, kwargs = adapter.process(
        "portal.fetch.completed", {"extra": {"course_count": 4}}
    )

    assert kwargs["extra"] == {"portal": "canvas", "course_count": 4}


def test_context_filter_adds_and_resets_scrape_context() -> None:
    token = bind_log_context(portal="canvas", student_record_id=7)
    try:
        record = logging.makeLogRecord({})
        assert ContextFilter().filter(record) is True
        assert getattr(record, "portal", None) == "canvas"
        assert getattr(record, "student_record_id", None) == 7
    finally:
        reset_log_context(token)

    next_record = logging.makeLogRecord({})
    ContextFilter().filter(next_record)
    assert not hasattr(next_record, "portal")


def _remove_scraper_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if (handler.get_name() or "").startswith("scraper."):
            root.removeHandler(handler)
            handler.close()


def test_configure_logging_writes_rotating_jsonl_and_readable_console(
    tmp_path, monkeypatch
) -> None:
    _remove_scraper_handlers()
    monkeypatch.setenv("LOG_DIRECTORY", str(tmp_path))
    monkeypatch.delenv("LOG_FILE", raising=False)
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    try:
        path = configure_logging(log_name="portal-tests")
        logging.getLogger("scraper.test").info(
            "portal.test.passed", extra={"portal": "canvas"}
        )

        assert path == tmp_path / "portal-tests.jsonl"
        assert path is not None
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["event"] == "portal.test.passed"
        assert payload["portal"] == "canvas"

        handlers = logging.getLogger().handlers
        console = next(
            handler for handler in handlers if handler.get_name() == "scraper.console"
        )
        persisted = next(
            handler
            for handler in handlers
            if (handler.get_name() or "").startswith("scraper.file.")
        )
        assert not isinstance(console.formatter, JsonFormatter)
        assert isinstance(persisted, RotatingFileHandler)
        assert isinstance(persisted.formatter, JsonFormatter)
    finally:
        _remove_scraper_handlers()


def test_configure_logging_does_not_duplicate_handlers(tmp_path, monkeypatch) -> None:
    _remove_scraper_handlers()
    monkeypatch.setenv("LOG_DIRECTORY", str(tmp_path))
    monkeypatch.delenv("LOG_FILE", raising=False)
    try:
        first = configure_logging()
        second = configure_logging()

        assert first == second
        assert sum(
            handler.get_name() == "scraper.console"
            for handler in logging.getLogger().handlers
        ) == 1
        assert sum(
            (handler.get_name() or "").startswith("scraper.file.")
            for handler in logging.getLogger().handlers
        ) == 1
    finally:
        _remove_scraper_handlers()
