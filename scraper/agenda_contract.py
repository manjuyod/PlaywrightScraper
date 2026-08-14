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


class AgendaSlotSnapshot(TypedDict):
    portal: str | None
    weeks: AgendaWeeks


class AgendaBundle(TypedDict):
    agenda1: AgendaSlotSnapshot
    agenda2: AgendaSlotSnapshot


def monday_for(due_date: date) -> str:
    return (due_date - timedelta(days=due_date.weekday())).isoformat()


def empty_agenda_bundle(portals: Sequence[str | None]) -> AgendaBundle:
    first = portals[0] if len(portals) > 0 else None
    second = portals[1] if len(portals) > 1 else None
    return {
        "agenda1": {"portal": first, "weeks": {}},
        "agenda2": {"portal": second, "weeks": {}},
    }


_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _display_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:500]


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
        existing = deduplicated.get(identity)
        if existing is not None and existing[4] == "missing":
            continue
        if existing is None or status == "missing":
            deduplicated[identity] = (
                course,
                title,
                due_date.isoformat(),
                due_time,
                status,
            )

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
        for course in sorted(grouped[week], key=str.casefold):
            buckets = grouped[week][course]
            ordered[week][course] = {
                status: sorted(
                    buckets[status],
                    key=lambda item: (
                        item["dueDate"],
                        item["dueTime"] or "",
                        item["title"].casefold(),
                    ),
                )
                for status in ("missing", "due")
            }
    return ordered
