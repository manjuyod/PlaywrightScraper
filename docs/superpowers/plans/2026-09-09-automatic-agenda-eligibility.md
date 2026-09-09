# Automatic Agenda Eligibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use inline execution unless the user chooses delegation; creating this plan does not start implementation.

**Goal:** Automatically attempt agendas for grade-eligible students with at least one complete, recognized, agenda-capable slot, regardless of the retained `track_agenda` value.

**Architecture:** Rust continues to select CRM candidates and validate/persist jobs and channel results, but removes its two agenda-flag gates. Python converts and filters each candidate inside a student-specific preparation boundary before creating browser workers, reports filtered student totals, and preserves saved agendas for students skipped entirely. The JSON subprocess protocol, portal engines, collection workers, and dashboard stay unchanged.

**Tech Stack:** Python, asyncio, Playwright async API, pytest, Rust, Tokio, serde, existing SQL Server/Neon gateways, PowerShell.

**Spec:** [Automatic Agenda Eligibility Design](../specs/2026-09-09-automatic-agenda-eligibility-design.md)

## Global Constraints

The following requirements are copied from the approved spec; all tasks inherit them:

- “Grade eligibility remains a prerequisite.”
- “A complete secondary slot does not rescue an incomplete primary slot at the Rust boundary.”
- “Neither a stored portal key nor `track_agenda` overrides capability detected from each slot's current URL.”
- “Shared grade-runner conversion behavior is unchanged.”
- “For retained students, downstream `fetch_agenda` and per-slot result behavior remain unchanged.”
- “Preserve both saved agenda snapshots, their statuses, and their update timestamps.”
- “Progress counts students, not slots, and a retained student advances attempted progress once after its existing collection/result workflow finishes.”
- “The field will remain in the SQL schema, Rust models, serialized records, and existing data for backward compatibility.”
- “The design does not change `MAX_CONCURRENT_AGENDA_WORKERS`, per-student slot bounds, lease behavior, or collection failure isolation.”
- “Implementation and automated verification use local code, fixtures, and mocked database gateways.”
- “Any live pilot or scheduled run requires separate explicit authorization for its database writes, including job start and completion even when no agenda is collected.”

Additional execution constraints:

- Work on `feat/agenda-availablility`. Preserve concurrent user changes. Use the worktree skill if execution needs isolation; do not create a second feature branch automatically.
- No dependency additions, migrations, backfills, flag updates, credential files, or scraper changes. Current agenda worker limit is 6; leave its value unchanged.
- Use synthetic credentials in tests. The historical production counts are context, not test expectations.
- Run upstream GitNexus impact analysis before editing every existing function/class/method, including existing tests. Report direct callers, processes, and risk; warn before HIGH/CRITICAL edits. Use GitNexus rename for symbol renames.
- Run GitNexus change detection and `git diff --cached --check` before every commit. Inspect scope before staging; stage only the task's files. Do not commit generated binaries, logs, or Cargo targets.
- All commands below run from the repository root in PowerShell. Tests use the existing `.venv` and no `--run-integration` option.

## File Map and Task Order

| File | Planned responsibility |
| --- | --- |
| `scraper/agenda.py` | Capability helper; candidate preparation/counts; sanitized preparation log; one replacement in `main` |
| `tests/test_agenda_eligibility.py` (new) | Slot-selection matrix, error isolation, cancellation, filtered progress, no-browser/no-post behavior |
| `grade_db/src/service.rs` | Remove only `track_agenda` selection and result-acceptance gates |
| `grade_db/tests/service.rs` | Mixed tracking states, exact/franchise/global scope, primary prerequisite, accepted agenda channels, rejection guards |
| `grade_db/tests/protocol.rs` | Zero and reduced totals accepted by existing lifecycle validation |
| `README.md`, `grade_db/README.md`, `scraper_internal_guide.md` | Automatic eligibility and compatibility-field documentation; skip/retain behavior; coordinated release |
| `grade_db/sql/operations/set_runner_config.sql`, `grade_db/sql/operations/clear_runner_config.sql` | Comments only: `track_agenda` is compatibility data; preserve executable SQL |

Reference without modifying: `scraper/runner.py`, `scraper/db_cli.py`, `scraper/portals/registry.py`, `grade_db/src/models.rs`, `grade_db/src/neon.rs`, all SQL migrations, `ui/dashboard_data.py`, and existing agenda/grade/lease/idempotency tests.

Tasks 1 and 2 establish Python behavior; Task 3 establishes the matching Rust behavior; Task 4 documents and verifies the complete change. Intermediate commits are reviewable checkpoints, not independently deployable releases.

Planning-time GitNexus results (re-run before edits):

| Symbol | Direct callers/references | Upstream reach | Risk |
| --- | ---: | --- | --- |
| `scraper.agenda.main` | 2 (module entrypoint and browser-cleanup regression) | 7 nodes, primarily tests; function participates in agenda collection/result flows | LOW |
| `BoundaryService.start_job` | 4 | 5 nodes; Rust CLI entrypoint and service tests | LOW |
| `BoundaryService.post_result` | 5 | 6 nodes; Rust CLI entrypoint and channel/rejection tests | MEDIUM |

The analyzer reports truncated whole-repository flow discovery, so these counts do not prove complete coverage. Source review confirms the Python/Rust connection is a subprocess boundary. MCP transport closed during planning; the same GitNexus operations succeeded through its local CLI.

Planning validation parsed all six Python examples, the inline diagnostic, and all five Rust examples without executing them. Links and code fences were checked. Syntax validation does not establish passing tests or Rust type correctness; those are execution-time checks below. No feature tests, live probe, or deployment were executed while authoring this plan.

## Task 1: Select Students from Portal Capability

**Files:** Modify `scraper/agenda.py` immediately after `resolve_agenda_slots`; create `tests/test_agenda_eligibility.py`.

**Interfaces:**

- Consumes `resolve_agenda_slots(student: Mapping[str, object]) -> tuple[AgendaSlot, AgendaSlot]` and `get_portal(key) -> type[PortalEngine]`.
- Produces `is_agenda_eligible(student: Mapping[str, object]) -> bool`.
- The helper accepts already converted runner contexts. Rust supplies the grade prerequisite; do not duplicate CRM eligibility in Python.
- `ValueError` from `get_portal` means no registered engine for that slot. Unexpected exceptions propagate to Task 2's preparation boundary. Do not catch errors around reading `engine.agenda_capable` here.

