# Agenda Failure Cleanup Design

Date: 2026-09-09

Status: Implemented and verified locally on 2026-09-09. The confirmed failure
below describes the pre-fix behavior.

## Goal

Make `fetch_agenda` finish managing every portal-slot task it creates before it
returns or propagates an error or cancellation. Database failures during slot
setup must receive the same task cleanup as failures during result collection.

This is a bounded change to the existing agenda collection lifecycle. It does
not increase concurrency or change which students or portals are collected.

## Current Behavior and Confirmed Failure

In `scraper/agenda.py`, `fetch_agenda` loops over the primary and secondary
slots. A capable, configured slot starts an asynchronous collection task.
Absent, partially configured, unknown, or unsupported slots instead call the
result callback immediately. In a normal agenda job, that callback posts a
result through `GradeDbClient`.

The slot setup loop currently runs before the `try/finally` that cancels pending
slot tasks. This permits the following sequence:

1. The primary slot starts collecting in its own browser context.
2. The secondary slot is absent, so its empty result is posted immediately.
3. The secondary result post raises `GradeDbUnavailable`.
4. `fetch_agenda` exits without entering its task cleanup block.
5. The student task fails, but its primary slot task is still running.

The isolated reproduction returned `neon_unavailable` from
`_collect_and_post_agendas` with one slot task pending and zero context close
calls. These observations were made inside the running event loop, immediately
after the collector returned. The test harness then explicitly cancelled and
awaited the leftover task.

The outer runner still attempts to close the browser, and `asyncio.run` cancels
remaining tasks during loop shutdown. Those fallbacks can conceal the ownership
gap; they do not establish that the collector cleaned up its own work.

Existing cleanup inside `_collect_slot` already attempts page and context
closure independently, records cleanup outcomes, and preserves an established
collection result or original failure. That behavior remains the resource
cleanup mechanism.

## Scope and Ownership

| File or symbol | Responsibility in this change |
| --- | --- |
| `scraper/agenda.py::fetch_agenda` | Enclose task creation, early reports, and result processing in one task ownership boundary. |
| `scraper/agenda.py::_cancel_tasks` | Reuse its existing cancel-and-gather behavior; no signature or implementation change expected. |
| `scraper/agenda.py::_collect_slot` | Retain existing page/context cleanup and semaphore lifetime. |
| `tests/test_agenda_grade_db_boundary.py` | Add lifecycle regressions and strengthen existing cancellation assertions. |

`_collect_and_post_agendas`, `main`, portal engines, the grade runner, and the
Rust boundary are consumers or regression coverage, not planned production
edits. If implementation requires changing another existing symbol, reassess
its impact before expanding the change.

## Proposed Design

### One ownership boundary for all created slot tasks

Keep the existing `workers` mapping as the authoritative collection of tasks
owned by a `fetch_agenda` call. Initialize it before entering `try`.

Move the complete slot setup loop inside that `try`, including immediate
`report` calls, task creation, and the preparation diagnostic. Retain the
existing completion-order loop inside the same protected region.

Keep `pending` only as the working set used by `asyncio.wait`. In `finally`,
call the existing `_cancel_tasks` helper with a set of **all keys in `workers`**,
not just the latest `pending` set.

This makes cleanup independent of whether setup finished or `pending` was ever
initialized. It also retrieves exceptions from already-finished workers that
were not processed before another result callback raised. Cancelling a task
that has already finished does not rerun its cleanup; gathering it retrieves
its terminal result or exception.

### Failure and cancellation behavior

- An early result-post error propagates after owned slot tasks have been
  cancelled and awaited. Existing job mappings remain `lease_expired`,
  `neon_unavailable`, or `result_post_failed` as appropriate.
- An unexpected setup exception also triggers owned-task cleanup before
  propagating to the existing caller error handling.
- Cancellation of `fetch_agenda`, including while awaiting an early result
  callback, cancels and awaits its owned slot tasks and then propagates
  `asyncio.CancelledError`.
