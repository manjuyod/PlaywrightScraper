from __future__ import annotations

from scraper.agenda_contract import empty_agenda_bundle, normalize_agenda


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
                "due": [],
            },
        },
        "2026-08-17": {
            "Algebra II": {
                "missing": [],
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
