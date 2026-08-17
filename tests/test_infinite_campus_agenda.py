from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable

import pytest

from scraper.portals.infinite_campus_agenda import (
    AssignmentDetail,
    InfiniteCampusAgendaError,
    ListedAssignment,
    _CANONICAL_ROWS,
    _DETAIL_READY,
    _TITLE_CELL,
    _WORKSPACE_FRAME,
    collect_infinite_campus_agenda,
    classify_infinite_campus_assignment,
    parse_infinite_campus_detail,
    parse_infinite_campus_list,
)
from scraper.agenda_contract import normalize_agenda

LOW_DETAIL_HTML = '''
<div class="selcat-schedule-startdate">08/01/2026 8:00 AM</div>
<div class="selcat-schedule-enddate">08/14/2026 12:00 PM</div>
'''

FUTURE_DETAIL_HTML = '''
<div class="selcat-schedule-startdate">08/16/2026 8:00 AM</div>
<div class="selcat-schedule-enddate">08/18/2026 11:59 PM</div>
'''

REFERENCE = datetime(2026, 8, 16, 12, 0)


LIST_HTML = '''
<div class="assignment__largeScreen--row">
  <div class="assignment__largeScreen--cell-assignmentName">Responsive duplicate</div>
</div>
<div class="selcat-assignment-row">
  <div class="assignment__largeScreen--cell-assignmentName"><a href="/generic">Synthetic quiz</a></div>
  <div class="assignment__largeScreen--cell-courseDueDate"><div>Synthetic Algebra</div></div>
  <div class="assignment-score__scores">7 / 10 (70%)</div>
</div>
'''

DETAIL_HTML = '''
<div class="selcat-schedule-startdate">08/01/2026 8:00 AM</div>
<div class="selcat-schedule-enddate">08/18/2026 11:59 PM</div>
'''


def test_list_parser_uses_only_canonical_scored_rows() -> None:
    rows = parse_infinite_campus_list(LIST_HTML, missing_keys=frozenset())

    assert len(rows) == 1
    assert rows[0].ordinal == 0
    assert rows[0].key == ("synthetic quiz", "synthetic algebra")
    assert rows[0].title == "Synthetic quiz"
    assert rows[0].course == "Synthetic Algebra"
    assert rows[0].score_text == "7 / 10 (70%)"
    assert rows[0].missing is False


def test_detail_parser_reads_explicit_local_start_and_end_dates() -> None:
    assert parse_infinite_campus_detail(DETAIL_HTML) == AssignmentDetail(
        start_at=datetime(2026, 8, 1, 8, 0),
        end_at=datetime(2026, 8, 18, 23, 59),
    )


def test_duplicate_title_course_key_is_ambiguous() -> None:
    with pytest.raises(InfiniteCampusAgendaError):
        parse_infinite_campus_list(LIST_HTML + LIST_HTML, missing_keys=frozenset())


def test_recognizable_explicit_empty_list_returns_no_rows() -> None:
    assert parse_infinite_campus_list(
        '<div class="assignment__empty">No assignments.</div>',
        missing_keys=frozenset(),
    ) == []


def test_blank_explicit_empty_marker_is_a_valid_boundary() -> None:
    assert parse_infinite_campus_list(
        '<div class="assignment__empty"></div>',
        missing_keys=frozenset(),
    ) == []


@pytest.mark.parametrize(
    "score",
    [" EXEMPT ", "Not - Graded", " UNGRADED "],
)
def test_additional_excluded_score_states_are_not_due(score: str) -> None:
    assert (
        classify_infinite_campus_assignment(
            listed(score),
            AssignmentDetail(
                start_at=datetime(2026, 8, 1, 8, 0),
                end_at=datetime(2026, 8, 18, 23, 59),
            ),
            reference=datetime(2026, 8, 16, 12, 0),
        )
        is None
    )


@pytest.mark.parametrize("score", ["79.5%", "Score: 7 / 10"])
def test_numeric_scores_below_80_are_low_score_with_decimal_or_label(score: str) -> None:
    record = classify_infinite_campus_assignment(
        listed(score),
        AssignmentDetail(
            start_at=datetime(2026, 8, 1, 8, 0),
            end_at=datetime(2026, 8, 18, 23, 59),
        ),
        reference=datetime(2026, 8, 16, 12, 0),
    )

    assert record is not None
    assert record["status"] == "low_score"


