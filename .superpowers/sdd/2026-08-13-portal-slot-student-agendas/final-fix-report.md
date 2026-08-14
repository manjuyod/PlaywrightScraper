# Final branch-review fix report

Date: 2026-08-14

## Scope and root causes

No live portal, database, credential, integration flag, or saved browser state
was used. Production Rust and SQL were not changed.

1. `normalize_agenda()` canonicalized every valid row without bounding the
   resulting JSON tree. The unchanged Rust result validator counts every JSON
   object, array, and primitive value and rejects node 1,001.
2. `_canvas_timezone()` wrapped both `page.evaluate()` and `ZoneInfo()` in one
   broad `Exception` handler. Runtime/page failures were therefore mistaken for
   invalid timezone names and collection continued in UTC.
3. `_fetch_pages()` required every row of an otherwise valid list payload to be
   a dictionary. One row-local malformed value rejected valid sibling rows.
4. ParentVUE visibility was checked only on the Missing marker itself, and
   assignment rows were never filtered for visibility before classification.

The focused fixes cap each normalized slot independently, narrow Canvas
timezone fallback to invalid `ZoneInfo` construction, filter Canvas non-object
rows after retaining the strict list boundary, and make ParentVUE visibility
ancestor-aware before row classification.

## Exact result-node budget

Object keys are not values in the Rust count. A stored assignment row costs
four nodes (one object plus `title`, `dueDate`, and `dueTime`). One populated
week/course subtree has five fixed nodes (the `weeks` root, week object, course
object, and two status arrays), so one course can retain 123 rows exactly:

```text
5 + 4 * 123 = 497
```

Each slot receives that same independent `weeks` budget. The maximum complete
bundle is therefore:

```text
1 bundle + 2 * (1 slot object + 1 portal value + 497 weeks values) = 999
```

Rows are selected from the already canonical order: ascending week,
case-insensitive course, missing before due, then date/time/title. Course/week
scaffolding is created only when its first row fits. No portal order changes,
cross-slot deduplication, or shared budget were introduced. The runner
regression retained 123 rows in each slot and measured the full bundle at 999
nodes. The Rust contract separately accepts exactly 1,000 nodes and rejects
1,001 with `result payload is too large`.

## TDD evidence

### Result-node budget

RED:

```text
test_normalize_truncates_canonical_rows_at_the_weeks_node_boundary FAILED
test_two_slots_are_independently_bounded_below_rust_result_limit FAILED
2 failed in 0.41s
```

The old normalizer retained the later week and 124 rows per slot. GREEN after
the focused cap:

```text
focused regressions: 2 passed in 0.28s
complete agenda contract + boundary files: 25 passed in 0.86s
Rust contracts: 7 passed
```

### Canvas timezone failures

RED for the two separate cases:

```text
invalid timezone fallback guard: passed
evaluation RuntimeError boundary regression: failed because an API request began
1 failed, 1 passed in 0.35s
```

GREEN after moving `page.evaluate()` outside the catch and accepting only
`ValueError`/`ZoneInfoNotFoundError` from `ZoneInfo()`:

```text
focused pair: 2 passed in 0.27s
complete Canvas file at this checkpoint: 8 passed in 0.26s
```

### Canvas row-local malformed data

RED:

```text
mixed courses payload: failed with CanvasAgendaError
mixed missing-submissions payload: failed with CanvasAgendaError
mixed planner payload: failed with CanvasAgendaError
3 failed in 0.42s
```

GREEN while existing HTTP and top-level malformed cases remain strict:

```text
focused endpoint cases: 3 passed in 0.27s
complete Canvas file: 11 passed in 0.26s
```

### ParentVUE visibility

RED:

```text
hidden-ancestor Missing marker was classified missing
four hidden/ancestor-hidden assignment rows were emitted
2 failed in 0.26s
```

GREEN after ancestor traversal and the pre-classification row guard:

```text
focused visibility cases: 2 passed in 0.21s
complete ParentVUE file: 9 passed in 0.20s
```

## Files changed

- `scraper/agenda_contract.py`
- `scraper/portals/canvas_agenda.py`
- `scraper/portals/parentvue_agenda.py`
- `tests/test_agenda_contract.py`
- `tests/test_canvas_agenda.py`
- `tests/test_parentvue_agenda.py`
- `tests/test_agenda_grade_db_boundary.py`
- `grade_db/tests/contracts.rs` (tests only)
- `README.md`
- `scraper_internal_guide.md`
- `.superpowers/sdd/2026-08-13-portal-slot-student-agendas/final-fix-report.md`

## Verification

All Python verification removed environment variables whose names matched CRM,
Postgres, database, or supported portal names, disabled dotenv/bytecode, and
disabled the pytest cache provider.

```text
agenda contract + Canvas + ParentVUE + agenda boundary + secret redaction:
48 passed in 0.86s

Task 9 prescribed focused Python selection:
143 passed, 2 failed in 11.53s

complete Python suite:
199 passed, 2 failed, 1 skipped in 8.87s

Rust contract suite:
7 passed

complete Rust suite:
32 passed, 0 failed; doc-tests contained 0 tests

owned Python Ruff selection:
All checks passed!

cargo fmt --check:
exit 0

git diff --check:
exit 0
```

The two Python failures are the previously approved, unrelated authorization
baselines in `tests/test_read_only_dashboard_routes.py`: the non-dev overview
and jobs API tests expect 403 and receive 200. No agenda, portal, boundary,
redaction, or browser test failed.

## GitNexus

The index was current at `38c4727d796bb412f26ea6cacf3cc9d5c3fb2b3e`.
Pre-edit impacts were MEDIUM for `normalize_agenda` (five direct callers, one
agenda-main flow) and `parse_parentvue_agenda` (six direct callers, one
ParentVUE flow), and LOW for `_fetch_pages`, `_canvas_timezone`, and
`_is_hidden` (one direct production caller each). No HIGH or CRITICAL symbol
edit was made.

Staged change detection reported 56 changed indexed symbols in 11 files, eight
affected processes, and aggregate HIGH risk. Every trace was inspected:

- `Main -> _display_text`
- `Main -> Monday_for`
- `Get_agenda -> _same_origin`
- `Get_agenda -> _next_url`
- `Get_agenda -> _canvas_timezone`
- `Get_agenda -> _text`
- `Get_agenda -> _assignment_rows`
- `Get_agenda -> _date_and_time`

The affected production paths are limited to agenda normalization, Canvas
collection, and ParentVUE parsing. No database mutation, grade runner,
dashboard/Heatmap, credential persistence, or browser-state flow was reported.

## Concerns

The two pre-existing dashboard authorization failures keep the broader and full
Python suites from being wholly green; they are outside this owned-file scope.
Bounded agenda storage intentionally omits the later canonical tail of an
exceptionally large slot rather than rejecting the atomic snapshot. That
behavior and its independent per-slot capacity are documented in both guides.
