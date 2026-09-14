# Assignment Score and Category Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute inline, task by task, with checkpoints. The user selected inline execution; do not dispatch subagents. Steps use checkbox syntax for tracking.

**Goal:** Preserve Infinite Campus assignment scores and Formative/Summative categories and display them inline in Grades assignment rows.

**Architecture:** Add validated optional scalar metadata to the shared agenda contract. Map bulk course rows to their explicit category containers, preserve metadata through bounded JSON normalization and UI shaping, and extend the existing shared React row. Existing JSON storage and other portals remain compatible.

**Tech Stack:** Python, BeautifulSoup, Playwright, Flask, React via `createElement`, CSS, pytest, existing Rust `grade_db` JSON contracts.

**Spec:** [Assignment scores and categories](../specs/2026-09-13-assignment-score-category-design.md).

## Global Constraints

- Agents must not directly access application databases in any environment, including read-only, local, test, QA or production access. Do not execute queries, writes, migrations or database-backed tests/diagnostics, whether through SQL clients, repository helpers or scraper jobs.
- If database information or database-backed verification is needed, a human must fetch the data or run the check and provide a sanitized export or result. Agents use that material, synthetic fixtures and mocks, and continue independent work while awaiting it.
- Execute inline in the current conversation; do not delegate implementation to subagents.
- Preserve unrelated working-tree changes, including `scraper/runner.py`.
- Add no runtime dependencies and no database migrations.
- Add optional scalar `score` and `category` fields; preserve existing required item fields and primary/secondary agenda storage.
- Preserve existing assignment inclusion, status precedence, date filtering, course matching and ordering.
- Extract new metadata from Infinite Campus only; never infer formative/summative for other portals.
- Keep the existing 497-node per-weeks budget and Rust payload limits.
- Keep credentials and browser state out of committed files; use synthetic student and assignment records in test fixtures.

## Workspace and boundaries

All paths below are relative to `C:\Users\17026\Documents\Code stuff\reporting-v2\PlaywrightScraper`. Run commands from that directory using `.venv/Scripts/python.exe`. Read the spec before starting. Refresh GitNexus and run impact before editing each existing symbol; run complete change analysis before commits.

Planning impact results: `normalize_agenda` reaches `_collect_slot` and agenda collection; the Infinite Campus parser reaches `collect_infinite_campus_agenda`/`InfiniteCampus.get_agenda`; `_agenda_slots` reaches `_student_detail`/`student_view`. These graph walks reported LOW. `AgendaAssignment` reported UNKNOWN; text inspection confirms calls from both `GradeRow` and `AgendaClass`, so verify both rendered placements. Re-run current analysis during implementation because the MCP cached stale index metadata during planning.

Keep each task's production code and meaningful tests in one reviewed commit. Do not stage unrelated `scraper/runner.py` edits. No changes are planned to `crm-device-auth` for this subsystem; its Rust service is separate from the `grade_db` crate within this repository.

Before running a test command or diagnostic, inspect its setup to confirm it cannot connect to a database. Use only pure validation, synthetic fixtures and mocked readers/writers. Do not invoke a runner that fetches accounts or persists job state, even for a read-only portal investigation. Request any required stored data, schema facts or database-backed test results from a human as a sanitized export or result; do not retrieve database credentials or read database rows yourself.

### Task 1: Add the optional metadata contract and deterministic persistence

**Files**

- Create `scraper/assignment_metadata.py` and `tests/test_assignment_metadata.py`.
- Modify `scraper/agenda_contract.py` types and normalization.
- Extend `tests/test_agenda_contract.py`, `tests/test_agenda_grade_db_boundary.py`, and `grade_db/tests/contracts.rs`.

**Interfaces**

- `AssignmentCategory = Literal["formative", "summative"]`.
- `normalize_assignment_score(value: object) -> str | None`.
- `normalize_assignment_category(value: object) -> AssignmentCategory | None`.
- `AgendaRecord` and `StoredAgendaItem` gain optional `score: str` and `category: AssignmentCategory`.
- `normalize_agenda(records, *, known_course_titles=()) -> AgendaWeeks` keeps its signature and adds only validated optional fields to retained items.