def test_multiple_points_pairs_are_not_accepted_as_a_score() -> None:
    record = classify_infinite_campus_assignment(
        listed("Scores: 7 / 10 and 8 / 10"),
        AssignmentDetail(
            start_at=datetime(2026, 8, 1, 8, 0),
            end_at=datetime(2026, 8, 18, 23, 59),
        ),
        reference=datetime(2026, 8, 16, 12, 0),
    )

    assert record is not None
    assert record["status"] == "due"


@pytest.mark.parametrize(
    "html",
    [
        '<div class="selcat-schedule-startdate">not a date</div>'
        '<div class="selcat-schedule-enddate">08/18/2026 11:59 PM</div>',
        '<div class="selcat-schedule-startdate">08/01/2026 8:00 AM</div>'
        '<div class="selcat-schedule-enddate">not a date</div>',
    ],
)
def test_detail_parser_rejects_malformed_nonblank_dates(html: str) -> None:
    with pytest.raises(InfiniteCampusAgendaError):
        parse_infinite_campus_detail(html)


@pytest.mark.parametrize(
    "cell_class",
    [
        "assignment__largeScreen--cell-assignmentName",
        "assignment__largeScreen--cell-courseDueDate",
    ],
)
def test_list_parser_rejects_missing_required_title_or_course_cell(cell_class: str) -> None:
    cells = {
        "assignment__largeScreen--cell-assignmentName": "Synthetic quiz",
        "assignment__largeScreen--cell-courseDueDate": "Synthetic Algebra",
    }
    cells.pop(cell_class)
    row = "<div class=\"selcat-assignment-row\">" + "".join(
        f'<div class="{key}">{value}</div>' for key, value in cells.items()
    ) + "</div>"

    with pytest.raises(InfiniteCampusAgendaError):
        parse_infinite_campus_list(row, missing_keys=frozenset())


def listed(score: str, *, missing: bool = False) -> ListedAssignment:
    return parse_infinite_campus_list(
        LIST_HTML.replace("7 / 10 (70%)", score),
        missing_keys=(frozenset({("synthetic quiz", "synthetic algebra")}) if missing else frozenset()),
    )[0]


@pytest.mark.parametrize(
    ("score", "missing", "end_at", "expected_status"),
    [
        ("9 / 10 (90%)", True, datetime(2026, 8, 18, 23, 59), "missing"),
        ("79%", False, datetime(2026, 8, 14, 12, 0), "low_score"),
        ("7 / 10", False, datetime(2026, 8, 14, 12, 0), "low_score"),
        ("80%", False, datetime(2026, 8, 18, 23, 59), None),
        ("81%", False, datetime(2026, 8, 18, 23, 59), None),
        ("", False, datetime(2026, 8, 18, 23, 59), "due"),
        ("", False, datetime(2026, 8, 15, 23, 59), None),
        ("Excused", False, datetime(2026, 8, 18, 23, 59), None),
        ("Pass/Fail", False, datetime(2026, 8, 18, 23, 59), None),
        ("0 / 0", False, datetime(2026, 8, 18, 23, 59), "due"),
    ],
)
def test_classification_rules(
    score: str, missing: bool, end_at: datetime, expected_status: str | None
) -> None:
    record = classify_infinite_campus_assignment(
        listed(score, missing=missing),
        AssignmentDetail(start_at=datetime(2026, 8, 1, 8, 0), end_at=end_at),
        reference=datetime(2026, 8, 16, 12, 0),
    )

    assert (record["status"] if record else None) == expected_status


def test_missing_or_low_without_end_date_fails() -> None:
    for assignment in (listed("", missing=True), listed("79%")):
        with pytest.raises(InfiniteCampusAgendaError):
            classify_infinite_campus_assignment(
                assignment,
                AssignmentDetail(start_at=None, end_at=None),
                reference=datetime(2026, 8, 16, 12, 0),
            )