- [x] **Step 1: Add the failing eligibility matrix.** Create this test module. Every URL is synthetic and matched locally; no portal instance is created.

```python
from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scraper import agenda
from scraper.config.logging import ContextFilter, bind_log_context, reset_log_context


def raw_student(number=1, **changes):
    row = {
        "crmstudentid": number, "franchiseid": 19,
        "firstname": "Synthetic", "lastname": "Student",
        "portal1": "https://canvas.example/login",
        "p1username": " primary user ", "p1password": " primary secret ",
        "portal2": None, "p2username": None, "p2password": None,
        "portal": "powerschool", "track_agenda": False, "auth_images": [],
    }
    row.update(changes)
    return row


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("primary,secondary,expected", [
    ("https://canvas.example/login", None, True),
    ("https://unknown.example/login", "https://canvas.example/login", True),
    ("https://powerschool.example/login", "https://canvas.example/login", True),
    ("https://canvas.example/login", "https://canvas.example/login", True),
    ("https://powerschool.example/login", None, False),
    ("https://unknown.example/login", None, False),
])
def test_capability_uses_slot_urls_not_legacy_metadata(flag, primary, secondary, expected):
    student = agenda.student_from_context(raw_student(
        portal1=primary, portal2=secondary,
        p2username="secondary user" if secondary else None,
        p2password="secondary secret" if secondary else None,
        track_agenda=flag,
    ))
    original = deepcopy(student)
    assert agenda.is_agenda_eligible(student) is expected
    assert student == original


@pytest.mark.parametrize("field", ["login_url", "id", "password"])
@pytest.mark.parametrize("blank", [None, "", " \t "])
def test_incomplete_slot_cannot_qualify(field, blank):
    student = agenda.student_from_context(raw_student())
    student[field] = blank
    assert not agenda.is_agenda_eligible(student)


def test_partial_secondary_does_not_suppress_primary():
    student = agenda.student_from_context(raw_student(
        portal2="https://canvas.example/login", p2username="secondary", p2password=" ",
    ))
    assert agenda.is_agenda_eligible(student)


def test_missing_engine_does_not_suppress_other_slot(monkeypatch):
    student = agenda.student_from_context(raw_student(
        portal2="https://parentvue.example/Login_Parent_PXP.aspx",
        p2username="secondary", p2password="secondary secret",
    ))
    real_get_portal = agenda.get_portal

    def lookup(key):
        if key == "canvas":
            raise ValueError("missing synthetic engine")
        return real_get_portal(key)

    monkeypatch.setattr(agenda, "get_portal", lookup)
    assert agenda.is_agenda_eligible(student)


def test_new_engine_capability_applies_on_next_evaluation(monkeypatch):
    engine = SimpleNamespace(agenda_capable=False)
    monkeypatch.setattr(agenda, "get_portal", lambda key: engine)
    student = agenda.student_from_context(raw_student())
    assert not agenda.is_agenda_eligible(student)
    engine.agenda_capable = True
    assert agenda.is_agenda_eligible(student)
```

- [x] **Step 2: Run the new tests and confirm the missing helper is the failure.**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agenda_eligibility.py -q
```

Expected RED: `scraper.agenda` has no `is_agenda_eligible`. Resolve environment/import failures separately; they are not the intended RED result.

- [x] **Step 3: Add this helper without changing existing slot resolution or collection functions.**

```python
def is_agenda_eligible(student: Mapping[str, object]) -> bool:
    for slot in resolve_agenda_slots(student):
        if not all((slot.login_url, slot.username, slot.password, slot.portal)):
            continue
        try:
            engine = get_portal(slot.portal)
        except ValueError:
            continue
        if engine.agenda_capable:
            return True
    return False
```

- [x] **Step 4: Run eligibility and existing registry/slot tests.**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agenda_eligibility.py tests/test_portal_registry.py tests/test_agenda_grade_db_boundary.py -q
```

Expected GREEN. Existing tests already cover collection of both slots, credential ownership, unsupported neutral results, and per-slot failure independence; preserve those assertions.

- [x] **Step 5: Review, stage these two files, run GitNexus change detection and whitespace checks, then commit.**

Commit message: `feat: determine agenda eligibility from portal capability`.

## Task 2: Filter Agenda Jobs and Isolate Preparation Failures

**Files:** Modify `scraper/agenda.py` (`main` and new helpers); extend `tests/test_agenda_eligibility.py`.

**Interfaces:**

- Consumes Task 1's `is_agenda_eligible(student: Mapping[str, object]) -> bool` and existing `student_from_context(context: Mapping[str, Any]) -> dict[str, Any]`.
- Produces `_prepare_agenda_students(rows: list[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]` and `_log_agenda_preparation(counts: Mapping[str, int]) -> None`.
- Count keys: `candidate_count`, `eligible_count`, `filtered_count`, `preparation_error_count`.
- Preserves `main(franchise_id: int | None, student_id: int | None)` and its existing return/progress, lease, browser, and completion paths.

- [x] **Step 1: Run upstream impact on `main` before editing it.** Use the MCP tool with `file_path="scraper/agenda.py"`; if transport is unavailable, use:

```powershell
npx gitnexus impact main --file scraper/agenda.py --repo PlaywrightScraper --direction upstream --include-tests --summary-only
```

- [x] **Step 2: Add preparation/error tests to the Task 1 module.** The first candidate fails at each of the three boundaries in separate cases; the second must remain selectable. Inject a `ContextFilter` as production logging does so identifiers would leak if log context were not suspended.

