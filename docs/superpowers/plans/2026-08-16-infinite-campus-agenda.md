# Infinite Campus Sequential Agenda Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Infinite Campus collect a complete current-term agenda sequentially and return the existing canonical `missing`, `low_score`, and `due` buckets.

**Architecture:** A focused `infinite_campus_agenda.py` module parses canonical scored list rows and assignment details, classifies records, and owns sequential navigation within `main-workspace`. `InfiniteCampus` keeps its existing login and student-selection behavior, opts into agenda collection, and delegates once to the focused collector. The existing runner, normalizer, result boundary, and dashboard remain unchanged.

**Tech Stack:** Python 3.11, async Playwright, BeautifulSoup, pytest, Ruff, GitNexus, existing `AgendaRecord`/`normalize_agenda` contract.

## Global Constraints

- Use only `.selcat-assignment-row` as the canonical scored row; never collect the duplicate responsive representation.
- Traverse every Current Term assignment sequentially in one `main-workspace` iframe; do not create pages, contexts, workers, or parallel detail tasks.
- Classification precedence is `missing > low_score > due`.
- Missing wins regardless of score; numeric scores below 80 are Low; scores exactly 80 or above are excluded.
- Excused, exempt, pass/fail, not-graded, and ungraded score states are never Low.
- Only unscored work whose End Date is today or later is Due; historical blank/nonnumeric work is excluded.
- End Date is the only due-date source. Start Date is validation-only and is never substituted.
- Scores are classification inputs only; never persist or log numeric scores.
- Duplicate title-plus-course keys, list reorder/count drift, malformed required dates, missing iframe, failed navigation, or missing Back control fail the student's complete agenda bundle atomically.
- Other students continue after a student failure; no CRM, Neon, Rust schema, frontend, or concurrency change is in scope.
- Live validation may use only the existing active-student SELECT boundary and read-only portal requests; print aggregate counts only.

---

### Task 1: Pure Infinite Campus list, detail, and classification contract

**Files:**
- Create: `scraper/portals/infinite_campus_agenda.py`
- Create: `tests/test_infinite_campus_agenda.py`

**Interfaces:**
- Consumes: `scraper.agenda_contract.AgendaRecord`.
- Produces: `InfiniteCampusAgendaError`, `AssignmentKey`, `ListedAssignment`,
  `AssignmentDetail`,
  `parse_infinite_campus_list(html: str, *, missing_keys: frozenset[AssignmentKey]) -> list[ListedAssignment]`,
  `parse_infinite_campus_detail(html: str) -> AssignmentDetail`, and
  `classify_infinite_campus_assignment(assignment: ListedAssignment, detail: AssignmentDetail, *, reference: datetime) -> AgendaRecord | None`.

- [ ] **Step 1: Confirm the baseline and create synthetic fixtures**

Run:

```powershell
uv run pytest tests/test_agenda_contract.py tests/test_agenda_grade_db_boundary.py tests/test_secret_redaction.py -q
```

Expected: the focused shared boundary is green. Do not use live academic HTML in fixtures.

Create `tests/test_infinite_campus_agenda.py` with synthetic list/detail HTML shaped like the live DOM:

```python
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
```

- [ ] **Step 2: Write RED parser tests**

Add literal behavior tests:

```python
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
```

- [ ] **Step 3: Run parser tests and verify RED**

Run:

```powershell
uv run pytest tests/test_infinite_campus_agenda.py -q
```

Expected: collection fails because the new module/interfaces do not exist.

- [ ] **Step 4: Implement the minimal pure parser types and helpers**

Create `scraper/portals/infinite_campus_agenda.py` with these exact public shapes:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import TypeAlias

from bs4 import BeautifulSoup, Tag

from scraper.agenda_contract import AgendaRecord


AssignmentKey: TypeAlias = tuple[str, str]


class InfiniteCampusAgendaError(RuntimeError):
    def __init__(self, code: str = "infinite_campus_agenda_failed") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ListedAssignment:
    ordinal: int
    key: AssignmentKey
    course: str
    title: str
    score_text: str
    missing: bool


@dataclass(frozen=True)
class AssignmentDetail:
    start_at: datetime | None
    end_at: datetime | None


_DATE_FORMAT = "%m/%d/%Y %I:%M %p"


def _text(element: Tag | None) -> str:
    return " ".join(element.get_text(" ", strip=True).split()) if element else ""


