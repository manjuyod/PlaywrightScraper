# Agenda Failure Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure agenda slot tasks finish cleanup before their owner returns or propagates a failure, while preserving immediate, independent posting of each completed agenda.

**Architecture:** Extend `fetch_agenda`'s existing `try/finally` to include slot setup, early result callbacks, and collection. Keep its full `workers` mapping as the ownership record and cancel/gather every recorded task on exit. Reuse `_collect_slot` and `_cancel_tasks` without changing their contracts.

**Tech Stack:** Python 3.11+, asyncio, Playwright async API, pytest, existing fake browser fixtures.

**Spec:** [Agenda Failure Cleanup Design](../specs/2026-09-09-agenda-failure-cleanup-design.md)

## Global Constraints

- `fetch_agenda` keeps its existing parameters and return contract.
- Primary and secondary slot identities and credentials remain independent.
- Normal results continue to post as slots finish; one slow slot does not delay reporting a completed sibling.
- The shared agenda worker limit remains `MAX_CONCURRENT_AGENDA_WORKERS = 6`.
- Result posts remain serialized by the job's existing lock.
- Existing cleanup diagnostics, error sanitization, progress semantics, leases, and database idempotency remain unchanged.
- The original regression fails before the fix and passes after it, with its decisive assertions made before event-loop shutdown.
- Verification uses fake engines and clients. No real portal collection or database job/result writes are needed.

The user confirmed that either agenda may finish first: **finish a slot, post its result; finish the other slot, post its result**. Collection remains concurrent, and a later sibling failure must preserve an already acknowledged result. Task 2 explicitly tests both completion orders and success/failure of the slower slot.

## Execution Scope

This document is an implementation handoff. Creating it does not execute its steps.

| File | Planned responsibility |
| --- | --- |
| `scraper/agenda.py` | Change only `fetch_agenda` task ownership. |
| `tests/test_agenda_grade_db_boundary.py` | Add deterministic regressions and strengthen two existing cleanup tests. |
| This plan | Record completed steps and actual verification results during execution. |

Read the spec and current code before execution. The current references are `fetch_agenda` at line 337, `_cancel_tasks` at line 424, `_collect_and_post_agendas` at line 431, and the existing cleanup tests starting at lines 1018 and 1071; locate symbols again if line numbers move.

No changes are planned to the grade runner, Rust boundary, portal engines, worker counts, timeouts, or subprocess lifecycle. Ordinary cooperative cancellation is covered; repeated cancellation interrupting cleanup and hung close operations remain outside this change.

## Task 1: Own Slot Tasks Throughout the Fetch Lifecycle

**Files:**

- Modify: `scraper/agenda.py::fetch_agenda`
- Modify: `tests/test_agenda_grade_db_boundary.py` (imports and new helpers/tests)

**Interfaces:**

- Consume existing `_student(student_id: int) -> dict`, `FakeBrowser`, `FakeContext`, and `FakePage` in the test module.
- Consume `_cancel_tasks(tasks: set[asyncio.Task[Any]]) -> None` unchanged.
- Preserve `fetch_agenda(browser, student, *, worker_semaphore=None, on_slot_result=None, diagnostic=False) -> tuple[AgendaFetchResult, dict[str, Any]]`.
- Add test-only `_AgendaCleanupProbe` and `_drain_cleanup_test_tasks` below. Task 2 reuses their exact interfaces.

- [x] **Step 1: Confirm baseline and impact before editing existing symbols.**

Read `AGENTS.md`, inspect `git status --short`, and preserve unrelated work. Apply the using-git-worktrees workflow if execution needs an isolated checkout; copy the approved spec and plan into that checkout if they are still untracked.

Run:

```text
gitnexus_impact({repo: "PlaywrightScraper", target: "fetch_agenda", file_path: "scraper/agenda.py", direction: "upstream"})
```

If stale, run `npx gitnexus analyze` first. If MCP transport is still unavailable, use the equivalent CLI:

```powershell
npx gitnexus impact fetch_agenda --direction upstream --repo PlaywrightScraper --file scraper/agenda.py
```

