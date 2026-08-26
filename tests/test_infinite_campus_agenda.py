from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import cast

import pytest
from playwright.async_api import Page
from scraper.agenda_contract import normalize_agenda
from scraper.portals.infinite_campus import InfiniteCampus
from scraper.portals import infinite_campus_agenda as ic_agenda
from scraper.portals.infinite_campus_agenda import (
    InfiniteCampusAgendaError,
    collect_infinite_campus_agenda,
    parse_infinite_campus_course_grades,
)


REFERENCE = datetime(2026, 8, 25, 12, 0)


def assignment_row(
    title: str,
    *,
    due: str | None,
    score: str = "",
    flags: tuple[str, ...] = (),
) -> str:
    due_content = f"<span>Due</span>: {due}" if due is not None else ""
    flag_content = "".join(f'<span class="label">{flag}</span>' for flag in flags)
    return f"""
    <div class="selcat-assignment-row assignment__row-largeScreen">
      <div class="assignment__largeScreen--cell-assignmentName">
        <h6><a href="javascript:void(0);">{title}</a></h6>
      </div>
      <div class="assignment__largeScreen--cell-courseDueDate">
        <div>{due_content}</div>
      </div>
      <div class="assignment__largeScreen--cell-commentsFlags">
        <span class="comment">ignored academic comment</span>
        <tl-curriculum-flags>{flag_content}</tl-curriculum-flags>
      </div>
      <div class="assignment-score__scores--largeScreen">{score}</div>
    </div>
    """


def course_html(*rows: str) -> str:
    return "".join(
        (
            "<tl-grading-task-list><h4>Semester Grade</h4></tl-grading-task-list>",
            '<button class="divider__header" aria-expanded="true" '
            + 'aria-controls="assignmentList-1">Category</button>',
            '<div id="assignmentList-1">',
            *rows,
            "</div>",
        )
    )


def test_bulk_course_parser_classifies_rows_without_assignment_navigation() -> None:
    records = parse_infinite_campus_course_grades(
        course_html(
            assignment_row(
                "Missing work",
                due="08/20/2026",
                score="9 / 10 (90%)",
                flags=("Missing",),
            ),
            assignment_row("Low quiz", due="08/21/2026", score="14 / 35 (40%)"),
            assignment_row("Future work", due="08/28/2026"),
            assignment_row("Boundary score", due=None, score="8 / 10 (80%)"),
            assignment_row("Completed work", due=None, flags=("Turned In",)),
            assignment_row("Excused work", due=None, score="Excused"),
            assignment_row("Past unscored work", due="08/18/2026"),
        ),
        course="  Synthetic Chemistry  ",
        reference=REFERENCE,
    )

    assert records == [
        {
            "course": "Synthetic Chemistry",
            "title": "Missing work",
            "dueDate": "2026-08-20",
            "dueTime": None,
            "status": "missing",
        },
        {
            "course": "Synthetic Chemistry",
            "title": "Low quiz",
            "dueDate": "2026-08-21",
            "dueTime": None,
            "status": "low_score",
        },
        {
            "course": "Synthetic Chemistry",
            "title": "Future work",
            "dueDate": "2026-08-28",
            "dueTime": None,
            "status": "due",
        },
    ]


@pytest.mark.parametrize("score", ["7 / 10", "79.5%", "14 / 35 (40%)"])
def test_bulk_course_parser_accepts_supported_low_score_shapes(score: str) -> None:
    records = parse_infinite_campus_course_grades(
        course_html(assignment_row("Low work", due="08/21/2026", score=score)),
        course="Synthetic Mathematics",
        reference=REFERENCE,
    )

    assert [record["status"] for record in records] == ["low_score"]


@pytest.mark.parametrize("score", ["0%", "0 / 10"])
def test_bulk_course_parser_classifies_zero_scores_as_missing(score: str) -> None:
    records = parse_infinite_campus_course_grades(
        course_html(assignment_row("Zero work", due="08/21/2026", score=score)),
        course="Synthetic Mathematics",
        reference=REFERENCE,
    )

    assert [record["status"] for record in records] == ["missing"]


def test_missing_still_wins_when_turned_in_or_scored() -> None:
    records = parse_infinite_campus_course_grades(
        course_html(
            assignment_row(
                "Flagged work",
                due="08/21/2026",
                score="100%",
                flags=("Missing", "Turned In"),
            )
        ),
        course="Synthetic English",
        reference=REFERENCE,
    )
    assert records[0]["status"] == "missing"


@pytest.mark.parametrize(
    "html",
    [
        "<div></div>",
        course_html(assignment_row("", due="08/21/2026", score="70%")),
        course_html(assignment_row("Low work", due=None, score="70%")),
        course_html(assignment_row("Future work", due="not-a-date")),
    ],
)
def test_bulk_course_parser_rejects_ambiguous_or_malformed_snapshots(html: str) -> None:
    with pytest.raises(InfiniteCampusAgendaError):
        _ = parse_infinite_campus_course_grades(
            html,
            course="Synthetic Course",
            reference=REFERENCE,
        )


def test_explicit_course_grades_root_without_assignments_is_valid() -> None:
    assert (
        parse_infinite_campus_course_grades(
            course_html(),
            course="Synthetic Course",
            reference=REFERENCE,
        )
        == []
    )


