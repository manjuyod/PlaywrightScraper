# Portal-Slot Student Agendas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect complete missing-plus-upcoming agendas independently from Portal 1 and Portal 2, atomically store the two slot snapshots in the existing `weekly_agenda` JSONB field, and render two bounded, independently scrolling agenda cards beneath an equal-height grades row.

**Architecture:** Add a small portal-neutral agenda contract that converts validated assignment records into canonical Monday-week/class/status buckets. Canvas, ParentVUE, and Google Classroom each return that record contract; the agenda runner resolves both credential slots without reordering them, runs only capable collectors, and posts one all-or-nothing `agenda_success` bundle. Flask converts the stored bundle into a safe presentation model, while the existing no-JSX React dashboard renders the responsive grade and agenda grids and retains a legacy single-agenda fallback.

**Tech Stack:** Python 3.11–3.14, async Playwright 1.53+, BeautifulSoup 4.13+, Flask 3.1+, React/ReactDOM without JSX, plain CSS, pytest 8.4+, Node syntax/runtime checks, Rust/Cargo contract tests.

## Global Constraints

- Portal 1 always maps to `agenda1`; Portal 2 always maps to `agenda2`. Never reorder slots by portal type.
- Every agenda-capable portal collection includes both missing and upcoming/due assignments in one run.
- Always post both slot objects. Unconfigured, unidentified, unsupported, parserless, and successfully empty slots keep `weeks: {}` and do not fail the run.
- If any capable worker that starts fails, post one controlled failure and no partial agenda snapshot; the existing stored snapshot must remain unchanged.
- Do not deduplicate across slots. Within one slot, prefer a stable remote ID, fall back to normalized course/title/date/time, and let `missing` win over `due`.
- Store only `agenda1`, `agenda2`, each safe lowercase `portal` key or `null`, and canonical `weeks`. Never store portal URLs, usernames, passwords, student identity, cookies, tokens, session IDs, or raw responses.
- Pass the Canvas username exactly as stored. Do not append a domain or synthesize a login identifier.
- Canvas uses its authenticated same-origin session and `/api/v1/users/self/missing_submissions`, `/api/v1/planner/items`, and `/api/v1/courses`; follow pagination and interpret timestamps in the Canvas/browser timezone before calculating the local date and Monday week.
- The Canvas upcoming window starts on the current local date and ends one year later.
- ParentVUE uses the authenticated Grade Book, including concrete missing-assignment rows and the complete Upcoming Assignments section; do not infer work from counts or grade percentages.
- Preserve Google Classroom agenda support and collect both Assigned and Missing tabs.
- Do not add a database migration, table, column, token store, or assignment-level database mutation. Do not access or mutate a live database while implementing or verifying.
- Do not run a live portal scrape during implementation or automated verification. Use sanitized fixtures, mocks, and a synthetic local preview only.
- Do not export Playwright storage state or write authentication artifacts to disk.
- Do not put usernames, passwords, assignment titles, course names, raw URLs, raw HTML/JSON, or session material in logs or failure payloads.
- Keep the Heatmap tab and franchise page unchanged.
- Before editing any existing function, class, or method, run its upstream GitNexus impact analysis and report direct callers, affected processes, and risk. Stop and warn before any HIGH or CRITICAL edit.
- Before every commit, run `gitnexus_detect_changes(scope="all")` and review the affected symbols and execution flows.

## Planned File Structure

- Create `scraper/agenda_contract.py`: shared typed assignment record, canonical week bucketing, validation, ordering, and within-slot deduplication.
- Create `scraper/portals/canvas_agenda.py`: authenticated Canvas pagination, timezone conversion, and API-to-record parsing.
- Modify `scraper/portals/base.py`: explicit `agenda_capable` signal and the common `get_agenda() -> list[AgendaRecord]` interface.
- Modify `scraper/portals/canvas.py`: delegate Canvas agenda collection to the focused Canvas module.
- Create `scraper/portals/parentvue_agenda.py`: pure sanitized Grade Book HTML parser.
- Modify `scraper/portals/parentvue.py`: unambiguous Grade Book navigation and agenda delegation.
- Modify `scraper/portals/google_classroom.py`: parse and collect both tabs into the common record contract.
- Modify `scraper/agenda.py`: resolve both credential slots, run capable workers on separate pages, and post one atomic two-slot bundle.
- Modify `ui/routes.py`: defensively project new nested snapshots while retaining the legacy flat projection.
- Modify `ui/static/react-dashboard.js`: add agenda card/class/row components and restructure the student report.
- Modify `ui/static/react-dashboard.css`: shared card heights, internal scrolling, responsive stacking, disclosure, and status styling.
- Create `tests/test_agenda_contract.py`, `tests/test_canvas_agenda.py`, `tests/test_parentvue_agenda.py`, and `tests/test_google_classroom_agenda.py`: fixture/mock portal and normalization coverage.
- Create `tests/fixtures/parentvue_gradebook_agenda.html`: sanitized Grade Book rows, duplicate hidden navigation text, malformed rows, and empty sections.
- Modify `tests/test_portal_registry.py`, `tests/test_agenda_grade_db_boundary.py`, `tests/test_runner_grade_db_boundary.py`, `tests/test_read_only_dashboard_routes.py`, and `tests/test_read_only_dashboard_frontend.py`: capability, slot, boundary, projection, and component contracts.
- Create `tests/fixtures/student_agenda_page_data.json` and `tests/support/student_agenda_preview.py`: synthetic local Playwright preview with no database dependency.
- Modify `README.md` and `scraper_internal_guide.md`: document the complete slot-based workflow and remove the obsolete single-target semantics.

---

### Task 1: Portal-neutral agenda record and week contract

**Files:**
- Create: `scraper/agenda_contract.py`
- Modify: `scraper/portals/base.py`
- Create: `tests/test_agenda_contract.py`
- Modify: `tests/test_portal_registry.py`

**Interfaces:**
- Produces: `AgendaRecord`, containing `course`, `title`, `dueDate`, `dueTime`, `status`, and optional `sourceId`.
- Produces: `normalize_agenda(records: Iterable[Mapping[str, object]]) -> AgendaWeeks`.
- Produces: `empty_agenda_bundle(portals: Sequence[str | None]) -> AgendaBundle`.
- Produces: `PortalEngine.agenda_capable: ClassVar[bool] = False` and `PortalEngine.get_agenda() -> list[AgendaRecord]`.
- Consumes: ISO `YYYY-MM-DD` dates and normalized `HH:MM` local times from portal collectors.