Report direct callers, affected flows, and risk before the production edit. The spec's previous LOW-risk report is context, not a substitute for a current check. The direct agenda caller is `collect_student`; include diagnostic workflow coverage even if GitNexus classifies that path as a test. Warn before proceeding with any HIGH or CRITICAL result.

- [x] **Step 2: Add the primary regression and reusable test probe.**

Add `gc` and `threading` to the test module's imports. Expand its boundary import to:

```python
from scraper.db_cli import GradeDbError, GradeDbLeaseExpired, GradeDbUnavailable
```

Place these new helpers after the existing fake browser classes. All asyncio objects are constructed inside the scenario's running loop by constructing the probe there. The thread event is used only when a fake synchronous client runs in `asyncio.to_thread`.

```python
async def _drain_cleanup_test_tasks(tasks) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class _AgendaCleanupProbe:
    def __init__(self, expected: int = 1) -> None:
        self.expected = expected
        self.browser = FakeBrowser()
        self.started = asyncio.Event()
        self.started_thread = threading.Event()
        self.tasks = set()
        self.cancelled = set()
        probe = self

        class Engine:
            agenda_capable = True

            def __init__(self, *_args, **_kwargs):
                pass

            async def login(self, first_name=None):
                pass

            async def get_agenda(self):
                task = asyncio.current_task()
                assert task is not None
                probe.tasks.add(task)
                if len(probe.tasks) == probe.expected:
                    probe.started.set()
                    probe.started_thread.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    probe.cancelled.add(task)
                    raise

        self.engine = Engine

    def assert_closed(self) -> None:
        assert len(self.tasks) == self.expected
        assert all(task.done() for task in self.tasks)
        assert self.cancelled == self.tasks
        assert len(self.browser.contexts) == self.expected
        assert len(self.browser.pages) == self.expected
        assert all(context.close_calls == 1 for context in self.browser.contexts)
        assert all(page.close_calls == 1 for page in self.browser.pages)


@pytest.mark.parametrize("error_type", [
    GradeDbUnavailable, GradeDbLeaseExpired, GradeDbError,
])
@pytest.mark.parametrize("secondary", ["absent", "partial", "unknown", "unsupported"])
def test_early_report_failure_closes_owned_slot_before_return(
    monkeypatch, error_type, secondary,
) -> None:
    async def scenario() -> None:
        probe = _AgendaCleanupProbe()
        student = _student(7)
        if secondary == "absent":
            student.update(alt_login_url=None, alt_id=None, alt_password=None)
        elif secondary == "partial":
            student["alt_password"] = None
        elif secondary == "unknown":
            student["alt_login_url"] = "https://unknown.invalid/login"

        class UnsupportedEngine:
            agenda_capable = False

        def get_engine(portal):
            if secondary == "unsupported" and portal == "parentvue":
                return UnsupportedEngine
            return probe.engine

        error = error_type("simulated boundary failure")
        reports = []

        async def report(slot_key, snapshot, failure_code):
            reports.append((slot_key, failure_code))
            await probe.started.wait()
            raise error

        monkeypatch.setattr(agenda, "get_portal", get_engine)
        try:
            with pytest.raises(error_type) as raised:
                async with asyncio.timeout(2):
                    await agenda.fetch_agenda(
                        probe.browser, student, on_slot_result=report,
                    )
            assert raised.value is error
            assert reports == [(
                "agenda2", "configuration_missing" if secondary == "partial" else None,
            )]
            probe.assert_closed()
        finally:
            await _drain_cleanup_test_tasks(probe.tasks)

    asyncio.run(scenario())
```

The callback waits for primary collection to start before raising. Assertions run before the scenario returns. The harness drains leftovers only afterward, including when an assertion fails.

- [x] **Step 3: Confirm the regression is RED on the current production code.**

```powershell
uv run pytest -q tests/test_agenda_grade_db_boundary.py -k early_report_failure_closes_owned_slot_before_return
```

Expected: failure at the task-completion or cleanup assertion, with the original boundary error still observed. A timeout or setup error is a test problem to resolve before editing production code.

