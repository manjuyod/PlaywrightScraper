from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scraper import agenda
from scraper.config.logging import ContextFilter, bind_log_context, reset_log_context


def raw_student(number=1, **changes):
    row = {
        "crmstudentid": number, "franchiseid": 19,
        "firstname": "Synthetic", "lastname": "Student",
        "portal1": "https://canvas.example/login",
        "p1username": " primary user ", "p1password": " primary secret ",
        "portal2": None, "p2username": None, "p2password": None,
        "portal": "powerschool", "track_agenda": False, "auth_images": [],
    }
    row.update(changes)
    return row


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("primary,secondary,expected", [
    ("https://canvas.example/login", None, True),
    ("https://unknown.example/login", "https://canvas.example/login", True),
    ("https://powerschool.example/login", "https://canvas.example/login", True),
    ("https://canvas.example/login", "https://canvas.example/login", True),
    ("https://powerschool.example/login", None, False),
    ("https://unknown.example/login", None, False),
])
def test_capability_uses_slot_urls_not_legacy_metadata(flag, primary, secondary, expected):
    student = agenda.student_from_context(raw_student(
        portal1=primary, portal2=secondary,
        p2username="secondary user" if secondary else None,
        p2password="secondary secret" if secondary else None,
        track_agenda=flag,
    ))
    original = deepcopy(student)
    assert agenda.is_agenda_eligible(student) is expected
    assert student == original


@pytest.mark.parametrize("field", ["login_url", "id", "password"])
@pytest.mark.parametrize("blank", [None, "", " \t "])
def test_incomplete_slot_cannot_qualify(field, blank):
    student = agenda.student_from_context(raw_student())
    student[field] = blank
    assert not agenda.is_agenda_eligible(student)


def test_partial_secondary_does_not_suppress_primary():
    student = agenda.student_from_context(raw_student(
        portal2="https://canvas.example/login", p2username="secondary", p2password=" ",
    ))
    assert agenda.is_agenda_eligible(student)


def test_missing_engine_does_not_suppress_other_slot(monkeypatch):
    student = agenda.student_from_context(raw_student(
        portal2="https://parentvue.example/Login_Parent_PXP.aspx",
        p2username="secondary", p2password="secondary secret",
    ))
    real_get_portal = agenda.get_portal

    def lookup(key):
        if key == "canvas":
            raise ValueError("missing synthetic engine")
        return real_get_portal(key)

    monkeypatch.setattr(agenda, "get_portal", lookup)
    assert agenda.is_agenda_eligible(student)


def test_new_engine_capability_applies_on_next_evaluation(monkeypatch):
    engine = SimpleNamespace(agenda_capable=False)
    monkeypatch.setattr(agenda, "get_portal", lambda key: engine)
    student = agenda.student_from_context(raw_student())
    assert not agenda.is_agenda_eligible(student)
    engine.agenda_capable = True
    assert agenda.is_agenda_eligible(student)
