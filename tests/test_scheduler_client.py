from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scraper import scheduler_client
from scripts import windows_pipeline


ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps({"id": "00000000-0000-0000-0000-000000000042"}).encode()


def test_scheduler_client_uses_its_own_bearer_identity(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setenv("GRADE_API_BASE_URL", "http://api.local:3000")
    monkeypatch.setenv("SCHEDULER_API_KEY", "scheduler-secret")
    monkeypatch.setenv("SCHEDULER_ID", "windows-prod-01")
    monkeypatch.setattr(scheduler_client.urllib.request, "urlopen", fake_urlopen)

    scheduler_client.enqueue_job(
        franchise_id=19,
        kind="grade",
        idempotency_key="00000000-0000-0000-0000-000000000042",
    )

    assert captured["headers"]["Authorization"] == "Bearer scheduler-secret"
    assert captured["body"]["franchise_id"] == 19
    assert captured["body"]["kind"] == "grade"
    assert captured["timeout"] == 30


def test_daily_scheduler_keys_are_deterministic_and_semantic():
    first = windows_pipeline.daily_job_key(date(2030, 1, 2), 19, "grade")
    assert first == windows_pipeline.daily_job_key(date(2030, 1, 2), 19, "grade")
    assert first != windows_pipeline.daily_job_key(date(2030, 1, 3), 19, "grade")
    assert first != windows_pipeline.daily_job_key(date(2030, 1, 2), 19, "agenda")
    assert first != windows_pipeline.daily_job_key(date(2030, 1, 2), 20, "grade")


def test_scheduled_franchise_parser_requires_unique_positive_ids():
    assert windows_pipeline.parse_franchises("6, 19,74") == [6, 19, 74]
    for invalid in ["", "0", "-1", "19,19", "abc"]:
        try:
            windows_pipeline.parse_franchises(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(invalid)


def test_windows_pipeline_and_batch_files_are_api_only():
    production_sources = [
        ROOT / "scripts" / "windows_pipeline.py",
        ROOT / "batches" / "pipeline_franchise.bat",
        ROOT / "batches" / "pipeline_all_franchises.bat",
        ROOT / "batches" / "update_students_all.bat",
    ]
    forbidden = (
        "insert_grades",
        "update_sheets",
        "update_students",
        "gspread",
        "sqlalchemy",
        "pyodbc",
    )
    for source in production_sources:
        text = source.read_text(encoding="utf-8").lower()
        for value in forbidden:
            assert value not in text, f"{source}: {value}"