- [x] **Step 4: Extend the existing ownership boundary.**

In `fetch_agenda`, retain everything through the nested `report` definition. Replace the region from `for slot in slots:` through its existing `finally` with the following exact block. Keep the existing return statement after it.

```python
    try:
        for slot in slots:
            configured_values = (slot.login_url, slot.username, slot.password)
            if not any(configured_values):
                attempted_slots.append(slot.key)
                await report(slot, None)
                continue
            attempted_slots.append(slot.key)
            if not all(configured_values):
                failures[slot.key] = "configuration_missing"
                await report(slot, failures[slot.key])
                continue
            if not slot.portal:
                await report(slot, None)
                continue
            try:
                engine = get_portal(slot.portal)
            except ValueError:
                await report(slot, None)
                continue
            if not engine.agenda_capable:
                await report(slot, None)
                continue
            workers[asyncio.create_task(collect_slot(slot))] = slot

        _log_agenda_fetch_prepared(len(workers))
        pending = set(workers)
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                slot = workers[task]
                try:
                    bundle[slot.key]["weeks"] = task.result()
                    failure_code = None
                except LoginError:
                    failure_code = "bad_login"
                    failures[slot.key] = failure_code
                except BaseException:
                    failure_code = "scrape_failed"
                    failures[slot.key] = failure_code
                await report(slot, failure_code)
    finally:
        await _cancel_tasks(set(workers))
```

The ownership record remains `workers`; `pending` is only for scheduling. Gathering all owned tasks retrieves exceptions even when an early failure prevents the result loop from examining every finished task. No child outcome is reposted during cleanup.

- [x] **Step 5: Verify GREEN, then add the remaining direct lifecycle cases.**

First rerun Step 3 and require it to pass. Add these tests to the same module:

```python
def test_parent_cancellation_during_early_report_closes_owned_slot(monkeypatch) -> None:
    async def scenario() -> None:
        probe = _AgendaCleanupProbe()
        reporting = asyncio.Event()
        student = _student(7)
        student.update(alt_login_url=None, alt_id=None, alt_password=None)

        async def report(*_args):
            await probe.started.wait()
            reporting.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(agenda, "get_portal", lambda _portal: probe.engine)
        run = asyncio.create_task(agenda.fetch_agenda(
            probe.browser, student, on_slot_result=report,
        ))
        try:
            async with asyncio.timeout(2):
                await reporting.wait()
                run.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await run
            probe.assert_closed()
        finally:
            await _drain_cleanup_test_tasks({run})
            await _drain_cleanup_test_tasks(probe.tasks)

    asyncio.run(scenario())


def test_early_report_failure_cancels_slot_waiting_for_permit(monkeypatch) -> None:
    async def scenario() -> None:
        probe = _AgendaCleanupProbe()
        queued = asyncio.Event()
        waiting_tasks = set()

        class ObservedSemaphore(asyncio.Semaphore):
            async def acquire(self):
                task = asyncio.current_task()
                assert task is not None
                waiting_tasks.add(task)
                queued.set()
                return await super().acquire()

        semaphore = ObservedSemaphore(0)
        student = _student(7)
        student.update(alt_login_url=None, alt_id=None, alt_password=None)

        async def report(*_args):
            await queued.wait()
            raise GradeDbUnavailable("simulated boundary failure")

        monkeypatch.setattr(agenda, "get_portal", lambda _portal: probe.engine)
        try:
            with pytest.raises(GradeDbUnavailable):
                async with asyncio.timeout(2):
                    await agenda.fetch_agenda(
                        probe.browser, student,
                        worker_semaphore=semaphore, on_slot_result=report,
                    )
            assert len(waiting_tasks) == 1
            assert all(task.done() and task.cancelled() for task in waiting_tasks)
            assert not probe.browser.contexts
        finally:
            await _drain_cleanup_test_tasks(waiting_tasks)
            semaphore.release()

    asyncio.run(scenario())


def test_setup_exception_cancels_already_created_slot(monkeypatch) -> None:
    async def scenario() -> None:
        probe = _AgendaCleanupProbe()
        baseline = asyncio.all_tasks()

        def get_engine(portal):
            if portal == "parentvue":
                raise RuntimeError("simulated setup failure")
            return probe.engine

        monkeypatch.setattr(agenda, "get_portal", get_engine)
        try:
            with pytest.raises(RuntimeError, match="simulated setup failure"):
                async with asyncio.timeout(2):
                    await agenda.fetch_agenda(probe.browser, _student(7))
            assert not (asyncio.all_tasks() - baseline)
            assert not probe.browser.contexts
        finally:
            await _drain_cleanup_test_tasks(asyncio.all_tasks() - baseline)

    asyncio.run(scenario())


def test_report_failure_retrieves_all_completed_slot_exceptions(monkeypatch) -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        messages = []
        browser = FakeBrowser()
        real_wait = asyncio.wait

        class Engine:
            agenda_capable = True

            def __init__(self, *_args, **_kwargs):
                pass

            async def login(self, first_name=None):
                pass

            async def get_agenda(self):
                raise RuntimeError("simulated slot failure")

        async def wait_for_finished_batch(tasks, *, return_when):
            return await real_wait(tasks, return_when=asyncio.ALL_COMPLETED)

        async def report(*_args):
            raise GradeDbUnavailable("simulated boundary failure")

        async def invoke_without_retaining_exception():
            try:
                await agenda.fetch_agenda(browser, _student(7), on_slot_result=report)
            except GradeDbUnavailable:
                return
            raise AssertionError("boundary failure did not propagate")

        monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)
        # Force both slot outcomes into the same completed batch.
        monkeypatch.setattr(agenda.asyncio, "wait", wait_for_finished_batch)
        loop.set_exception_handler(
            lambda _loop, context: messages.append(context.get("message", ""))
        )
        try:
            async with asyncio.timeout(2):
                await invoke_without_retaining_exception()
            gc.collect()
            drained = asyncio.Event()
            loop.call_soon(drained.set)
            await drained.wait()
            assert not messages
            assert len(browser.contexts) == 2
            assert all(context.close_calls == 1 for context in browser.contexts)
            assert all(page.close_calls == 1 for page in browser.pages)
        finally:
            loop.set_exception_handler(previous_handler)

    asyncio.run(scenario())
```

The finished-batch test intentionally controls `asyncio.wait` scheduling so both failures exist before reporting fails. It asserts behavior through the loop's exception handler and retains no exception object whose traceback could delay collection. Keep the original `real_wait` reference to avoid recursion.

- [x] **Step 6: Run direct lifecycle tests and review the production diff.**

```powershell
uv run pytest -q tests/test_agenda_grade_db_boundary.py -k "early_report or parent_cancellation or setup_exception or retrieves_all_completed"
git diff -- scraper/agenda.py
```

Expected: all selected tests pass. The production diff is confined to moving setup inside `try` and gathering the full ownership set, without changing slot classification or result posting.

## Task 2: Verify Job Cleanup and Independent Result Posting

**Files:**

- Modify: `tests/test_agenda_grade_db_boundary.py`
- Verify: `scraper/agenda.py`, `tests/test_agenda_eligibility.py`, `tests/test_portal_workflow.py`, `tests/test_runner_grade_db_boundary.py`

**Interfaces:**

- Consume Task 1's `_AgendaCleanupProbe(expected: int = 1)`, `.engine`, `.browser`, `.started`, `.started_thread`, `.tasks`, and `.assert_closed()`.
- Consume Task 1's `_drain_cleanup_test_tasks(tasks)` test cleanup helper.
- Preserve `_collect_and_post_agendas(client, session, browser, students, progress, lease_failed, on_progress=None) -> str | None`.
- Produce evidence that the real job coordinator retains boundary failure codes, awaits slot cleanup, and posts either slot independently.

- [x] **Step 1: Check impact for the two existing tests being replaced.**

Use upstream GitNexus impact analysis for:

```text
test_neon_failure_closes_started_slot_contexts_and_pages_once
test_lease_failure_closes_started_slot_context_and_page_once
```

