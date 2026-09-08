from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Literal, NotRequired, TypedDict


AgendaStatus = Literal["missing", "low_score", "due"]
AGENDA_STATUSES: tuple[AgendaStatus, ...] = ("missing", "low_score", "due")
_STATUS_PRIORITY: dict[AgendaStatus, int] = {
    "due": 0,
    "low_score": 1,
    "missing": 2,
}


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
    low_score: list[StoredAgendaItem]
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
    first = (
        portals[0]
        if len(portals) > 0 and _PORTAL_KEY.fullmatch(portals[0] or "")
        else None
    )
    second = (
        portals[1]
        if len(portals) > 1 and _PORTAL_KEY.fullmatch(portals[1] or "")
        else None
    )
    return {
        "agenda1": {"portal": first, "weeks": {}},
        "agenda2": {"portal": second, "weeks": {}},
    }


_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_NAMED_PERIOD_PREFIX = re.compile(
    r"^\s*(?:period|per)\s*(\d+)\s*[,;:.\-\N{EN DASH}\N{EM DASH}]?\s*",
    re.IGNORECASE,
)
_NUMBERED_PERIOD_PREFIX = re.compile(r"^\s*(\d+)\s*:\s*")
_MATCH_TOKEN = re.compile(r"[A-Z0-9]+")
_COURSE_ABBREVIATIONS = {
    "ACCT": "ACCOUNTING",
    "ALG": "ALGEBRA",
    "BIO": "BIOLOGY",
    "CHEM": "CHEMISTRY",
    "ECON": "ECONOMICS",
    "ENG": "ENGLISH",
    "GEOM": "GEOMETRY",
    "GOV": "GOVERNMENT",
    "HIST": "HISTORY",
    "MKTG": "MARKETING",
}


def _display_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:500]


def _course_match_parts(title: str) -> tuple[str | None, str]:
    normalized = unicodedata.normalize("NFKC", title).upper()
    period: str | None = None
    for pattern in (_NAMED_PERIOD_PREFIX, _NUMBERED_PERIOD_PREFIX):
        match = pattern.match(normalized)
        if match is not None:
            period = match.group(1)
            normalized = normalized[match.end() :]
            break
    tokens = [
        _COURSE_ABBREVIATIONS.get(token, token)
        for token in _MATCH_TOKEN.findall(normalized)
    ]
    return period, " ".join(tokens)


def _contains_token_sequence(source: Sequence[str], candidate: Sequence[str]) -> bool:
    if not candidate or len(candidate) > len(source):
        return False
    width = len(candidate)
    return any(
        tuple(source[start : start + width]) == tuple(candidate)
        for start in range(len(source) - width + 1)
    )


def _best_course_window_score(source_key: str, candidate_key: str) -> float:
    source_tokens = source_key.split()
    candidate_tokens = candidate_key.split()
    if not source_tokens or not candidate_tokens:
        return 0.0

    candidate_numbers = {token for token in candidate_tokens if token.isdigit()}
    candidate_width = len(candidate_tokens)
    scores: list[float] = []
    for width in range(max(1, candidate_width - 1), candidate_width + 2):
        if width > len(source_tokens):
            continue
        for start in range(len(source_tokens) - width + 1):
            window_tokens = source_tokens[start : start + width]
            window_numbers = {token for token in window_tokens if token.isdigit()}
            if window_numbers != candidate_numbers:
                continue
            scores.append(
                SequenceMatcher(
                    None,
                    " ".join(window_tokens),
                    candidate_key,
                ).ratio()
            )
    return max(scores, default=0.0)


def _canonical_course_title(source: str, known_titles: Sequence[object]) -> str:
    source_period, source_key = _course_match_parts(source)
    if not source_key:
        return source

    candidates: list[tuple[str, str | None, str]] = []
    for raw_title in known_titles:
        title = _display_text(raw_title)
        if not title:
            continue
        period, key = _course_match_parts(title)
        if key:
            candidates.append((title, period, key))

    exact = [candidate for candidate in candidates if candidate[2] == source_key]
    if source_period is not None:
        same_period = [
            candidate for candidate in exact if candidate[1] == source_period
        ]
        if len(same_period) == 1:
            return same_period[0][0]
    if len(exact) == 1:
        return exact[0][0]
    if exact:
        return source

    source_tokens = source_key.split()
    contained: list[tuple[int, int, str]] = []
    for title, candidate_period, candidate_key in candidates:
        if (
            source_period is not None
            and candidate_period is not None
            and source_period != candidate_period
        ):
            continue
        candidate_tokens = candidate_key.split()
        if _contains_token_sequence(source_tokens, candidate_tokens):
            contained.append((len(candidate_tokens), len(candidate_key), title))

    contained.sort(key=lambda item: (-item[0], -item[1], item[2].casefold(), item[2]))
    if contained:
        top_specificity = contained[0][:2]
        equally_specific = [
            candidate for candidate in contained if candidate[:2] == top_specificity
        ]
        if len(equally_specific) == 1:
            return contained[0][2]
        return source

    scored: list[tuple[float, str]] = []
    for title, candidate_period, candidate_key in candidates:
        if (
            source_period is not None
            and candidate_period is not None
            and source_period != candidate_period
        ):
            continue
        score = _best_course_window_score(source_key, candidate_key)
        scored.append((score, title))

    scored.sort(key=lambda item: (-item[0], item[1].casefold(), item[1]))
    if not scored or scored[0][0] < 0.88:
        return source
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.08:
        return source
    return scored[0][1]


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
            for status in AGENDA_STATUSES:
                for item in buckets[status]:
                    has_week = week in retained
                    has_course = has_week and course in retained[week]
                    added_nodes = _json_value_nodes(item)
                    if not has_week:
                        added_nodes += 1  # The week object.
                    if not has_course:
                        added_nodes += 4  # Course object plus all status arrays.
                    if retained_nodes + added_nodes > MAX_AGENDA_WEEKS_NODES:
                        return retained
                    if not has_week:
                        retained[week] = {}
                    if not has_course:
                        retained[week][course] = {
                            "missing": [],
                            "low_score": [],
                            "due": [],
                        }
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


def normalize_agenda(
    records: Iterable[Mapping[str, object]],
    *,
    known_course_titles: Sequence[object] = (),
) -> AgendaWeeks:
    deduplicated: dict[
        tuple[object, ...],
        tuple[str, str, str, str | None, AgendaStatus],
    ] = {}
    for raw in records:
        course = _display_text(raw.get("course"))
        course = _canonical_course_title(course, known_course_titles)
        title = _display_text(raw.get("title"))
        status = raw.get("status")
        if not course or not title or status not in AGENDA_STATUSES:
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
            or _STATUS_PRIORITY[status] > _STATUS_PRIORITY[existing[4]]
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
            {"missing": [], "low_score": [], "due": []},
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
                for status in AGENDA_STATUSES
            }
    return _bounded_weeks(ordered)
