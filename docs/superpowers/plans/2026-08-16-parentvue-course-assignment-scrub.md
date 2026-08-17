# ParentVUE Current-Period Assignment Scrub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect ParentVUE upcoming, explicitly missing, and below-80-percent assignments by sequentially scrubbing every visible course in the current grading period, then display low-score items with a `LOW` frontend marker.

**Architecture:** Extend the shared agenda contract with a `low_score` bucket and deterministic status precedence. Keep ParentVUE HTML interpretation in pure parser functions, place stateful sequential course navigation in a focused scrub module, and let `ParentVUE.get_agenda()` delegate to it. Update the dashboard projection and renderer for the third bucket while accepting legacy stored payloads that omit it.

**Tech Stack:** Python 3.11+, Playwright async API, BeautifulSoup, pytest, Flask dashboard projection, vanilla React `createElement`, CSS, Rust grade-db JSON boundary, GitNexus.

## Global Constraints

- Use the current grading period only; hidden and old-term courses are excluded.
- Missing status requires explicit ParentVUE evidence and takes precedence over `low_score` and `due`.
- `low_score` means a numeric percentage strictly below 80, including a calculated earned/possible ratio with a positive denominator.
- Exactly 80 or higher, ungraded, excused, pass/fail, blank, and nonnumeric scores are excluded unless explicitly missing.
- Do not store or display the numeric score; the frontend marker is `LOW` with accessible label `Low-scoring assignment`.
- Course traversal is sequential and atomic. Any ambiguous or malformed course fails the entire ParentVUE agenda slot.
- Do not increase agenda worker concurrency or retry a login after password submission.
- Diagnostics must not contain assignment content, credentials, URLs, HTML, or exception messages.
- SMSS access is limited to the existing Rust-boundary `SELECT` path. Neon remains read-only for live validation.
- Before editing any production symbol, run `gitnexus_impact({target: "<symbol>", direction: "upstream"})`; warn before HIGH/CRITICAL changes.
- Before every commit, run `gitnexus_detect_changes()` and confirm the affected symbols and flows are expected.

---

### Task 1: Extend the canonical agenda contract with `low_score`

**Files:**
- Modify: `scraper/agenda_contract.py:9-210`
- Modify: `tests/test_agenda_contract.py`
- Modify expected normalized buckets in: `tests/test_agenda_grade_db_boundary.py`
- Modify expected normalized buckets in: `tests/test_canvas_agenda.py`
- Modify: `grade_db/tests/contracts.rs:109-133`

**Interfaces:**
- Consumes: portal records shaped as `AgendaRecord`.
- Produces: `AgendaStatus = Literal["missing", "low_score", "due"]`; every newly normalized `AgendaBuckets` contains lists named `missing`, `low_score`, and `due`.

- [ ] **Step 1: Add failing normalization and precedence tests**

Add focused tests that make the intended bucket and precedence explicit:

```python
def test_normalize_adds_low_score_bucket_and_prefers_it_over_due() -> None:
    due = {
        "sourceId": "pv-7",
        "course": "Algebra II",
        "title": "Systems practice",
        "dueDate": "2026-08-14",
        "dueTime": None,
        "status": "due",
    }
    low = {**due, "status": "low_score"}

    assert normalize_agenda([due, low]) == {
        "2026-08-10": {
            "Algebra II": {
                "missing": [],
                "low_score": [
                    {
                        "title": "Systems practice",
                        "dueDate": "2026-08-14",
                        "dueTime": None,
                    }
                ],
                "due": [],
            }
        }
    }


def test_missing_precedes_low_score_for_same_assignment() -> None:
    base = {
        "sourceId": "pv-9",
        "course": "Biology",
        "title": "Cell transport",
        "dueDate": "2026-08-13",
        "dueTime": None,
    }
    result = normalize_agenda(
        [{**base, "status": "low_score"}, {**base, "status": "missing"}]
    )

    buckets = result["2026-08-10"]["Biology"]
    assert [row["title"] for row in buckets["missing"]] == ["Cell transport"]
    assert buckets["low_score"] == []
    assert buckets["due"] == []
```