def test_neutral_undated_assignment_is_excluded() -> None:
    assert classify_infinite_campus_assignment(
        listed(""),
        AssignmentDetail(start_at=None, end_at=None),
        reference=datetime(2026, 8, 16, 12, 0),
    ) is None


class FakeSelection:
    def __init__(
        self,
        workspace: "FakeWorkspace",
        *,
        generation: int,
        rows: list[tuple[int, tuple[str, str, str, str], int]] | None = None,
        count: int | None = None,
        on_click: Callable[[], None] | None = None,
        attribute: Callable[[], str | None] | None = None,
    ) -> None:
        self._workspace = workspace
        self._generation = generation
        self._rows = rows if rows is not None else []
        self._count = count
        self._on_click = on_click
        self._attribute = attribute

    def _assert_fresh(self) -> None:
        if self._generation != self._workspace._generation():
            raise InfiniteCampusAgendaError()

    async def count(self) -> int:
        self._assert_fresh()
        if self._count is not None:
            return self._count
        return len(self._rows)

    @property
    def first(self) -> "FakeSelection":
        return self.nth(0)

    def nth(self, index: int) -> "FakeSelection":
        self._assert_fresh()
        if index < 0 or index >= len(self._rows):
            return FakeSelection(
                self._workspace,
                generation=self._workspace._generation(),
                rows=[],
            )
        return FakeSelection(
            self._workspace,
            generation=self._workspace._generation(),
            rows=[self._rows[index]],
            on_click=self._on_click,
            attribute=self._attribute,
        )

    def locator(self, selector: str) -> "FakeSelection":
        self._assert_fresh()
        if selector == f"{_TITLE_CELL} a[href]" and len(self._rows) == 1:
            visible_position = self._rows[0][2]
            return FakeSelection(
                self._workspace,
                generation=self._workspace._generation(),
                rows=[self._rows[0]],
                on_click=lambda: self._workspace._open_detail(visible_position),
            )
        return FakeSelection(
            self._workspace,
            generation=self._workspace._generation(),
            rows=[],
        )

    async def get_attribute(self, name: str) -> str | None:
        self._assert_fresh()
        if name != "aria-pressed":
            return None
        if self._attribute is None:
            return None
        return self._attribute()

    async def evaluate_all(self, _script: str) -> list[str]:
        self._assert_fresh()
        return [self._workspace._row_html(index, row) for index, row, _ in self._rows]

    async def click(self) -> None:
        self._assert_fresh()
        if self._on_click is None:
            raise InfiniteCampusAgendaError()
        self._on_click()