- [x] Write score/category tests with the following matrix before implementing the helper:

```python
@pytest.mark.parametrize("raw,expected", [
    ("Score 7 / 10 (70%)", "7/10"), ("Score: 7.50/10.0 (75%)", "7.5/10"),
    ("79.50%", "79.5%"), ("0/10", "0/10"), ("12/10 (120%)", "12/10"),
    ("", None), (None, None), (True, None), ({"score": "7/10"}, None),
    ("Excused", None), ("Pass/Fail", None), ("Ungraded", None),
    ("7/0", None), ("-7/10", None), ("NaN", None),
    ("7/10 8/10", None), ("<b>7/10</b>", None),
])
def test_score_normalization(raw, expected):
    assert normalize_assignment_score(raw) == expected

@pytest.mark.parametrize("raw,expected", [
    (" Formative ", "formative"), ("SUMMATIVE", "summative"),
    ("Practice", None), ("Formative Weight: 20", None), (None, None),
])
def test_category_normalization(raw, expected):
    assert normalize_assignment_category(raw) == expected
```

Add length-limit cases and decimal precision cases. Add normalization tests proving old rows remain byte-for-byte equivalent, valid metadata survives, invalid optional fields do not discard an assignment, and duplicate ordering/conflicts follow the spec. Assert both `normalize_agenda(rows)` and `normalize_agenda(reversed(rows))` equal the expected result.

- [x] Run and confirm failure for the missing metadata helper/new fields:

```powershell
.venv/Scripts/python.exe tests/support/run_without_databases.py tests/test_assignment_metadata.py tests/test_agenda_contract.py -q
```

- [x] Implement pure helpers. Full-match one permitted score shape after bounded whitespace normalization and optional `Score` prefix removal. Use `Decimal` and fixed-point formatting, removing trailing fractional zeros only. Validate denominator >0 and output length <=32; return `None` on all invalid inputs. Use exact casefolded enum lookup for category.

```python
_NUMBER = r"[0-9]+(?:\.[0-9]+)?"
_FRACTION = re.compile(
    rf"(?P<earned>{_NUMBER})\s*/\s*(?P<possible>{_NUMBER})"
    rf"(?:\s*\(\s*{_NUMBER}\s*%\s*\))?"
)
_PERCENTAGE = re.compile(rf"(?P<percent>{_NUMBER})\s*%")
# Remove only a leading 'Score' word (optional colon), then fullmatch.
# No substring search: malformed/negative text must not become a valid score.
```

- [x] Preserve the existing canonical tuple/identity/status logic in `normalize_agenda`, but group normalized observations per identity before selecting the existing core representative. Select metadata by these rules:

```python
scores = {
    row_score for row_status, row_score, _ in observations
    if row_status == winning_status and row_score is not None
}
categories = {category for _, _, category in observations if category is not None}
if len(scores) == 1:
    item["score"] = next(iter(scores))
if len(categories) == 1:
    item["category"] = next(iter(categories))
```

Here `observations` is the new per-identity collection of `(status, validated_score, validated_category)` tuples. Keep core data selection unchanged and attach metadata before `_bounded_weeks` counts nodes. Do not add metadata values to identity keys or change status precedence.

- [x] Extend the existing large dual-slot boundary tests with rows carrying both new fields. Assert each weeks subtree is <=497 nodes, the bundle is <=999 nodes, deterministic prefix retention holds and no credential fields appear. Add a `ResultOutcome::PrimaryAgendaSuccess` case in `grade_db/tests/contracts.rs` containing the example enriched row and assert `validate_for_job(JobKind::Agenda) == Ok(())`; repeat for `SecondaryAgendaSuccess`. Keep the exact-limit/one-over tests unchanged.