Also update the node-budget regression so it asserts the bounded result remains below the Rust limit after every new course owns three empty status arrays.

- [ ] **Step 2: Run the tests and capture RED**

Run:

```powershell
uv run pytest tests/test_agenda_contract.py -q
```

Expected: failures because `low_score` is rejected or absent and due currently wins over it.

- [ ] **Step 3: Run pre-edit impact analysis**

Run upstream impact for `normalize_agenda`, `_bounded_weeks`, `AgendaStatus`, and `AgendaBuckets`. Report direct callers, processes, modules, and risk before editing.

- [ ] **Step 4: Implement the minimum contract change**

Use a single status order everywhere:

```python
AgendaStatus = Literal["missing", "low_score", "due"]
AGENDA_STATUSES: tuple[AgendaStatus, ...] = ("missing", "low_score", "due")
_STATUS_PRIORITY: dict[AgendaStatus, int] = {
    "due": 0,
    "low_score": 1,
    "missing": 2,
}


class AgendaBuckets(TypedDict):
    missing: list[StoredAgendaItem]
    low_score: list[StoredAgendaItem]
    due: list[StoredAgendaItem]
```

Replace two-status loops with `AGENDA_STATUSES`, initialize all three arrays, and replace the special-case missing/due duplicate rule with the priority comparison:

```python
if (
    existing is None
    or _STATUS_PRIORITY[status] > _STATUS_PRIORITY[existing[4]]
    or (
        status == existing[4]
        and _agenda_record_sort_key(candidate)
        < _agenda_record_sort_key(existing)
    )
):
    deduplicated[identity] = candidate
```

In `_bounded_weeks`, change the new-course structural increment from `3` to `4` nodes: one course object plus three status arrays. Keep `MAX_AGENDA_WEEKS_NODES = 497`; it caps the complete weeks subtree independent of bucket count.

- [ ] **Step 5: Update cross-portal expectations and the Rust acceptance fixture**

Add empty `low_score` arrays to newly normalized Python expectations. Extend the Rust agenda fixture with a `low_score` item and keep `ResultOutcome::AgendaSuccess::validate_for_job` unchanged because the Rust boundary intentionally validates bounded JSON rather than portal-specific bucket names.

- [ ] **Step 6: Run GREEN verification**

Run:

```powershell
uv run pytest tests/test_agenda_contract.py tests/test_agenda_grade_db_boundary.py tests/test_canvas_agenda.py -q
cargo test --manifest-path grade_db/Cargo.toml agenda_result_accepts_portal_slot_bundle
uv run ruff check scraper/agenda_contract.py tests/test_agenda_contract.py tests/test_agenda_grade_db_boundary.py tests/test_canvas_agenda.py
git diff --check
```

Expected: all pass.

- [ ] **Step 7: Detect impact and commit**

Run `gitnexus_detect_changes()`, confirm only the agenda normalization/result-contract flows are affected, then commit:

```powershell
git add scraper/agenda_contract.py tests/test_agenda_contract.py tests/test_agenda_grade_db_boundary.py tests/test_canvas_agenda.py grade_db/tests/contracts.rs
git commit -m "feat: add low-score agenda bucket"
```

---

### Task 2: Parse live ParentVUE overview and course assignment markup

**Files:**
- Modify: `scraper/portals/parentvue_agenda.py:10-187`
- Modify: `tests/test_parentvue_agenda.py`

**Interfaces:**
- Consumes: sanitized ParentVUE overview or course-detail HTML plus an explicit course name and reference date.
- Produces:
  - `parse_parentvue_overview(html: str, *, reference: datetime) -> list[AgendaRecord]`
  - `parse_parentvue_course_assignments(html: str, *, course: str, reference: datetime) -> list[AgendaRecord]`
  - `parse_parentvue_agenda(html: str) -> list[AgendaRecord]` retained as a compatibility wrapper for existing callers/tests.