- [ ] **Step 1: Run impact analysis for the shared portal interface**

Run GitNexus upstream impact for `PortalEngine` and `PortalEngine.get_agenda` in `scraper/portals/base.py`. Report the direct subclasses, any affected processes, and the risk level before editing.

- [ ] **Step 2: Write failing normalization and capability tests**

Create tests with these exact behavioral assertions:

```python
from scraper.agenda_contract import empty_agenda_bundle, normalize_agenda
from scraper.portals import PortalEngine


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
        }
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


def test_portals_are_not_agenda_capable_by_default() -> None:
    assert PortalEngine.agenda_capable is False
```

- [ ] **Step 3: Run the focused tests and verify the expected import/interface failures**

Run: `uv run pytest tests/test_agenda_contract.py tests/test_portal_registry.py -q`

Expected: FAIL because `scraper.agenda_contract`, `agenda_capable`, and the new no-argument record interface do not exist yet.

- [ ] **Step 4: Implement the common contract and base capability**

Add these public types and functions to `scraper/agenda_contract.py`:

```python
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
```

Implement `normalize_agenda()` with these exact rules:

- strip and cap `course` and `title` at 500 characters;
- accept only `missing` and `due`;
- parse `dueDate` with `date.fromisoformat()` and omit failures;
- accept `dueTime` only when it is `None` or matches `00:00` through `23:59`;
- deduplicate by nonblank `sourceId`, otherwise by case-folded whitespace-normalized course/title plus date/time;
- replace a prior `due` record with a duplicate `missing` record;
- insert both status arrays for every class bucket;
- sort week keys ascending, class keys case-insensitively, and rows by date/time/title.

Use this implementation shape so deduplication happens before bucket construction:

```python
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
```

In `PortalEngine`, add the new record interface while temporarily retaining the old `AgendaItem` alias so Canvas and Google Classroom remain importable until Tasks 2 and 4 migrate them:

```python
from scraper.agenda_contract import AgendaRecord

agenda_capable: ClassVar[bool] = False

async def get_agenda(self) -> list[AgendaRecord]:
    raise NotImplementedError
```

- [ ] **Step 5: Run focused tests and verify they pass**

Run: `uv run pytest tests/test_agenda_contract.py tests/test_portal_registry.py -q`

Expected: PASS.

- [ ] **Step 6: Detect changes and commit the shared contract**

Run GitNexus change detection, verify only the agenda contract and portal base interface are affected, then run:

```bash
git add scraper/agenda_contract.py scraper/portals/base.py tests/test_agenda_contract.py tests/test_portal_registry.py
git commit -m "feat: define portal agenda contract"
```

---

### Task 2: Canvas authenticated JSON agenda collector

**Files:**
- Create: `scraper/portals/canvas_agenda.py`
- Modify: `scraper/portals/canvas.py`
- Create: `tests/test_canvas_agenda.py`

**Interfaces:**
- Consumes: the authenticated `Page.context.request` cookie jar and the current Canvas origin.
- Produces: `collect_canvas_agenda(page: Page, origin: str, *, today: date | None = None) -> list[AgendaRecord]`.
- Produces: `CanvasEngine.agenda_capable = True` and `CanvasEngine.get_agenda() -> list[AgendaRecord]`.
- Consumes: `normalize_agenda()` only in tests and later in the runner; the collector itself returns records so provenance can be deduplicated centrally.

- [ ] **Step 1: Run impact analysis for the Canvas integration point**

Run GitNexus upstream impact for `CanvasEngine` and `CanvasEngine.get_agenda` in `scraper/portals/canvas.py`. Report its caller/process blast radius and stop for user review if the risk is HIGH or CRITICAL.

- [ ] **Step 2: Write failing Canvas pagination, filtering, timezone, and identity tests**

Build a fake `APIRequestContext`/`APIResponse` in `tests/test_canvas_agenda.py` whose responses cover:

```python
COURSES_PAGE_1 = [{"id": 11, "name": "English 11"}]
COURSES_PAGE_2 = [{"id": 22, "name": "Algebra II"}]
MISSING_PAGE = [
    {
        "id": 701,
        "course_id": 11,
        "name": "Late reading",
        "due_at": "2026-08-11T06:30:00Z",
    }
]
PLANNER_PAGE = [
    {
        "plannable_type": "assignment",
        "course_id": 11,
        "plannable": {
            "id": 701,
            "title": "Late reading",
            "due_at": "2026-08-11T06:30:00Z",
        },
    },
    {
        "plannable_type": "assignment",
        "course_id": 22,
        "plannable": {
            "id": 702,
            "title": "Practice set",
            "due_at": "2026-08-18T07:00:00Z",
        },
    },
    {
        "plannable_type": "calendar_event",
        "course_id": 22,
        "plannable": {
            "id": 703,
            "title": "Pep rally",
            "start_at": "2026-08-20T18:00:00Z",
        },
    },
]
```

The fake page must return `America/Phoenix` from `evaluate()`. Assert that:

- every `Link: <https://canvas.example/api/v1/courses?page=2>; rel="next"` page is fetched;
- every request stays under the supplied Canvas origin;
- `per_page=100` is sent to all three endpoints;
- planner `start_date` is `2026-08-13` local and `end_date` is `2027-08-13` local;
- the `calendar_event` row is omitted;
- UTC `2026-08-11T06:30:00Z` becomes local `2026-08-10` at `23:30`, proving dates are not truncated in UTC;
- assignment `701` has the same `sourceId` in missing and planner records so central normalization makes missing win;
- an invalid per-row timestamp is omitted, while an HTTP error, non-list response, or malformed top-level payload raises `CanvasAgendaError("canvas_agenda_request_failed")` without embedding a response body.
- `CanvasEngine.agenda_capable is True` after the delegated collector is installed.

- [ ] **Step 3: Run the Canvas tests and verify they fail**

Run: `uv run pytest tests/test_canvas_agenda.py -q`

Expected: FAIL because the focused Canvas collector and delegated engine method do not exist.

- [ ] **Step 4: Implement Canvas pagination and local-time conversion**

Create `canvas_agenda.py` with these focused helpers:

```python
_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


def _next_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    match = _NEXT_LINK.search(link_header)
    return match.group(1) if match else None


def _local_due(raw_due: object, timezone: ZoneInfo) -> tuple[str, str] | None:
    if not isinstance(raw_due, str) or not raw_due.strip():
        return None
    normalized = raw_due.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    local = parsed.astimezone(timezone)
    return local.date().isoformat(), local.strftime("%H:%M")
```

