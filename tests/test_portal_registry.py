from __future__ import annotations

import asyncio
from typing import cast

import pytest
from playwright.async_api import Page

from scraper.portals import (
    GradeTableConfig,
    PortalEngine,
    UniversalLoginConfig,
    get_portal,
    managed_portals,
)
from scraper.portals import registry, utils
from scraper.portals.base import GradeMap


EXPECTED_PORTALS = {
    "aeries",
    "asuprep",
    "blackbaud",
    "canvas",
    "classlink",
    "google_classroom",
    "gps",
    "homeaccess",
    "howsschoolgoing",
    "infinite_campus",
    "k12",
    "microsoft_benjamin_franklin",
    "parentvue",
    "powerschool",
    "schoology",
    "schooltool",
    "student_connection",
}


def test_portals_are_not_agenda_capable_by_default() -> None:
    assert PortalEngine.agenda_capable is False


def _isolate_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_REGISTRY", {})
    monkeypatch.setattr(registry, "managed_portals", {})


def test_discovery_registers_every_portal_from_class_metadata() -> None:
    assert set(managed_portals) == EXPECTED_PORTALS
    for key, patterns in managed_portals.items():
        engine = get_portal(key)
        assert engine.portal_key == key
        assert tuple(patterns) == engine.url_patterns


def test_url_detection_is_case_insensitive_and_prefers_longest_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        registry,
        "managed_portals",
        {"short": ["portal"], "specific": ["district/portal"]},
    )

    assert (
        registry.get_portal_key_from_url("HTTPS://EXAMPLE.ORG/DISTRICT/PORTAL/LOGIN")
        == "specific"
    )


def test_jocombs_root_url_uses_parentvue() -> None:
    assert (
        registry.get_portal_key_from_url("https://az-joc.edupoint.com/")
        == "parentvue"
    )


def test_portal_declaration_rejects_missing_login_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_registry(monkeypatch)

    with pytest.raises(ValueError, match=r"configure or override login"):

        class InvalidPortal(PortalEngine):
            portal_key = "invalid"
            url_patterns = ("invalid.example",)

            async def fetch_grades(self) -> GradeMap:
                return {}


def test_duplicate_portal_keys_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_registry(monkeypatch)

    class FirstPortal(PortalEngine):
        portal_key = "duplicate"
        url_patterns = ("first.example",)
        login_config = UniversalLoginConfig("#user", "#password")

        async def fetch_grades(self) -> GradeMap:
            return {}

    with pytest.raises(ValueError, match="Duplicate portal key"):

        class SecondPortal(PortalEngine):
            portal_key = "duplicate"
            url_patterns = ("second.example",)
            login_config = UniversalLoginConfig("#user", "#password")

            async def fetch_grades(self) -> GradeMap:
                return {}


def test_shared_login_forwards_config_and_runs_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_registry(monkeypatch)
    events: list[object] = []

    async def fake_login_flow(
        page,
        login_url,
        sid,
        password,
        username_selector,
        password_selector,
        **kwargs,
    ) -> None:
        events.append(
            (
                "flow",
                page,
                login_url,
                sid,
                password,
                username_selector,
                password_selector,
                callable(kwargs["microsoft_callback"]),
                kwargs["google_callback"],
                kwargs["sso_login_selector"],
            )
        )

    monkeypatch.setattr(utils, "universal_login_flow", fake_login_flow)

    class HookPortal(PortalEngine):
        portal_key = "hooks"
        url_patterns = ("hooks.example",)
        login_config = UniversalLoginConfig(
            "#user",
            "#password",
            sso_entry_selector="#sso",
            microsoft_sso=True,
        )

        async def validate_login(self) -> None:
            events.append("validate")

        async def after_login(self, first_name: str | None) -> None:
            events.append(("after", first_name))

        async def fetch_grades(self) -> GradeMap:
            return {}

    page = cast(Page, object())
    portal = HookPortal(page, "student", "secret", "https://hooks.example/login")
    asyncio.run(portal.login("Ada"))

    assert events == [
        (
            "flow",
            page,
            "https://hooks.example/login",
            "student",
            "secret",
            "#user",
            "#password",
            True,
            None,
            "#sso",
        ),
        "validate",
        ("after", "Ada"),
    ]


def test_declarative_grade_table_uses_shared_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_registry(monkeypatch)
    calls: list[tuple[object, str, str, str, dict[str, object]]] = []

    async def fake_table_parser(
        page,
        table_selector,
        title_selector,
        grade_selector,
        **kwargs,
    ) -> GradeMap:
        calls.append(
            (
                page,
                table_selector,
                title_selector,
                grade_selector,
                kwargs,
            )
        )
        return {"MATH": 95.0}

    monkeypatch.setattr(utils, "grades_table_to_dict", fake_table_parser)

    class TablePortal(PortalEngine):
        portal_key = "table"
        url_patterns = ("table.example",)
        grade_table_config = GradeTableConfig(
            ".row",
            ".title",
            ".grade",
            truncate_title_on="-",
        )

        async def login(self, first_name: str | None = None) -> None:
            return None

    page = cast(Page, object())
    portal = TablePortal(page, "student", "secret", "https://table.example")

    assert asyncio.run(portal.fetch_grades()) == {"MATH": 95.0}
    assert calls[0][0:4] == (page, ".row", ".title", ".grade")
    assert calls[0][4]["truncate_title_on"] == "-"