class FakeWorkspace:
    def __init__(self, page: "FakeInfiniteCampusPage") -> None:
        self._page = page

    def _generation(self) -> int:
        return self._page.generation

    def _current_rows(self) -> list[tuple[int, tuple[str, str, str, str], int]]:
        rows: list[tuple[int, tuple[str, str, str, str], int]] = []
        for position, row in enumerate(self._page.rows):
            _, _, score_text, _ = row
            if self._page.missing_pressed and score_text != "Missing":
                continue
            rows.append((position, row, len(rows)))
        return rows

    def _row_html(self, index: int, row: tuple[str, str, str, str]) -> str:
        title, course, score, _ = row
        return (
            "<div class=\"selcat-assignment-row\">"
            f"<div class=\"assignment__largeScreen--cell-assignmentName\"><a href=\"/generic\">{title}</a></div>"
            f"<div class=\"assignment__largeScreen--cell-courseDueDate\">{course}</div>"
            f"<div class=\"assignment-score__scores\">{score}</div>"
            "</div>"
        )

    def _open_detail(self, visible_position: int) -> None:
        self._page._open_detail(visible_position)

    async def wait_for_selector(self, selector: str, **_kwargs: object) -> None:
        if selector == _DETAIL_READY:
            if self._page.view != "detail":
                raise InfiniteCampusAgendaError()
            return None
        raise InfiniteCampusAgendaError()

    def locator(self, selector: str) -> FakeSelection:
        if selector == _CANONICAL_ROWS:
            rows = self._current_rows()
            return FakeSelection(
                self,
                generation=self._generation(),
                rows=rows,
            )
        if selector == ".assignment__empty:visible":
            return FakeSelection(
                self,
                generation=self._generation(),
                count=1 if len(self._current_rows()) == 0 else 0,
            )
        return FakeSelection(self, generation=self._generation(), rows=[])

    def get_by_role(self, role: str, name: str, *, exact: bool) -> FakeSelection:
        del exact
        if role == "link" and name == "Assignments":
            rows = [(0, ("", "", "", ""), 0)] if self._page.view == "menu-open" else []
            return FakeSelection(
                self,
                generation=self._generation(),
                rows=rows,
                on_click=self._page._open_assignments,
            )

        if self._page.view != "assignments" and name != "Back":
            return FakeSelection(self, generation=self._generation(), rows=[])

        if role == "button":
            if name == "Current Term":
                return FakeSelection(
                    self,
                    generation=self._generation(),
                    rows=[(0, ("", "", "", ""), 0)],
                    on_click=self._page._open_current_term,
                    attribute=lambda: "true" if self._page.term_pressed else "false",
                )
            if name == "Missing":
                return FakeSelection(
                    self,
                    generation=self._generation(),
                    rows=[(0, ("", "", "", ""), 0)],
                    on_click=self._page._toggle_missing,
                    attribute=lambda: "true" if self._page.missing_pressed else "false",
                )
            if name == "Back":
                if self._page.hide_back_on_detail == self._page.active_detail_position:
                    return FakeSelection(
                        self,
                        generation=self._generation(),
                        rows=[],
                    )
                return FakeSelection(
                    self,
                    generation=self._generation(),
                    rows=[(0, ("", "", "", ""), 0)],
                    on_click=self._page._click_back,
                )

        return FakeSelection(self, generation=self._generation(), rows=[])

    async def content(self) -> str:
        if self._page.view != "assignments":
            if self._page.view == "detail":
                return self._page._detail_html()
            return "<div></div>"

        rows = self._current_rows()
        if not rows:
            return '<div class="assignment__empty"></div>'

        fragments: list[str] = []
        for _, row, _ in rows:
            title, course, score, _ = row
            fragments.append(
                "<div class=\"assignment__largeScreen--row\">"
                f"<div class=\"assignment__largeScreen--cell-assignmentName\">{title}</div>"
                "</div>"
            )
            fragments.append(
                "<div class=\"selcat-assignment-row\">"
                f"<div class=\"assignment__largeScreen--cell-assignmentName\"><a href=\"/generic\">{title}</a></div>"
                f"<div class=\"assignment__largeScreen--cell-courseDueDate\">{course}</div>"
                f"<div class=\"assignment-score__scores\">{score}</div>"
                "</div>"
            )
        return "<div>" + "".join(fragments) + "</div>"