Implement `_fetch_pages()` using `page.context.request.get()`, `response.ok`, `response.headers.get("link")`, `await response.json()`, and `await response.dispose()` in `finally`. Reject next-page URLs whose scheme/netloc do not match the original `origin`.

Define the controlled boundary explicitly:

```python
class CanvasAgendaError(RuntimeError):
    def __init__(self, code: str = "canvas_agenda_request_failed") -> None:
        self.code = code
        super().__init__(code)
```

Implement `collect_canvas_agenda()` to:

1. read `window.ENV.TIMEZONE` when present, otherwise `Intl.DateTimeFormat().resolvedOptions().timeZone`, and fall back to UTC only when the returned name is not accepted by `ZoneInfo`;
2. fetch active courses, missing submissions, and planner items with `per_page=100`;
3. use local midnight ISO timestamps for planner start/end parameters;
4. resolve course IDs from active course names, with planner context title as a safe fallback;
5. map missing rows to `status="missing"` and assignment planner rows to `status="due"`;
6. set `sourceId` to `canvas:assignment:{id}` when an assignment ID exists;
7. omit unrecognized objects, missing class/title/date rows, and due work outside the inclusive one-year local window;
8. never log or include response content in an exception.

In `CanvasEngine`, delete the dashboard List View scraper and delegate exactly once:

```python
agenda_capable = True

async def get_agenda(self) -> list[AgendaRecord]:
    return await collect_canvas_agenda(self.page, _origin(self.login_url))
```

- [ ] **Step 5: Run Canvas and shared contract tests**

Run: `uv run pytest tests/test_canvas_agenda.py tests/test_agenda_contract.py -q`

Expected: PASS, including pagination, timezone, filtering, and missing-wins integration.

- [ ] **Step 6: Detect changes and commit Canvas collection**

Run GitNexus change detection, verify the Canvas agenda process and common portal contract are the only affected flows, then run:

```bash
git add scraper/portals/canvas.py scraper/portals/canvas_agenda.py tests/test_canvas_agenda.py
git commit -m "feat: collect complete Canvas agendas"
```

---

### Task 3: ParentVUE Grade Book agenda collector

**Files:**
- Create: `scraper/portals/parentvue_agenda.py`
- Modify: `scraper/portals/parentvue.py`
- Create: `tests/fixtures/parentvue_gradebook_agenda.html`
- Create: `tests/test_parentvue_agenda.py`

**Interfaces:**
- Produces: `parse_parentvue_agenda(html: str) -> list[AgendaRecord]`.
- Produces: `ParentVUE.agenda_capable = True` and `ParentVUE.get_agenda() -> list[AgendaRecord]`.
- Consumes: current authenticated Grade Book HTML only; no additional credential, token, or storage state.

- [ ] **Step 1: Run impact analysis for ParentVUE navigation and class behavior**

Run GitNexus upstream impact for `ParentVUE`, `ParentVUE.after_login`, and `ParentVUE.select_student` in `scraper/portals/parentvue.py`. Report direct callers/processes and warn before any HIGH or CRITICAL change.

- [ ] **Step 2: Add the sanitized Grade Book fixture and failing parser tests**

The fixture must contain these concrete cases without any real student data:

```html
<nav>
  <a href="/hidden-gradebook" hidden>Grade Book</a>
  <a href="/PXP2_Gradebook.aspx">Grade Book</a>
</nav>
<section class="gb-class-section" data-course-title="Algebra II">
  <h2 class="course-title">Algebra II</h2>
  <div class="assignment-row missing" data-assignment-id="pv-41">
    <span class="assignment-title">Linear review</span>
    <time class="due-date" datetime="2026-08-11">08/11/2026</time>
    <span class="status">Missing</span>
  </div>
  <div class="assignment-row missing">
    <span class="assignment-title">Undated row</span>
  </div>
</section>
<section aria-labelledby="upcoming-heading">
  <h2 id="upcoming-heading">Upcoming Assignments</h2>
  <div class="assignment-row" data-assignment-id="pv-52" data-course-title="English 11">
    <span class="assignment-title">Reading response</span>
    <time class="due-date" datetime="2026-08-16T23:59:00-07:00">08/16/2026 11:59 PM</time>
  </div>
  <div class="assignment-row"><span class="assignment-title">Malformed row</span></div>
</section>
<section aria-labelledby="empty-upcoming-heading">
  <h2 id="empty-upcoming-heading">Upcoming Assignments</h2>
</section>
```

Assert the parser returns one missing Algebra record and one due English record, omits undated/malformed rows, preserves `pv-41`/`pv-52` as `parentvue:` source IDs, and returns `[]` for valid empty sections. Add an engine test with a fake page to assert `get_agenda()` uses the current authenticated HTML and does not make a live request.

Also assert `ParentVUE.agenda_capable is True` after the collector is installed.

- [ ] **Step 3: Run ParentVUE tests and verify they fail**

Run: `uv run pytest tests/test_parentvue_agenda.py -q`

Expected: FAIL because ParentVUE has no parser, capability signal, or agenda method.

- [ ] **Step 4: Implement semantic Grade Book parsing and unambiguous navigation**

In `parentvue_agenda.py`, use BeautifulSoup and implement these exact extraction rules:

- identify assignment rows through `.assignment-row`, `.gb-assignment-row`, or a table row containing an assignment-title cell;
- resolve course from `data-course-title`, the nearest `.gb-class-section`/`.gb-class-row` course title, or a row course cell;
- resolve title from `.assignment-title`, `.assignment-name`, or a cell whose `data-label` is `Assignment`;
- resolve due value from `time[datetime]`, `.due-date`, or a cell whose `data-label` is `Due Date`;
- accept ISO `date`/`datetime` attributes and `MM/DD/YYYY` text with an optional `h:mm AM/PM` time, outputting ISO date and 24-hour time;
- classify rows under an Upcoming Assignments section as `due` and rows with a missing class/data-status/visible `Missing` marker as `missing`;
- when a row appears in both structures, emit the same `parentvue:{assignment-id}` source identity so `normalize_agenda()` makes missing win;
- omit any row without a concrete course, title, and date;
- raise `ParentVueAgendaError("parentvue_agenda_parse_failed")` only when the overall document is not recognizable, never with raw markup.

In `ParentVUE.after_login`, replace the text-filtered list-item click with a visible, href-specific Grade Book link:

```python
gradebook_link = self.page.locator(
    'a[href*="Gradebook"]:visible, a[href*="GradeBook"]:visible'
).first
await gradebook_link.click()
await self.page.wait_for_load_state(state="domcontentloaded", timeout=30000)
```

Add:

```python
agenda_capable = True

async def get_agenda(self) -> list[AgendaRecord]:
    return parse_parentvue_agenda(await self.page.content())
```

- [ ] **Step 5: Run ParentVUE and contract tests**

Run: `uv run pytest tests/test_parentvue_agenda.py tests/test_agenda_contract.py -q`

Expected: PASS for missing, upcoming, duplicate labels, empty sections, malformed rows, undated rows, and canonical weeks after normalization.

- [ ] **Step 6: Detect changes and commit ParentVUE collection**

Run GitNexus change detection, verify only ParentVUE login/navigation and agenda parsing flows are affected, then run:

```bash
git add scraper/portals/parentvue.py scraper/portals/parentvue_agenda.py tests/fixtures/parentvue_gradebook_agenda.html tests/test_parentvue_agenda.py
git commit -m "feat: collect ParentVUE agendas"
```

---

### Task 4: Google Classroom complete record normalization

**Files:**
- Modify: `scraper/portals/google_classroom.py`
- Modify: `scraper/portals/base.py`
- Create: `tests/test_google_classroom_agenda.py`

**Interfaces:**
- Produces: `_parse_classroom_agenda(html: str, status: AgendaStatus, *, reference: datetime) -> list[AgendaRecord]`.
- Produces: `GoogleClassroom.agenda_capable = True` and one `get_agenda()` call that collects Assigned and Missing.
- Consumes: the existing To-do navigation and `reconcile_day_time()` helper.

- [ ] **Step 1: Run impact analysis for Google Classroom agenda behavior**

Run GitNexus upstream impact for `GoogleClassroom` and `GoogleClassroom.get_agenda`. Report the risk, direct callers, and affected process before editing.

- [ ] **Step 2: Write failing dual-tab and parser tests**

Use sanitized Assigned and Missing HTML literals matching the existing selectors (`data-course-id`, `data-stream-item-id`, `.y9bEQb`, `.pOf0gc`). Assert:

```python
assert records == [
    {
        "sourceId": "google_classroom:stream-9",
        "course": "English 11",
        "title": "Reading response",
        "dueDate": "2026-08-16",
        "dueTime": "23:59",
        "status": "missing",
    },
    {
        "sourceId": "google_classroom:stream-10",
        "course": "Algebra II",
        "title": "Practice set",
        "dueDate": "2026-08-18",
        "dueTime": None,
        "status": "due",
    },
]
```

The fake page must record both Assigned and Missing link clicks. Also assert parser/navigation failures raise `GoogleClassroomAgendaError("google_classroom_agenda_failed")` rather than returning a silent partial/empty result.

Assert `GoogleClassroom.agenda_capable is True`, then remove the temporary tuple `AgendaItem` alias/imports once Canvas and Google Classroom both use `AgendaRecord`.

- [ ] **Step 3: Run Google Classroom tests and verify they fail**

Run: `uv run pytest tests/test_google_classroom_agenda.py -q`

Expected: FAIL because the current method accepts one target and swallows exceptions.

- [ ] **Step 4: Implement complete two-tab collection**

Refactor the existing HTML extraction into `_parse_classroom_agenda()`, preserving the current title/course/due selectors and adding `data-stream-item-id` as `sourceId`. Then implement `get_agenda()` to:

1. open To-do;
2. click Assigned, wait for `**/a/not-turned-in/**`, parse with `status="due"`;
3. click Missing, wait for `**/a/missing/**`, parse with `status="missing"`;
4. return `due_records + missing_records` so central deduplication makes missing win;
5. let navigation/parser exceptions propagate without logging page content.

Define `GoogleClassroomAgendaError` with the fixed safe code `google_classroom_agenda_failed`, wrap noncontrolled navigation/parser exceptions with `raise GoogleClassroomAgendaError() from None`, set `agenda_capable = True`, and remove the obsolete `Literal["upcoming", "missing"]` parameter.

- [ ] **Step 5: Run Google Classroom and shared contract tests**

Run: `uv run pytest tests/test_google_classroom_agenda.py tests/test_agenda_contract.py -q`

Expected: PASS.

- [ ] **Step 6: Detect changes and commit Google Classroom normalization**

Run GitNexus change detection, confirm the affected flow is limited to Google Classroom agenda collection, then run:

```bash
git add scraper/portals/google_classroom.py scraper/portals/base.py tests/test_google_classroom_agenda.py
git commit -m "feat: normalize Google Classroom agendas"
```

---

### Task 5: Slot-preserving atomic agenda runner

**Files:**
- Modify: `scraper/agenda.py`
- Modify: `tests/test_agenda_grade_db_boundary.py`
- Modify: `tests/test_runner_grade_db_boundary.py`
- Modify: `grade_db/tests/contracts.rs`

**Interfaces:**
- Consumes: `student_from_context()` fields `login_url`/`id`/`password` for Portal 1 and `alt_login_url`/`alt_id`/`alt_password` for Portal 2.
- Produces: `resolve_agenda_slots(student: Mapping[str, object]) -> tuple[AgendaSlot, AgendaSlot]`.
- Produces: `fetch_agenda(context: BrowserContext, student: dict[str, Any]) -> tuple[AgendaBundle, dict[str, Any]]`.
- Produces: one `agenda_success` post per student when all started workers succeed, including all-empty bundles.

- [ ] **Step 1: Run impact analysis for the runner boundary**

Run GitNexus upstream impact for `student_from_context`, `fetch_agenda`, `_collect_and_post_agendas`, and `main`. Report direct callers, agenda/grade processes, and the risk level. Warn before proceeding if any result is HIGH or CRITICAL.

- [ ] **Step 2: Extend the failing runner/boundary tests**

Update the shared context assertion so exact slot credentials remain unchanged in memory:

```python
student = runner.student_from_context(_context())
assert student["login_url"] == "https://portal.example/login"
assert student["id"] == "ada-user"
assert student["alt_login_url"] == "https://classroom.google.com"
assert student["alt_id"] == "ada-alt"
```

Add agenda tests for these exact cases:

- Portal 1 Canvas and Portal 2 ParentVUE remain `agenda1`/`agenda2` even if the student's legacy primary `portal` field points elsewhere.
- Two Canvas URLs create two distinct pages/workers and do not collide.
- The same normalized-looking assignment returned by both slots remains present once in each slot; no cross-slot deduplication occurs.
- Each fake engine constructor receives only its own slot username/password and receives no alternate credential.
- Unsupported and unidentified slots retain their detected key or `None` with empty weeks.
- An unconfigured slot and a capable successfully empty slot still produce one `agenda_success` bundle with both slot keys.
- Canvas/ParentVUE/Google Classroom dispatch from either slot.
- One capable worker success plus one controlled failure posts only a failure outcome, never the successful partial bundle.
- Posted/logged failure data does not contain sentinel username, password, course, assignment, query-string, cookie, or token values.
- Lease and database-client failures still cancel outstanding per-student tasks.
- The existing Rust result validator accepts the complete nested two-slot bundle, including one empty slot, without any production Rust or SQL change.

Add this Rust contract test, importing `JobKind` with the existing model imports:

```rust
#[test]
fn agenda_result_accepts_portal_slot_bundle() {
    let outcome = ResultOutcome::AgendaSuccess {
        weekly_agenda: json!({
            "agenda1": {
                "portal": "canvas",
                "weeks": {
                    "2026-08-10": {
                        "English 11": {
                            "missing": [],
                            "due": [{
                                "title": "Reading response",
                                "dueDate": "2026-08-16",
                                "dueTime": null
                            }]
                        }
                    }
                }
            },
            "agenda2": {"portal": "parentvue", "weeks": {}}
        }),
    };

    assert_eq!(outcome.validate_for_job(JobKind::Agenda), Ok(()));
}
```

- [ ] **Step 3: Run runner tests and verify the old single-target workflow fails them**

Run: `uv run pytest tests/test_runner_grade_db_boundary.py tests/test_agenda_grade_db_boundary.py -q`

Expected: FAIL because the runner selects one portal, passes one target, rejects empty results, and cannot preserve two slots.

- [ ] **Step 4: Implement safe slot resolution and complete bundle collection**

Add a credentials-safe slot type whose representation cannot print secrets:

```python
@dataclass(frozen=True, repr=False)
class AgendaSlot:
    number: Literal[1, 2]
    key: Literal["agenda1", "agenda2"]
    portal: str | None
    login_url: str | None
    username: str | None
    password: str | None


class AgendaSlotCollectionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
```

`resolve_agenda_slots()` must map the existing primary and alternate fields in their original order, normalize empty credential strings to `None`, and detect the safe portal key from each URL independently.

Implement `_collect_slot()` so it:

- creates its own page and closes it in `finally`;
- instantiates only the resolved engine with only `page`, that slot's username/password/login URL, and the existing first-name selection hint;
- calls `login()` and then the no-argument `get_agenda()`;
- normalizes the returned records with `normalize_agenda()`;
- never forwards alternate credentials, exports storage state, or logs raw values.

Implement `fetch_agenda()` using this control flow:

```python
slots = resolve_agenda_slots(student)
bundle = empty_agenda_bundle([slot.portal for slot in slots])
workers: list[tuple[AgendaSlot, asyncio.Task[AgendaWeeks]]] = []
for slot in slots:
    if not slot.portal or not slot.login_url or not slot.username or not slot.password:
        continue
    engine = get_portal(slot.portal)
    if not engine.agenda_capable:
        continue
    workers.append((slot, asyncio.create_task(_collect_slot(context, student, slot))))

results = await asyncio.gather(
    *(task for _, task in workers),
    return_exceptions=True,
)
for (slot, _task), result in zip(workers, results, strict=True):
    if isinstance(result, BaseException):
        raise AgendaSlotCollectionError(
            f"{slot.key}_{slot.portal}_failed"
        ) from None
    bundle[slot.key]["weeks"] = result
return bundle, student
```

In `_collect_and_post_agendas()`, treat every returned two-slot object—including both-empty—as `agenda_success`. Catch `AgendaSlotCollectionError` and post its safe `code`; retain `agenda_failed` for unexpected errors. Keep existing lease/database cancellation behavior.

Remove `target` from `fetch_agenda`, `_collect_and_post_agendas`, `main`, and the CLI parser. A documented synthetic example is `uv run python -m scraper.agenda --franchise-id 19 --student 42`; implementation tests must use synthetic IDs only and must not execute that command against configured services.

- [ ] **Step 5: Run runner, portal, and secret-redaction tests**

Run:

```bash
uv run pytest tests/test_runner_grade_db_boundary.py tests/test_agenda_grade_db_boundary.py tests/test_secret_redaction.py tests/test_canvas_agenda.py tests/test_parentvue_agenda.py tests/test_google_classroom_agenda.py -q
cargo test --manifest-path grade_db/Cargo.toml --test contracts
```

Expected: PASS, with exactly one success bundle or one controlled failure per student.

- [ ] **Step 6: Detect changes and commit the atomic runner**

Run GitNexus change detection, verify the expected agenda runner/result-post flow and no grade runner flow changes beyond the shared context test, then run:

```bash
git add scraper/agenda.py tests/test_agenda_grade_db_boundary.py tests/test_runner_grade_db_boundary.py grade_db/tests/contracts.rs
git commit -m "feat: collect agendas by portal slot"
```

---

### Task 6: Defensive dashboard projection with legacy fallback

**Files:**
- Modify: `ui/routes.py`
- Modify: `tests/test_read_only_dashboard_routes.py`

**Interfaces:**
- Produces: `_agenda_slots(student: DashboardStudent, *, today: date | None = None) -> list[dict[str, Any]]`.
- Preserves: `_agenda_items(student) -> list[dict[str, str]]` for legacy date-to-tuples snapshots.
- Produces: student payload fields `agendaSlots` and `agendaItems`; only one is populated for a recognized snapshot shape.

- [ ] **Step 1: Run impact analysis for the student payload projection**

Run GitNexus upstream impact for `_agenda_items`, `_student_detail`, and `student_view` in `ui/routes.py`. Report the student route process and risk before editing.

- [ ] **Step 2: Write failing new-shape, ordering, malformed-data, and legacy route tests**

Use a new-shaped synthetic `weekly_agenda` containing current, past, and future week keys, deliberately unsorted classes/status rows, malformed nested entries, and portal keys `canvas`/`parentvue`. Assert the projection is:

