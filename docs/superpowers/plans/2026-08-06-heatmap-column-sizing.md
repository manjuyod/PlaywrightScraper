# Heatmap Column Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cap the adaptive Course column at 260px and restore compact, independently sized generated grade columns without splitting the semantic heatmap table.

**Architecture:** Keep `GradeHeatmap` as one HTML table inside its existing horizontal overflow container. Constrain an inner Course label so automatic table layout receives a bounded intrinsic width, and give all later columns a separate responsive sizing rule.

**Tech Stack:** Flask, React through `React.createElement`, plain CSS, pytest contract tests, Node syntax checking, Playwright CLI.

## Global Constraints

- Course has a 100px minimum and 260px total maximum.
- Course cells retain exactly 8px of horizontal padding on each side.
- Visible Course labels use a 244px maximum, stay on one line, and ellipsize overflow.
- Every Course row header exposes the complete course name through `title` and retains the complete text in the DOM.
- Generated grade columns use automatic width with a 58px minimum on regular screens and 48px at the existing 760px mobile breakpoint.
- Only Course uses `width: 1%`; generated grade columns must not use it because they need to absorb spare table width without stretching Course.
- Keep one semantic table and the existing horizontal overflow container.
- Do not add sticky positioning, JavaScript width measurement, wrapping, API changes, or data-shape changes.

---

## File Structure

- Modify `tests/test_read_only_dashboard_frontend.py`: define the static frontend contracts for Course and generated grade sizing.
- Modify `ui/static/react-dashboard.css`: implement independent Course-label and generated-grade sizing rules.
- Modify `ui/static/react-dashboard.js`: render a constrained Course label and expose each complete name through `title`.
- Preserve `docs/superpowers/specs/2026-08-06-heatmap-column-sizing-design.md` as the approved design record.

### Task 1: Implement independent heatmap column sizing

**Files:**
- Modify: `tests/test_read_only_dashboard_frontend.py:115-150`
- Modify: `ui/static/react-dashboard.css:694-715, 1145-1173`
- Modify: `ui/static/react-dashboard.js:697-761`
- Create: `docs/superpowers/plans/2026-08-06-heatmap-column-sizing.md`

**Interfaces:**
- Consumes: `GradeHeatmap({ history })`, where `history` is an object keyed by week and then course.
- Produces: unchanged heatmap markup semantics with the internal CSS class `tc-heatmap-course-label` and native row-header `title` attributes.

- [x] **Step 1: Replace the Course CSS contract test with the bounded adaptive contract**

```python
def test_heatmap_course_column_is_bounded_and_adaptive() -> None:
    css = (ROOT / "ui" / "static" / "react-dashboard.css").read_text(
        encoding="utf-8"
    )
    javascript = (ROOT / "ui" / "static" / "react-dashboard.js").read_text(
        encoding="utf-8"
    )
    column_selector = ".tc-heatmap-table th:first-child {"
    label_selector = ".tc-heatmap-course-label {"

    course_column = css.split(column_selector, 1)[1].split("}", 1)[0]
    course_label = css.split(label_selector, 1)[1].split("}", 1)[0]

    assert "min-width: 100px;" in course_column
    assert "width: 1%;" in course_column
    assert "padding-inline: 8px;" in course_column
    assert "display: block;" in course_label
    assert "max-width: 244px;" in course_label
    assert "overflow: hidden;" in course_label
    assert "text-overflow: ellipsis;" in course_label
    assert "white-space: nowrap;" in course_label
    assert 'className: "tc-heatmap-course-label"' in javascript
    assert "title: course" in javascript
```

- [x] **Step 2: Run the Course test and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_read_only_dashboard_frontend.py::test_heatmap_course_column_is_bounded_and_adaptive -q
```

Expected: FAIL because `.tc-heatmap-course-label` and `title: course` do not exist.

- [x] **Step 3: Update the generated-grade CSS contract test**

```python
def test_heatmap_grade_columns_are_individually_compact() -> None:
    css = (ROOT / "ui" / "static" / "react-dashboard.css").read_text(
        encoding="utf-8"
    )
    selector = ".tc-heatmap-table tr > :not(:first-child) {"

    grade_columns = css.split(selector, 1)[1].split("}", 1)[0]
    mobile_css = css.split("@media (max-width: 760px) {", 1)[1]
    mobile_grade_columns = mobile_css.split(selector, 1)[1].split("}", 1)[0]

    assert "min-width: 58px;" in grade_columns
    assert "width: 1%;" not in grade_columns
    assert "white-space: nowrap;" in grade_columns
    assert "padding-inline: 8px;" in grade_columns
    assert "min-width: 48px;" in mobile_grade_columns
```

- [x] **Step 4: Run the grade-column test and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_read_only_dashboard_frontend.py::test_heatmap_grade_columns_are_individually_compact -q
```

Expected: FAIL because the regular rule still uses 100px and no mobile override exists.

- [x] **Step 5: Implement the constrained Course label markup**

Update the Course header and each row header inside `GradeHeatmap`:

```javascript
h(
    "th",
    { scope: "col" },
    h("span", { className: "tc-heatmap-course-label" }, "Course"),
),
```

```javascript
h(
    "th",
    { scope: "row", title: course },
    h("span", { className: "tc-heatmap-course-label" }, course),
),
```

- [x] **Step 6: Implement independent CSS sizing**

Use separate rules for Course and later columns:

```css
.tc-heatmap-table th:first-child {
    min-width: 100px;
    width: 1%;
    white-space: nowrap;
    padding-inline: 8px;
}

.tc-heatmap-course-label {
    display: block;
    max-width: 244px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.tc-heatmap-table tr > :not(:first-child) {
    min-width: 58px;
    white-space: nowrap;
    padding-inline: 8px;
}
```

At the existing mobile breakpoint, add:

```css
.tc-heatmap-table tr > :not(:first-child) {
    min-width: 48px;
}
```

- [x] **Step 7: Run focused tests and verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_read_only_dashboard_frontend.py -k "heatmap_course_column or heatmap_grade_columns" -q
```

Expected: 2 passed.

- [x] **Step 8: Run static and frontend verification**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_read_only_dashboard_frontend.py -q
node --check ui/static/react-dashboard.js
.venv\Scripts\ruff.exe check tests/test_read_only_dashboard_frontend.py
git diff --check
```

Expected: all frontend tests pass, JavaScript parses, Ruff passes, and the diff has no whitespace errors.

- [x] **Step 9: Run the full Python suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected: no new failures beyond the two known authorization-status failures in `tests/test_read_only_dashboard_routes.py`.

- [x] **Step 10: Restart the server and run Playwright smoke verification**

Start Flask on `127.0.0.1:8080`, use the controlled grade-history fixture from the earlier reproduction, and inspect both 1440px desktop and 390px mobile viewports.

Expected:

- Course width is at least 100px and no more than 260px.
- The long Course label ellipsizes and its row header has the full native tooltip.
- Generated grade columns are compact and aligned between header and body.
- The table scrolls horizontally on mobile.
- The page reports no browser errors.

- [x] **Step 11: Analyze scope and commit**

Run GitNexus change detection, review the diff, and then commit only the planned files:

```powershell
git add docs/superpowers/plans/2026-08-06-heatmap-column-sizing.md tests/test_read_only_dashboard_frontend.py ui/static/react-dashboard.css ui/static/react-dashboard.js
git commit -m "fix: separate heatmap column sizing"
```