class FakeInfiniteCampusPage:
    def __init__(self, rows: list[tuple[str, str, str, str]]) -> None:
        self.rows = rows
        self.actions: list[str] = []
        self.view = "home"
        self.missing_pressed = False
        self.term_pressed = False
        self.generation = 0
        self.active_detail_position: int | None = None
        self._back_count = 0
        self.reorder_after_first_detail = False
        self.shrink_after_first_back = False
        self.duplicate_key_after_first_back = False
        self.hide_back_on_detail = -1
        self._did_reorder = False
        self._did_shrink = False
        self._did_duplicate = False

    def frame(self, name: str) -> FakeWorkspace:
        assert name == _WORKSPACE_FRAME
        return FakeWorkspace(self)

    def _set_generation(self, view: str) -> None:
        self.view = view
        self.generation += 1

    def locator(self, selector: str) -> FakeSelection:
        if selector == "#menu-toggle-button":
            return FakeSelection(
                FakeWorkspace(self),
                generation=self.generation,
                count=1 if self.view in {"home", "assignments", "detail"} else 0,
                on_click=self._open_menu,
            )
        return FakeSelection(
            FakeWorkspace(self),
            generation=self.generation,
            count=0,
        )

    def _open_menu(self) -> None:
        if self.view == "menu-open":
            raise InfiniteCampusAgendaError()
        self._set_generation("menu-open")

    def _open_assignments(self) -> None:
        if self.view != "menu-open":
            raise InfiniteCampusAgendaError()
        self._set_generation("assignments")
        self.term_pressed = False
        self.actions.append("open-assignments")

    def _open_current_term(self) -> None:
        if self.view != "assignments":
            raise InfiniteCampusAgendaError()
        if self.term_pressed:
            return
        self.term_pressed = True
        self.actions.append("enable-current-term")

    def _toggle_missing(self) -> None:
        if self.view != "assignments":
            raise InfiniteCampusAgendaError()
        self.missing_pressed = not self.missing_pressed
        self.actions.append("enable-missing" if self.missing_pressed else "disable-missing")

    def _open_detail(self, visible_position: int) -> None:
        rows = [row for row in self.rows if self.missing_pressed is False or row[2] == "Missing"]
        if visible_position >= len(rows):
            raise InfiniteCampusAgendaError()
        self.active_detail_position = visible_position
        self._set_generation("detail")

    def _click_back(self) -> None:
        if self.view != "detail":
            raise InfiniteCampusAgendaError()
        self.view = "home"
        self._back_count += 1
        self._set_generation("home")
        if self._back_count == 1:
            if self.reorder_after_first_detail and not self._did_reorder and len(self.rows) >= 2:
                self._did_reorder = True
                self.rows = [self.rows[1], self.rows[0]]
            if self.shrink_after_first_back and not self._did_shrink and self.rows:
                self._did_shrink = True
                self.rows = self.rows[:-1]
            if (
                self.duplicate_key_after_first_back
                and not self._did_duplicate
                and self.rows
            ):
                self._did_duplicate = True
                self.rows.append(self.rows[0])

    def _detail_html(self) -> str:
        if self.active_detail_position is None:
            raise InfiniteCampusAgendaError()
        filtered = [row for row in self.rows if self.missing_pressed is False or row[2] == "Missing"]
        _, _, _, detail_html = filtered[self.active_detail_position]
        return detail_html


def test_collector_scrubs_every_current_term_assignment_sequentially() -> None:
    page = FakeInfiniteCampusPage(
        [
            ("Synthetic quiz", "Synthetic Algebra", "70%", LOW_DETAIL_HTML),
            ("Future notes", "Synthetic English", "", FUTURE_DETAIL_HTML),
        ]
    )

    records = asyncio.run(
        collect_infinite_campus_agenda(
            page,
            reference=REFERENCE,
        )
    )

    assert [record["status"] for record in records] == ["low_score", "due"]
    assert page.actions == [
        "open-assignments",
        "enable-current-term",
        "enable-missing",
        "capture-missing",
        "disable-missing",
        "capture-current-term",
        "open-assignments",
        "enable-current-term",
        "validate-list:0",
        "capture-detail:0",
        "back:0",
        "open-assignments",
        "enable-current-term",
        "validate-list:1",
        "capture-detail:1",
        "back:1",
    ]


def test_collector_rejects_row_reorder_without_partial_records() -> None:
    page = FakeInfiniteCampusPage(
        [
            ("Synthetic quiz", "Synthetic Algebra", "70%", LOW_DETAIL_HTML),
            ("Future notes", "Synthetic English", "", FUTURE_DETAIL_HTML),
        ]
    )
    page.reorder_after_first_detail = True

    with pytest.raises(InfiniteCampusAgendaError):
        asyncio.run(collect_infinite_campus_agenda(page, reference=REFERENCE))


def test_collector_rejects_missing_back_control() -> None:
    page = FakeInfiniteCampusPage(
        [
            ("Synthetic quiz", "Synthetic Algebra", "70%", LOW_DETAIL_HTML),
            ("Future notes", "Synthetic English", "", FUTURE_DETAIL_HTML),
        ]
    )
    page.hide_back_on_detail = 0

    with pytest.raises(InfiniteCampusAgendaError):
        asyncio.run(collect_infinite_campus_agenda(page, reference=REFERENCE))