- [ ] **Step 1: Add representative live-markup fixtures and failing overview tests**

Add sanitized inline fixtures matching the observed structures:

```html
<div id="gb-assignments">
  <h2 class="title">Upcoming Assignments</h2>
  <div class="gb-student-assignments-grid">
    <table>
      <tr class="gb-upcoming-assignment" data-guid="pv-upcoming-1">
        <td>
          <div><a href="/assignment/details/1">Systems review</a></div>
          <div>Algebra II</div>
          <div>Due Date: 08/18/2026</div>
          <div class="hide">internal</div>
        </td>
      </tr>
    </table>
  </div>
</div>
```

Assert `parse_parentvue_overview(..., reference=...)` returns one `due` record with course, title, date, and `sourceId="parentvue:pv-upcoming-1"`. Add a conflict fixture proving a visible legacy no-data marker plus a live upcoming row raises `ParentVueAgendaError`.

- [ ] **Step 2: Add failing course-detail classification tests**

Use the observed `.pxp-course-content .item-container` shape:

```html
<div class="pxp-course-content">
  <div class="item-container">
    <div class="item-text-main">Quiz One</div>
    <div class="item-text-special">Aug 14</div>
    <div class="item-text-special">72%</div>
  </div>
  <div class="item-container missing">
    <div class="item-text-main">Worksheet</div>
    <div class="item-text-special">Aug 15</div>
    <div class="item-text-small">Missing</div>
    <div class="item-text-special">0%</div>
  </div>
  <div class="item-container">
    <div class="item-text-main">Lab</div>
    <div class="item-text-special">Aug 16</div>
    <div class="item-text-small">7 / 10</div>
  </div>
</div>
```

Assert:

- `72%` becomes `low_score`.
- explicit Missing becomes `missing`, never `low_score`.
- `7 / 10` becomes `low_score`.
- `80%`, `8 / 10`, blank, `Excused`, `Pass`, and `Not Graded` are excluded.
- a denominator of zero is excluded.
- a recognized `.pxp-course-content .no-data` returns `[]`.
- no item rows and no explicit empty marker raises `ParentVueAgendaError`.
- a present item missing title or due date makes the whole course fail rather than silently dropping it.

- [ ] **Step 3: Run parser tests and capture RED**

Run:

```powershell
uv run pytest tests/test_parentvue_agenda.py -q
```

Expected: new parser functions are absent and live rows are not recognized.

- [ ] **Step 4: Run pre-edit impact analysis**

Run upstream impact for `_assignment_rows` and `parse_parentvue_agenda`. Report risk before editing.

- [ ] **Step 5: Implement pure parsing helpers**

Add strict helpers with these responsibilities:

```python
_PERCENT = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*%")
_POINTS = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")


def _academic_year_date(value: str, *, reference: datetime) -> tuple[str, str | None] | None:
    # Existing full ISO/MM-DD-YYYY formats remain supported.
    # For live "Aug 14" values, July-December use the academic-year start;
    # January-June use the following calendar year.


def _assignment_percentage(item: Tag) -> float | None:
    # Prefer an explicit percent. Otherwise calculate earned / possible * 100
    # only when the denominator is positive.


def _explicitly_missing(item: Tag) -> bool:
    # Accept a missing class/data-status or an exact visible "Missing" status
    # descendant. Do not infer missing from zero, blanks, or past due dates.
```

For live overview rows, use only rows under the Upcoming Assignments panel or `#gb-assignments tr.gb-upcoming-assignment`. Interpret the direct visible cells as title link, course text, and due-date text; ignore `.hide` content.

For course detail, require `.pxp-course-content`, then parse each visible `.item-container`. Title comes from `.item-text-main`; date and score candidates come from `.item-text-special` and `.item-text-small`. If the course is recognizable and any item is malformed, raise `ParentVueAgendaError` to preserve atomicity.