def _key(title: str, course: str) -> AssignmentKey:
    return title.casefold(), course.casefold()
```

Implement `parse_infinite_campus_list()` to:

1. select only `.selcat-assignment-row`;
2. require nonblank title and course from the two approved cells;
3. capture score text or `""`;
4. return `[]` for a recognizable `.assignment__empty` marker;
5. reject zero rows without canonical rows or the explicit empty marker;
6. reject duplicate normalized keys;
7. set `missing=key in missing_keys`.

Implement `parse_infinite_campus_detail()` to require both date elements to exist, parse nonblank values with `_DATE_FORMAT`, preserve explicit blanks as `None`, and raise `InfiniteCampusAgendaError` for nonblank malformed values.

- [ ] **Step 5: Run parser tests and verify GREEN**

Run:

```powershell
uv run pytest tests/test_infinite_campus_agenda.py -q
uv run ruff check scraper/portals/infinite_campus_agenda.py tests/test_infinite_campus_agenda.py
```

Expected: parser tests and Ruff pass.

- [ ] **Step 6: Write RED classification tests**

Add a small literal helper in the test file:

```python
def listed(score: str, *, missing: bool = False) -> ListedAssignment:
    return parse_infinite_campus_list(
        LIST_HTML.replace("7 / 10 (70%)", score),
        missing_keys=(frozenset({("synthetic quiz", "synthetic algebra")}) if missing else frozenset()),
    )[0]
```

Add parameterized expectations that do not reuse production helpers:

```python
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
def test_classification_rules(score, missing, end_at, expected_status) -> None:
    record = classify_infinite_campus_assignment(
        listed(score, missing=missing),
        AssignmentDetail(start_at=datetime(2026, 8, 1, 8, 0), end_at=end_at),
        reference=datetime(2026, 8, 16, 12, 0),
    )

    assert (record["status"] if record else None) == expected_status
```

Add required-date tests:

```python
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
```

- [ ] **Step 7: Run classification tests and verify RED**

Run:

```powershell
uv run pytest tests/test_infinite_campus_agenda.py -q
```

Expected: parser tests pass and classification tests fail because classification is not implemented.

- [ ] **Step 8: Implement minimal classification**

Add `_PERCENT`, `_POINTS`, excluded-state matching, and
`classify_infinite_campus_assignment()`:

```python
def classify_infinite_campus_assignment(
    assignment: ListedAssignment,
    detail: AssignmentDetail,
    *,
    reference: datetime,
) -> AgendaRecord | None:
    percentage = _score_percentage(assignment.score_text)
    excluded = _excluded_score_state(assignment.score_text)

    if assignment.missing:
        status = "missing"
    elif not excluded and percentage is not None and percentage < 80:
        status = "low_score"
    elif percentage is not None or excluded:
        return None
    elif detail.end_at is not None and detail.end_at.date() >= reference.date():
        status = "due"
    else:
        return None

    if detail.end_at is None:
        raise InfiniteCampusAgendaError()
    return {
        "course": assignment.course,
        "title": assignment.title,
        "dueDate": detail.end_at.date().isoformat(),
        "dueTime": detail.end_at.strftime("%H:%M"),
        "status": status,
    }
```

Percentage takes precedence over points when both are present. Points are used only when exactly one valid earned/possible pair exists and possible is greater than zero.

- [ ] **Step 9: Run Task 1 verification**

Run:

```powershell
uv run pytest tests/test_infinite_campus_agenda.py tests/test_agenda_contract.py -q
uv run ruff check scraper/portals/infinite_campus_agenda.py tests/test_infinite_campus_agenda.py
git diff --check
```

Expected: all commands pass.

- [ ] **Step 10: Review and commit Task 1**

Request an independent read-only review of the parser/classifier diff. Fix all Critical and Important findings with RED-to-GREEN tests. Then run GitNexus change detection and commit:

```powershell
git add scraper/portals/infinite_campus_agenda.py tests/test_infinite_campus_agenda.py
git commit -m "feat(infinite-campus): classify agenda assignments"
```

---

### Task 2: Sequential Current Term assignment traversal

**Files:**
- Modify: `scraper/portals/infinite_campus_agenda.py`
- Modify: `tests/test_infinite_campus_agenda.py`

**Interfaces:**
- Consumes: Task 1 parser and classifier interfaces.
- Produces: `collect_infinite_campus_agenda(page: Page, *, reference: datetime | None = None) -> list[AgendaRecord]`.

- [ ] **Step 1: Run required impact analysis before editing existing Task 1 symbols**

Run GitNexus upstream impact for `parse_infinite_campus_list`,
`parse_infinite_campus_detail`, and `classify_infinite_campus_assignment`.
Report direct callers, processes, modules, and risk. Stop for user review if any
risk is HIGH or CRITICAL.

- [ ] **Step 2: Add a navigation fake that models the real iframe lifecycle**

In `tests/test_infinite_campus_agenda.py`, add a fake page/frame with these
observable behaviors:

```python
LOW_DETAIL_HTML = '''
<div class="selcat-schedule-startdate">08/01/2026 8:00 AM</div>
<div class="selcat-schedule-enddate">08/14/2026 12:00 PM</div>
'''

