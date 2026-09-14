from __future__ import annotations

import pytest

from scraper.assignment_metadata import normalize_assignment_category, normalize_assignment_score


@pytest.mark.parametrize("raw,expected", [
    ("Score 7 / 10 (70%)", "7/10"), ("Score: 7.50/10.0 (75%)", "7.5/10"),
    ("score 0.125 / 0.25 (50%)", "0.125/0.25"), ("79.50%", "79.5%"),
    ("0/10", "0/10"), ("0.00%", "0%"), ("12/10 (120%)", "12/10"),
    ("1.234567890123456789/10", "1.234567890123456789/10"),
    ("", None), (None, None), (True, None), (7, None), ({"score": "7/10"}, None),
    ("Excused", None), ("Pass/Fail", None), ("Ungraded", None),
    ("7/0", None), ("7/0.00 (70%)", None), ("-7/10", None), ("NaN", None),
    ("7/10 8/10", None), ("<b>7/10</b>", None), ("70%, 80%", None),
    ("7/10 (-70%)", None), ("Scoreboard 7/10", None), ("7,5/10", None),
    ("9" * 32 + "%", None), (" " * 129 + "7/10", None),
])
def test_score_normalization_preserves_only_unambiguous_numeric_display(raw, expected):
    assert normalize_assignment_score(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    (" Formative ", "formative"), ("SUMMATIVE", "summative"),
    ("Practice", None), ("Formative Weight: 20", None), (None, None),
    (True, None), ({"category": "formative"}, None), ("formative summative", None),
])
def test_category_requires_an_explicit_recognized_label(raw, expected):
    assert normalize_assignment_category(raw) == expected