Disambiguate with `tests/test_agenda_grade_db_boundary.py`. Inspect the existing nested functions/methods being changed as well; use symbol UIDs from GitNexus context when names such as `Client.post_result` are ambiguous. Report the scope before editing. An UNKNOWN result for an externally discovered pytest test is not evidence that it is unused; confirm its pytest discovery and references.

- [x] **Step 2: Replace the existing database-failure cleanup test with a deterministic pre-shutdown assertion.**

Retain its name and add the error parameterization below. Two students exercise cancellation through the real outer coordinator. Only the first fake post fails; later fake calls acknowledge their neutral result so an additional database exception cannot obscure the cleanup assertion. The bounded thread wait ensures both primary collectors have started before the first failure.

```python
@pytest.mark.parametrize(("error_type", "failure_code"), [
    (GradeDbUnavailable, "neon_unavailable"),
    (GradeDbLeaseExpired, "lease_expired"),
    (GradeDbError, "result_post_failed"),
])
def test_neon_failure_closes_started_slot_contexts_and_pages_once(
    monkeypatch, error_type, failure_code,
) -> None:
    async def scenario() -> None:
        probe = _AgendaCleanupProbe(expected=2)
        students = [_student(7), _student(8)]
        for student in students:
            student.update(alt_login_url=None, alt_id=None, alt_password=None)
        progress = _new_progress(2)
        baseline = asyncio.all_tasks()

        class Client:
            calls = 0

            def post_result(self, **_kwargs):
                assert probe.started_thread.wait(2), "collectors did not start"
                self.calls += 1
                if self.calls == 1:
                    raise error_type("simulated boundary failure")
                return {"applied": True, "duplicate": False}

        monkeypatch.setattr(agenda, "get_portal", lambda _portal: probe.engine)
        try:
            async with asyncio.timeout(3):
                failure = await agenda._collect_and_post_agendas(
                    Client(), {"job_id": "job", "lease_token": "lease"},
                    probe.browser, students, progress, asyncio.Event(),
                )
            assert failure == failure_code
            assert progress == {"total": 2, "attempted": 0, "success": 0, "errors": 0}
            probe.assert_closed()
            assert not (asyncio.all_tasks() - baseline)
        finally:
            await _drain_cleanup_test_tasks(asyncio.all_tasks() - baseline)

    asyncio.run(scenario())
```

- [x] **Step 3: Replace the existing lease cleanup test with a valid fake client and in-loop assertions.**

The current test passes `object()` as the client, which can fail at the early secondary post before lease cancellation is exercised. A successful fake client makes this test specifically cover the lease-loss path.

```python
def test_lease_failure_closes_started_slot_context_and_page_once(monkeypatch) -> None:
    async def scenario() -> None:
        probe = _AgendaCleanupProbe()
        lease_failed = asyncio.Event()
        student = _student(7)
        student.update(alt_login_url=None, alt_id=None, alt_password=None)
        baseline = asyncio.all_tasks()

        class Client:
            def post_result(self, **_kwargs):
                return {"applied": True, "duplicate": False}

        async def lose_lease():
            await probe.started.wait()
            lease_failed.set()

        monkeypatch.setattr(agenda, "get_portal", lambda _portal: probe.engine)
        lease_task = asyncio.create_task(lose_lease())
        try:
            async with asyncio.timeout(2):
                failure = await agenda._collect_and_post_agendas(
                    Client(), {"job_id": "job", "lease_token": "lease"},
                    probe.browser, [student], _new_progress(1), lease_failed,
                )
                await lease_task
            assert failure == "lease_renewal_failed"
            probe.assert_closed()
            assert not (asyncio.all_tasks() - baseline)
        finally:
            await _drain_cleanup_test_tasks(asyncio.all_tasks() - baseline)

    asyncio.run(scenario())
```

- [x] **Step 4: Add explicit coverage for both completion orders and later sibling failure.**

Keep the existing `test_completed_slot_is_posted_while_other_slot_is_still_running`. Add the following test to cover the user's clarified behavior in both directions. The synchronous fake client signals the asyncio event via `call_soon_threadsafe`, not by directly setting it from a worker thread.