FUTURE_DETAIL_HTML = '''
<div class="selcat-schedule-startdate">08/16/2026 8:00 AM</div>
<div class="selcat-schedule-enddate">08/18/2026 11:59 PM</div>
'''

REFERENCE = datetime(2026, 8, 16, 12, 0)


class FakeInfiniteCampusPage:
    def __init__(self, rows: list[tuple[str, str, str, str]]) -> None:
        self.rows = rows
        self.actions: list[str] = []
        self.view = "home"
        self.missing_pressed = False
        self.term_pressed = False
        self.generation = 0

    def frame(self, name: str):
        assert name == "main-workspace"
        return FakeWorkspace(self, self.generation)
```

The fake must reject stale locators after every detail/back/menu navigation,
expose only one canonical `.selcat-assignment-row` per assignment, preserve the
duplicate responsive DOM in `content()`, and require the collector to reopen
Assignments and Current Term after Back.

- [ ] **Step 3: Write RED strict-sequence and atomicity tests**

Add tests with literal action ordering:

```python
def test_collector_scrubs_every_current_term_assignment_sequentially() -> None:
    page = FakeInfiniteCampusPage([
        ("Synthetic quiz", "Synthetic Algebra", "70%", LOW_DETAIL_HTML),
        ("Future notes", "Synthetic English", "", FUTURE_DETAIL_HTML),
    ])

    records = asyncio.run(
        collect_infinite_campus_agenda(
            page,
            reference=datetime(2026, 8, 16, 12, 0),
        )
    )

    assert [record["status"] for record in records] == ["low_score", "due"]
    assert page.actions == [
        "open-assignments", "enable-current-term", "enable-missing",
        "capture-missing", "disable-missing", "capture-current-term",
        "open-assignments", "enable-current-term", "validate-list:0",
        "open-detail:0", "capture-detail:0", "back:0",
        "open-assignments", "enable-current-term", "validate-list:1",
        "open-detail:1", "capture-detail:1", "back:1",
    ]


def test_collector_rejects_row_reorder_without_partial_records() -> None:
    page = FakeInfiniteCampusPage([
        ("Synthetic quiz", "Synthetic Algebra", "70%", LOW_DETAIL_HTML),
        ("Future notes", "Synthetic English", "", FUTURE_DETAIL_HTML),
    ])
    page.reorder_after_first_detail = True

    with pytest.raises(InfiniteCampusAgendaError):
        asyncio.run(collect_infinite_campus_agenda(page, reference=REFERENCE))


def test_collector_rejects_missing_back_control() -> None:
    page = FakeInfiniteCampusPage([
        ("Synthetic quiz", "Synthetic Algebra", "70%", LOW_DETAIL_HTML),
        ("Future notes", "Synthetic English", "", FUTURE_DETAIL_HTML),
    ])
    page.hide_back_on_detail = 0

    with pytest.raises(InfiniteCampusAgendaError):
        asyncio.run(collect_infinite_campus_agenda(page, reference=REFERENCE))
```

- [ ] **Step 4: Run collector tests and verify RED**

Run:

```powershell
uv run pytest tests/test_infinite_campus_agenda.py -q
```

Expected: parser/classifier tests pass; collector tests fail because
`collect_infinite_campus_agenda` is absent.

- [ ] **Step 5: Implement exact navigation helpers**

Add these constants and private helpers:

```python
_WORKSPACE_FRAME = "main-workspace"
_CANONICAL_ROWS = ".selcat-assignment-row:visible"
_TITLE_CELL = ".assignment__largeScreen--cell-assignmentName"
_COURSE_CELL = ".assignment__largeScreen--cell-courseDueDate"
_DETAIL_READY = ".selcat-schedule-startdate, .selcat-schedule-enddate"
_READINESS_TIMEOUT_MS = 30_000