```powershell
.venv/Scripts/python.exe tests/support/run_without_databases.py tests/test_assignment_metadata.py tests/test_agenda_contract.py tests/test_agenda_grade_db_boundary.py -q
$env:SQLX_OFFLINE = 'true'
cargo test --locked --manifest-path grade_db/Cargo.toml --test contracts
```

- [x] Review and commit the additive contract after checks pass. No SQL or Rust validator change is expected.

### Task 2: Extract metadata from Infinite Campus category containers

**Files**

- Modify `scraper/portals/infinite_campus_agenda.py`.
- Extend `tests/test_infinite_campus_agenda.py`.
- Add `tests/fixtures/infinite_campus_score_categories.html` with synthetic titles and dates.

**Interfaces**

- Consumes Task 1's score/category helpers and extended `AgendaRecord`.
- `parse_infinite_campus_course_grades(html, *, course, reference=None) -> list[AgendaRecord]` retains its signature and emits optional validated metadata.
- Private `_assignment_category(row: Tag, categories_by_target: dict[str, AssignmentCategory | None]) -> AssignmentCategory | None` walks to the nearest known controlled ancestor.
- `collect_infinite_campus_agenda` retains bulk collection: one expanded snapshot per course; no per-assignment page opens.

- [x] Build a small synthetic HTML fixture using the observed structural contract. Include a category total that differs from individual scores:

```html
<tl-grading-task-list>
  <button class="divider__header" aria-controls="formative-list" aria-expanded="true">
    <div><h5>Formative</h5><div class="divider__subtext">Weight: 20</div></div>
    <div class="totals__row">78/80 (97.5%)</div>
  </button>
  <div id="formative-list">
    <div class="selcat-assignment-row">
      <div class="assignment__largeScreen--cell-assignmentName"><h6><a>Synthetic practice</a></h6></div>
      <div class="assignment__largeScreen--cell-courseDueDate">Due: 09/14/2026</div>
      <div class="assignment-score__scores--largeScreen">Score 7/10 (70%)</div>
    </div>
  </div>
  <button class="divider__header" aria-controls="summative-list" aria-expanded="true"><h5>Summative</h5></button>
  <div id="summative-list">
    <div class="selcat-assignment-row">
      <div class="assignment__largeScreen--cell-assignmentName"><h6><a>Synthetic project</a></h6></div>
      <div class="assignment__largeScreen--cell-courseDueDate">Due: 09/10/2026</div>
      <div class="assignment-score__scores--largeScreen"></div>
    </div>
  </div>
</tl-grading-task-list>
```

- [x] Add tests using `reference=date(2026, 9, 10)` that assert the practice row has `score="7/10"`, `category="formative"`, `status="low_score"`, while the project row has `category="summative"`, `status="due"`, and no score. With September 13 as the reference, the unscored September 10 project remains excluded. Add unknown/missing `h5`, missing target, duplicate IDs, nested sections, repeated quarter-task observations and explicit missing flags on a scored row. Existing tests that use generic `Category` text should continue to get no category.

- [x] Run and observe metadata assertions fail before implementation.
- [x] Build a target-ID map from `button.divider__header[aria-controls]` and its `h5`. Duplicate IDs or ambiguous category labels map to `None`. For each existing assignment row, retain the current status/due checks, then attach the normalized row-local score and nearest controlled-ancestor category if present. Scope title, score and due selectors to that assignment row. Use `soup.find(id=target_id)` or direct attribute comparison, not a CSS selector interpolating an unescaped ID.
- [x] Update existing exact-dictionary tests only to include newly expected score fields on qualifying rows. Do not weaken assertions or change the >=80%, zero-score, missing, turned-in or past-due tests.

```powershell
.venv/Scripts/python.exe tests/support/run_without_databases.py tests/test_infinite_campus_agenda.py tests/test_infinite_campus_navigation.py tests/test_infinite_campus_login.py tests/test_agenda_contract.py -q
```

