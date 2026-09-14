# Assignment metadata rollout

Implemented locally on `feature/grades-destinations-metadata` on 2026-09-13.

Infinite Campus includes optional canonical `score` and `category` fields in
eligible agenda records. The normalizer retains unambiguous metadata through
deduplication and node-budget enforcement; the UI revalidates stored values and
renders score/category in both embedded Current grades rows and standalone
Agenda rows. Old snapshots remain readable. An absent score displays an em dash;
an unknown category has no badge.

## Verification

- Synthetic parser tests cover explicit category container ownership, category
  totals distinct from assignment scores, the `Score` prefix, missing/unknown
  labels, duplicate IDs, nested containers and unchanged inclusion rules.
- Normalization tests cover fractions, percentages, zero, extra credit, invalid
  metadata, conflicts, metadata-less duplicates and deterministic truncation.
- Mocked-writer tests verify the score/category reaches the correct student and
  slot even when the other slot fails. All account inputs, readers and writers
  in these tests are synthetic or mocked.
- Rust `grade_db` contract tests accept enriched primary and secondary results
  under the existing payload limits. No schema or validator change was needed.
- Real browser tests passed at 360, 768 and 1440 pixels for both row placements,
  missing/zero scores, full category labels, long titles, numeric overflow,
  keyboard focus and existing report interactions.
- A portal-only live check on 2026-09-13 passed category containment, absent-score
  handling, fraction extraction and normalization using human-supplied inputs.
  The existing menu-navigation helper passed in this run; no navigation change
  was required. Both supplied unscored assignment examples were checked with a
  fixed September 10 reference date. Normal inclusion still uses the current
  date, so older unscored work can disappear on a fresh scrape.

The live check blocked application database connectors and did not invoke an
account lookup, scheduled job, job-state writer or result writer. Its sanitized
summary and synthetic screenshots are local ignored artifacts:

- `output/assignment-metadata/live-validation.json`
- `output/assignment-metadata/student-360.png`
- `output/assignment-metadata/student-768.png`
- `output/assignment-metadata/student-1440.png`

Run selected Python checks with `tests/support/run_without_databases.py`. It blocks
the application's Python database connectors before collection. Inspect selected
tests first: subprocesses do not inherit those patches. The browser preview
subprocess reads only synthetic fixture JSON and renders local templates.

```powershell
.venv/Scripts/python.exe tests/support/run_without_databases.py tests/test_assignment_metadata.py tests/test_agenda_contract.py tests/test_infinite_campus_agenda.py tests/test_infinite_campus_navigation.py tests/test_agenda_grade_db_boundary.py tests/test_read_only_dashboard_routes.py tests/test_read_only_dashboard_frontend.py tests/test_student_agenda_browser.py tests/test_template_javascript.py -q
$env:SQLX_OFFLINE = 'true'
cargo test --locked --manifest-path grade_db/Cargo.toml --test contracts
```

## Release and human-only database checks

1. Release the compatible shared metadata helper, contract and UI reader/renderer.
2. Release the enriched Infinite Campus collector. Existing data acquires fields
   on the next successful agenda refresh; no migration or mandatory backfill.
3. If deployment acceptance needs a stored-result smoke check, a human runs a
   one-student job and provides sanitized results. Do not request a franchise-wide
   rescrape just to verify this feature.

Agents must not directly access application databases in any environment, even
read-only or for tests. A human fetches any needed database data and executes any
database-backed smoke test. No application database connection, deployment or
production refresh was performed during implementation.

The existing 497-node per-weeks and Rust payload limits remain effective. Fully
enriched rows consume two more scalar nodes and can reduce retained assignment
counts in unusually large snapshots. Score/category fields remain optional for
rollback: an older reader ignores them and an older producer omits them.