```python
[
    {
        "number": 1,
        "portal": "canvas",
        "portalLabel": "Canvas",
        "weeks": [
            {
                "weekStart": "2026-08-10",
                "label": "Week of Aug 10",
                "classes": [
                    {
                        "name": "English 11",
                        "count": 2,
                        "assignments": [
                            {
                                "status": "missing",
                                "title": "Late reading",
                                "dueDate": "2026-08-11",
                                "dueTime": None,
                                "dueDisplay": "Aug 11",
                            },
                            {
                                "status": "due",
                                "title": "Reading response",
                                "dueDate": "2026-08-16",
                                "dueTime": "23:59",
                                "dueDisplay": "Aug 16 · 23:59",
                            },
                        ],
                    }
                ],
            }
        ],
    },
    {
        "number": 2,
        "portal": "parentvue",
        "portalLabel": "ParentVUE",
        "weeks": [],
    },
]
```

Also assert:

- current week comes first, past weeks are newest-first, and future weeks are soonest-first when `today=date(2026, 8, 13)`;
- class names sort case-insensitively;
- missing rows precede due rows, and each bucket sorts date/time/title;
- strings are capped at the current 500-character dashboard limit;
- unsafe portal keys become `None`/no label without exposing the raw value, while malformed week/class/bucket/row values are skipped;
- a legacy `{"2026-07-15": [["English", "Essay"]]}` snapshot still populates only `agendaItems`.

- [ ] **Step 3: Run route tests and verify the projection is absent**

Run: `uv run pytest tests/test_read_only_dashboard_routes.py -q`

Expected: FAIL because the route exposes only `agendaItems`.

- [ ] **Step 4: Implement safe projection and payload selection**

Add a strict portal-key regex and safe labels:

```python
_PORTAL_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PORTAL_LABELS = {
    "aeries": "Aeries",
    "asuprep": "ASU Prep",
    "blackbaud": "Blackbaud",
    "canvas": "Canvas",
    "classlink": "ClassLink",
    "google_classroom": "Google Classroom",
    "gps": "GPS",
    "homeaccess": "Home Access Center",
    "howsschoolgoing": "How's School Going",
    "infinite_campus": "Infinite Campus",
    "k12": "K12",
    "microsoft_benjamin_franklin": "Benjamin Franklin",
    "parentvue": "ParentVUE",
    "powerschool": "PowerSchool",
    "schoology": "Schoology",
    "schooltool": "SchoolTool",
    "student_connection": "Student Connection",
}
```

For any other safe key, derive a label by replacing `_` with spaces and title-casing; never display an unsafe raw value. Parse week/due dates with `date.fromisoformat()`, require week keys to be Mondays, validate the `missing`/`due` lists, and build the exact presentation shape tested above.

Implement current/past/future week ordering with:

```python
current_monday = reference - timedelta(days=reference.weekday())

def week_rank(week_start: date) -> tuple[int, int]:
    ordinal = week_start.toordinal()
    if week_start == current_monday:
        return (0, 0)
    if week_start < current_monday:
        return (1, -ordinal)
    return (2, ordinal)
```

Update `_student_detail()` to set both keys:

```python
agenda_slots = _agenda_slots(student)
payload["agendaSlots"] = agenda_slots
payload["agendaItems"] = [] if agenda_slots else _agenda_items(student)
```

- [ ] **Step 5: Run route and dashboard-data tests**

Run: `uv run pytest tests/test_read_only_dashboard_routes.py tests/test_dashboard_data.py -q`

Expected: PASS, including legacy projection and read-only data-access assertions.

- [ ] **Step 6: Detect changes and commit the dashboard projection**

Run GitNexus change detection, confirm only the student detail/agenda projection flow is affected, then run:

```bash
git add ui/routes.py tests/test_read_only_dashboard_routes.py
git commit -m "feat: project portal slot agendas"
```

---

### Task 7: Equal-height grade row and dual scrollable agenda cards

**Files:**
- Modify: `ui/static/react-dashboard.js`
- Modify: `ui/static/react-dashboard.css`
- Modify: `tests/test_read_only_dashboard_frontend.py`
- Create: `tests/fixtures/student_agenda_page_data.json`
- Create: `tests/support/student_agenda_preview.py`
- Create: `tests/test_student_agenda_browser.py`

**Interfaces:**
- Consumes: `student.agendaSlots`, an ordered two-element presentation array, or legacy `student.agendaItems`.
- Produces: `AgendaCard({ slot })`, `AgendaClass({ classGroup })`, and `LegacyAgendaCard({ items })`.
- Produces: a localhost-only synthetic preview using the real template/assets and a Playwright behavior test that never reads CRM or Neon.
- Preserves: `GradeHistory({ history })`, `GradeHeatmap({ history })`, the Report/Heatmap navigation, and existing Card/Badge/scrollbar assets.

- [ ] **Step 1: Run impact analysis for the student React page**

Run GitNexus upstream impact for `StudentPage` and `GradeHistory` in `ui/static/react-dashboard.js`. Report the risk/process results. If either is HIGH or CRITICAL, warn before editing.

- [ ] **Step 2: Write failing component-tree and browser-behavior tests**

Extend the existing Node harness injection to expose `StudentPage`, `AgendaCard`, and `AgendaClass`. Build a synthetic two-slot payload and recursively inspect the returned React element tree. Assert:

- headings are exactly `Agenda 1 · Canvas` and `Agenda 2 · ParentVUE`;
- two agenda cards render in slot order even when Agenda 1 is empty;
- an empty slot renders no scrape-error or empty-state sentence inside the scroll region;
- week headings, native `details`/`summary`, class assignment counts, titles, and due display values are present;
- missing rows visibly contain `M` and `Missing assignment`; due rows visibly contain `DUE` and `Upcoming assignment`;
- scroll regions have `tabIndex: 0` and slot-specific `aria-label` values;
- legacy `agendaItems` still render when `agendaSlots` is empty;
- the Heatmap branch still renders `GradeHeatmap` unchanged.

Create `student_agenda_page_data.json` with a fictional student, seven current classes, at least six grade-history weeks, Agenda 1 Canvas records with both statuses, and Agenda 2 ParentVUE records with more rows than fit in the bounded card. Do not use the authorized student's name, IDs, credentials, portal URLs, or assignment content.

Create `tests/support/student_agenda_preview.py` as a standalone Flask app that loads only the synthetic page-data JSON and serves the real `ui/templates/dashboard.html`, `ui/static/react-dashboard.js`, and `ui/static/react-dashboard.css`. It must bind only `127.0.0.1`, accept an explicit `--port`, never import `ui.routes`, never create a database engine/connection, and never write a file.

