from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Literal, NotRequired, TypedDict


AgendaStatus = Literal["missing", "due"]


class AgendaRecord(TypedDict):
    course: str
    title: str
    dueDate: str
    dueTime: str | None
    status: AgendaStatus
    sourceId: NotRequired[str]


class StoredAgendaItem(TypedDict):
    title: str
    dueDate: str
    dueTime: str | None


class AgendaBuckets(TypedDict):
    missing: list[StoredAgendaItem]
    due: list[StoredAgendaItem]


AgendaWeeks = dict[str, dict[str, AgendaBuckets]]


# Rust accepts at most 1,000 recursively counted JSON values. Capping each
# independently normalized weeks subtree here makes the largest bundle
# 1 + 2 * (1 slot object + 1 portal value + 497 weeks values) = 999 nodes.
MAX_AGENDA_WEEKS_NODES = 497


_PORTAL_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class AgendaSlotSnapshot(TypedDict):
    portal: str | None
    weeks: AgendaWeeks


class AgendaBundle(TypedDict):
    agenda1: AgendaSlotSnapshot
    agenda2: AgendaSlotSnapshot


def monday_for(due_date: date) -> str:
    return (due_date - timedelta(days=due_date.weekday())).isoformat()


def empty_agenda_bundle(portals: Sequence[str | None]) -> AgendaBundle:
    first = portals[0] if len(portals) > 0 and _PORTAL_KEY.fullmatch(portals[0] or "") else None
    second = portals[1] if len(portals) > 1 and _PORTAL_KEY.fullmatch(portals[1] or "") else None
    return {
        "agenda1": {"portal": first, "weeks": {}},
        "agenda2": {"portal": second, "weeks": {}},
    }


_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _display_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:500]


def _json_value_nodes(value: object) -> int:
    """Count JSON values exactly as the unchanged Rust result validator does."""
    if isinstance(value, Mapping):
        return 1 + sum(_json_value_nodes(item) for item in value.values())
    if isinstance(value, list):
        return 1 + sum(_json_value_nodes(item) for item in value)
    return 1


def _bounded_weeks(weeks: AgendaWeeks) -> AgendaWeeks:
    """Retain the canonical prefix that fits one slot's independent budget."""
    retained: AgendaWeeks = {}
    retained_nodes = 1  # The root weeks object itself.
    for week, courses in weeks.items():
        for course, buckets in courses.items():
            for status in ("missing", "due"):
                for item in buckets[status]:
                    has_week = week in retained
                    has_course = has_week and course in retained[week]
                    added_nodes = _json_value_nodes(item)
                    if not has_week:
                        added_nodes += 1  # The week object.
                    if not has_course:
                        added_nodes += 3  # Course object plus both status arrays.
                    if retained_nodes + added_nodes > MAX_AGENDA_WEEKS_NODES:
                        return retained
                    if not has_week:
                        retained[week] = {}
                    if not has_course:
                        retained[week][course] = {"missing": [], "due": []}
                    retained[week][course][status].append(item)
                    retained_nodes += added_nodes
    return retained


def _agenda_record_sort_key(
    record: tuple[str, str, str, str | None, AgendaStatus],
) -> tuple[str, str, str, str, str, str]:
    """Return a total order for canonical rows and duplicate representatives."""
    course, title, due_date, due_time, _status = record
    return (
        course.casefold(),
        course,
        due_date,
        due_time or "",
        title.casefold(),
        title,
    )


def normalize_agenda(records: Iterable[Mapping[str, object]]) -> AgendaWeeks:
    deduplicated: dict[
        tuple[object, ...],
        tuple[str, str, str, str | None, AgendaStatus],
    ] = {}
    for raw in records:
        course = _display_text(raw.get("course"))
        title = _display_text(raw.get("title"))
        status = raw.get("status")
        if not course or not title or status not in ("missing", "due"):
            continue
        raw_date = raw.get("dueDate")
        if not isinstance(raw_date, str):
            continue
        try:
            due_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        raw_time = raw.get("dueTime")
        if raw_time is not None and (
            not isinstance(raw_time, str) or _TIME.fullmatch(raw_time) is None
        ):
            continue
        due_time = raw_time if isinstance(raw_time, str) else None
        source_id = raw.get("sourceId")
        identity = (
            ("source", source_id.strip())
            if isinstance(source_id, str) and source_id.strip()
            else (
                "fallback",
                course.casefold(),
                title.casefold(),
                due_date.isoformat(),
                due_time,
            )
        )
        candidate = (
            course,
            title,
            due_date.isoformat(),
            due_time,
            status,
        )
        existing = deduplicated.get(identity)
        if (
            existing is None
            or (status == "missing" and existing[4] == "due")
            or (
                status == existing[4]
                and _agenda_record_sort_key(candidate)
                < _agenda_record_sort_key(existing)
            )
        ):
            deduplicated[identity] = candidate

    grouped: AgendaWeeks = {}
    for course, title, due_date, due_time, status in deduplicated.values():
        week = monday_for(date.fromisoformat(due_date))
        buckets = grouped.setdefault(week, {}).setdefault(
            course,
            {"missing": [], "due": []},
        )
        buckets[status].append(
            {"title": title, "dueDate": due_date, "dueTime": due_time}
        )

    ordered: AgendaWeeks = {}
    for week in sorted(grouped):
        ordered[week] = {}
        for course in sorted(
            grouped[week],
            key=lambda value: (value.casefold(), value),
        ):
            buckets = grouped[week][course]
            ordered[week][course] = {
                status: sorted(
                    buckets[status],
                    key=lambda item: (
                        item["dueDate"],
                        item["dueTime"] or "",
                        item["title"].casefold(),
                        item["title"],
                    ),
                )
                for status in ("missing", "due")
            }
    return _bounded_weeks(ordered)