```python
@pytest.mark.parametrize("first_slot", ["agenda1", "agenda2"])
@pytest.mark.parametrize("second_fails", [False, True])
def test_each_slot_posts_independently_in_completion_order(
    monkeypatch, first_slot, second_fails,
) -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        first_posted = asyncio.Event()
        release_second = asyncio.Event()
        both_started = asyncio.Event()
        started = set()
        posts = []
        applied = {}
        browser = FakeBrowser()
        progress = _new_progress(1)
        first_channel = "primary_agenda" if first_slot == "agenda1" else "secondary_agenda"
        second_channel = "secondary_agenda" if first_slot == "agenda1" else "primary_agenda"
        first_username = "user-7" if first_slot == "agenda1" else "alt-user-7"
        baseline = asyncio.all_tasks()

        class Engine:
            agenda_capable = True

            def __init__(self, _page, username, *_args, **_kwargs):
                self.username = username

            async def login(self, first_name=None):
                pass

            async def get_agenda(self):
                started.add(self.username)
                if len(started) == 2:
                    both_started.set()
                await both_started.wait()
                if self.username != first_username:
                    await release_second.wait()
                    if second_fails:
                        raise RuntimeError("simulated later slot failure")
                return []

        class Client:
            def post_result(self, **kwargs):
                outcome = kwargs["outcome"]
                posts.append(outcome)
                if outcome["kind"] != "failure":
                    channel = outcome["kind"].removesuffix("_success")
                    applied[channel] = outcome["agenda"]
                if outcome["kind"] == f"{first_channel}_success":
                    loop.call_soon_threadsafe(first_posted.set)
                return {"applied": True, "duplicate": False}

        monkeypatch.setattr(agenda, "get_portal", lambda _portal: Engine)
        run = asyncio.create_task(agenda._collect_and_post_agendas(
            Client(), {"job_id": "job", "lease_token": "lease"},
            browser, [_student(7)], progress, asyncio.Event(),
        ))
        try:
            async with asyncio.timeout(2):
                await first_posted.wait()
                assert not run.done()
                assert [post["kind"] for post in posts] == [f"{first_channel}_success"]
                saved_first = dict(applied[first_channel])
                release_second.set()
                assert await run is None
            assert len(posts) == 2
            if second_fails:
                assert posts[1] == {
                    "kind": "failure", "channel": second_channel, "code": "scrape_failed",
                }
                assert second_channel not in applied
            else:
                assert posts[1]["kind"] == f"{second_channel}_success"
                assert second_channel in applied
            assert applied[first_channel] == saved_first
            assert progress == {
                "total": 1, "attempted": 1,
                "success": int(not second_fails), "errors": int(second_fails),
            }
            assert len(browser.contexts) == 2
            assert all(context.close_calls == 1 for context in browser.contexts)
            assert all(page.close_calls == 1 for page in browser.pages)
            assert not (asyncio.all_tasks() - baseline)
        finally:
            release_second.set()
            await _drain_cleanup_test_tasks(asyncio.all_tasks() - baseline)

    asyncio.run(scenario())
```

This verifies runner calls against an acknowledging fake store; it is not a live database durability test. A slot scrape failure is reported as a slot failure and increments the student's error count, while the collection function can still return normally.

- [x] **Step 5: Run the focused verification set and targeted lint.**

```powershell
uv run pytest -q tests/test_agenda_grade_db_boundary.py tests/test_agenda_eligibility.py tests/test_portal_workflow.py tests/test_runner_grade_db_boundary.py
uv run ruff check scraper/agenda.py tests/test_agenda_grade_db_boundary.py
git diff --check
```

Expected: all selected tests pass, no new lint problems, and no whitespace errors. Existing tests cover independent contexts, cleanup failures, normal slot error handling, and the current worker cap. No Rust source or protocol changed, so a Rust rebuild is not part of this fix.