Create `tests/test_student_agenda_browser.py` using the real preview app and Playwright. At a 1440×1000 viewport, assert from bounding boxes and DOM properties that:

- Current Grades and Grade History have equal rendered heights and aligned top/bottom edges;
- Grade History has `scrollHeight > clientHeight`, accepts a changed `scrollTop`, and its card heading does not move;
- Agenda 1 and Agenda 2 have equal rendered heights and aligned top/bottom edges;
- both agenda content regions have `scrollHeight > clientHeight`, and scrolling one does not change the other's `scrollTop`;
- agenda headings and legends do not move when their content regions scroll.

At a 720×1000 viewport, assert the grade cards and agenda cards have the same left coordinate within one pixel, strictly increasing top coordinates, and the same bounded heights they had at desktop. Use real click and keyboard operations to open/close native class disclosures, assert the visible `M`/`DUE` text and accessible status labels, and switch Report → Heatmap → Report without losing the layout.

- [ ] **Step 3: Run frontend tests and verify the old layout fails**

Run:

```bash
node --check ui/static/react-dashboard.js
uv run pytest tests/test_read_only_dashboard_frontend.py tests/test_student_agenda_browser.py -q
```

Expected: JavaScript syntax PASS before edits; pytest FAIL because the two-card structure, bounded scrolling, and responsive behavior are absent.

- [ ] **Step 4: Implement reusable agenda components**

Add `AgendaClass` using native disclosure semantics:

```javascript
function AgendaClass({ classGroup }) {
    return h(
        "details",
        { className: "tc-agenda-class rounded-lg border border-slate-200" },
        h(
            "summary",
            { className: "tc-focus-ring flex items-center justify-between gap-3 rounded-lg px-3 py-3" },
            h("span", { className: "min-w-0 truncate font-bold text-slate-900", title: classGroup.name }, classGroup.name),
            h("span", { className: "shrink-0 text-xs font-bold text-slate-500" }, `${classGroup.count} assignments`),
        ),
        h(
            "div",
            { className: "grid gap-2 border-t border-slate-200 p-3" },
            classGroup.assignments.map((assignment, index) =>
                h(
                    "article",
                    { key: `${assignment.status}-${assignment.dueDate}-${assignment.title}-${index}`, className: "tc-agenda-assignment" },
                    h(
                        "span",
                        {
                            className: cn("tc-agenda-marker", `tc-agenda-marker--${assignment.status}`),
                            "aria-label": assignment.status === "missing" ? "Missing assignment" : "Upcoming assignment",
                        },
                        assignment.status === "missing" ? "M" : "DUE",
                    ),
                    h("span", { className: "min-w-0 flex-1", title: assignment.title }, assignment.title),
                    h("time", { className: "shrink-0 text-xs text-slate-500", dateTime: assignment.dueDate }, assignment.dueDisplay),
                ),
            ),
        ),
    );
}
```

Implement `AgendaCard` as a fixed-height flex card with heading/legend outside its independently focusable `.tc-report-card__scroll.tc-scrollbar` region. Render no placeholder paragraph when `slot.weeks` is empty:

```javascript
function AgendaCard({ slot }) {
    const heading = `Agenda ${slot.number}${slot.portalLabel ? ` · ${slot.portalLabel}` : ""}`;
    return h(
        Card,
        { className: "tc-report-card tc-agenda-card p-5" },
        h("h2", { className: "text-lg font-extrabold text-slate-900" }, heading),
        h(
            "div",
            { className: "mt-2 flex gap-3 text-xs font-bold text-slate-500", "aria-label": "Assignment status legend" },
            h("span", null, h("span", { className: "tc-agenda-marker tc-agenda-marker--missing" }, "M"), " Missing"),
            h("span", null, h("span", { className: "tc-agenda-marker tc-agenda-marker--due" }, "DUE"), " Upcoming"),
        ),
        h(
            "div",
            {
                className: "tc-report-card__scroll tc-scrollbar mt-4 grid content-start gap-4 pr-2",
                tabIndex: 0,
                "aria-label": `${heading} assignments`,
            },
            (slot.weeks || []).map((week) =>
                h(
                    "section",
                    { key: week.weekStart, className: "grid gap-2" },
                    h("h3", { className: "font-extrabold text-slate-900" }, week.label),
                    week.classes.map((classGroup) => h(AgendaClass, { key: classGroup.name, classGroup })),
                ),
            ),
        ),
    );
}
```

Implement `LegacyAgendaCard({ items })` with the current flat article presentation and heading `Agenda`, so old snapshots remain visible without being mislabeled as a portal slot.

- [ ] **Step 5: Restructure `StudentPage` into two responsive rows**

Use this exact hierarchy in the Report branch:

```javascript
const currentGradesContent = [
    h(
        "div",
        { key: "heading", className: "flex items-center justify-between gap-3" },
        h("h2", { className: "text-lg font-extrabold text-slate-900" }, "Current grades"),
        h(Badge, { tone: statusTone(student.status) }, student.status || "never"),
    ),
    h("div", { key: "grades", className: "mt-4" }, h(GradeList, { grades: student.gradesSnapshot })),
    h("p", { key: "updated", className: "mt-4 text-xs text-slate-500" }, `Updated ${formatDate(student.updatedAt)}`),
];
const gradeHistoryHeading = h(
    "h2",
    { className: "text-lg font-extrabold text-slate-900" },
    "Grade history",
);

h(
    "div",
    { className: "grid gap-6" },
    h(
        "div",
        { className: "tc-grade-row" },
        h(Card, { className: "tc-report-card tc-grade-card p-5" }, currentGradesContent),
        h(
            Card,
            { className: "tc-report-card tc-grade-card p-5" },
            gradeHistoryHeading,
            h(
                "div",
                {
                    className: "tc-report-card__scroll tc-scrollbar mt-4 pr-2",
                    tabIndex: 0,
                    "aria-label": "Grade history",
                },
                h(GradeHistory, { history: student.grades }),
            ),
        ),
    ),
    student.agendaSlots && student.agendaSlots.length === 2
        ? h("div", { className: "tc-agenda-row" }, student.agendaSlots.map((slot) => h(AgendaCard, { key: slot.number, slot })))
        : h(LegacyAgendaCard, { items: student.agendaItems || [] }),
)
```