Keep legacy fixture support inside `parse_parentvue_agenda`; do not search arbitrary document-wide table rows.

- [ ] **Step 6: Run GREEN parser verification**

Run:

```powershell
uv run pytest tests/test_parentvue_agenda.py -q
uv run ruff check scraper/portals/parentvue_agenda.py tests/test_parentvue_agenda.py
git diff --check
```

Expected: all pass.

- [ ] **Step 7: Detect impact and commit**

Run `gitnexus_detect_changes()`, confirm the diff is limited to ParentVUE pure parsing/tests, then commit:

```powershell
git add scraper/portals/parentvue_agenda.py tests/test_parentvue_agenda.py
git commit -m "feat: parse ParentVUE course assignments"
```

---

### Task 3: Scrub current-period ParentVUE courses sequentially

**Files:**
- Create: `scraper/portals/parentvue_course_scrub.py`
- Modify: `scraper/portals/parentvue.py:13-100`
- Create: `tests/test_parentvue_course_scrub.py`
- Modify: `tests/test_parentvue_agenda.py`

**Interfaces:**
- Consumes: authenticated ParentVUE `Page`; pure parsers from Task 2.
- Produces: `collect_parentvue_course_agenda(page: Page, *, reference: datetime | None = None) -> list[AgendaRecord]`.
- `ParentVUE.get_agenda()` returns `await collect_parentvue_course_agenda(self.page)`.

- [ ] **Step 1: Write failing sequential-navigation tests**

Create an API-shaped fake page that models:

- Overview: three live `gb-upcoming-assignment` rows and seven visible `div.gb-class-header.gb-class-row` rows, each containing `button.course-title`.
- Course view: `.pxp-course-content` containing item markup or an explicit `.no-data` marker.
- Return control: visible button with accessible name `All Classes`.

Assert the collector performs this exact sequence:

```python
assert page.actions == [
    "capture-overview",
    "open-course:0", "capture-course:0", "all-classes",
    "open-course:1", "capture-course:1", "all-classes",
]
```

Add separate tests proving:

- course locators are reacquired after every `All Classes` return;
- hidden course rows are not visited;
- an explicit-empty course succeeds with no rows;
- one malformed course raises `ParentVueAgendaError` and returns no partial list;
- no course is opened concurrently;
- credentials/login are never invoked by the collector.

- [ ] **Step 2: Run navigation tests and capture RED**

Run:

```powershell
uv run pytest tests/test_parentvue_course_scrub.py -q
```

Expected: import failure because the scrub module does not exist.

- [ ] **Step 3: Run pre-edit impact analysis**

Run upstream impact for `ParentVUE.after_login` and `ParentVUE.get_agenda`. Report risk before editing.

- [ ] **Step 4: Implement the focused sequential collector**

Create an immutable descriptor so no live locator survives a navigation:

```python
@dataclass(frozen=True)
class ParentVueCourse:
    index: int
    title: str
```

The collector must:

```python
async def collect_parentvue_course_agenda(
    page: Page,
    *,
    reference: datetime | None = None,
) -> list[AgendaRecord]:
    effective_reference = reference or datetime.now()
    overview = parse_parentvue_overview(
        await page.content(), reference=effective_reference
    )
    courses = await _visible_current_courses(page)
    records = list(overview)
    for course in courses:
        rows = page.locator("div.gb-class-header.gb-class-row:visible")
        row = rows.nth(course.index)
        current_title = " ".join(
            (await row.locator("button.course-title").inner_text()).split()
        )
        if current_title != course.title:
            raise ParentVueAgendaError()
        await row.locator("button.course-title").click()
        await _wait_for_course_detail(page)
        records.extend(
            parse_parentvue_course_assignments(
                await page.content(),
                course=course.title,
                reference=effective_reference,
            )
        )
        await page.get_by_role("button", name="All Classes", exact=True).click()
        await _wait_for_overview(page)
    return records
```

