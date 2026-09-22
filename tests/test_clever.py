from __future__ import annotations

import asyncio
from typing import cast

import pytest
from playwright.async_api import Page

from scraper.portals.base import LoginError
from scraper.portals.clever import Clever, is_student_dashboard
from scraper.portals.clever_entry import is_clever_entry


ENTRY = (
    "https://clever.com/oauth/authorize?channel=clever&client_id=app"
    "&redirect_uri=https%3A%2F%2Fclever.com%2Fin%2Fauth_callback"
    "&response_type=code&district_id=district"
)


@pytest.mark.parametrize(
    "url",
    [
        "http://clever.com/oauth/authorize?channel=clever",
        "https://clever.com.evil.example/oauth/authorize?channel=clever",
        "https://user@clever.com/oauth/authorize?channel=clever",
        "https://clever.com:444/oauth/authorize?channel=clever",
        "https://clever.com/oauth/google/login?channel=clever",
        "https://clever.com/oauth/authorize?channel=google",
    ],
)
def test_rejects_untrusted_or_incomplete_entry_routes(url: str) -> None:
    assert is_clever_entry(url) is False


def test_accepts_clever_oauth_entry_independent_of_ephemeral_state() -> None:
    assert is_clever_entry(ENTRY)
    assert is_clever_entry(f"{ENTRY}&state=expired")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://clever.com/in/district/student/portal", True),
        ("https://clever.com/in/district/teacher/portal", False),
        ("https://attacker.example/in/district/student/portal", False),
    ],
)
def test_recognizes_only_clever_student_dashboards(url: str, expected: bool) -> None:
    assert is_student_dashboard(url) is expected


def test_grade_fetch_fails_instead_of_reporting_a_false_empty_success() -> None:
    engine = Clever(cast(Page, object()), "student", "password", ENTRY)
    with pytest.raises(RuntimeError, match="clever_downstream_portal_not_configured"):
        _ = asyncio.run(engine.fetch_grades())


def test_login_rejects_an_untrusted_configured_entry_before_navigation() -> None:
    engine = Clever(
        cast(Page, object()),
        "student",
        "password",
        "https://attacker.example/oauth/authorize",
    )
    with pytest.raises(LoginError, match="portal login rejected"):
        asyncio.run(engine.login())
