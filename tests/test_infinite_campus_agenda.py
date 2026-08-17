from __future__ import annotations

from datetime import datetime

import pytest

from scraper.portals.infinite_campus_agenda import (
    AssignmentDetail,
    InfiniteCampusAgendaError,
    ListedAssignment,
    classify_infinite_campus_assignment,
    parse_infinite_campus_detail,
    parse_infinite_campus_list,
)


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