`_wait_for_course_detail` requires `.pxp-course-content` and then either visible `.item-container` or a recognized course-level `.no-data`. `_wait_for_overview` requires `#gb-assignments` plus the original visible course count. Use the page's 15-second default for actions and an explicit 30-second content readiness ceiling.

Update `ParentVUE.after_login` so overview readiness accepts the observed live conditions:

```css
#gb-assignments tr.gb-upcoming-assignment:visible,
div.gb-class-header.gb-class-row:visible,
#gb-assignments .no-data:visible
```

Do not wait for Recent History and do not parse it.

- [ ] **Step 5: Wire `ParentVUE.get_agenda` to the collector**

Replace direct `page.content()` parsing with the new async collector. Preserve the existing sanitized `LoginError` conversion around post-submit readiness; do not add login retries.

- [ ] **Step 6: Run GREEN engine verification**

Run:

```powershell
uv run pytest tests/test_parentvue_course_scrub.py tests/test_parentvue_agenda.py tests/test_agenda_grade_db_boundary.py -q
uv run ruff check scraper/portals/parentvue.py scraper/portals/parentvue_course_scrub.py tests/test_parentvue_course_scrub.py
git diff --check
```

Expected: all pass.

- [ ] **Step 7: Detect impact and commit**

Run `gitnexus_detect_changes()`, inspect ParentVUE login/agenda flows, then commit:

```powershell
git add scraper/portals/parentvue.py scraper/portals/parentvue_course_scrub.py tests/test_parentvue_course_scrub.py tests/test_parentvue_agenda.py
git commit -m "feat: scrub ParentVUE courses sequentially"
```

---

### Task 4: Project and render the `LOW` frontend bucket

**Files:**
- Modify: `ui/routes.py:100-230`
- Modify: `ui/static/react-dashboard.js:883-974`
- Modify: `ui/static/react-dashboard.css:850-880`
- Modify: `tests/test_read_only_dashboard_routes.py`
- Modify: `tests/test_read_only_dashboard_frontend.py`

**Interfaces:**
- Consumes: canonical stored buckets where `low_score` may be present or absent.
- Produces: projected assignments with `status` equal to `missing`, `low_score`, or `due`; frontend marker `LOW` and accessible label `Low-scoring assignment`.

- [ ] **Step 1: Add failing route-projection tests**

Extend the dashboard route fixture with one low-score item and assert all three statuses are projected. Add a legacy fixture with only `missing` and `due` and assert projection still succeeds with no low items.

```python
assert assignments == [
    {
        "status": "low_score",
        "title": "Systems practice",
        "dueDate": "2026-08-14",
        "dueTime": None,
        "dueDisplay": "Aug 14",
    }
]
```

- [ ] **Step 2: Add failing frontend behavior tests**

In the Node scenario fixture, render a `low_score` assignment and assert the output contains:

```javascript
assert.ok(html.includes("LOW"));
assert.ok(html.includes("Low-scoring assignment"));
assert.ok(html.includes("tc-agenda-marker--low_score"));
```

Also assert the status legend contains `Low` exactly once.

- [ ] **Step 3: Run dashboard tests and capture RED**

Run:

```powershell
uv run pytest tests/test_read_only_dashboard_routes.py tests/test_read_only_dashboard_frontend.py -q
```

Expected: the route omits `low_score` and the frontend renders it as `DUE`.

- [ ] **Step 4: Run pre-edit impact analysis**

Run upstream impact for `_agenda_slots`/its indexed route-projection symbol and `AgendaClass` if indexed. Report risk before editing.

- [ ] **Step 5: Implement backward-compatible projection**

In `ui/routes.py`, require `missing` and `due` to remain lists, but default a missing low bucket for legacy records:

```python
missing = raw_buckets.get("missing")
low_score = raw_buckets.get("low_score", [])
due = raw_buckets.get("due")
if not all(isinstance(rows, list) for rows in (missing, low_score, due)):
    continue

for status, rows in (
    ("missing", missing),
    ("low_score", low_score),
    ("due", due),
):
    ...
```

