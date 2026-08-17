from __future__ import annotations

import json

from scraper.agenda_contract import empty_agenda_bundle, normalize_agenda


def _json_value_nodes(value: object) -> int:
    if isinstance(value, dict):
        return 1 + sum(_json_value_nodes(item) for item in value.values())
    if isinstance(value, list):
        return 1 + sum(_json_value_nodes(item) for item in value)
    return 1


def test_normalize_groups_by_monday_class_and_status_with_missing_winning() -> None:
    records = [
        {
            "sourceId": "assignment-7",
            "course": " English 11 ",
            "title": " Reading response ",
            "dueDate": "2026-08-16",
            "dueTime": "23:59",
            "status": "due",
        },
        {
            "sourceId": "assignment-7",
            "course": "English 11",
            "title": "Reading response",
            "dueDate": "2026-08-16",
            "dueTime": "23:59",
            "status": "missing",
        },
        {
            "course": "Algebra II",
            "title": "Practice set",
            "dueDate": "2026-08-18",
            "dueTime": None,
            "status": "due",
        },
    ]

    assert normalize_agenda(records) == {
        "2026-08-10": {
            "English 11": {
                "missing": [
                    {
                        "title": "Reading response",
                        "dueDate": "2026-08-16",
                        "dueTime": "23:59",
                    }
                ],
                "low_score": [],
                "due": [],
            },
        },
        "2026-08-17": {
            "Algebra II": {
                "missing": [],
                "low_score": [],
                "due": [
                    {
                        "title": "Practice set",
                        "dueDate": "2026-08-18",
                        "dueTime": None,
                    }
                ],
            },
        },
    }


def test_normalize_skips_undated_and_malformed_records() -> None:
    assert normalize_agenda(
        [
            {"course": "Math", "title": "No date", "status": "missing"},
            {"course": "Math", "title": "Bad date", "dueDate": "soon", "status": "due"},
            {"course": "", "title": "Blank course", "dueDate": "2026-08-14", "status": "due"},
        ]
    ) == {}


def test_normalize_uses_known_gradebook_title_for_agenda_abbreviation() -> None:
    records = [
        {
            "course": "Period 4, MKTG 1",
            "title": "Campaign brief",
            "dueDate": "2026-08-18",
            "dueTime": None,
            "status": "due",
        }
    ]

    weeks = normalize_agenda(
        records,
        known_course_titles=["ENGLISH 11", "MARKETING 1"],
    )

    assert list(weeks["2026-08-17"]) == ["MARKETING 1"]


def test_normalize_can_fuzzy_match_a_unique_minor_title_difference() -> None:
    records = [
        {
            "course": "Environmental Scince",
            "title": "Lab",
            "dueDate": "2026-08-18",
            "dueTime": None,
            "status": "due",
        }
    ]

    weeks = normalize_agenda(
        records,
        known_course_titles=["Environmental Science", "English 11"],
    )

    assert list(weeks["2026-08-17"]) == ["Environmental Science"]


def test_normalize_preserves_source_title_when_known_match_is_ambiguous() -> None:
    records = [
        {
            "course": "English",
            "title": "Essay",
            "dueDate": "2026-08-18",
            "dueTime": None,
            "status": "due",
        }
    ]

    weeks = normalize_agenda(
        records,
        known_course_titles=["1: ENGLISH", "2: ENGLISH"],
    )

    assert list(weeks["2026-08-17"]) == ["English"]


def test_normalize_uses_matching_period_to_resolve_duplicate_course_names() -> None:
    records = [
        {
            "course": "Period 2 English",
            "title": "Essay",
            "dueDate": "2026-08-18",
            "dueTime": None,
            "status": "due",
        }
    ]

    weeks = normalize_agenda(
        records,
        known_course_titles=["1: ENGLISH", "2: ENGLISH"],
    )

    assert list(weeks["2026-08-17"]) == ["2: ENGLISH"]


def test_empty_bundle_always_retains_both_slot_identities() -> None:
    assert empty_agenda_bundle(["canvas", None]) == {
        "agenda1": {"portal": "canvas", "weeks": {}},
        "agenda2": {"portal": None, "weeks": {}},
    }


def test_empty_bundle_discards_noncanonical_portal_keys() -> None:
    assert empty_agenda_bundle(["Canvas", "https://portal.example/canvas"]) == {
        "agenda1": {"portal": None, "weeks": {}},
        "agenda2": {"portal": None, "weeks": {}},
    }


def test_normalize_truncates_canonical_rows_at_the_weeks_node_boundary() -> None:
    """Would fail if ordering changes or one slot can exceed its result budget."""
    missing = [
        {
            "sourceId": f"assignment-{index:03d}",
            "course": "Alpha",
            "title": f"Missing {index:03d}",
            "dueDate": "2026-08-16",
            "dueTime": None,
            "status": "missing",
        }
        for index in reversed(range(124))
    ]
    later_rows = [
        {
            "sourceId": "due-after-missing",
            "course": "Alpha",
            "title": "Due before missing alphabetically",
            "dueDate": "2026-08-16",
            "dueTime": None,
            "status": "due",
        },
        {
            "sourceId": "later-course",
            "course": "Beta",
            "title": "Later course",
            "dueDate": "2026-08-16",
            "dueTime": None,
            "status": "due",
        },
        {
            "sourceId": "later-week",
            "course": "Alpha",
            "title": "Later week",
            "dueDate": "2026-08-18",
            "dueTime": None,
            "status": "due",
        },
    ]

    forward = normalize_agenda([*missing, *later_rows])
    reverse = normalize_agenda(reversed([*missing, *later_rows]))

    assert forward == reverse
    assert list(forward) == ["2026-08-10"]
    assert list(forward["2026-08-10"]) == ["Alpha"]
    assert [
        row["title"] for row in forward["2026-08-10"]["Alpha"]["missing"]
    ] == [f"Missing {index:03d}" for index in range(122)]
    assert forward["2026-08-10"]["Alpha"]["low_score"] == []
    assert forward["2026-08-10"]["Alpha"]["due"] == []
    assert _json_value_nodes(forward) == 494