def _workspace(page: Page) -> Frame:
    frame = page.frame(_WORKSPACE_FRAME)
    if frame is None:
        raise InfiniteCampusAgendaError()
    return frame
```

Implement `_open_current_term_assignments(page)` to:

1. expose the portal menu with `#menu-toggle-button` only when the exact
   Assignments link is not visible;
2. click the exact Assignments link;
3. reacquire `main-workspace`;
4. wait for exact Missing and Current Term buttons;
5. enable Current Term only when `aria-pressed != "true"`;
6. return the reacquired frame.

Implement `_set_missing(frame, enabled)` using the exact Missing button and
`aria-pressed` state. Never infer filter state from classes or row counts.

Capture list HTML only from the visible canonical locator:

```python
async def _visible_list_html(frame: Frame) -> str:
    rows = frame.locator(_CANONICAL_ROWS)
    if await rows.count() == 0:
        empty = frame.locator(".assignment__empty:visible")
        if await empty.count() == 0:
            raise InfiniteCampusAgendaError()
        return '<div class="assignment__empty"></div>'
    fragments = await rows.evaluate_all("rows => rows.map(row => row.outerHTML)")
    return "<div>" + "".join(fragments) + "</div>"
```

This prevents hidden or duplicate responsive DOM from entering the pure
parser.

- [ ] **Step 6: Implement sequential collection**

Implement `collect_infinite_campus_agenda()` with this order:

```python
async def collect_infinite_campus_agenda(
    page: Page,
    *,
    reference: datetime | None = None,
) -> list[AgendaRecord]:
    effective_reference = reference or datetime.now()
    frame = await _open_current_term_assignments(page)
    await _set_missing(frame, True)
    missing_rows = parse_infinite_campus_list(
        await _visible_list_html(frame), missing_keys=frozenset()
    )
    missing_keys = frozenset(row.key for row in missing_rows)

    await _set_missing(frame, False)
    captured = parse_infinite_campus_list(
        await _visible_list_html(frame), missing_keys=missing_keys
    )
    expected_keys = [row.key for row in captured]
    records: list[AgendaRecord] = []

    for assignment in captured:
        frame = await _open_current_term_assignments(page)
        current = parse_infinite_campus_list(
            await _visible_list_html(frame), missing_keys=missing_keys
        )
        if [row.key for row in current] != expected_keys:
            raise InfiniteCampusAgendaError()
        row = frame.locator(_CANONICAL_ROWS).nth(assignment.ordinal)
        await row.locator(f"{_TITLE_CELL} a[href]").first.click()
        await frame.wait_for_selector(_DETAIL_READY, timeout=_READINESS_TIMEOUT_MS)
        detail = parse_infinite_campus_detail(await frame.content())
        record = classify_infinite_campus_assignment(
            assignment, detail, reference=effective_reference
        )
        if record is not None:
            records.append(record)
        back = frame.get_by_role("button", name="Back", exact=True)
        if await back.count() != 1:
            raise InfiniteCampusAgendaError()
        await back.click()

    return records
```

If live/fake navigation replaces the frame after clicking detail or Back,
reacquire it before readiness/content operations. Do not loosen selectors or
add concurrency to compensate for a stale frame.

- [ ] **Step 7: Add list-count and malformed-detail atomicity regressions**

Add explicit tests for:

- row count shrinks after one Back;
- duplicate normalized key appears after one Back;
- a nonblank malformed Start Date;
- a Missing and a Low row with blank End Date;
- a neutral blank End Date that returns no record while later assignments are
  still visited.

Add a canonical normalization assertion using hand-derived literals:

```python
def test_collected_records_use_shared_week_class_status_structure() -> None:
    records = asyncio.run(
        collect_infinite_campus_agenda(
            FakeInfiniteCampusPage([
                ("Synthetic quiz", "Synthetic Algebra", "70%", LOW_DETAIL_HTML),
                ("Future notes", "Synthetic English", "", FUTURE_DETAIL_HTML),
            ]),
            reference=datetime(2026, 8, 16, 12, 0),
        )
    )

    assert normalize_agenda(records) == {
        "2026-08-10": {
            "Synthetic Algebra": {
                "missing": [],
                "low_score": [{"title": "Synthetic quiz", "dueDate": "2026-08-14", "dueTime": "12:00"}],
                "due": [],
            }
        },
        "2026-08-17": {
            "Synthetic English": {
                "missing": [],
                "low_score": [],
                "due": [{"title": "Future notes", "dueDate": "2026-08-18", "dueTime": "23:59"}],
            }
        },
    }
```

