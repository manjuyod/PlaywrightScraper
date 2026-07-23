from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal, cast


_STANDARD_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__)
_LOG_CONTEXT: ContextVar[dict[str, object]] = ContextVar(
    "scraper_log_context", default={}
)
LogName = Literal["scraper", "portal-tests"]
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 10


class ContextFilter(logging.Filter):
    def filter(
        self, record: logging.LogRecord
    ) -> bool:
        for key, value in _LOG_CONTEXT.get().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


def bind_log_context(**values: object) -> Token[dict[str, object]]:
    return _LOG_CONTEXT.set({**_LOG_CONTEXT.get(), **values})


def reset_log_context(token: Token[dict[str, object]]) -> None:
    _LOG_CONTEXT.reset(token)


class JsonFormatter(logging.Formatter):
    """Small JSON formatter for machine-readable production logs."""

    def format(
        self, record: logging.LogRecord
    ) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        record_fields = cast(Mapping[str, object], record.__dict__)
        for key, value in record_fields.items():
            if key not in _STANDARD_RECORD_FIELDS and key not in {"message", "asctime"}:
                payload[key] = value
        if record.exc_info:
            exception_type = record.exc_info[0]
            if exception_type is not None:
                payload["exception_type"] = exception_type.__name__
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _default_log_directory() -> Path:
    return Path(__file__).resolve().parents[1] / "logs"


def _log_path(log_name: LogName) -> Path:
    explicit_file = os.getenv("LOG_FILE", "").strip()
    if explicit_file:
        return Path(explicit_file).expanduser().resolve()
    configured_directory = os.getenv("LOG_DIRECTORY", "").strip()
    directory = (
        Path(configured_directory).expanduser().resolve()
        if configured_directory
        else _default_log_directory()
    )
    return directory / f"{log_name}.jsonl"


def configure_logging(*, log_name: LogName = "scraper") -> Path | None:
    """Configure readable console logs and rotating JSONL persistence."""

    root = logging.getLogger()
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)

    context_filter = ContextFilter()
    console_handler_name = "scraper.console"
    if not any(
        handler.get_name() == console_handler_name for handler in root.handlers
    ):
        console = logging.StreamHandler(sys.stderr)
        console.set_name(console_handler_name)
        console.addFilter(context_filter)
        if os.getenv("LOG_FORMAT", "text").lower() == "json":
            console.setFormatter(JsonFormatter())
        else:
            console.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
            )
        root.addHandler(console)

    if os.getenv("LOG_FILE_ENABLED", "1").lower() in {"0", "false", "no"}:
        return None

    path = _log_path(log_name)
    file_handler_name = f"scraper.file.{path}"
    if any(handler.get_name() == file_handler_name for handler in root.handlers):
        return path

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=_positive_int_env("LOG_MAX_BYTES", _DEFAULT_MAX_BYTES),
            backupCount=_positive_int_env(
                "LOG_BACKUP_COUNT", _DEFAULT_BACKUP_COUNT
            ),
            encoding="utf-8",
        )
        file_handler.set_name(file_handler_name)
        file_handler.addFilter(context_filter)
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "logging.file.unavailable",
            extra={"exception_type": type(exc).__name__},
        )
        return None
    return path
