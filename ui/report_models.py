from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


DictRow = Mapping[str, Any]


class Standing(StrEnum):
    Good = "Good"
    Fair = "Fair"
    Poor = "Poor"


@dataclass
class CourseGrade:
    course: str
    grade: float
    change: Literal[None, "+", "-"] = None


def _json_object(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


@dataclass
class Student:
    """Credential-free dashboard report data returned by the private API."""

    id: int
    grade_level: int | str
    first_name: str
    last_name: str
    grades: dict[str, dict]
    status: str
    portal: str
    portal_url: str
    alt_portal_url: str | None = None
    agenda: dict | None = None
    grades_snapshot: list[CourseGrade] | None = None
    low_grades: list[CourseGrade] | None = None
    high_grades: list[CourseGrade] | None = None
    standing: Standing | None = None

    @classmethod
    def from_api(cls, row: Mapping[str, object]) -> Student:
        student_id = row.get("crmstudentid") or row.get("id")
        if isinstance(student_id, bool):
            raise ValueError("Student ID is invalid")
        try:
            normalized_id = int(student_id)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("Student ID is invalid") from exc
        if normalized_id <= 0:
            raise ValueError("Student ID is invalid")

        grades = {
            key: value
            for key, value in _json_object(row.get("weeklydata")).items()
            if isinstance(key, str) and isinstance(value, dict) and value
        }
        return cls(
            id=normalized_id,
            first_name=str(row.get("firstname") or ""),
            last_name=str(row.get("lastname") or ""),
            grade_level=row.get("grade")
            or row.get("grade_level")
            or row.get("gradeLevel")
            or "",
            portal_url=str(row.get("portal1") or ""),
            portal=str(row.get("portal") or ""),
            alt_portal_url=(
                str(row["portal2"]) if row.get("portal2") is not None else None
            ),
            status=str(row.get("status") or "never"),
            grades=grades,
            agenda=_json_object(row.get("weekly_agenda")),
        )
