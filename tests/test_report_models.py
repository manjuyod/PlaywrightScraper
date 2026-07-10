from dataclasses import fields

from ui.report_models import Student


def test_report_student_dto_excludes_portal_credentials():
    names = {field.name for field in fields(Student)}

    assert "portal_username" not in names
    assert "portal_password" not in names
    assert "alt_portal_username" not in names
    assert "alt_portal_password" not in names


def test_report_student_maps_only_safe_api_fields():
    student = Student.from_api(
        {
            "crmstudentid": 42,
            "firstname": "Ada",
            "lastname": "Lovelace",
            "grade": "10th",
            "portal": "homeaccess",
            "portal1": "https://portal.example.test",
            "portal2": "https://agenda.example.test",
            "weeklydata": {"2026-07-06": {"Math": 94.0}},
            "weekly_agenda": {},
            "status": "synced",
            "p1username": "must-not-be-retained",
            "p1password": "must-not-be-retained",
        }
    )

    assert student.id == 42
    assert student.first_name == "Ada"
    assert student.grades["2026-07-06"]["Math"] == 94.0
    assert not hasattr(student, "portal_username")