If a verification failure is unrelated to the change, report its exact command and cause instead of widening production scope silently. Keep all new test synchronization event-driven; do not resolve flakiness by adding sleeps.

- [x] **Step 6: Review scope and record verification before committing.**

Run:

```text
gitnexus_detect_changes({repo: "PlaywrightScraper", scope: "all"})
```

Expected production change: `fetch_agenda` only. Expected callers: agenda job collection and diagnostic workflow. Inspect the complete diff, confirm that the Task 1 regression was observed failing before the fix, and record actual test/lint results in the execution report. Do not mark this plan complete based only on the proposed code snippets.

When execution includes a commit, stage only the scoped files that actually changed and include the approved documents if not already tracked:

```powershell
git add -- scraper/agenda.py tests/test_agenda_grade_db_boundary.py docs/superpowers/specs/2026-09-09-agenda-failure-cleanup-design.md docs/superpowers/plans/2026-09-09-agenda-failure-cleanup.md
git commit -m "fix: clean up agenda tasks after early failures"
```

## Spec Coverage Check

| Requirement | Implementation or verification |
| --- | --- |
| Own tasks during setup and early reports | Task 1, Steps 2–4 |
| Active child cleanup and all early-report branches | Task 1, Step 2 parameterized regression |
| Parent cancellation and waiting permit | Task 1, Step 5 |
| Unexpected setup error | Task 1, Step 5 |
| Retrieve already-completed child exceptions | Task 1, Steps 4–5 |
| Job error codes and progress | Task 2, Step 2 |
| Lease failure cleanup before shutdown | Task 2, Step 3 |
| Immediate posts in either order; saved result survives sibling failure | Task 2, Step 4 |
| Existing close-failure behavior, isolation, and worker cap | Task 2, Step 5 existing boundary suite |
| GitNexus impact before edits and scope check before commit | Task 1, Step 1; Task 2, Steps 1 and 6 |

## Handoff

Recommended execution for this bounded change is inline using `superpowers:executing-plans`, completing Task 1 before Task 2. The tasks share one test module and helper definitions, so they must not be edited in parallel. A sequential subagent implementation with review between tasks is also possible if requested.

The intended final behavior remains: either slot may complete first, that result is posted immediately, and remaining owned tasks are cancelled and awaited when their owner must abort.


## Execution Record — 2026-09-09

Completed inline on the existing `feat/agenda-availablility` branch. The checkout
had no pre-existing source changes; the approved spec and plan were untracked.
Implementation stayed in this checkout, and verification used local fakes.

- Baseline focused suite: **97 passed, 1 skipped**.
- Initial RED: all **12** early-report variants failed at the in-loop assertion
  that the owned portal task had finished.
- GREEN after the production fix: all **12** variants passed.
- Expanded direct lifecycle checks: **16 passed**.
- Test sensitivity check: an isolated Python process loaded the original HEAD
  `fetch_agenda` into memory, leaving source files untouched; all **16** direct
  cases failed on pending-task, cleanup, or unobserved-exception assertions.
- Final planned focused suite: **119 passed, 1 skipped**. The skip is the
  existing POSIX-permissions test on Windows.
- `uv run ruff check scraper/agenda.py tests/test_agenda_grade_db_boundary.py`:
  **passed**.
- `git diff --check`: **passed**.
- Independent read-only code review: **no actionable findings**.
- AST comparison with HEAD confirmed that `fetch_agenda` is the only changed
  existing production definition; two existing test functions were strengthened.
- GitNexus CLI impact: **LOW** for `fetch_agenda`; existing pytest entry points
  had UNKNOWN risk because discovery occurs outside indexed callers. Indexed
  local test callbacks were LOW risk; nested fake classes were not indexed.
- GitNexus CLI change detection: the expected five agenda collection flows;
  aggregate risk **medium**, including test symbols. Its line-based test-symbol
  matches were checked against the actual diff and AST comparison.

The production change follows the planned ownership boundary exactly. Both
completion orders and a later sibling scrape failure are covered. No live
portal or database verification was performed; subprocess cancellation,
repeated cancellation during cleanup, and hung close operations remain outside
this fix's scope.
