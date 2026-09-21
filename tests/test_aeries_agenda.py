from __future__ import annotations

import asyncio
from datetime import date

import pytest

from scraper.portals.aeries import Aeries
from scraper.portals.aeries_agenda import (
    AeriesAgendaError,
    collect_aeries_agenda,
    parse_aeries_gradebook,
)


def card(
    number: int,
    title: str,
    *,
    due: str,
    grading_complete: bool,
    score: str = "",
    due_time: str = "",
    category: str = "",
) -> str:
    return f"""
    <div class="Card">
      <div id="assignment-{number}-scoreData"><span>{score}</span></div>
      <div id="assignment-{number}-completeData"><span>{score}</span></div>
      <div class="TextSubSectionCategory"><span>{category}</span></div>
      <div class="TextHeading">{number} - {title}</div>
      <div><span class="TextSubSection">Due Date:</span> {due}</div>
      <div><span class="TextSubSection">Due Time:</span> {due_time}</div>
      <div><span class="TextSubSection">Grading Complete:</span>
        {str(grading_complete)}</div>
    </div>
    """


def gradebook(*cards: str) -> str:
    return (
        '<div id="ctl00_MainContent_subGBS_assignmentsView">'
        + "".join(cards)
        + "</div>"
    )


def test_parses_one_gradebook_into_missing_low_score_and_due_records() -> None:
    html = gradebook(
        card(1, "Overdue work", due="09/08/2026", grading_complete=False,
             category="Homework"),
        card(
            2,
            "Needs revision",
            due="09/08/2026",
            due_time="3:05 PM",
            grading_complete=True,
            score="7 / 10 70%",
            category="Summative",
        ),
        card(3, "Upcoming work", due="09/14/2026", grading_complete=False,
             category="Formative"),
        card(
            4,
            "Completed work",
            due="09/09/2026",
            grading_complete=True,
            score="9 / 10 90%",
        ),
    )

    assert parse_aeries_gradebook(
        html,
        course="1- Science 8 - Trimester 1 8/26/2026 - 11/20/2026",
        reference=date(2026, 9, 11),
    ) == [
        {
            "course": "Science 8",
            "title": "Overdue work",
            "dueDate": "2026-09-08",
            "dueTime": None,
            "status": "missing",
            "category": "Homework",
        },
        {
            "course": "Science 8",
            "title": "Needs revision",
            "dueDate": "2026-09-08",
            "dueTime": "15:05",
            "status": "low_score",
            "category": "summative",
        },
        {
            "course": "Science 8",
            "title": "Upcoming work",
            "dueDate": "2026-09-14",
            "dueTime": None,
            "status": "due",
            "category": "formative",
        },
    ]


def test_zero_score_on_overdue_assignment_is_missing() -> None:
    html = gradebook(
        card(
            1,
            "Zero score",
            due="09/10/2026",
            grading_complete=True,
            score="0 / 20 0.00%",
        )
    )

    assert parse_aeries_gradebook(
        html, course="Math", reference=date(2026, 9, 11)
    )[0]["status"] == "missing"


def test_explicit_empty_assignment_panel_is_valid() -> None:
    assert parse_aeries_gradebook(
        gradebook(), course="Math", reference=date(2026, 9, 11)
    ) == []


@pytest.mark.parametrize(
    "html",
    [
        "<html></html>",
        gradebook(card(1, "No due date", due="", grading_complete=False)),
        gradebook(card(1, "Bad due date", due="not-a-date", grading_complete=False)),
    ],
)
def test_rejects_missing_or_malformed_assignment_structure(html: str) -> None:
    with pytest.raises(AeriesAgendaError):
        parse_aeries_gradebook(html, course="Math", reference=date(2026, 9, 11))


class Locator:
    def __init__(self) -> None:
        self.waits: list[dict[str, object]] = []

    async def wait_for(self, **kwargs: object) -> None:
        self.waits.append(kwargs)


class Page:
    def __init__(self, payloads: object) -> None:
        self.url = "https://district.example/student/Dashboard.aspx"
        self.payloads = payloads
        self.selector = Locator()
        self.gotos: list[str] = []
        self.evaluate_calls = 0

    async def goto(self, url: str, **_kwargs: object) -> None:
        self.gotos.append(url)
        self.url = url

    def locator(self, selector: str) -> Locator:
        assert selector == "#ctl00_MainContent_subGBS_dlGN"
        return self.selector

    async def evaluate(self, script: str) -> object:
        assert "Promise.all" in script
        self.evaluate_calls += 1
        return self.payloads


def test_collector_uses_one_navigation_and_one_concurrent_browser_evaluation() -> None:
    page = Page(
        [
            {
                "course": "Math",
                "html": gradebook(
                    card(
                        1,
                        "Tomorrow",
                        due="09/12/2099",
                        grading_complete=False,
                    )
                ),
            },
            {"course": "English", "html": gradebook()},
        ]
    )

    records = asyncio.run(
        collect_aeries_agenda(
            page,  # type: ignore[arg-type]
            login_url="https://district.example/student/LoginParent.aspx",
        )
    )

    assert len(records) == 1
    assert page.gotos == ["https://district.example/student/GradebookDetails.aspx"]
    assert page.evaluate_calls == 1
    assert page.selector.waits == [{"state": "visible", "timeout": 30_000}]


def test_aeries_advertises_agenda_capability() -> None:
    assert Aeries.agenda_capable is True