Each test asserts `InfiniteCampusAgendaError` or the complete final record
list, never a partial internal accumulator.

- [ ] **Step 8: Run Task 2 verification**

Run:

```powershell
uv run pytest tests/test_infinite_campus_agenda.py tests/test_agenda_contract.py tests/test_secret_redaction.py -q
uv run ruff check scraper/portals/infinite_campus_agenda.py tests/test_infinite_campus_agenda.py
git diff --check
```

Expected: all commands pass.

- [ ] **Step 9: Review and commit Task 2**

Request an independent reviewer to check strict sequencing, frame
reacquisition, filter state, atomicity, and absence of content-bearing logs.
Fix Critical/Important findings with RED-to-GREEN evidence. Run GitNexus
change detection, then commit:

```powershell
git add scraper/portals/infinite_campus_agenda.py tests/test_infinite_campus_agenda.py
git commit -m "feat(infinite-campus): scrub current-term agenda sequentially"
```

---

### Task 3: Infinite Campus engine and runner integration

**Files:**
- Modify: `scraper/portals/infinite_campus.py`
- Modify: `tests/test_infinite_campus_agenda.py`
- Modify: `tests/test_agenda_grade_db_boundary.py` only if the existing generic capable-slot coverage cannot assert the real registry class.
- Modify: `README.md`

**Interfaces:**
- Consumes: `collect_infinite_campus_agenda(page, *, reference=None)` from Task 2.
- Produces: `InfiniteCampus.agenda_capable = True` and `InfiniteCampus.get_agenda() -> list[AgendaRecord]`.

- [ ] **Step 1: Run mandatory impact analysis before editing `InfiniteCampus`**

Run GitNexus upstream impact for the `InfiniteCampus` class and its inherited
`get_agenda` contract. Report direct callers, affected processes/modules, and
risk. Stop for user review on HIGH or CRITICAL risk.

- [ ] **Step 2: Write RED engine delegation tests**

Add:

```python
from scraper.portals import infinite_campus as infinite_campus_module
from scraper.portals.infinite_campus import InfiniteCampus


def test_engine_delegates_agenda_collection_once(monkeypatch) -> None:
    page = object()
    calls = []

    async def collect(current_page):
        calls.append(current_page)
        return [{
            "course": "Synthetic Algebra",
            "title": "Synthetic quiz",
            "dueDate": "2026-08-18",
            "dueTime": "23:59",
            "status": "low_score",
        }]

    monkeypatch.setattr(
        infinite_campus_module,
        "collect_infinite_campus_agenda",
        collect,
        raising=False,
    )
    engine = InfiniteCampus(page, "student", "password", "https://ic.example/campus/portal")

    records = asyncio.run(engine.get_agenda())

    assert InfiniteCampus.agenda_capable is True
    assert calls == [page]
    assert records[0]["status"] == "low_score"
```

Add a registry/runner assertion that resolving `infinite_campus` creates a
capable collector without changing slot order or credential ownership.

- [ ] **Step 3: Run integration tests and verify RED**

Run:

```powershell
uv run pytest tests/test_infinite_campus_agenda.py tests/test_portal_registry.py tests/test_agenda_grade_db_boundary.py -q
```

Expected: engine delegation fails because Infinite Campus is not agenda-capable
and has no concrete `get_agenda`.

- [ ] **Step 4: Implement minimal engine wiring**

Modify imports and the class only:

```python
from scraper.agenda_contract import AgendaRecord
from .infinite_campus_agenda import collect_infinite_campus_agenda


class InfiniteCampus(PortalEngine):
    portal_key = "infinite_campus"
    url_patterns = ("campus/portal", "infinitecampus")
    agenda_capable = True

    async def get_agenda(self) -> list[AgendaRecord]:
        return await collect_infinite_campus_agenda(self.page)
```

Do not modify login, student selection, grade scraping, shared SSO helpers, or
runner concurrency.