```python
@pytest.mark.parametrize("phase", ["conversion", "url", "capability"])
def test_preparation_errors_are_isolated_and_redacted(monkeypatch, caplog, phase):
    rows = [raw_student(1), raw_student(2)]
    secrets = ("PRIVATE_STUDENT", "PRIVATE_URL", "PRIVATE_PASSWORD", "PRIVATE_AUTH")
    message = " ".join(secrets)
    if phase == "conversion":
        original = agenda.student_from_context

        def convert(row):
            if row["crmstudentid"] == 1:
                raise RuntimeError(message)
            return original(row)

        monkeypatch.setattr(agenda, "student_from_context", convert)
    elif phase == "url":
        rows[0]["portal1"] = "https://unknown.example/PRIVATE_URL"
        original = agenda.get_portal_key_from_url

        def resolve(url):
            if "PRIVATE_URL" in url:
                raise RuntimeError(message)
            return original(url)

        monkeypatch.setattr(agenda, "get_portal_key_from_url", resolve)
    else:
        original = agenda.get_portal
        calls = 0

        def lookup(key):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError(message)
            return original(key)

        monkeypatch.setattr(agenda, "get_portal", lookup)

    caplog.set_level(logging.INFO, logger="scraper.agenda")
    context_filter = ContextFilter()
    caplog.handler.addFilter(context_filter)
    token = bind_log_context(student_name="PRIVATE_STUDENT", crmstudentid=999999)
    try:
        students, counts = agenda._prepare_agenda_students(rows)
        agenda._log_agenda_preparation(counts)
    finally:
        reset_log_context(token)
        caplog.handler.removeFilter(context_filter)

    assert [s["db_id"] for s in students] == [2]
    assert counts == {"candidate_count": 2, "eligible_count": 1,
                      "filtered_count": 0, "preparation_error_count": 1}
    record, = [r for r in caplog.records if r.getMessage() == "agenda.preparation.completed"]
    assert record.levelno == logging.WARNING
    assert not record.exc_info
    assert not hasattr(record, "crmstudentid")
    assert not hasattr(record, "student_name")
    assert all(secret not in repr(record.__dict__) for secret in secrets)
    assert all(getattr(record, key) == value for key, value in counts.items())


@pytest.mark.parametrize("signal", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
def test_preparation_does_not_swallow_process_signals(monkeypatch, signal):
    def stop(row):
        raise signal()

    monkeypatch.setattr(agenda, "student_from_context", stop)
    with pytest.raises(signal):
        agenda._prepare_agenda_students([raw_student()])


def test_preparation_logging_failure_does_not_abort_or_leak_context(monkeypatch):
    def fail_log(*args, **kwargs):
        raise RuntimeError("synthetic handler failure")

    monkeypatch.setattr(agenda.logger, "log", fail_log)
    token = bind_log_context(crmstudentid=999999)
    try:
        agenda._log_agenda_preparation({"candidate_count": 0, "eligible_count": 0,
                                       "filtered_count": 0, "preparation_error_count": 0})
        record = logging.makeLogRecord({})
        ContextFilter().filter(record)
        assert record.crmstudentid == 999999
    finally:
        reset_log_context(token)
```

- [x] **Step 3: Add a fake job harness and lifecycle tests.** Mock the boundary client and Playwright factory before calling `main`; never resolve or launch a real executable/browser. Keep `_collect_and_post_agendas` mocked here because its independent slot/result behavior has separate existing tests.

```python
@pytest.fixture
def job_harness(monkeypatch):
    state = SimpleNamespace(rows=[], starts=[], heartbeats=[], completed=[], posts=[],
                            launch_count=0, collection_count=0)

    class Client:
        def start_job(self, **kwargs):
            state.starts.append(kwargs)
            return {"job_id": "synthetic-job", "lease_token": "synthetic-lease",
                    "students": deepcopy(state.rows),
                    "progress": {"total": len(state.rows), "attempted": 0,
                                 "success": 0, "errors": 0}}

        def heartbeat(self, **kwargs):
            state.heartbeats.append(deepcopy(kwargs["progress"]))
            state.loop.call_soon_threadsafe(state.heartbeat_seen.set)
            return {"ok": True}

        def complete_job(self, **kwargs):
            state.completed.append(deepcopy(kwargs["progress"]))
            return {"ok": True}

        def post_result(self, **kwargs):
            state.posts.append(kwargs)
            return {"applied": True}

        def fail_job(self, **kwargs):
            pytest.fail("Unexpected job failure in synthetic lifecycle")

    class PlaywrightContext:
        async def __aenter__(self):
            async def launch(**kwargs):
                state.launch_count += 1
                return SimpleNamespace(close=AsyncMock())
            return SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        async def __aexit__(self, *args):
            return False

    async def collect(client, session, browser, students, progress, lease_failed):
        state.collection_count += 1
        state.loop = asyncio.get_running_loop()
        state.heartbeat_seen = asyncio.Event()
        await asyncio.wait_for(state.heartbeat_seen.wait(), timeout=2)
        for student in students:
            await asyncio.to_thread(client.post_result, crmstudentid=student["db_id"])
            agenda._advance_progress(progress, success=True)
        return None

    monkeypatch.setattr(agenda, "GradeDbClient", Client)
    monkeypatch.setattr(agenda, "async_playwright", PlaywrightContext)
    monkeypatch.setattr(agenda, "_collect_and_post_agendas", collect)
    # Wait for an actual heartbeat; shorten only the interval in this test.
    monkeypatch.setattr("scraper.runner.HEARTBEAT_INTERVAL_SECONDS", 0.001)
    return state


def test_main_filters_before_collection_and_uses_filtered_totals(job_harness, caplog):
    good = raw_student(1)
    skipped = raw_student(2, portal1="https://powerschool.example/login")
    skipped.update(primary_agenda={"portal": "canvas", "weeks": {}},
                   primary_agenda_status="synced", primary_agenda_updated_at="2026-09-01",
                   secondary_agenda={"portal": None, "weeks": {}},
                   secondary_agenda_status="not_configured",
                   secondary_agenda_updated_at="2026-09-01")
    original = deepcopy(skipped)
    job_harness.rows = [good, skipped]
    caplog.set_level(logging.INFO, logger="scraper.agenda")
    result = asyncio.run(agenda.main(19, None))
    assert result == {"total": 1, "attempted": 1, "success": 1, "errors": 0}
    assert job_harness.completed == [result]
    assert job_harness.launch_count == 1
    assert [post["crmstudentid"] for post in job_harness.posts] == [1]
    assert skipped == original
    assert job_harness.heartbeats and all(p["total"] == 1 for p in job_harness.heartbeats)
    record, = [r for r in caplog.records if r.getMessage() == "agenda.preparation.completed"]
    assert record.levelno == logging.INFO
    assert (record.candidate_count, record.eligible_count, record.filtered_count,
            record.preparation_error_count) == (2, 1, 1, 0)
    restored = deepcopy(skipped)
    restored["portal1"] = "https://canvas.example/login"
    job_harness.rows = [restored]
    second_run = asyncio.run(agenda.main(19, None))
    assert second_run["total"] == second_run["success"] == 1
    assert [post["crmstudentid"] for post in job_harness.posts] == [1, 2]


@pytest.mark.parametrize("all_errors", [False, True])
def test_main_zero_eligible_never_starts_playwright(job_harness, monkeypatch, caplog, all_errors):
    job_harness.rows = [raw_student(portal1="https://powerschool.example/login")]
    if all_errors:
        def broken(row):
            raise RuntimeError("PRIVATE_PASSWORD")
        monkeypatch.setattr(agenda, "student_from_context", broken)

    def no_playwright():
        pytest.fail("Zero eligible students must not start Playwright")

    monkeypatch.setattr(agenda, "async_playwright", no_playwright)
    caplog.set_level(logging.INFO, logger="scraper.agenda")
    result = asyncio.run(agenda.main(19, None))
    assert result == {"total": 0, "attempted": 0, "success": 0, "errors": 0}
    assert job_harness.completed == [result]
    assert not job_harness.posts and job_harness.collection_count == 0
    record, = [r for r in caplog.records if r.getMessage() == "agenda.preparation.completed"]
    assert record.preparation_error_count == int(all_errors)
    assert record.filtered_count == int(not all_errors)
    assert "PRIVATE_PASSWORD" not in caplog.text


@pytest.mark.parametrize("phase", ["conversion", "selection"])
def test_main_continues_after_one_preparation_error(job_harness, monkeypatch, phase):
    job_harness.rows = [raw_student(1), raw_student(2)]
    name = "student_from_context" if phase == "conversion" else "is_agenda_eligible"
    original = getattr(agenda, name)

    def maybe_fail(value):
        if value.get("crmstudentid", value.get("db_id")) == 1:
            raise RuntimeError("synthetic preparation failure")
        return original(value)

    monkeypatch.setattr(agenda, name, maybe_fail)
    result = asyncio.run(agenda.main(19, None))
    assert result["total"] == result["success"] == 1
    assert [post["crmstudentid"] for post in job_harness.posts] == [2]


def test_main_empty_candidate_list_completes(job_harness, monkeypatch):
    def no_playwright():
        pytest.fail("Empty candidate list must not start Playwright")
    monkeypatch.setattr(agenda, "async_playwright", no_playwright)
    result = asyncio.run(agenda.main(19, None))
    assert result == {"total": 0, "attempted": 0, "success": 0, "errors": 0}
    assert job_harness.completed == [result]
```

