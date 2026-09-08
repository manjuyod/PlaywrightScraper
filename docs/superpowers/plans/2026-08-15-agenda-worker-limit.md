# Agenda Worker Limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cap each agenda job at two concurrently active portal-slot collectors using a constant in `scraper/agenda.py`.

**Architecture:** `scraper.agenda` will create one job-scoped `asyncio.Semaphore` and pass it to every student-level `fetch_agenda` task. Each portal-slot task will acquire that semaphore before `_collect_slot` opens its isolated browser context and release it only after `_collect_slot` completes cleanup; direct `fetch_agenda` calls will create their own semaphore from the same constant.

**Tech Stack:** Python 3.11+, `asyncio`, Playwright async API, pytest

## Global Constraints

- Define the editable limit as `MAX_CONCURRENT_AGENDA_WORKERS = 2` in `scraper/agenda.py`.
- Count active portal-slot collectors and Playwright browser contexts, not students.
- Preserve fixed `agenda1`/`agenda2` identity, all-or-nothing per-student results, lease cancellation, and result posting order by completion.
- Do not add an environment variable or CLI argument.

---

### Task 1: Bound Agenda Portal-Slot Collection

**Files:**
- Modify: `scraper/agenda.py:25-228`
- Test: `tests/test_agenda_grade_db_boundary.py`

**Interfaces:**
- Consumes: existing `_collect_slot(browser: Browser, student: Mapping[str, object], slot: AgendaSlot) -> AgendaWeeks`
- Produces: `MAX_CONCURRENT_AGENDA_WORKERS: int = 2`
- Produces: `fetch_agenda(browser: Browser, student: dict[str, Any], *, worker_semaphore: asyncio.Semaphore | None = None) -> tuple[AgendaBundle, dict[str, Any]]`
- Preserves: `_collect_and_post_agendas` parameters and `str | None` return contract

- [x] **Step 1: Run GitNexus impact analysis before editing existing symbols**

Run upstream impact analysis for `fetch_agenda` and `_collect_and_post_agendas` in `scraper/agenda.py`. Review direct callers and affected agenda execution flows. Warn before editing if either report is HIGH or CRITICAL.

- [x] **Step 2: Write the failing concurrency regression test**

Add this behavior test to `tests/test_agenda_grade_db_boundary.py`, using the existing `_student`, `FakeBrowser`, and `_new_progress` helpers:

```python
def test_agenda_job_limits_active_portal_workers(monkeypatch) -> None:
    active = 0
    peak_active = 0
    posts = []

    class Engine:
        agenda_capable = True

        def __init__(self, *_args, **_kwargs):
            pass

        async def login(self, first_name=None):
            nonlocal active, peak_active
            active += 1
            peak_active = max(peak_active, active)
            await asyncio.sleep(0)

        async def get_agenda(self):
            nonlocal active
            await asyncio.sleep(0)
            active -= 1
            return []

    class Client:
        def post_result(self, **kwargs):
            posts.append(kwargs)
            return {"applied": True, "duplicate": False}

    monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)
    students = [_student(student_id) for student_id in range(1, 5)]
    for student in students:
        student.update(alt_login_url=None, alt_id=None, alt_password=None)

    failure = asyncio.run(
        agenda._collect_and_post_agendas(
            Client(),
            {"job_id": "job", "lease_token": "lease"},
            FakeBrowser(),
            students,
            _new_progress(len(students)),
            asyncio.Event(),
        )
    )

    assert failure is None
    assert peak_active == min(agenda.MAX_CONCURRENT_AGENDA_WORKERS, len(students))
    assert len(posts) == len(students)
```

Update the three existing monkeypatched `fetch_agenda` test doubles in
`test_agenda_neon_failure_cancels_pending_collection`,
`test_lease_failure_cancels_outstanding_collection`, and
`test_heartbeat_failure_prevents_agenda_tasks_from_starting` to accept the new
keyword-only plumbing. Change only each declaration as shown and leave each
function body unchanged:

```python
# Before
async def fetch(_context, student):

# After
async def fetch(_context, student, **_kwargs):
```

- [x] **Step 3: Run the new test and verify the RED state**

Run:

```powershell
uv run pytest -q tests/test_agenda_grade_db_boundary.py::test_agenda_job_limits_active_portal_workers
```

Expected: FAIL because `MAX_CONCURRENT_AGENDA_WORKERS` is absent or because the observed peak exceeds two. Fix only test mistakes if it errors for an unrelated reason; do not add production code until the test fails for the missing limit.

- [x] **Step 4: Implement the minimal shared semaphore**

In `scraper/agenda.py`, add the constant immediately after `load_dotenv()`:

```python
MAX_CONCURRENT_AGENDA_WORKERS = 2
```

Extend `fetch_agenda` with an optional keyword-only semaphore. Create a local semaphore for direct calls, then wrap each real slot collection:

```python
async def fetch_agenda(
    browser: Browser,
    student: dict[str, Any],
    *,
    worker_semaphore: asyncio.Semaphore | None = None,
) -> tuple[AgendaBundle, dict[str, Any]]:
    if worker_semaphore is None:
        worker_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENDA_WORKERS)

    async def collect_slot(slot: AgendaSlot) -> AgendaWeeks:
        async with worker_semaphore:
            return await _collect_slot(browser, student, slot)
```

Change portal-slot task creation to call `collect_slot(slot)`. In `_collect_and_post_agendas`, create one semaphore before creating student tasks and pass the same instance to every call:

```python
worker_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENDA_WORKERS)
tasks = {
    asyncio.create_task(
        fetch_agenda(
            browser,
            student,
            worker_semaphore=worker_semaphore,
        )
    ): student
    for student in students
}
```

- [x] **Step 5: Run focused tests and verify the GREEN state**

Run:

```powershell
uv run pytest -q tests/test_agenda_grade_db_boundary.py::test_agenda_job_limits_active_portal_workers
uv run pytest -q tests/test_agenda_grade_db_boundary.py tests/test_canvas_agenda.py
```

Expected: both commands exit 0, the regression observes a peak of exactly two, and all existing agenda boundary tests pass.

- [x] **Step 6: Run lint and the complete unit suite**

Run:

```powershell
uv run ruff check scraper/agenda.py tests/test_agenda_grade_db_boundary.py
uv run pytest -q
```

Expected: both commands exit 0 with no lint errors or test failures.

- [x] **Step 7: Review the final affected scope and commit**

Run `gitnexus_detect_changes({repo: "PlaywrightScraper", scope: "all"})` and verify only the expected agenda collection flows are affected. Then inspect `git diff --check` and commit:

```powershell
git add -- scraper/agenda.py tests/test_agenda_grade_db_boundary.py docs/superpowers/plans/2026-08-15-agenda-worker-limit.md
git commit -m "fix: limit concurrent agenda workers"
```
