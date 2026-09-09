# Automatic Agenda Eligibility Design

Updated: September 9, 2026. Status: implementation and local verification complete; deployment and live portal/pilot validation are pending. See the [implementation execution record](../plans/2026-09-09-automatic-agenda-eligibility.md#execution-record).

## Context

Agenda scheduling currently depends on two independent signals:

1. The Python portal registry declares whether a portal engine supports agendas through `agenda_capable`.
2. The Rust boundary service requires the student's persisted `track_agenda` flag to be true.

This duplicated eligibility state prevents otherwise runnable students from receiving agenda runs. Weston Nunes is a concrete example: the J.O. Combs ParentVUE entry point is now supported and live verification succeeded, but his `track_agenda` value remains false, so the Rust service excludes him before Python can inspect the portal.

The original August 27 design recorded this read-only production measurement. These are historical counts, not a new measurement for the September 9 revision:

| Population | Students |
| --- | ---: |
| Grade-runnable students | 511 |
| Agenda-capable and enabled | 23 |
| Agenda-capable but disabled | 160 |
| Not agenda-capable and disabled | 327 |
| Not agenda-capable but enabled | 1 |

The 160 capable-but-disabled students were distributed across Canvas (82), Infinite Campus (76), and ParentVUE (2). This design makes the portal registry the single runtime source of truth for agenda capability without changing student configuration or legacy flag values.

## Goal

Automatically schedule every grade-runnable student who has at least one complete, recognized, agenda-capable portal slot. Keep the change non-destructive by preserving the legacy `track_agenda` field and its current values while removing it from agenda scheduling and result-acceptance decisions.

When both slots qualify, collect both agendas using their own configured credentials. Capability means collection will be attempted; successful retrieval still depends on authentication, portal availability, and parsing. Existing collection failures retain their controlled result behavior.

## Eligibility Rule

A student is eligible for an agenda run when at least one primary or secondary portal slot satisfies all of these conditions:

- Portal URL, username, and password are all nonblank.
- The URL resolves to a registered portal key through the existing portal URL registry.
- The resolved portal engine declares `agenda_capable=True`.

A partial slot, an unknown URL, or a registered engine without agenda support does not make the student eligible. Among students who satisfy the grade prerequisite below, one such slot does not suppress another qualifying slot; existing per-slot collection logic handles the nonqualifying slot normally. Unexpected preparation exceptions are handled separately under Errors and Edge Cases.

Grade eligibility remains a prerequisite. The current Rust rule requires nonblank primary portal URL, username, and password, in addition to the existing CRM and requested-scope restrictions. A complete secondary slot does not rescue an incomplete primary slot at the Rust boundary. This design does not broaden franchise scope or admit students who are already excluded from grade runs.

Assuming the existing CRM and requested-scope checks pass:

| Primary slot | Secondary slot | Agenda selection |
| --- | --- | --- |
| Complete and agenda-capable | Absent or nonqualifying | Select; collect primary |
| Complete but unknown or without agenda support | Complete and agenda-capable | Select; collect secondary |
| Complete and agenda-capable | Complete and agenda-capable | Select; collect both, even if they use the same engine |
| Incomplete | Complete and agenda-capable | Exclude under the unchanged grade prerequisite |
| Complete but without a qualifying engine | Absent or nonqualifying | Exclude from collection |

Neither a stored portal key nor `track_agenda` overrides capability detected from each slot's current URL.

## Architecture

### Existing Python/Rust connection

The Python grade and agenda runners call `GradeDbClient` in `scraper/db_cli.py`. Each operation launches the local compiled `grade-db.exe`, writes one JSON request to standard input, and reads a JSON response from standard output. The operations are job start, heartbeat, result post, job completion, and job failure.

Rust reads CRM student data and owns the runners' Neon job, result, and state writes. Python owns portal registration, Playwright collection, concurrency, and progress reporting. The dashboard's separate Python database read path is unchanged. This design retains the existing command and JSON shapes and requires a matching Python/Rust release.

### Rust boundary service

For an agenda `start_job` request, `BoundaryService.start_job` will return all grade-eligible candidate students in the requested scope. It will no longer filter candidates using `track_agenda`. Existing franchise, exact-student, and all-franchise request semantics remain unchanged.

For agenda result submission, the boundary service will no longer reject a valid result solely because `track_agenda` is false. Existing checks remain in force, including:

- valid job and lease;
- matching franchise and student scope;
- current CRM eligibility;
- idempotency and result-state rules.

This allows the Python runner to own capability detection while Rust continues to enforce the execution boundary. Rust does not receive a duplicate portal registry or independently verify agenda capability. Acceptance still covers the existing primary and secondary agenda result channels, their failure outcomes, and their independent idempotency identities.

### Python agenda runner

After receiving candidate students, the Python runner will apply the eligibility rule before launching a browser:

1. Process each returned row within its own preparation error boundary, including conversion through `student_from_context`.
2. Resolve the primary and secondary portal slots using existing slot normalization, preserving slot order and the stored credential strings.
3. Retain a student if any complete slot resolves to an agenda-capable engine. An ordinary unknown URL or missing engine does not prevent evaluation of the other slot.
4. Record aggregate preparation counts, then initialize and report job progress using the retained student count.
5. Launch the existing bounded collection workflow, using the current browser launch settings, only when at least one student is retained.

Express capability selection as a small side-effect-free helper near the existing agenda slot-resolution code. It may inspect registry metadata, but must not instantiate a scraper, open a browser, authenticate, or make database calls. Keep conversion and selection inside the per-row error boundary: `student_from_context` already performs a registry lookup, so protecting only the new helper would leave a job-wide failure path. Shared grade-runner conversion behavior is unchanged.

For retained students, downstream `fetch_agenda` and per-slot result behavior remain unchanged. Both capable slots receive workers with their own credentials. Results are posted independently through the existing primary and secondary agenda channels; a failure in one slot does not discard the successful other slot. A nonqualifying slot retains its current empty-result or configuration-failure behavior.

The boundary service initially creates the job from the broader candidate set, but runner heartbeats and completion use the retained total. Current Rust progress validation and persistence already allow replacement of the initial total with a smaller internally consistent total, including zero. Progress counts students, not slots, and a retained student advances attempted progress once after its existing collection/result workflow finishes.

If no student is retained, Python completes the job successfully with all four progress counts set to zero, without starting Playwright or posting agenda results. This includes runs where preparation errors prevent selection; those errors must remain visible in the separate diagnostic counts rather than being described as ordinary unsupported portals. Job creation and completion still persist lifecycle records during an authorized live run.

### Stored agendas for skipped students

When a student is filtered out or skipped after a preparation error, post no agenda result for that student. Preserve both saved agenda snapshots, their statuses, and their update timestamps. This is an explicit change from the current workflow, which posts empty results for wholly unsupported students returned by Rust.

For example, if a previously supported student changes to an unsupported primary portal with no qualifying secondary slot, the prior snapshot remains stored and the dashboard continues to project it with its existing status and timestamp. This design accepts that the content can be stale; it does not mark it freshly collected, clear it automatically, or redesign the dashboard. If a qualifying slot is configured later, normal collection resumes on the next scheduled cycle.

When another slot still qualifies and the student is retained, existing per-slot result handling continues to apply, including neutral empty results for unsupported or absent slots. The preserve-without-posting policy applies only to students skipped entirely.

## Data Flow

```text
Scheduler
  -> Rust start_job via GradeDbClient (grade-eligible candidates in requested scope)
  -> Python per-student preparation and capability filter
       -> skipped students: preserve saved agendas; no result posts
  -> bounded browser workers (eligible students only)
  -> Rust post_result (lease/scope/CRM/idempotency validation)
  -> Python job completion (filtered totals)
```

Portal capability remains defined in one place: the Python portal registry. Adding future agenda support to a registered portal will therefore make matching students eligible on their next scheduled cycle without a database flag update.

## Legacy `track_agenda` Field

The field will remain in the SQL schema, Rust models, serialized records, and existing data for backward compatibility. Its default and stored values will not be changed. Runtime scheduling and agenda result authorization will ignore it.

Where documentation or comments describe it as an active scheduling control, they will be updated to identify it as deprecated compatibility data.

This design intentionally includes:

- no migration;
- no backfill;
- no reconciliation job;
- no student-configuration update to establish eligibility;
- no deletion or repurposing of existing legacy flag values.

Keeping the field intact provides a safe rollback path to the prior matched Python/Rust release.

Implementation and automated verification use local code, fixtures, and mocked database gateways. They perform no live database mutations. Separately authorized runtime execution continues to use the existing Rust writes for state initialization, jobs, leases, events, results, and agenda snapshots. The restriction is on schema/configuration changes and unauthorized live writes, not on the normal persistence required by an authorized agenda run.

## Errors and Edge Cases

- A complete slot with an unrecognized URL is filtered out unless another slot qualifies.
- A recognized but agenda-ineligible portal is filtered out unless another slot qualifies.
- Partial credentials do not qualify a slot; existing slot-level diagnostics remain available when the student passes the grade prerequisite and another slot qualifies.
- An ordinary missing-engine lookup is a nonqualifying slot, consistent with the existing `fetch_agenda` behavior. Continue evaluating the other slot.
- An unexpected conversion, URL-resolution, or capability-inspection exception makes that student's preparation fail. Skip that student, open no browser context for it, preserve its saved agenda state, and continue evaluating unrelated students. This exception policy takes precedence over the normal slot-selection rule when preparation cannot finish safely.
- The preparation error boundary must cover `student_from_context`, slot resolution, and capability inspection. Catch ordinary exceptions there without swallowing cancellation or process-interruption signals.
- A student who becomes grade-ineligible remains excluded by the existing Rust boundary logic.
- Changes to portal support take effect at job time, so no cached database flag can become stale.

Emit aggregate `candidate_count`, `eligible_count`, `filtered_count`, and `preparation_error_count` diagnostics, with `candidate_count = eligible_count + filtered_count + preparation_error_count`. Here `filtered_count` means successfully evaluated students with no qualifying slot. Emit a warning when preparation errors occur. Do not add these counts to the existing progress JSON contract; progress remains `total`, `attempted`, `success`, and `errors` for retained students only.

Preparation diagnostics expose counts and controlled event codes only. They must not include raw exceptions, tracebacks, student names or identifiers, portal URLs, usernames, passwords, authentication answers, or assignment content.

## Workload and Concurrency

The historical measurement suggests an additional 160 students in agenda cycles for that measured population; it is not a current rollout limit. The design does not change `MAX_CONCURRENT_AGENDA_WORKERS`, per-student slot bounds, lease behavior, or collection failure isolation. Existing concurrency limits bound the larger population rather than increasing simultaneous browser load.

Operational validation will begin with one franchise. Run duration, lease health, browser failures, and result counts should be observed before enabling the normal all-franchise schedule.

## Deployment and Rollback

The Rust and Python changes form one contract change and must be deployed as a coordinated release:

1. Pause agenda schedulers/runners.
2. Deploy the new Python runner.
3. Deploy the matching rebuilt Rust `grade-db.exe` before resuming agenda jobs.
4. Run boundary diagnostics and a non-persisting headed verification.
5. After separate authorization for live lifecycle and result writes, pilot one explicitly selected franchise.
6. Review duration, lease, filtering, and result metrics.
7. Resume the normal schedule for all franchises.

The new Rust service must not be paired with the old Python runner because the old runner would treat every returned candidate as part of the agenda workload. Rollback likewise restores the matching prior Python and Rust versions together.

The retained `track_agenda` values allow rollback without rewriting configuration. Results already persisted during an authorized pilot remain stored; rollback does not undo collected data. Any live pilot or scheduled run requires separate explicit authorization for its database writes, including job start and completion even when no agenda is collected. Before that authorization, database interaction remains read-only and headed verification must bypass job starts and result posts.

## Testing Strategy

### Python

- A complete capable primary slot qualifies.
- A complete capable secondary slot qualifies when the Rust grade prerequisite is met, including a complete but unknown or unsupported primary portal.
- Two capable slots are both collected with their own credentials, including two slots using the same engine.
- Partial or whitespace-only credentials do not qualify a slot; credential strings that are nonblank remain unchanged.
- An unknown URL does not qualify.
- A registered non-agenda portal does not qualify.
- One ordinarily nonqualifying slot does not suppress another qualifying slot among grade-eligible candidates.
- Stored portal metadata and either value of `track_agenda` do not override URL-based capability selection.
- An ordinary missing-engine lookup does not suppress a qualifying other slot.
- Exceptions from context conversion, URL resolution, and capability inspection each skip only the affected student; unrelated eligible students still complete.
- Preparation-error diagnostics expose the required aggregate counts without supplied secrets or raw exceptions, and cancellation is not swallowed.
- `main` filters candidates before browser launch.
- Progress and completion totals use the filtered population.
- A zero-eligible job completes with zero progress without starting Playwright or posting agenda results, including when every candidate has a preparation error; diagnostics distinguish that case.
- A previously supported student with no remaining qualifying slots receives no result posts, leaving stored snapshots, statuses, and timestamps unchanged; later restored capability makes the student selectable again.
- A retained student with one unsupported slot keeps the existing per-slot empty-result behavior and independent success/failure handling.

### Rust

- Agenda job start returns all grade-eligible candidates regardless of `track_agenda`.
- A complete secondary slot with an incomplete primary slot remains excluded by grade eligibility.
- Agenda result submission accepts otherwise valid primary and secondary successes and controlled failure outcomes when `track_agenda` is false.
- Franchise, CRM eligibility, lease, and idempotency rejection tests continue to pass.
- Primary and secondary results retain independent idempotency keys.
- Progress validation accepts a smaller internally consistent total, including zero, while retaining the existing completion and lease requirements.
- Grade-job behavior remains unchanged.

### Verification

- Run the focused Python and Rust tests first.
- Run the full Python test suite and the complete Rust test suite with live integrations disabled and database gateways mocked.
- Run GitNexus change detection to confirm only the expected agenda flows and tests are affected.
- Rebuild the Rust executable and run boundary diagnostics.
- Perform a headed, non-persisting portal verification without job starts, result posts, or saved browser-session artifacts before seeking authorization for a persisted pilot.

## Acceptance Criteria

- Weston and every other grade-runnable student with a complete agenda-capable portal slot are selected without changing `track_agenda`.
- Students whose configured portals cannot collect agendas do not launch browsers.
- Both qualifying slots are attempted independently; capability does not guarantee authentication or collection success.
- Secondary-only credentials remain excluded under the unchanged primary-credential grade prerequisite.
- Preparation exceptions are isolated per student and reported through sanitized diagnostics.
- Students skipped entirely retain their previous agendas, statuses, and timestamps without result posts; retained students keep existing per-slot behavior.
- Agenda results are accepted for capable students even when the legacy flag is false.
- Progress totals reflect the runtime-filtered student population.
- Grade collection, concurrency limits, and boundary security checks remain unchanged.
- No migration, backfill, or student-configuration mutation is introduced. No live job or result writes occur during implementation or verification without separate authorization.
- Python and Rust test suites pass, and the coordinated executable is rebuilt successfully.

## Alternatives Considered

### Automatically synchronize `track_agenda`

A reconciliation service could continually write capability decisions into the database. This was rejected because it creates a second source of truth, requires recurring database mutations, can become stale when URLs or engine support change, and introduces unclear opt-out semantics.

### Default or backfill `track_agenda` to true

Changing the default or mass-enabling existing rows was rejected because it would schedule unsupported portals until another layer filtered them, mutate production data unnecessarily, and preserve the duplicated-state problem.

### Remove the field immediately

Dropping `track_agenda` was rejected for this change because it would require a migration and broaden the rollback and compatibility risk without improving runtime eligibility.

## Non-Goals

- Removing the legacy database column or model fields.
- Changing portal-engine capability declarations.
- Increasing agenda concurrency.
- Redesigning dashboards or job APIs.
- Mutating student configuration or agenda flags.
- Automatically clearing or hiding saved agendas for students skipped entirely.
- Changing the primary-credential grade prerequisite or guaranteeing successful collection from every capable portal.
- Replacing the local Python/Rust command transport or adding a Rust portal registry.
- Persisting any live job lifecycle or pilot results without separate approval.