- A task waiting for a semaphore permit is cancelled without opening a browser
  context. An active task reaches `_collect_slot`'s existing cleanup and
  releases its permit as it exits.
- Cleanup does not generate replacement slot results, replay successful posts,
  or convert a database failure into a successful empty agenda.
- Already-finished slot tasks are awaited along with active tasks. Their
  exceptions must not become unobserved background-task warnings.

These requirements cover ordinary cooperative cancellation with completing
cleanup awaits. This change does not add a force-stop mechanism for hung
browser operations or repeated cancellation during cleanup.

### Preserved behavior

- `fetch_agenda` keeps its existing parameters and return contract.
- Primary and secondary slot identities and credentials remain independent.
- Normal results continue to post as slots finish; one slow slot does not
  delay reporting a completed sibling.
- Missing and unsupported slots retain their existing neutral-result behavior;
  partial credentials retain their existing failure result.
- The shared agenda worker limit remains `MAX_CONCURRENT_AGENDA_WORKERS = 6`.
- Result posts remain serialized by the job's existing lock.
- Existing cleanup diagnostics, error sanitization, progress semantics, leases,
  and database idempotency remain unchanged.

## Alternatives Considered

**Expand the existing `try/finally` and gather all owned tasks — selected.**
This closes the ownership gap while preserving existing exception and reporting
semantics. The production change stays within `fetch_agenda`.

**Use `asyncio.TaskGroup`.** This would require adapting child-failure and
exception-group handling to preserve independent slot outcomes and current
caller contracts. It is unnecessary for this localized fix.

**Rely on browser or event-loop shutdown.** This leaves the collector unable to
guarantee that its children have stopped when it returns and does not protect
direct callers in a longer-lived loop.

## Regression Test Design

Use the existing `_student`, `FakeBrowser`, `FakeContext`, and `FakePage`
fixtures. Tests use fake portal engines and result callbacks or clients; no
live portal sessions or database writes are required.

### Required scenarios

| Scenario | Required observation before loop shutdown |
| --- | --- |
| Primary collection is active; posting an absent secondary slot fails | Primary task is done after cancellation; page and context were each closed once; original boundary error is preserved. |
| Early report fails for partial, unknown, or unsupported secondary configuration | The same ownership guarantee applies to each early-report branch. |
| Parent is cancelled while an early result callback is suspended | Parent raises `CancelledError` only after its owned active slot task has finished cleanup. |
| Primary task is queued behind an unavailable semaphore permit when the early report fails | Queued task is done and no browser context was created for it. |
| Secondary setup raises unexpectedly after the primary task was created | The setup error propagates and no owned task remains pending. |
| Multiple slot tasks finish, then a result callback fails before all completed tasks are handled | Every owned task's terminal outcome is retrieved; no unobserved task exception is emitted. |
| Full collector encounters an early boundary failure or a lease-loss signal | Existing failure code is returned after child cleanup, with no reliance on outer browser closure. |

Parameterize boundary failures over `GradeDbUnavailable`,
`GradeDbLeaseExpired`, and the generic `GradeDbError` path. At the job layer,
assert the corresponding existing failure code and do not count an
unacknowledged student as successfully processed.

### Deterministic synchronization and assertion placement

1. Make the fake primary engine record its current task, signal a `started`
   event, and wait on an event that the scenario will not release normally.
2. Make the early secondary callback wait for `started` before raising. This
   ensures the regression exercises an active browser context, not just a task
   that never ran. For a synchronous fake client invoked through `to_thread`,
   use a bounded `threading.Event` wait and signal it from the fake engine;
   never wait synchronously on the event-loop thread.
3. Await the failing or cancelled operation and assert task completion,
   cancellation observation, and exact page/context close counts **inside the
   scenario coroutine**, before it returns to `asyncio.run`.
4. For the queued-task case, hold the semaphore permit and capture newly
   created tasks relative to the isolated scenario's baseline. Assert no owned
   task remains pending before releasing the test's permit.