- [ ] **Step 6: Implement the frontend status metadata**

Replace the missing/due ternary with an exact map:

```javascript
const agendaStatus = {
    missing: { marker: "M", label: "Missing assignment" },
    low_score: { marker: "LOW", label: "Low-scoring assignment" },
    due: { marker: "DUE", label: "Upcoming assignment" },
}[assignment.status];
```

Add the Low legend entry and a dedicated `.tc-agenda-marker--low_score` style. Do not display the numeric score.

- [ ] **Step 7: Run GREEN dashboard verification**

Run:

```powershell
uv run pytest tests/test_read_only_dashboard_routes.py tests/test_read_only_dashboard_frontend.py -q
git diff --check
```

Expected: all pass.

- [ ] **Step 8: Detect impact and commit**

Run `gitnexus_detect_changes()`, confirm only dashboard projection/rendering flows are affected, then commit:

```powershell
git add ui/routes.py ui/static/react-dashboard.js ui/static/react-dashboard.css tests/test_read_only_dashboard_routes.py tests/test_read_only_dashboard_frontend.py
git commit -m "feat: display low-score agenda items"
```

---

### Task 5: Integrated review, verification, and Jordan live validation

**Files:**
- Review all files changed by Tasks 1-4.
- Do not save live HTML, screenshots, traces, cookies, or storage state.

**Interfaces:**
- Consumes: complete contract, ParentVUE parser/scrub, and dashboard support.
- Produces: reviewed commits plus a live ParentVUE result containing bounded counts only.

- [ ] **Step 1: Run specification and security review**

Review the full feature diff against:

```text
docs/superpowers/specs/2026-08-16-parentvue-course-assignment-scrub-design.md
```

Resolve every Critical or Important finding and repeat the relevant review. Specifically audit status precedence, legacy payload compatibility, sequential navigation, atomic failure, selector scoping, credential resubmission, and assignment-content logging.

- [ ] **Step 2: Run focused verification**

Run:

```powershell
uv run pytest tests/test_agenda_contract.py tests/test_agenda_grade_db_boundary.py tests/test_parentvue_agenda.py tests/test_parentvue_course_scrub.py tests/test_read_only_dashboard_routes.py tests/test_read_only_dashboard_frontend.py tests/test_secret_redaction.py -q
uv run ruff check scraper/agenda_contract.py scraper/portals/parentvue.py scraper/portals/parentvue_agenda.py scraper/portals/parentvue_course_scrub.py ui/routes.py tests/test_agenda_contract.py tests/test_parentvue_agenda.py tests/test_parentvue_course_scrub.py
git diff --check
```

- [ ] **Step 3: Run the broader suites**

Run:

```powershell
uv run pytest -q
cargo test --manifest-path grade_db/Cargo.toml
```

Record the two previously approved unrelated dashboard authorization failures separately if they remain. Any new failure blocks completion.

- [ ] **Step 4: Validate against Jordan's authenticated session**

Reuse the preserved CDP browser session if it is still authenticated. Otherwise perform one bounded login using the Rust-boundary SMSS `SELECT` and no database writes. Run the new ParentVUE collector sequentially and emit only:

```text
portal=parentvue
courses_visited=<bounded integer>
due_count=<bounded integer>
missing_count=<bounded integer>
low_score_count=<bounded integer>
weeks_count=<bounded integer>
```

Require every visible current-period course to be visited and the collector to return without `ParentVueAgendaError`. Do not print assignment titles, course names, scores, URLs, credentials, HTML, or exception messages.

- [ ] **Step 5: Analyze the complete change scope**

Run `gitnexus_detect_changes(scope="all")`, inspect all affected processes, verify `git status --short` is clean, and compare the feature range against its base with `git diff --check <base>...HEAD`.

- [ ] **Step 6: Publish only after user approval**

Use git identity `manjuyod <manjuyod@gmail.com>`. Push the current `dev` branch only after review and live verification satisfy the criteria above.