- [x] Perform a one-account portal-only read-only check using inputs supplied by a human. A human must provide any needed account configuration or sanitized portal HTML; never fetch it from CRM, Neon or another database. Inspect the harness to confirm it has no database connection, account lookup, job-state persistence or `result post` call. Inspect extracted records in memory, then pass them through `normalize_agenda`. Confirm the two observed Chemistry category relationships and inspect scored rows at the DOM/parser-helper level; >=80% rows are intentionally excluded from the actual agenda output. Verify navigation through this portal-only harness, since the planning probe used direct authenticated overview navigation after its menu helper timed out. If a database-backed scheduled-workflow check is needed, have a human run it and provide the sanitized result. Await missing human inputs while continuing synthetic tests; do not copy credentials into source, documentation or fixtures.
- [x] Commit the collector and synthetic regression fixtures when these checks pass. If workflow navigation still fails, record a separate minimal reproduction before changing navigation; do not silently fold a scraper-wide navigation rewrite into this feature.

### Task 3: Shape and render score/category in the shared assignment row

**Files**

- Modify `ui/routes.py:_agenda_slots`.
- Modify `ui/static/react-dashboard.js:AgendaAssignment`.
- Modify `ui/static/react-dashboard.css:.tc-agenda-assignment` and add metadata classes.
- Extend `tests/test_read_only_dashboard_routes.py`, `tests/test_read_only_dashboard_frontend.py`, `tests/test_student_agenda_browser.py`.
- Extend synthetic `tests/fixtures/student_agenda_page_data.json`; its preview server already exists at `tests/support/student_agenda_preview.py`.

**Interfaces**

- Consumes optional stored `score`/`category` validated with Task 1's pure helpers.
- `_agenda_slots` emits optional `score` and `category` on each existing shaped assignment. No additional academic queries are needed.
- `AgendaAssignment({assignment})` renders the same shape in `GradeRow` and `AgendaClass`.

- [x] Add server-shaping tests with old rows, enriched rows and malformed metadata in both primary/secondary slots. Assert exact normalized fields on valid assignments and absence of unsafe/unrecognized fields. Existing title/status/date fields and ordering must be unchanged.
- [x] Enrich selected synthetic browser rows with a fractional score/Formative, percentage/Summative, zero score, no metadata and a long title. Add rendered browser assertions to the existing preview tests:

```python
@pytest.mark.parametrize("width", [360, 768, 1440])
def test_assignment_metadata_stays_in_the_row(browser_page, preview_url, width):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(preview_url, wait_until="networkidle")
    course = page.locator(".tc-grade-agenda").first
    course.locator("summary").click()
    row = course.locator(".tc-agenda-assignment").first
    expect(row.get_by_label("Score: 7/10", exact=True)).to_be_visible()
    expect(row.get_by_text("Formative", exact=True)).to_be_visible()
    assert page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    )
```

Add equivalent checks for the standalone Agenda card, the em-dash `Score unavailable` placeholder, unchanged due/status, and full title accessibility. Use bounding boxes to assert score/category/date remain within the row and horizontally ordered at all three widths. Existing keyboard focus and expand/collapse tests must still pass.

- [x] Run and observe failures for missing fields and rendering.
- [x] In `_agenda_slots`, explicitly add validated `score`/`category` after constructing each existing row dictionary. Keep unknown metadata out of page data. Import the pure helper, not the portal engine.
- [x] Add the score and optional category spans before the existing `time` element:

```javascript
h("span", {
    className: "tc-agenda-score",
    "aria-label": assignment.score ? `Score: ${assignment.score}` : "Score unavailable",
    title: assignment.score || "Score unavailable",
}, assignment.score || "—"),
assignment.category === "formative" || assignment.category === "summative"
    ? h("span", { className: "tc-agenda-category" },
        assignment.category === "formative" ? "Formative" : "Summative")
    : null,
```

Keep React text rendering and the existing title attribute. Use flex/grid with a shrinking title, nonshrinking normal score/category/date cells, tabular score numerals, and tighter narrow-screen gaps. Cap unusually long score text with ellipsis plus its full-value title. Do not abbreviate the standard category labels or hide the score at mobile width.