5. Use events to order execution. Use a short outer timeout, such as two
   seconds, only to prevent a broken test from hanging; do not use timing sleeps
   to establish the race.
6. In a separate test-harness `finally`, cancel and await any remaining test
   tasks and release blocked fake callbacks. This cleanup runs after the
   assertions, so it cannot turn a failed production-cleanup assertion into a
   pass. Keep fake thread waits bounded as well.

For the completed-worker exception scenario, arrange both workers to finish
before their result callback raises, install a temporary loop exception
handler, and require no unobserved-task exception after worker references are
released while the loop is still alive. Restore the original loop handler in
test cleanup.

Move the cleanup assertions in
`test_neon_failure_closes_started_slot_contexts_and_pages_once` and
`test_lease_failure_closes_started_slot_context_and_page_once` into their
running scenario coroutines. Currently their post-`asyncio.run` assertions can
pass because loop shutdown performs missing cancellation.

## Implementation and Verification Sequence

1. Refresh GitNexus if stale and run upstream impact analysis before modifying
   `fetch_agenda` or existing test functions. Report the direct callers,
   affected flows, and risk; warn before any HIGH or CRITICAL-risk edit.
2. Add the active-primary/early-report regression and verify it fails on the
   current implementation at a cleanup assertion, rather than at test setup.
3. Apply the ownership-boundary change in `fetch_agenda` and verify the same
   regression passes.
4. Add the remaining lifecycle cases and strengthen the existing tests. Keep
   normal completion-order reporting, isolation, and worker-limit coverage.
5. Run the focused verification commands below. Broaden investigation only for
   failures or a resulting change in scope.

```powershell
uv run pytest -q tests/test_agenda_grade_db_boundary.py tests/test_agenda_eligibility.py tests/test_portal_workflow.py tests/test_runner_grade_db_boundary.py
uv run ruff check scraper/agenda.py tests/test_agenda_grade_db_boundary.py
git diff --check
```

Before any implementation commit, run `gitnexus_detect_changes` and verify that
the changed production symbols and affected flows match this specification.
These commands are future implementation checks, not a claim that the proposed
fix has already been implemented or verified.

## Impact Assessment

The upstream GitNexus CLI report on 2026-09-09 classified `fetch_agenda` as LOW
risk: one direct production caller (`collect_student`), three affected symbols
through `_collect_and_post_agendas` and `main`, and two affected process entries.
The MCP transport was unavailable, so the CLI supplied this assessment.

The earlier symbol-context inspection also identified
`scraper/workflows/test_portal.py::test_portal` as a diagnostic caller. Include
its tests even though the default impact query excludes paths it classifies as
tests. This assessment must be refreshed if the implementation baseline moves.

## Acceptance Criteria

- An early result-post or setup failure leaves no pending slot task owned by
  that `fetch_agenda` call when it propagates.
- Ordinary parent cancellation waits for owned slot-task cleanup before
  propagating cancellation.
- Started fake pages and contexts each receive exactly one close attempt;
  existing behavior when close itself fails is preserved.
- Cancelled tasks waiting for permits create no browser contexts.
- Completed worker exceptions are retrieved even if result reporting aborts.
- Existing result payloads, failure codes, completion-order posting, browser
  isolation, and the six-worker cap retain their behavior.
- The original regression fails before the fix and passes after it, with its
  decisive assertions made before event-loop shutdown.
- The focused tests, targeted lint, and diff checks pass.

## Non-Goals and Limits

This spec does not add collection deadlines, retry policies, machine-wide
limits, per-portal throttling, a bounded student queue, new dependencies, or
changes to grade-worker concurrency. It does not redesign the outer job task
manager, cleanup shielding, or process shutdown.

Cancelling an await on `asyncio.to_thread` does not forcibly terminate an
already-running database CLI invocation. This fix owns portal-slot tasks; it
does not introduce subprocess cancellation or undo results already accepted by
the database. Existing subprocess timeouts, leases, and idempotency remain the
database boundary controls.