Define CSS variables/classes so desktop widths at `min-width: 1280px` use `0.8fr 1.2fr` for `.tc-grade-row` and `repeat(2, minmax(0, 1fr))` for `.tc-agenda-row`; below that threshold both are a single column. Keep headers/legend non-scrolling, use the existing `.tc-scrollbar`, and style missing markers with `var(--tc-red)` plus visible text while due markers use slate/blue neutrals.

- [ ] **Step 6: Run syntax and frontend contract tests**

Run:

```bash
node --check ui/static/react-dashboard.js
uv run pytest tests/test_read_only_dashboard_frontend.py tests/test_student_agenda_browser.py tests/test_template_javascript.py -q
```

Expected: PASS.

- [ ] **Step 7: Detect changes and commit the student report layout**

Run GitNexus change detection, verify only StudentPage/GradeHistory presentation paths are affected and Heatmap remains untouched, then run:

```bash
git add ui/static/react-dashboard.js ui/static/react-dashboard.css tests/test_read_only_dashboard_frontend.py tests/test_student_agenda_browser.py tests/fixtures/student_agenda_page_data.json tests/support/student_agenda_preview.py
git commit -m "feat: render dual portal agenda cards"
```

---

### Task 8: Agenda workflow documentation

**Files:**
- Modify: `README.md`
- Modify: `scraper_internal_guide.md`

**Interfaces:**
- Consumes: the synthetic student report preview created in Task 7.
- Documents: one agenda run always collects both statuses for both capable credential slots.

- [ ] **Step 1: Update workflow documentation**

Document the stored `agenda1`/`agenda2` contract, slot identity, blank-slot semantics, supported collectors, atomic failure behavior, and the synthetic preview command `uv run python tests/support/student_agenda_preview.py --port 8765`. Remove documentation that suggests `--target upcoming` or `--target missing`; the agenda CLI now always collects both.

- [ ] **Step 2: Run documentation and read-only safety checks**

Run:

```bash
uv run pytest tests/test_read_only_dashboard_frontend.py tests/test_read_only_dashboard_routes.py tests/test_retired_sheets.py -q
rg -n "storage_state|--target|p1password|p2password|access_token|refresh_token" README.md scraper_internal_guide.md tests/support/student_agenda_preview.py tests/fixtures/student_agenda_page_data.json
```

Expected: pytest PASS; the search returns no fixture secret/token/storage-state usage and no obsolete agenda target documentation. Mentions that explicitly explain forbidden fields in security documentation are acceptable only when they contain no values.

- [ ] **Step 3: Detect changes and commit the documentation**

Run GitNexus change detection, verify no production execution flow is changed by the fixture support and docs, then run:

```bash
git add README.md scraper_internal_guide.md
git commit -m "docs: explain portal slot agendas"
```

---

### Task 9: Full verification, headed visual review, and change audit

**Files:**
- Verify only; modify production files only if a failing test exposes a defect and repeat the applicable task's impact/TDD cycle first.

**Interfaces:**
- Consumes: all portal collectors, the slot runner, existing grade-database boundary, Flask projection, and React/CSS presentation.
- Produces: evidence that the feature is safe, fixture-backed, visually correct, and free of database/credential side effects.

- [ ] **Step 1: Run formatting, lint, syntax, and focused tests**

Run:

```bash
uv run ruff check scraper/agenda.py scraper/agenda_contract.py scraper/portals/base.py scraper/portals/canvas.py scraper/portals/canvas_agenda.py scraper/portals/parentvue.py scraper/portals/parentvue_agenda.py scraper/portals/google_classroom.py ui/routes.py tests
node --check ui/static/react-dashboard.js
uv run pytest tests/test_agenda_contract.py tests/test_canvas_agenda.py tests/test_parentvue_agenda.py tests/test_google_classroom_agenda.py tests/test_agenda_grade_db_boundary.py tests/test_runner_grade_db_boundary.py tests/test_dashboard_data.py tests/test_read_only_dashboard_routes.py tests/test_read_only_dashboard_frontend.py tests/test_student_agenda_browser.py tests/test_template_javascript.py tests/test_secret_redaction.py -q
```

Expected: all commands exit 0.

- [ ] **Step 2: Run complete Python and Rust suites without live credentials**

In a shell where CRM/Postgres/portal credentials are not configured, run:

```bash
uv run pytest -q
cargo test --manifest-path grade_db/Cargo.toml
```

Expected: PASS; integration tests requiring `--run-integration` remain skipped.

- [ ] **Step 3: Use the Playwright CLI skill for headed fixture verification**

Start the synthetic preview on localhost, then use a headed Playwright CLI session—never a live portal—to verify:

- desktop viewport: top cards share top/bottom edges, Grade History scrolls internally, bottom agendas have equal heights, and each agenda scrolls independently while its heading/legend stays fixed;
- narrow viewport: all four cards stack in the correct order and retain bounded internal scrolling;
- Agenda 1/Agenda 2 names match Canvas/ParentVUE fixture slots;
- class disclosures open/close by mouse and keyboard;
- `M`/`DUE` and accessible status labels are visible;
- long class/title text remains accessible through title/text;
- Report/Heatmap switching still works and Heatmap is unchanged.

Capture local screenshots only if needed for review, then close the headed browser and stop the fixture server. Do not save browser storage state.

- [ ] **Step 4: Audit the final diff and execution flows**

Run:

```bash
git diff --check
git status --short
git log --oneline -10
```

Then run `gitnexus_detect_changes(scope="compare", base_ref="HEAD~8")` or the correct pre-feature base commit, and review every affected process. Confirm there is no database schema/SQL mutation, no Heatmap/franchise-page behavior change, and no credential/token artifact.

- [ ] **Step 5: Request code review and resolve only evidence-backed findings**

Use `superpowers:requesting-code-review` for the completed feature. If review finds a defect, use `superpowers:receiving-code-review`, reproduce it with a failing test, run the required GitNexus impact analysis for the affected symbol, implement the minimal correction, re-run focused/full verification, and perform change detection before committing.

- [ ] **Step 6: Final verification commit if review required changes**

When and only when review produced code changes, run GitNexus change detection and commit the verified fix with a specific message such as:

```bash
git add scraper/agenda.py scraper/agenda_contract.py scraper/portals/base.py scraper/portals/canvas.py scraper/portals/canvas_agenda.py scraper/portals/parentvue.py scraper/portals/parentvue_agenda.py scraper/portals/google_classroom.py ui/routes.py ui/static/react-dashboard.js ui/static/react-dashboard.css tests README.md scraper_internal_guide.md
git commit -m "fix: harden portal agenda handling"
```

If review required no changes, do not create an empty commit.