- [x] Run the targeted data/frontend/browser set and capture three synthetic screenshots for review:

```powershell
.venv/Scripts/python.exe tests/support/run_without_databases.py tests/test_read_only_dashboard_routes.py tests/test_read_only_dashboard_frontend.py tests/test_student_agenda_browser.py tests/test_template_javascript.py -q
```

- [x] Review the screenshots for both embedded and standalone rows, inspect any overflow failures, and commit the UI change after checks pass.

### Task 4: Verify the complete data path and prepare rollout

**Files**

- Extend `tests/test_agenda_grade_db_boundary.py` with an enriched-result handoff assertion.
- Create `docs/operations/assignment-metadata-rollout.md`.
- Reuse `grade_db/tests/contracts.rs`, a verified database-free portal-only diagnostic and the synthetic student preview; no new production job or migration.

**Interfaces**

- Consumes Tasks 1–3: parsed `AgendaRecord` → bounded stored item → accepted Rust result JSON → `_agenda_slots` → shared row.
- Produces recorded automated checks, read-only live findings and deployment order.

- [x] Extend the existing mocked agenda-runner boundary to assert the payload passed to the mocked `result post` carries the normalized score/category for the right slot and CRM student. Include one independent failed slot case and confirm the other slot's metadata remains intact. Mock all database readers, writers and job-state operations; this test must make no real database connection, read or write.
- [x] Run the combined suite once after the final change:

```powershell
.venv/Scripts/python.exe tests/support/run_without_databases.py tests/test_assignment_metadata.py tests/test_agenda_contract.py tests/test_infinite_campus_agenda.py tests/test_infinite_campus_navigation.py tests/test_agenda_grade_db_boundary.py tests/test_read_only_dashboard_routes.py tests/test_read_only_dashboard_frontend.py tests/test_student_agenda_browser.py tests/test_template_javascript.py -q
$env:SQLX_OFFLINE = 'true'
cargo test --locked --manifest-path grade_db/Cargo.toml --test contracts
```

- [x] Document the release order: reader/renderer first, enriched collector second, human-executed one-student database-backed smoke check before broader scheduling if rollout requires it. Record that old snapshots have no metadata, future successful refreshes add it, and the existing node budget may retain fewer items in very large agendas. Record portal-only navigation, metadata-parser checks and human-provided database-backed results separately. Agents must not run production scrapes that post data or persist job state; request a human's sanitized result when needed.
- [x] Run GitNexus change analysis and inspect every changed file. Confirm no credentials or real-student fixtures were added, no SQL migration or unrelated runner edit is included, and commit the explicit final test/operations files.

## Completion checkpoint

The feature is complete only when enriched metadata survives the entire synthetic pipeline, both row placements pass browser checks, the existing agenda status/date rules still pass, and the read-only live extraction check is recorded. Report any unexecuted production/QA rollout step as outstanding rather than claiming it ran.

## Execution record — 2026-09-13

- Task 1: optional metadata, duplicate merging and bounded JSON contract committed
  in `366956d`; all 8 Rust `grade_db` contract tests passed.
- Task 2: bulk category/score extraction and synthetic markup committed in `754bc39`.
  Portal-only live verification passed, including the existing menu helper,
  category ownership, unscored rows, fractions and normalization. Database
  connectors were blocked and no job/result writer ran.
- Task 3: validated page data and responsive rows committed in `00a86ab`.
  Browser checks and synthetic screenshots passed at 360, 768 and 1440 pixels.
  A duplicate-title/date regression also verifies metadata cannot break sorting.
- Task 4: mocked-writer coverage proves each slot preserves metadata independently,
  including a failed sibling slot. Combined Grades verification passed **408 tests**.
- All work and review were inline. No application database connection, deployment
  or production refresh was performed. Database-backed rollout checks remain human-owned.

See [Rollout and verification evidence](../../operations/assignment-metadata-rollout.md).