def test_collector_rejects_row_count_shrink_after_back() -> None:
    page = FakeInfiniteCampusPage(
        [
            ("Synthetic quiz", "Synthetic Algebra", "70%", LOW_DETAIL_HTML),
            ("Future notes", "Synthetic English", "", FUTURE_DETAIL_HTML),
        ]
    )
    page.shrink_after_first_back = True

    with pytest.raises(InfiniteCampusAgendaError):
        asyncio.run(collect_infinite_campus_agenda(page, reference=REFERENCE))


def test_collector_rejects_duplicate_key_after_back() -> None:
    page = FakeInfiniteCampusPage(
        [
            ("Synthetic quiz", "Synthetic Algebra", "70%", LOW_DETAIL_HTML),
            ("Future notes", "Synthetic English", "", FUTURE_DETAIL_HTML),
        ]
    )
    page.duplicate_key_after_first_back = True

    with pytest.raises(InfiniteCampusAgendaError):
        asyncio.run(collect_infinite_campus_agenda(page, reference=REFERENCE))


def test_collector_rejects_malformed_nonblank_start_date_without_partial_records() -> None:
    page = FakeInfiniteCampusPage(
        [
            (
                "Malformed notes",
                "Synthetic Algebra",
                "70%",
                '<div class="selcat-schedule-startdate">not a date</div>'
                '<div class="selcat-schedule-enddate">08/18/2026 11:59 PM</div>',
            ),
            ("Future notes", "Synthetic English", "", FUTURE_DETAIL_HTML),
        ]
    )

    with pytest.raises(InfiniteCampusAgendaError):
        asyncio.run(collect_infinite_campus_agenda(page, reference=REFERENCE))


def test_missing_and_low_score_require_end_date() -> None:
    page = FakeInfiniteCampusPage(
        [
            (
                "Synthetic quiz",
                "Synthetic Algebra",
                "Missing",
                '<div class="selcat-schedule-startdate">08/01/2026 8:00 AM</div>'
                '<div class="selcat-schedule-enddate"></div>',
            ),
            (
                "Synthetic worksheet",
                "Synthetic English",
                "79%",
                '<div class="selcat-schedule-startdate">08/01/2026 8:00 AM</div>'
                '<div class="selcat-schedule-enddate"></div>',
            ),
        ]
    )

    with pytest.raises(InfiniteCampusAgendaError):
        asyncio.run(collect_infinite_campus_agenda(page, reference=REFERENCE))


def test_neutral_blank_end_date_does_not_block_later_assignments() -> None:
    page = FakeInfiniteCampusPage(
        [
            (
                "Synthetic quiz",
                "Synthetic Algebra",
                "90%",
                '<div class="selcat-schedule-startdate">08/01/2026 8:00 AM</div>'
                '<div class="selcat-schedule-enddate"></div>',
            ),
            ("Future notes", "Synthetic English", "", FUTURE_DETAIL_HTML),
        ]
    )

    records = asyncio.run(collect_infinite_campus_agenda(page, reference=REFERENCE))

    assert records == [
        {
            "course": "Synthetic English",
            "title": "Future notes",
            "dueDate": "2026-08-18",
            "dueTime": "23:59",
            "status": "due",
        }
    ]


def test_collected_records_use_shared_week_class_status_structure() -> None:
    records = asyncio.run(
        collect_infinite_campus_agenda(
            FakeInfiniteCampusPage([
                ("Synthetic quiz", "Synthetic Algebra", "70%", LOW_DETAIL_HTML),
                ("Future notes", "Synthetic English", "", FUTURE_DETAIL_HTML),
            ]),
            reference=REFERENCE,
        )
    )

    assert normalize_agenda(records) == {
        "2026-08-10": {
            "Synthetic Algebra": {
                "missing": [],
                "low_score": [
                    {
                        "title": "Synthetic quiz",
                        "dueDate": "2026-08-14",
                        "dueTime": "12:00",
                    }
                ],
                "due": [],
            }
        },
        "2026-08-17": {
            "Synthetic English": {
                "missing": [],
                "low_score": [],
                "due": [
                    {
                        "title": "Future notes",
                        "dueDate": "2026-08-18",
                        "dueTime": "23:59",
                    }
                ],
            }
        },
    }