def test_boundary_truncation_is_deterministic_for_casefold_equal_titles() -> None:
    """Would fail if stable input order decides which tied row is truncated."""
    variants = [
        "".join(
            character.upper() if index & (1 << position) else character
            for position, character in enumerate("abcdefgh")
        )
        for index in range(124)
    ]
    records = [
        {
            "sourceId": f"assignment-{index:03d}",
            "course": "Alpha",
            "title": title,
            "dueDate": "2026-08-16",
            "dueTime": None,
            "status": "due",
        }
        for index, title in enumerate(variants)
    ]

    outputs = [
        normalize_agenda(records),
        normalize_agenda(reversed(records)),
        normalize_agenda([*records[::2], *records[1::2]]),
    ]
    serialized = [json.dumps(output, separators=(",", ":")) for output in outputs]
    retained_titles = outputs[0]["2026-08-10"]["Alpha"]["due"]
    retained_titles = [row["title"] for row in retained_titles]

    assert serialized[0] == serialized[1] == serialized[2]
    assert len(retained_titles) == 122
    assert retained_titles == sorted(retained_titles)
    assert set(variants) - set(retained_titles) == set(sorted(variants)[-2:])
    assert _json_value_nodes(outputs[0]) == 494


def test_casefold_equal_courses_use_exact_display_value_as_secondary_order() -> None:
    """Would fail if stable input order decides case-equivalent course order."""
    records = [
        {
            "sourceId": "lower-course",
            "course": "alpha",
            "title": "Lower course",
            "dueDate": "2026-08-16",
            "dueTime": None,
            "status": "due",
        },
        {
            "sourceId": "upper-course",
            "course": "Alpha",
            "title": "Upper course",
            "dueDate": "2026-08-16",
            "dueTime": None,
            "status": "due",
        },
    ]

    forward = normalize_agenda(records)
    reverse = normalize_agenda(reversed(records))

    assert json.dumps(forward, separators=(",", ":")) == json.dumps(
        reverse,
        separators=(",", ":"),
    )
    assert list(forward["2026-08-10"]) == ["Alpha", "alpha"]


def test_same_status_duplicates_choose_a_deterministic_display_representative() -> None:
    """Would fail if the first stable/fallback duplicate controls stored casing."""
    for source_id in (None, "shared-assignment"):
        records = [
            {
                "course": "math",
                "title": "essay",
                "dueDate": "2026-08-16",
                "dueTime": None,
                "status": "due",
            },
            {
                "course": "Math",
                "title": "Essay",
                "dueDate": "2026-08-16",
                "dueTime": None,
                "status": "due",
            },
        ]
        if source_id is not None:
            for record in records:
                record["sourceId"] = source_id

        forward = normalize_agenda(records)
        reverse = normalize_agenda(reversed(records))

        assert forward == reverse == {
            "2026-08-10": {
                "Math": {
                    "missing": [],
                    "due": [
                        {
                            "title": "Essay",
                            "dueDate": "2026-08-16",
                            "dueTime": None,
                        }
                    ],
                    "low_score": [],
                }
            }
        }


def test_missing_precedence_survives_deterministic_duplicate_selection() -> None:
    """Would fail if a lower-sorting due duplicate can replace missing work."""
    due = {
        "sourceId": "shared-assignment",
        "course": "Math",
        "title": "Essay",
        "dueDate": "2026-08-16",
        "dueTime": None,
        "status": "due",
    }
    missing = {
        "sourceId": "shared-assignment",
        "course": "math",
        "title": "essay",
        "dueDate": "2026-08-16",
        "dueTime": None,
        "status": "missing",
    }

    assert normalize_agenda([due, missing]) == normalize_agenda([missing, due]) == {
        "2026-08-10": {
            "math": {
                "missing": [
                    {
                        "title": "essay",
                        "dueDate": "2026-08-16",
                        "dueTime": None,
                    }
                ],
                "low_score": [],
                "due": [],
            }
        }
    }


def test_normalize_adds_low_score_bucket_and_prefers_it_over_due() -> None:
    """Would fail if low-scoring work is discarded or duplicated as upcoming."""
    due = {
        "sourceId": "pv-7",
        "course": "Algebra II",
        "title": "Systems practice",
        "dueDate": "2026-08-14",
        "dueTime": None,
        "status": "due",
    }
    low = {**due, "status": "low_score"}

    assert normalize_agenda([due, low]) == {
        "2026-08-10": {
            "Algebra II": {
                "missing": [],
                "low_score": [
                    {
                        "title": "Systems practice",
                        "dueDate": "2026-08-14",
                        "dueTime": None,
                    }
                ],
                "due": [],
            }
        }
    }


def test_missing_precedes_low_score_for_same_assignment() -> None:
    """Would fail if an explicit missing marker can be weakened to Low."""
    base = {
        "sourceId": "pv-9",
        "course": "Biology",
        "title": "Cell transport",
        "dueDate": "2026-08-13",
        "dueTime": None,
    }

    result = normalize_agenda(
        [
            {**base, "status": "low_score"},
            {**base, "status": "missing"},
        ]
    )

    buckets = result["2026-08-10"]["Biology"]
    assert [row["title"] for row in buckets["missing"]] == ["Cell transport"]
    assert buckets["low_score"] == []
    assert buckets["due"] == []
