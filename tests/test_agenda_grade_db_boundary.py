from __future__ import annotations

import asyncio

from scraper import agenda
from scraper.db_cli import GradeDbUnavailable
from scraper.runner import _new_progress


def _student(student_id: int) -> dict:
    return {
        "db_id": student_id,
        "student_name": f"Student {student_id}",
        "portal": "canvas",
        "login_url": "https://canvas.example/login",
        "id": f"user-{student_id}",
        "password": "secret",
        "alt_login_url": None,
        "alt_id": None,
        "alt_password": None,
        "auth_images": [],
    }


def test_agenda_success_posts_immediately(monkeypatch) -> None:
    posts = []

    async def fetch(_context, student, _target):
        return {"2026-07-13": [{"title": "Homework"}]}, student

    class Client:
        def post_result(self, **kwargs):
            posts.append(kwargs)
            return {"applied": True, "duplicate": False}

    monkeypatch.setattr(agenda, "fetch_agenda", fetch)
    progress = _new_progress(1)
    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            object(),
            [_student(7)],
            "upcoming",
            progress,
            asyncio.Event(),
        )
    )

    assert failure is None
    assert posts[0]["outcome"]["kind"] == "agenda_success"
    assert progress == {"total": 1, "attempted": 1, "success": 1, "errors": 0}


def test_agenda_neon_failure_cancels_pending_collection(monkeypatch) -> None:
    cancelled = asyncio.Event()

    async def fetch(_context, student, _target):
        if student["db_id"] == 7:
            return {"week": []}, student
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    class Client:
        def post_result(self, **_kwargs):
            raise GradeDbUnavailable("safe")

    monkeypatch.setattr(agenda, "fetch_agenda", fetch)
    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            object(),
            [_student(7), _student(8)],
            "upcoming",
            _new_progress(2),
            asyncio.Event(),
        )
    )

    assert failure == "neon_unavailable"
    assert cancelled.is_set()


def test_heartbeat_failure_prevents_agenda_tasks_from_starting(monkeypatch) -> None:
    started = []

    async def fetch(_context, student, _target):
        started.append(student["db_id"])
        return {}, student

    monkeypatch.setattr(agenda, "fetch_agenda", fetch)
    lease_failed = asyncio.Event()
    lease_failed.set()

    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            object(),
            {"job_id": "job", "lease_token": "lease"},
            object(),
            [_student(7)],
            "upcoming",
            _new_progress(1),
            lease_failed,
        )
    )

    assert failure == "lease_renewal_failed"
    assert started == []
