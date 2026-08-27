# Automatic Agenda Eligibility Design

## Context

Agenda scheduling currently depends on two independent signals:

1. The Python portal registry declares whether a portal engine supports agendas through `agenda_capable`.
2. The Rust boundary service requires the student's persisted `track_agenda` flag to be true.

This duplicated eligibility state prevents otherwise runnable students from receiving agenda runs. Weston Nunes is a concrete example: the J.O. Combs ParentVUE entry point is now supported and live verification succeeded, but his `track_agenda` value remains false, so the Rust service excludes him before Python can inspect the portal.

A read-only production measurement found:

| Population | Students |
| --- | ---: |
| Grade-runnable students | 511 |
| Agenda-capable and enabled | 23 |
| Agenda-capable but disabled | 160 |
| Not agenda-capable and disabled | 327 |
| Not agenda-capable but enabled | 1 |

The 160 capable-but-disabled students are distributed across Canvas (82), Infinite Campus (76), and ParentVUE (2). This design makes the portal registry the single runtime source of truth for agenda capability without modifying production data.

## Goal

Automatically schedule every grade-runnable student who has at least one complete, recognized, agenda-capable portal slot. Keep the change non-destructive by preserving the legacy `track_agenda` field and its current values while removing it from agenda scheduling and result-acceptance decisions.

## Eligibility Rule

A student is eligible for an agenda run when at least one primary or secondary portal slot satisfies all of these conditions:

- Portal URL, username, and password are all nonblank.
- The URL resolves to a registered portal key through the existing portal URL registry.
- The resolved portal engine declares `agenda_capable=True`.

A partial slot, an unknown URL, or a registered engine without agenda support does not make the student eligible. If one slot is invalid but the other meets the rule, the student remains eligible and the existing per-slot collection logic handles the invalid slot normally.

Grade eligibility remains a prerequisite. This design does not broaden franchise scope or admit students who are already excluded from grade runs.

## Architecture

### Rust boundary service

For an agenda `start_job` request, the boundary service will return all grade-eligible candidate students in the requested franchise. It will no longer filter candidates using `track_agenda`.

For agenda result submission, the boundary service will no longer reject a valid result solely because `track_agenda` is false. Existing checks remain in force, including:

- valid job and lease;
- matching franchise and student scope;
- current CRM eligibility;
- idempotency and result-state rules.

This allows the Python runner to own capability detection while Rust continues to enforce the execution boundary.

### Python agenda runner

After receiving candidate students, the Python runner will apply the eligibility rule before launching a browser:

1. Convert returned rows into runner student contexts.
2. Resolve the primary and secondary portal slots using existing slot normalization.
3. Retain a student if any complete slot resolves to an agenda-capable engine.
4. Initialize and report job progress using the filtered student count.
5. Launch the existing bounded, headed or headless collection workflow only for retained students.

The filter will be expressed as a small pure helper near the existing agenda slot-resolution code. The downstream `fetch_agenda` and per-slot result behavior will remain unchanged, avoiding a second implementation of scraping rules.

The boundary service may initially create the job from the broader candidate set, but runner heartbeats and completion will use the filtered total. If no candidate has an agenda-capable slot, Python will complete the job successfully with a total of zero and will not launch a browser.

## Data Flow

```text
Scheduler
  -> Rust start_job (all grade-eligible candidates in franchise)
  -> Python capability filter (credentials + URL registry + agenda_capable)
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
- no production database write;
- no deletion or repurposing of existing values.

Keeping the field intact provides a safe rollback path to the prior matched Python/Rust release.

## Errors and Edge Cases

- A complete slot with an unrecognized URL is filtered out unless another slot qualifies.
- A recognized but agenda-ineligible portal is filtered out unless another slot qualifies.
- Partial credentials do not qualify a student; existing slot-level diagnostics remain available when another slot qualifies and collection proceeds.
- Portal lookup errors must not launch a browser or abort eligibility evaluation for unrelated students.
- A student who becomes grade-ineligible remains excluded by the existing Rust boundary logic.
- Changes to portal support take effect at job time, so no cached database flag can become stale.

## Workload and Concurrency

Based on the read-only measurement, the first full rollout can add up to 160 students to agenda cycles. The design does not change `MAX_CONCURRENT_AGENDA_WORKERS`, per-student slot bounds, lease behavior, or failure isolation. Existing concurrency limits will absorb the larger population rather than increasing simultaneous browser load.

Operational validation will begin with one franchise. Run duration, lease health, browser failures, and result counts should be observed before enabling the normal all-franchise schedule.

## Deployment and Rollback

The Rust and Python changes form one contract change and must be deployed as a coordinated release:

1. Pause agenda schedulers/runners.
2. Deploy the new Python runner.
3. Deploy the matching rebuilt Rust `grade-db.exe` before resuming agenda jobs.
4. Run boundary diagnostics and a non-persisting headed verification.
5. Pilot one explicitly selected franchise.
6. Review duration, lease, filtering, and result metrics.
7. Resume the normal schedule for all franchises.

The new Rust service must not be paired with the old Python runner because the old runner would treat every returned candidate as part of the agenda workload. Rollback likewise restores the matching prior Python and Rust versions together.

The retained `track_agenda` values make rollback non-destructive. Any live pilot or scheduled run that persists agenda results requires separate explicit authorization because the current database-interaction constraint is read-only.

## Testing Strategy

### Python

- A complete capable primary slot qualifies.
- A complete capable secondary slot qualifies.
- Partial credentials do not qualify.
- An unknown URL does not qualify.
- A registered non-agenda portal does not qualify.
- One invalid slot does not suppress another qualifying slot.
- `main` filters candidates before browser launch.
- Progress and completion totals use the filtered population.
- A zero-eligible job completes without launching a browser.

### Rust

- Agenda job start returns all grade-eligible candidates regardless of `track_agenda`.
- Agenda result submission accepts an otherwise valid result when `track_agenda` is false.
- Franchise, CRM eligibility, lease, and idempotency rejection tests continue to pass.
- Grade-job behavior remains unchanged.

### Verification

- Run the focused Python and Rust tests first.
- Run the full Python test suite and the complete Rust test suite.
- Run GitNexus change detection to confirm only the expected agenda flows and tests are affected.
- Rebuild the Rust executable and run boundary diagnostics.
- Perform a headed, non-persisting portal verification before seeking authorization for a persisted pilot.

## Acceptance Criteria

- Weston and every other grade-runnable student with a complete agenda-capable portal slot are selected without changing `track_agenda`.
- Students whose configured portals cannot collect agendas do not launch browsers.
- Agenda results are accepted for capable students even when the legacy flag is false.
- Progress totals reflect the runtime-filtered student population.
- Grade collection, concurrency limits, and boundary security checks remain unchanged.
- No migration, backfill, or production database mutation is introduced.
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
- Persisting a production pilot without separate approval.