def test_bulk_records_keep_the_shared_week_course_status_contract() -> None:
    records = parse_infinite_campus_course_grades(
        course_html(
            assignment_row("Low work", due="08/21/2026", score="70%"),
            assignment_row("Future work", due="08/28/2026"),
        ),
        course="Synthetic Science",
        reference=REFERENCE,
    )

    assert normalize_agenda(records) == {
        "2026-08-17": {
            "Synthetic Science": {
                "missing": [],
                "low_score": [
                    {
                        "title": "Low work",
                        "dueDate": "2026-08-21",
                        "dueTime": None,
                    }
                ],
                "due": [],
            }
        },
        "2026-08-24": {
            "Synthetic Science": {
                "missing": [],
                "low_score": [],
                "due": [
                    {
                        "title": "Future work",
                        "dueDate": "2026-08-28",
                        "dueTime": None,
                    }
                ],
            }
        },
    }
class FakeCourseCount:
    def __init__(self, frame: "FakeFrame") -> None:
        self.frame: FakeFrame = frame

    async def count(self) -> int:
        return self.frame.course_count


class FakeFrame:
    def __init__(self) -> None:
        self.course_count: int = 2

    def locator(self, selector: str) -> FakeCourseCount:
        assert selector == "div.collapsible-card.grades__card:visible"
        return FakeCourseCount(self)

    async def content(self) -> str:
        return "<synthetic-course-grades></synthetic-course-grades>"


class FakePage:
    def __init__(self, frame: FakeFrame) -> None:
        self.current_frame: FakeFrame = frame

    def frame(self, name: str) -> FakeFrame | None:
        assert name == "main-workspace"
        return self.current_frame


def test_collector_opens_each_course_once_and_returns_between_courses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = FakeFrame()
    page = FakePage(frame)
    actions: list[str] = []
    current_course = ""

    async def course_titles(_frame: FakeFrame) -> list[str]:
        return ["First Course", "Second Course"]

    async def open_course(_frame: FakeFrame, index: int, title: str) -> None:
        nonlocal current_course
        assert ("First Course", "Second Course")[index] == title
        current_course = title
        actions.append(f"open:{title}")

    async def wait_for_course_page(_page: FakePage) -> FakeFrame:
        actions.append(f"loaded:{current_course}")
        return frame

    async def open_course_grades(current_frame: FakeFrame) -> FakeFrame:
        actions.append(f"grades:{current_course}")
        return current_frame

    async def expand(_frame: FakeFrame) -> None:
        actions.append(f"expand:{current_course}")

    def parse(
        _html: str,
        *,
        course: str,
        reference: datetime | None,
    ) -> list[dict[str, object]]:
        assert reference == REFERENCE
        actions.append(f"parse:{course}")
        return [
            {
                "course": course,
                "title": "Synthetic work",
                "dueDate": "2026-08-28",
                "dueTime": None,
                "status": "due",
            }
        ]

    async def return_to_grades() -> None:
        actions.append("return")

    monkeypatch.setattr(ic_agenda, "_visible_course_titles", course_titles)
    monkeypatch.setattr(ic_agenda, "_open_course", open_course)
    monkeypatch.setattr(ic_agenda, "_wait_for_course_page", wait_for_course_page)
    monkeypatch.setattr(ic_agenda, "_open_course_grades", open_course_grades)
    monkeypatch.setattr(ic_agenda, "_expand_assignment_categories", expand)
    monkeypatch.setattr(ic_agenda, "parse_infinite_campus_course_grades", parse)

    records = asyncio.run(
        collect_infinite_campus_agenda(
            cast(Page, cast(object, page)),
            return_to_grades=return_to_grades,
            reference=REFERENCE,
        )
    )

    assert actions == [
        "open:First Course",
        "loaded:First Course",
        "grades:First Course",
        "expand:First Course",
        "parse:First Course",
        "return",
        "open:Second Course",
        "loaded:Second Course",
        "grades:Second Course",
        "expand:Second Course",
        "parse:Second Course",
    ]
    assert [record["course"] for record in records] == [
        "First Course",
        "Second Course",
    ]


def test_engine_navigates_to_grades_and_supplies_return_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage(FakeFrame())
    engine = InfiniteCampus(
        cast(Page, cast(object, page)),
        "student",
        "password",
        "https://ic.example/campus/portal",
    )
    navigation: list[bool] = []
    selected: list[tuple[str, ...]] = []
    collections = 0

    async def navigate(*, force: bool = False) -> None:
        navigation.append(force)

    async def select(_frame: FakeFrame, names: tuple[str, ...]) -> str:
        selected.append(names)
        return names[0]

    async def collect(
        page: object,
        *,
        return_to_grades: Callable[[], Awaitable[None]],
    ) -> list[dict[str, object]]:
        nonlocal collections
        assert page is engine.page
        _ = return_to_grades
        collections += 1
        return []

    monkeypatch.setattr(engine, "nav_to_grades", navigate)
    monkeypatch.setattr(engine, "select_timeframe", select)
    monkeypatch.setattr(
        "scraper.portals.infinite_campus.collect_infinite_campus_agenda",
        collect,
    )

    assert asyncio.run(engine.get_agenda()) == []
    assert selected == [("QT1", "Q1"), ("QT2", "Q2")]
    assert collections == 2
    assert navigation == [False, True, True]


def test_timeframes_and_grade_merge_keep_the_later_complete_snapshot() -> None:
    assert InfiniteCampus.timeframe_groups(1) == (
        (("QT1", "Q1"), ("QT2", "Q2")),
        ("S1",),
    )
    assert InfiniteCampus.timeframe_groups(2) == (
        (("QT3", "Q3"), ("QT4", "Q4")),
        ("S2",),
    )
    assert InfiniteCampus.merge_grade_snapshots(
        [
            {"Earlier only": 72.0, "Shared": 81.0},
            {"Current only": 94.0, "Shared": 0.0},
        ]
    ) == {
        "Earlier only": 72.0,
        "Current only": 94.0,
        "Shared": 0.0,
    }