The no-post assertion is the persistence guarantee for skipped students: all agenda-state mutations occur through the Rust result boundary. Do not invent a Python snapshot-clearing/state-write method for the fake or for production.

- [x] **Step 4: Run the module and observe failures for missing preparation helpers, unfiltered totals, and the zero-student browser path.**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agenda_eligibility.py -q
```

- [x] **Step 5: Add the helpers below and replace only `main`'s current student list comprehension.** Place the helpers beside `is_agenda_eligible`. Keep the original progress initialization, empty-job branch, heartbeat loop, and collection/completion code after the replacement.

```python
def _prepare_agenda_students(
    rows: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    students: list[dict[str, Any]] = []
    counts = {"candidate_count": len(rows), "eligible_count": 0,
              "filtered_count": 0, "preparation_error_count": 0}
    for row in rows:
        try:
            student = student_from_context(row)
            eligible = is_agenda_eligible(student)
        except Exception:
            counts["preparation_error_count"] += 1
            continue
        if eligible:
            students.append(student)
            counts["eligible_count"] += 1
        else:
            counts["filtered_count"] += 1
    return students, counts


def _log_agenda_preparation(counts: Mapping[str, int]) -> None:
    extra = {key: counts[key] for key in (
        "candidate_count", "eligible_count", "filtered_count", "preparation_error_count",
    )}
    token = suspend_log_context()
    try:
        level = logging.WARNING if extra["preparation_error_count"] else logging.INFO
        logger.log(level, "agenda.preparation.completed", extra=extra)
    except Exception:
        pass
    finally:
        reset_log_context(token)
```

Replacement inside `main`:

```python
students, preparation_counts = _prepare_agenda_students(session.get("students", []))
_log_agenda_preparation(preparation_counts)
```

Do not extend `_emit_agenda_diagnostic` just to change severity; its existing callers log slot phases and have a different field contract. The small dedicated preparation logger uses the same suspend/reset pattern and a fixed allowlist.

- [x] **Step 6: Run focused regressions; retain the existing concurrency, per-slot isolation, lease, and browser-cleanup assertions.**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agenda_eligibility.py tests/test_agenda_grade_db_boundary.py tests/test_runner_grade_db_boundary.py tests/test_logging_config.py tests/test_secret_redaction.py -q
```

Expected GREEN. The existing browser-cleanup test returns a fully capable synthetic `_student(7)`, so it should still exercise collection without weakening the new filter.

- [x] **Step 7: Review, stage the two task files, run change detection and whitespace checks, then commit.**

Commit message: `feat: filter agenda jobs and isolate preparation failures`.

## Task 3: Remove Rust's Legacy Agenda Gates

**Files:** Modify `grade_db/src/service.rs`, `grade_db/tests/service.rs`, and `grade_db/tests/protocol.rs`.

**Interfaces:** Consumes the unchanged `JobStartRequest`, `JobStartResponse`, `ResultPostRequest`, `ResultPostResponse`, `NeonGateway`, and CRM gateway. Produces the same JSON shapes with candidate selection and result authorization independent of `track_agenda`. Both primary and secondary channels remain independent.

- [x] **Step 1: Run upstream impact for the two service methods and the old tracking-only test.** Disambiguate the service method from the trait method using context/UID. At planning time the service UID is `Function:grade_db/src/service.rs:BoundaryService.start_job#1`.

```powershell
npx gitnexus impact --uid 'Function:grade_db/src/service.rs:BoundaryService.start_job#1' --repo PlaywrightScraper --direction upstream --include-tests --summary-only
npx gitnexus impact post_result --file grade_db/src/service.rs --repo PlaywrightScraper --direction upstream --include-tests --summary-only
npx gitnexus impact agenda_job_returns_only_students_with_tracking_enabled --file grade_db/tests/service.rs --repo PlaywrightScraper --direction upstream --include-tests --summary-only
```

Use `gitnexus_rename` to rename `agenda_job_returns_only_students_with_tracking_enabled` to `agenda_job_returns_all_grade_eligible_students_regardless_of_tracking`, first previewing the rename and then applying it. If an unused test reports UNKNOWN risk, inspect its test attribute and text references rather than declaring it safe from the empty graph alone.

- [x] **Step 2: Replace the renamed test with the following matrix using the existing `FakeCrm`, `FakeNeon`, and `crm_student` helpers.** This tests both job kinds to protect grade behavior. Student 1 has false state, 2 true state, 3 missing state, 4 incomplete primary with complete secondary, and 5 another franchise.

```rust
#[tokio::test]
async fn agenda_job_returns_all_grade_eligible_students_regardless_of_tracking() {
    for kind in [JobKind::Agenda, JobKind::Grade] {
        for (franchise_id, student_id, expected) in [
            (Some(19), None, vec![1_i64, 2, 3]),
            (None, Some(1), vec![1]),
            (None, Some(4), vec![]),
            (None, None, vec![1, 2, 3, 5]),
            (Some(20), None, vec![5]),
        ] {
            let crm = Arc::new(FakeCrm::default());
            let mut incomplete = crm_student(4, None);
            incomplete.portal2 = Some("https://canvas.example/login".into());
            incomplete.p2username = Some("secondary".into());
            incomplete.p2password = Some("secondary-secret".into());
            let mut outside = crm_student(5, Some("pw"));
            outside.franchiseid = 20;
            crm.students.lock().unwrap().extend([
                crm_student(1, Some("pw")), crm_student(2, Some("pw")),
                crm_student(3, Some("pw")), incomplete, outside,
            ]);
            let neon = Arc::new(FakeNeon::default());
            for (id, enabled) in [(1, false), (2, true)] {
                neon.states.lock().unwrap().insert(id, StudentGradeState {
                    crmstudentid: id, track_agenda: enabled, ..Default::default()
                });
            }
            let service = BoundaryService::new(crm, neon.clone(), "worker-a".into(), 600);
            let response = service.start_job(JobStartRequest {
                kind, franchise_id, student_id,
            }).await.unwrap();
            assert_eq!(response.students.iter().map(|s| s.crmstudentid).collect::<Vec<_>>(), expected);
            assert_eq!(response.progress.total as usize, expected.len());
            assert_eq!(response.lease.franchise_id,
                       if student_id.is_some() { Some(19) } else { franchise_id });
            for student in &response.students {
                assert_eq!(student.track_agenda, student.crmstudentid == 2);
            }
            assert!(!neon.states.lock().unwrap().get(&1).unwrap().track_agenda);
            assert!(neon.states.lock().unwrap().get(&2).unwrap().track_agenda);
        }
    }
}
```

- [x] **Step 3: Add positive results and negative authorization tests.** Keep existing tests intact. The new success/failure cases verify applied results and safe audit contents when the flag is false or no state was returned. Repeated successful submissions must retain the same channel key; `FakeNeon` always returns `duplicate=false`, so existing SQL idempotency tests still provide the storage-level evidence.

```rust
#[tokio::test]
async fn agenda_results_ignore_legacy_tracking_for_both_channels() {
    for stored_flag in [None, Some(false), Some(true)] {
        let crm = Arc::new(FakeCrm::default());
        crm.students.lock().unwrap().push(crm_student(1, Some("pw")));
        let neon = Arc::new(FakeNeon::default());
        if let Some(track_agenda) = stored_flag {
            neon.states.lock().unwrap().insert(1, StudentGradeState {
                crmstudentid: 1, track_agenda, ..Default::default()
            });
        }
        *neon.active_job.lock().unwrap() = Some(ActiveJob {
            job_id: Uuid::from_u128(19), lease_token: Uuid::from_u128(42),
            kind: JobKind::Agenda, franchise_id: Some(19), student_id: None,
        });
        let service = BoundaryService::new(crm, neon.clone(), "worker-a".into(), 600);
        for channel in ["primary_agenda", "secondary_agenda"] {
            for failure in [false, true, false] {
                let outcome = if failure {
                    json!({"kind": "failure", "channel": channel, "code": "scrape_failed"})
                } else {
                    json!({"kind": format!("{channel}_success"),
                           "agenda": {"portal": "canvas", "weeks": {}}})
                };
                let request = serde_json::from_value(json!({
                    "job_id": Uuid::from_u128(19), "lease_token": Uuid::from_u128(42),
                    "crmstudentid": 1, "outcome": outcome,
                })).unwrap();
                let response = service.post_result(request).await.unwrap();
                assert!(response.applied);
                assert!(response.rejection_code.is_none());
                let writes = neon.writes.lock().unwrap();
                let write = writes.last().unwrap();
                assert!(write.applied);
                assert_eq!(write.idempotency_key,
                           deterministic_result_key(Uuid::from_u128(19), 1, channel));
                assert!(!write.audit_payload.to_string().contains("pw"));
            }
        }
    }
}


#[tokio::test]
async fn agenda_result_guards_remain_enforced_without_tracking() {
    for scenario in ["expired", "student_scope", "franchise_scope", "crm_missing", "crm_ineligible"] {
        let crm = Arc::new(FakeCrm::default());
        if scenario != "crm_missing" {
            crm.students.lock().unwrap().push(crm_student(
                1, if scenario == "crm_ineligible" { None } else { Some("pw") },
            ));
        }
        let neon = Arc::new(FakeNeon::default());
        if scenario != "expired" {
            *neon.active_job.lock().unwrap() = Some(ActiveJob {
                job_id: Uuid::from_u128(19), lease_token: Uuid::from_u128(42),
                kind: JobKind::Agenda,
                franchise_id: Some(if scenario == "franchise_scope" { 20 } else { 19 }),
                student_id: if scenario == "student_scope" { Some(2) } else { None },
            });
        }
        let service = BoundaryService::new(crm, neon.clone(), "worker-a".into(), 600);
        let request = serde_json::from_value(json!({
            "job_id": Uuid::from_u128(19), "lease_token": Uuid::from_u128(42),
            "crmstudentid": 1, "outcome": {"kind": "primary_agenda_success",
                "agenda": {"portal": "canvas", "weeks": {}}},
        })).unwrap();
        let response = service.post_result(request).await;
        if scenario == "expired" {
            assert!(matches!(response, Err(AppError::LeaseExpired)));
            assert!(neon.writes.lock().unwrap().is_empty());
        } else {
            let response = response.unwrap();
            assert!(!response.applied);
            assert_eq!(response.rejection_code.as_deref(), Some(
                if scenario == "student_scope" { "job_scope_mismatch" } else { "crm_ineligible" }
            ));
            assert!(neon.writes.lock().unwrap().iter().all(|write| !write.applied));
        }
    }
}
```

The franchise-mismatch case is rejected by the CRM query's scope filter before the explicit franchise guard, matching real gateway behavior. Do not weaken either guard to force a different error code.

- [x] **Step 4: Add this lifecycle compatibility test to `grade_db/tests/protocol.rs`.** SQL remains unchanged; pair this model test with existing `lifecycle_mutations_require_the_current_unexpired_lease` and a review that `HEARTBEAT`/`COMPLETE_JOB` still replace the progress JSON.

```rust
#[test]
fn agenda_filtered_totals_include_zero_without_changing_lifecycle_rules() {
    for total in [0, 1, 3] {
        let job_id = Uuid::from_u128(19);
        let lease_token = Uuid::from_u128(42);
        let progress = Progress { total, attempted: total, success: total, errors: 0 };
        assert!(JobHeartbeatRequest { job_id, lease_token, progress }.validate().is_ok());
        assert!(JobCompleteRequest { job_id, lease_token, progress }.validate().is_ok());
    }
    let unfinished = Progress { total: 1, attempted: 0, success: 0, errors: 0 };
    assert!(JobCompleteRequest {
        job_id: Uuid::from_u128(19), lease_token: Uuid::from_u128(42), progress: unfinished,
    }.validate().is_err());
}
```

- [x] **Step 5: Run the focused Rust tests before changing service behavior.**

```powershell
cargo test --manifest-path grade_db/Cargo.toml --test service --test protocol --test sql_contracts
```

Expected RED: mixed candidates still exclude false/missing states; valid agenda results return `agenda_not_enabled`. Guard and progress tests should already pass.

- [x] **Step 6: Make the two minimal service changes.** In `start_job`, the selection becomes:

```rust
let students: Vec<_> = eligible
    .into_iter()
    .map(|row| merge_runner_student(row, state_by_id.get(&row.crmstudentid)))
    .collect();
```

In `post_result`, delete exactly this obsolete block:

```rust
if job.kind == JobKind::Agenda {
    let states = self.neon.states_by_crm_ids(&[request.crmstudentid]).await?;
    if !states
        .get(&request.crmstudentid)
        .is_some_and(|row| row.track_agenda)
    {
        return self.record_rejected(request, "agenda_not_enabled").await;
    }
}
```

Remove `JobKind` from the `service.rs` imports if these deletions leave it unused. Preserve the initial state reads/merge in `start_job`, all model fields, schema checks, result channels, and everything before/after the removed result guard. No SQL or protocol mutation is needed.

- [x] **Step 7: Format edited Rust files and rerun the focused checks.**

```powershell
rustfmt --edition 2021 grade_db/src/service.rs grade_db/tests/service.rs grade_db/tests/protocol.rs
cargo test --manifest-path grade_db/Cargo.toml --test service --test protocol --test contracts --test sql_contracts --test crm_contracts
```

Expected GREEN, including the retained-field serialization test in `contracts.rs`. Do not claim fake-gateway tests verify live database transactions.

- [x] **Step 8: Stage the three Rust files, inspect change detection and whitespace checks, then commit.**

Commit message: `feat: remove legacy agenda tracking gates from Rust boundary`.

## Task 4: Document the Contract and Verify the Matched Release

**Files:** Modify `README.md`, `grade_db/README.md`, `scraper_internal_guide.md`, and comments only in the two operation SQL files listed in the file map. Production code changes are complete after Task 3.

**Interfaces:** Documents the same Python/Rust command and result contracts. Produces focused/current documentation, recorded verification output, and an identified Windows release binary for later deployment.

- [ ] **Step 1: Add automatic eligibility documentation and remove active-control wording.** In the root README's Rust boundary section, insert:

```markdown
### Automatic agenda eligibility

Agenda jobs receive the grade-eligible CRM roster, then Python selects students
with a complete portal slot whose registered engine advertises agenda support.
The primary URL, username, and password must still satisfy grade eligibility;
secondary-only credentials do not qualify a student. Both capable slots are
collected with their own credentials.

`track_agenda` is deprecated compatibility data. Its stored value and default
are retained for rollback, but it no longer controls agenda selection or result
acceptance. No flag update, backfill, or migration enables this behavior.

Students skipped entirely receive no agenda result posts: their previous
snapshots, statuses, and timestamps remain unchanged and can be stale. Retained
students keep the existing independent per-slot result behavior. Job progress
counts retained students; `agenda.preparation.completed` logs candidate,
eligible, filtered, and preparation-error counts separately.

Deploy the matching Python and rebuilt Rust executable together while agenda
runners are stopped. Normal job creation, completion, and result persistence
write to Neon and require explicit live-run authorization during this rollout.
```

Replace root README rollout step 9's “portal override, agenda tracking, and GPS fields” with “portal override and GPS fields; the retained `track_agenda` parameter is deprecated compatibility data and does not enable or disable agenda runs.” Preserve the historical schema rollout instructions; they are not steps for this feature.

In `grade_db/README.md`, add after Commands:

```markdown
Agenda job start returns all grade-eligible students in the requested scope.
Python filters their current portal slots by registered agenda capability before
collection. Rust validates leases, CRM eligibility, scope, result channels, and
idempotency independently of the legacy `track_agenda` value. The field remains
serialized and stored for compatibility; no migration or flag update is needed.
```

Change the `sql/operations/` description to identify portal override/GPS configuration and retained legacy agenda values, without suggesting an active agenda switch. Prepend this comment to both SQL operation templates and leave their SQL statements byte-for-byte unchanged:

```sql
-- track_agenda is deprecated compatibility data; it does not control agenda runs.
```

In the internal guide's Agenda Collection Contract, replace its hardcoded capable-portal list with registry-based eligibility, qualify absent/unsupported-slot clearing as applying to retained students, and replace its outdated all-or-nothing paragraph with:

```markdown
The agenda runner filters grade-eligible candidates using the current registry
before opening a browser. Complete capable slots receive workers with the
existing shared agenda-worker limit. Each slot posts its own primary or secondary
result, so one slot's failure does not discard the other slot's success.

Students skipped entirely preserve saved agendas, statuses, and timestamps.
Unexpected preparation errors skip only that student and appear in aggregate
warning diagnostics. Progress totals count retained students; zero retained
students complete with zero counts and no Playwright startup or result posts.
```

- [ ] **Step 2: Check documentation scope and retained compatibility fields.**

```powershell
rg -n 'track_agenda|agenda tracking|agenda_not_enabled' README.md grade_db/README.md scraper_internal_guide.md scraper grade_db/src grade_db/sql/operations
git diff -- grade_db/sql/operations/set_runner_config.sql grade_db/sql/operations/clear_runner_config.sql
git diff -- grade_db/src/models.rs grade_db/src/neon.rs scraper/runner.py scraper/db_cli.py scraper/portals ui
```

Expected: live-control wording is gone from current guides; code retains model/serialized/schema fields and removes the service gates; operation SQL changes only comments; reference-only production files have no feature changes. Historical specs remain historical.

- [ ] **Step 3: Run focused tests, then full non-live Python/Rust suites and required Rust checks.** Stop on an unexpected failure and diagnose it; do not loosen behavior tests to make them pass. Record unrelated pre-existing failures separately if encountered, with evidence from the unchanged baseline.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agenda_eligibility.py tests/test_agenda_grade_db_boundary.py tests/test_portal_registry.py tests/test_runner_grade_db_boundary.py tests/test_db_cli.py tests/test_secret_redaction.py -q
.\.venv\Scripts\python.exe -m pytest -q -m 'not integration'
cargo test --manifest-path grade_db/Cargo.toml
cargo fmt --manifest-path grade_db/Cargo.toml -- --check
cargo clippy --manifest-path grade_db/Cargo.toml --all-targets -- -D warnings
```

Expected: all selected tests/checks pass. Do not run integration tests or any `GradeDbClient.start_job` command as an automated smoke test. Audit uncommitted files if tests create artifacts.

- [ ] **Step 4: Build and identify the matching executable without starting a job.** The explicit path avoids Python selecting an older default release binary.

```powershell
cargo build --manifest-path grade_db/Cargo.toml --target x86_64-pc-windows-msvc --release
$agendaReleaseExe = (Resolve-Path -LiteralPath '.\grade_db\target\x86_64-pc-windows-msvc\release\grade-db.exe').Path
& $agendaReleaseExe --version
Get-FileHash -LiteralPath $agendaReleaseExe -Algorithm SHA256
git diff --check
```

Record source commit and binary hash together in the implementation handoff. Building a binary does not deploy it or prove live schema readiness.

- [ ] **Step 5: Review the full feature diff and spec coverage; stage only the documentation/comment files and run change detection before committing.**

Commit message: `docs: document automatic agenda eligibility and rollout`.

After the commit, report test/check results, source commit, executable path/hash, known limits (saved snapshots can be stale), and the remaining live-validation steps below. Leave deployment and live results unexecuted until their authorization and scope are established.

## Operational Validation and Coordinated Rollout

These are subsequent operator steps, not part of plan authoring. Local fixture tests and build completion do not establish production eligibility or a live collection result.

- [ ] **Record the prior matched Python source and Rust binary for rollback.** Pause the actual agenda scheduler and wait for its running jobs to drain before replacing code/binaries. The checked-in `pipeline_franchise.bat` and `pipeline_all_franchises.bat` currently run the grade runner; do not assume they are the agenda scheduler or change them for this feature.
- [ ] **Deploy Python and the matching executable together while agenda jobs remain paused.** Set `GRADE_DB_CLI_PATH` in the actual runner environment to the verified executable, not merely in the operator's temporary shell. Do not run the new Rust build with old Python.
- [ ] **Run read-only boundary diagnostics against that exact binary.**

```powershell
& $agendaReleaseExe doctor
```

Require the response's `ok` field and every reported check to be true, not merely exit code zero. `doctor` can return a successful process exit with `ok=false`.

- [ ] **Perform one headed, non-persisting collection using memory-only credentials.** Select an explicit CRM student for the diagnostic. Use an inline diagnostic, not `scraper.agenda.main` or `GradeDbClient.start_job`. The procedure is:

  1. Read that student's active CRM row with a parameterized exact-ID SELECT using a read-only SQL Server account and `ApplicationIntent=ReadOnly`. Join `dbo.tblStudentGradePortalSecondary` on `StudentID = tblStudents.Id` to obtain the student's own secondary credentials. Require exactly one row and complete primary URL/username/password.
  2. Read only that student's canonical `auth_answers` and `weeklydata` from `students_grades_20262027` inside `SET TRANSACTION READ ONLY`. Derive `auth_images` and latest known course titles as the Rust merge currently does; missing state means empty arrays. Close database connections before browser work. Keep values in memory only.
  3. Convert the row with `student_from_context`, run `is_agenda_eligible`, then run `fetch_agenda` with no `on_slot_result` callback in an ephemeral headed Playwright browser. Pass a semaphore of 1 for this diagnostic; leave production concurrency unchanged. Close the browser in `finally`.
  4. Disable ordinary logging during the probe and catch exceptions without printing messages or tracebacks. Emit only collected-slot counts, success/failure, and aggregate assignment counts. Do not use debug/pause mode, export browser state, or save credentials, screenshots, traces, HTML, or assignment content.
  5. Confirm that no job/result API or database mutation was invoked. A failed external login or challenge remains a failed diagnostic; do not claim collection success from capability alone.

Use the following inline probe after selecting the student for non-persisting validation. The only input stored temporarily in the process environment is the record ID; credentials stay in the Python process. Review this script and its fixed queries before execution. The child process disables logs and browser debug environment switches before importing the scraper. It does not create a credential manifest or add a production diagnostic command.

```powershell
$env:AGENDA_PROBE_STUDENT_ID = Read-Host 'CRM student ID selected for non-persisting validation'
try {
@'
import asyncio
import json
import logging
import os
from contextlib import closing

logging.disable(logging.CRITICAL)
os.environ.pop("PWDEBUG", None)
os.environ.pop("DEBUG", None)

import pyodbc
from sqlalchemy import text
from playwright.async_api import async_playwright
from db_core import get_engine
from scraper.agenda import fetch_agenda, is_agenda_eligible, resolve_agenda_slots
from scraper.portals import get_portal
from scraper.runner import student_from_context
from ui.dashboard_data import _crm_connection_string


def read_context(student_id):
    query = """
    SELECT s.Id AS crmstudentid, s.FranchiseID AS franchiseid,
           s.FirstName AS firstname, s.GradePortalURL AS portal1,
           s.GradePortalUser AS p1username, s.GradePortalPwd AS p1password,
           secondary.URL2 AS portal2, secondary.URL2Username AS p2username,
           secondary.URL2Password AS p2password
    FROM dbo.tblStudents AS s
    LEFT JOIN dbo.tblStudentGradePortalSecondary AS secondary ON secondary.StudentID = s.Id
    WHERE s.Id = ? AND s.IsTrail = 'Active'
    """
    with closing(pyodbc.connect(_crm_connection_string(), timeout=10)) as connection:
        with closing(connection.cursor()) as cursor:
            cursor.execute(query, student_id)
            records = cursor.fetchall()
            if len(records) != 1:
                raise ValueError("probe_scope_invalid")
            columns = [column[0].lower() for column in cursor.description]
            row = dict(zip(columns, records[0]))
    if not all(isinstance(row.get(key), str) and row[key].strip()
               for key in ("portal1", "p1username", "p1password")):
        raise ValueError("probe_grade_ineligible")

    engine = get_engine()
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                state = connection.execute(text(
                    "SELECT auth_answers, weeklydata FROM students_grades_20262027 "
                    "WHERE crmstudentid = :student_id"
                ), {"student_id": student_id}).mappings().one_or_none()
    finally:
        engine.dispose()
    state = dict(state or {})
    answers = state.get("auth_answers")
    row["auth_images"] = [value for value in answers if isinstance(value, str)] if isinstance(answers, list) else []
    weeks = state.get("weeklydata")
    nonempty = [(week, courses) for week, courses in (weeks or {}).items()
                if isinstance(courses, dict) and courses] if isinstance(weeks, dict) else []
    latest = max(nonempty, key=lambda pair: pair[0])[1] if nonempty else {}
    row["known_course_titles"] = sorted(title for title in latest if title.strip())
    return student_from_context(row)


async def probe(student):
    if not is_agenda_eligible(student):
        return {"ok": False, "code": "probe_agenda_ineligible"}
    capable = []
    for slot in resolve_agenda_slots(student):
        if all((slot.login_url, slot.username, slot.password, slot.portal)):
            try:
                engine = get_portal(slot.portal)
            except ValueError:
                continue
            if engine.agenda_capable:
                capable.append(slot.key)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=False, args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            result, _ = await fetch_agenda(
                browser, student, worker_semaphore=asyncio.Semaphore(1),
            )
        finally:
            await browser.close()
    assignments = sum(len(rows)
                      for key in capable
                      for classes in result.bundle[key]["weeks"].values()
                      for buckets in classes.values()
                      for rows in buckets.values())
    return {"ok": not result.failures, "capable_slot_count": len(capable),
            "failed_slot_count": len(result.failures), "assignment_count": assignments}


try:
    student_id = int(os.environ["AGENDA_PROBE_STUDENT_ID"])
    if student_id <= 0:
        raise ValueError("probe_scope_invalid")
    summary = asyncio.run(probe(read_context(student_id)))
except (Exception, asyncio.CancelledError, KeyboardInterrupt):
    summary = {"ok": False, "code": "agenda_probe_failed"}
print(json.dumps(summary))
raise SystemExit(0 if summary["ok"] else 1)
'@ | .\.venv\Scripts\python.exe -
} finally {
    Remove-Item -LiteralPath 'Env:AGENDA_PROBE_STUDENT_ID' -ErrorAction SilentlyContinue
}
```

Require the printed `ok` value to be true. This probe confirms portal collection only; the fake-gateway tests establish false-flag result acceptance until an authorized persisted pilot verifies it live.

- [ ] **Obtain separate authorization for an explicit franchise pilot and its lifecycle/result writes.** Only after that authorization, run the agenda entrypoint directly for the approved franchise:

```powershell
$agendaPilotFranchise = [int](Read-Host 'Enter the franchise ID authorized for this pilot')
if ($agendaPilotFranchise -le 0) { throw 'Franchise ID must be positive' }
.\.venv\Scripts\python.exe -m scraper.agenda --franchise-id $agendaPilotFranchise
```

Require `GRADE_DB_CLI_PATH` to resolve to the verified binary for this process. Do not run grade batch pipelines to approximate this agenda pilot.

- [ ] **Review pilot evidence before all-franchise resumption.** Check candidate/eligible/filtered/preparation-error counts, final filtered totals, duration, lease renewals, browser failures, and per-channel result counts. One student can produce two channel results, so do not compare result rows directly with student totals. Verify an eligible false-flag student is attempted, and skipped students' prior snapshots/statuses/timestamps are untouched. Investigate every preparation error; zero-total completion alone does not prove successful preparation.
- [ ] **Resume the normal agenda schedule only under the authorized rollout scope.** If rollback is required, pause/drain again and restore the prior matched Python/Rust pair. Do not alter legacy flag values or delete results already collected by the pilot.

## Coverage and Handoff

| Spec requirement | Evidence planned |
| --- | --- |
| URL/credentials/capability selection; flag/override ignored | Task 1 matrix and existing registry tests |
| Secondary-only exclusion; exact/franchise/global scope | Task 3 start-job matrix for agenda and grade |
| Conversion/lookup failure isolation; cancellation; secret-free counts | Task 2 preparation and logging tests |
| Filtering before browser; reduced/zero totals; preserved snapshots | Task 2 main tests, Task 3 progress test, unchanged Neon lifecycle SQL |
| Both slots and independent results/concurrency/cancellation | Existing agenda boundary tests in Tasks 1, 2, and 4 |
| False flag accepted; lease/CRM/scope/idempotency unchanged | Task 3 result tests and existing SQL/protocol contracts |
| Compatibility fields and no migrations/configuration changes | Task 3 retained-field tests and Task 4 scope review |
| Documentation, compiled executable, coordinated validation/rollback | Task 4 and operational checklist |

Before execution, read both this plan and the linked spec. After each code task, report its tests and reviewed diff; do not deploy intermediate commits. Execution choice is inline with `superpowers:executing-plans`, or delegated with `superpowers:subagent-driven-development` if the user selects that approach.

## Execution Record

- Baseline: 500 Python tests passed, 1 skipped, 1 integration test deselected; all 34 Rust tests passed.
- Task 1: all 24 new eligibility cases failed for the missing helper before implementation; 68 focused eligibility/registry/agenda tests passed afterward.
- Task 2: 12 new preparation/startup tests failed against the unfiltered runner; all 92 focused preparation, agenda, grade-runner, logging, and secret-redaction tests passed after implementation.
- Task 2 integration adjustment: agenda CLI now initializes the existing structured logger, matching the grade CLI. Its new regression failed before the two-line wiring change. All 93 focused tests passed afterward. No shared logger implementation changed.
- Task 3: the roster and channel-acceptance regressions failed against the legacy gates; all 32 focused Rust service/protocol/CRM/SQL/model tests passed after removing those gates. Test rename used the GitNexus MCP rename tool through a temporary local stdio connection because the app connector transport was closed.
