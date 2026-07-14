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


def test_enqueue_sends_target_without_scheduler_id(monkeypatch):
    captured = {}

    def fake_request(method, path, payload=None):
        captured.update(method=method, path=path, payload=payload)
        return {"id": "00000000-0000-0000-0000-000000000042"}

    monkeypatch.delenv("SCHEDULER_ID", raising=False)
    monkeypatch.setattr(scheduler_client, "request_json", fake_request)
    scheduler_client.enqueue_job(
        franchise_id=19,
        kind="grade",
        idempotency_key="00000000-0000-0000-0000-000000000042",
        target_worker_id="dev-alice-laptop",
    )
    assert captured == {
        "method": "POST",
        "path": "/api/scheduler/jobs",
        "payload": {
            "franchise_id": 19,
            "kind": "grade",
            "idempotency_key": "00000000-0000-0000-0000-000000000042",
            "target_worker_id": "dev-alice-laptop",
        },
    }


def test_enqueue_sends_target_worker_id(monkeypatch):
    captured = {}

    def fake_request(method, path, payload=None):
        captured.update(method=method, path=path, payload=payload)
        return {"id": "00000000-0000-0000-0000-000000000042"}

    monkeypatch.setattr(scheduler_client, "request_json", fake_request)
    scheduler_client.enqueue_job(
        franchise_id=19,
        kind="agenda",
        idempotency_key="00000000-0000-0000-0000-000000000042",
        target_worker_id="worker-test",
        student_id=42,
    )

    assert captured["payload"]["target_worker_id"] == "worker-test"
    assert captured["payload"]["student_id"] == 42


def test_scheduler_request_does_not_require_scheduler_id(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setenv("GRADE_API_BASE_URL", "http://api.local:3000")
    monkeypatch.setenv("SCHEDULER_API_KEY", "scheduler-secret")
    monkeypatch.delenv("SCHEDULER_ID", raising=False)
    monkeypatch.setattr(scheduler_client.urllib.request, "urlopen", fake_urlopen)

    scheduler_client.enqueue_job(
        franchise_id=19,
        kind="grade",
        idempotency_key="00000000-0000-0000-0000-000000000042",
        target_worker_id="worker-test",
    )

    assert captured["headers"]["Authorization"] == "Bearer scheduler-secret"
    assert "Scheduler-Id" not in captured["headers"]
    assert captured["body"]["franchise_id"] == 19
    assert captured["body"]["kind"] == "grade"
    assert "scheduler_id" not in captured["body"]
    assert captured["timeout"] == 30


def test_enqueue_rejects_blank_or_padded_target(monkeypatch):
    monkeypatch.setattr(
        scheduler_client,
        "request_json",
        lambda *_args, **_kwargs: {
            "id": "00000000-0000-0000-0000-000000000042"
        },
    )
    for target in ["", " worker-test", "worker-test "]:
        try:
            scheduler_client.enqueue_job(
                franchise_id=19,
                kind="grade",
                idempotency_key="00000000-0000-0000-0000-000000000042",
                target_worker_id=target,
            )
        except ValueError as exc:
            assert "target_worker_id" in str(exc)
        else:
            raise AssertionError(target)


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