Update the README agenda support list so it names Infinite Campus alongside
Canvas, ParentVUE, and Google Classroom, and state that Infinite Campus uses a
sequential Current Term assignment-detail scrub.

- [ ] **Step 5: Run Task 3 verification**

Run:

```powershell
uv run pytest tests/test_infinite_campus_agenda.py tests/test_portal_registry.py tests/test_agenda_grade_db_boundary.py tests/test_secret_redaction.py -q
uv run ruff check scraper/portals/infinite_campus.py scraper/portals/infinite_campus_agenda.py tests/test_infinite_campus_agenda.py tests/test_agenda_grade_db_boundary.py
git diff --check
```

Expected: all commands pass.

- [ ] **Step 6: Review and commit Task 3**

Request independent review of engine ownership, slot ordering, login retry
non-regression, and runner atomicity. Resolve Critical/Important findings with
tests. Run GitNexus change detection and commit:

```powershell
git add scraper/portals/infinite_campus.py scraper/portals/infinite_campus_agenda.py tests/test_infinite_campus_agenda.py tests/test_agenda_grade_db_boundary.py README.md
git commit -m "feat(infinite-campus): enable agenda collection"
```

Stage only files actually changed.

---

### Task 4: Final verification and bounded live validation

**Files:**
- Verify only unless a RED regression exposes a defect.
- Read: `docs/superpowers/specs/2026-08-16-infinite-campus-agenda-design.md`

**Interfaces:**
- Consumes: the complete Infinite Campus agenda path from Tasks 1-3.
- Produces: evidence that the feature satisfies the approved spec without database writes or academic-content output.

- [ ] **Step 1: Request final spec and security reviews**

Give reviewers the approved spec, the base/head commit range, and these exact
questions:

- Does every Current Term logical assignment get one sequential detail visit?
- Can duplicate responsive rows or generic links create duplicates?
- Do Missing and 80-point precedence rules match the spec?
- Can blank/malformed dates yield a partial student bundle?
- Can logs, test failures, or live output reveal content or credentials?

Fix every Critical/Important finding with a new RED test before production
changes.

- [ ] **Step 2: Run fresh focused verification**

Run:

```powershell
uv run pytest tests/test_infinite_campus_agenda.py tests/test_agenda_contract.py tests/test_agenda_grade_db_boundary.py tests/test_portal_registry.py tests/test_secret_redaction.py -q
uv run ruff check scraper/portals/infinite_campus.py scraper/portals/infinite_campus_agenda.py tests/test_infinite_campus_agenda.py tests/test_agenda_grade_db_boundary.py
git diff --check
```

Expected: zero failures and clean lint/diff output.

- [ ] **Step 3: Run the complete repository verification**

Run:

```powershell
uv run pytest -q
cargo test --manifest-path grade_db/Cargo.toml
```

Expected: all Rust tests pass. Python may show only the two user-approved,
unrelated dashboard authorization failures:

- `test_non_dev_home_is_unauthorized_without_loading_dashboard_data`
- `test_non_dev_jobs_api_is_unauthorized_without_loading_jobs`

No other failure is acceptable.

- [ ] **Step 4: Run bounded live Infinite Campus validation**

Use the exact active-student CRM SELECT boundary with franchise 57 and exact
student selection in memory. Do not start a grade-db job or write CRM/Neon.
Launch an isolated temporary Chrome profile, invoke the underlying Infinite
Campus login body once to avoid whole-login retry, then call
`InfiniteCampus.get_agenda()`.

Print one JSON object containing only `ok`, integer
`current_term_assignment_count`, integer `record_count`, integer
`status_counts` for `missing`/`low_score`/`due`, integer `week_count`, and
integer `week_class_group_count`. Never print names, titles, courses, dates,
scores, IDs, credentials, URLs, HTML, cookies, or tokens.

- [ ] **Step 5: Clean up the live session safely**

Validate the recorded PID is Chrome and the resolved profile path is under the
system temp directory with prefix `PlaywrightScraper-guided-munia-ic-`. Stop
only that PID and permanently remove only that validated temporary directory.
Report cleanup without printing its path.

- [ ] **Step 6: Run pre-publication change detection and status checks**

Run GitNexus `detect_changes` across the complete feature range, inspect every
affected process, then run:

```powershell
git status --short
git log --oneline -8
```

Expected: clean worktree and only the intended Infinite Campus/agenda flows.
Do not push until the user explicitly chooses publication.
