# Franchise Course Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove leading numeric period prefixes from Franchise Page course labels and alphabetize only the Recent Grades list.

**Architecture:** Keep raw course keys in Neon and in the weekly comparison path. Add one dashboard display helper, apply it only when constructing `CourseGrade` values, and sort the resulting current snapshot by its cleaned label before deriving the existing score-ranked Low and High lists.

**Tech Stack:** Python 3.12, dataclasses, regular expressions, pytest, Flask dashboard data shaping, GitNexus.

## Global Constraints

- Do not change scraper output or persisted `weeklydata` JSON.
- Strip only a leading integer-and-colon prefix, allowing whitespace around the integer and colon.
- Preserve the raw course key for exact-name change comparisons between weeks.
- Apply cleaned labels to Recent, Low, and High Grades.
- Alphabetize only Recent Grades; retain score-based Low and High ordering.
- Keep the route payload shape unchanged: `course`, `grade`, and `change`.
- Do not add dependencies.

---

## File Structure

- Modify `ui/dashboard_data.py`: define the course-prefix pattern and display helper, then use the helper and an alphabetical sort in `build_student_report`.
- Modify `tests/test_dashboard_data.py`: add focused helper and report behavior coverage.

### Task 1: Course display-label helper

**Files:**
- Modify: `ui/dashboard_data.py:104-106,414-423`
- Test: `tests/test_dashboard_data.py:109`

**Interfaces:**
- Consumes: raw course labels as `str` values.
- Produces: `_display_course_name(course: str) -> str`, returning a non-blank display label.

- [ ] **Step 1: Write the failing helper test**

Add this test before the existing student-report test in `tests/test_dashboard_data.py`:

```python
@pytest.mark.parametrize(
    ("raw_course", "expected"),
    [
        ("1: ENGLISH 8", "ENGLISH 8"),
        (" 3 : SPANISH IB ", "SPANISH IB"),
        ("12:  ADVANCED CHOIR", "ADVANCED CHOIR"),
        ("HISTORY 8-A", "HISTORY 8-A"),
        ("101 ALGEBRA", "101 ALGEBRA"),
        ("1:", "1:"),
    ],
)
def test_display_course_name_removes_only_a_leading_period_prefix(
    raw_course: str, expected: str
) -> None:
    assert dashboard_data._display_course_name(raw_course) == expected
```

- [ ] **Step 2: Run the helper test and verify RED**

Run:

```powershell
uv run pytest tests/test_dashboard_data.py::test_display_course_name_removes_only_a_leading_period_prefix -q
```

Expected: FAIL with `AttributeError` because `_display_course_name` does not exist.

- [ ] **Step 3: Implement the minimal display helper**

Add the compiled expression beside `_SAFE_CODE` and `_GRADE_LEVEL` in `ui/dashboard_data.py`:

```python
_COURSE_PERIOD_PREFIX = re.compile(r"^\s*\d+\s*:\s*")
```

Add the helper immediately after `_numeric_grades`:

```python
def _display_course_name(course: str) -> str:
    original = course.strip()
    cleaned = _COURSE_PERIOD_PREFIX.sub("", original).strip()
    return cleaned or original
```

- [ ] **Step 4: Run the helper test and verify GREEN**

Run:

```powershell
uv run pytest tests/test_dashboard_data.py::test_display_course_name_removes_only_a_leading_period_prefix -q
```

Expected: PASS with one parametrized test group and no warnings.

- [ ] **Step 5: Run the dashboard-data tests**

Run:

```powershell
uv run pytest tests/test_dashboard_data.py -q
```

Expected: all dashboard-data tests PASS.

- [ ] **Step 6: Check the first change scope before committing**

Run:

```text
gitnexus_detect_changes({repo: "PlaywrightScraper", scope: "all"})
```

Expected: only the new dashboard display helper and its test are changed; no scraper or persistence process is affected.

- [ ] **Step 7: Commit the helper**

```powershell
git add -- ui/dashboard_data.py tests/test_dashboard_data.py
git commit -m "feat: normalize franchise course display labels"
```

### Task 2: Alphabetical Recent Grades integration

**Files:**
- Modify: `ui/dashboard_data.py:423-458`
- Test: `tests/test_dashboard_data.py:110-132`

**Interfaces:**
- Consumes: `_display_course_name(course: str) -> str` from Task 1 and raw weekly course-to-grade mappings.
- Produces: `build_student_report(student: DashboardStudent) -> DashboardStudent` with cleaned labels, alphabetized `grades_snapshot`, score-ranked `low_grades` and `high_grades`, and raw-key change comparisons.

- [ ] **Step 1: Add failing report behavior tests**

Add these tests after the helper test in `tests/test_dashboard_data.py`:

```python
def _course_label_report() -> dashboard_data.DashboardStudent:
    return dashboard_data.merge_student_rows(
        [_crm_student(101)],
        [
            {
                "crmstudentid": 101,
                "weeklydata": {
                    "2026-07-06": {
                        "7: ALGEBRA": 70.0,
                        "2: BIOLOGY": 90.0,
                        "4: CHEMISTRY": 98.0,
                        "9: ZOOLOGY": 91.0,
                    },
                    "2026-07-13": {
                        "9: ZOOLOGY": 91.0,
                        "1: ALGEBRA": 72.0,
                        "2: BIOLOGY": 88.0,
                        "4: CHEMISTRY": 99.0,
                    },
                },
            }
        ],
    )[0]


def test_student_report_alphabetizes_cleaned_recent_course_labels() -> None:
    report = _course_label_report()

    assert [grade.course for grade in report.grades_snapshot] == [
        "ALGEBRA",
        "BIOLOGY",
        "CHEMISTRY",
        "ZOOLOGY",
    ]


def test_student_report_keeps_low_and_high_grades_ordered_by_score() -> None:
    report = _course_label_report()

    assert [(grade.course, grade.grade) for grade in report.low_grades] == [
        ("ALGEBRA", 72.0),
        ("BIOLOGY", 88.0),
        ("ZOOLOGY", 91.0),
    ]
    assert [(grade.course, grade.grade) for grade in report.high_grades] == [
        ("ZOOLOGY", 91.0),
        ("CHEMISTRY", 99.0),
    ]


def test_student_report_matches_changes_with_raw_course_names() -> None:
    report = _course_label_report()
    by_course = {grade.course: grade for grade in report.grades_snapshot}

    assert by_course["ALGEBRA"].change is None
    assert by_course["BIOLOGY"].change == "-"
    assert by_course["CHEMISTRY"].change == "+"
    assert by_course["ZOOLOGY"].change is None
```

- [ ] **Step 2: Run the new report tests and verify RED**

Run:

```powershell
uv run pytest tests/test_dashboard_data.py::test_student_report_alphabetizes_cleaned_recent_course_labels tests/test_dashboard_data.py::test_student_report_keeps_low_and_high_grades_ordered_by_score tests/test_dashboard_data.py::test_student_report_matches_changes_with_raw_course_names -q
```

Expected: all three tests FAIL because the report still exposes raw prefixed labels and keeps the incoming JSON-object order for Recent Grades.

- [ ] **Step 3: Reconfirm impact immediately before editing the report builder**

Run:

```text
gitnexus_impact({repo: "PlaywrightScraper", target: "build_student_report", direction: "upstream", includeTests: true})
```

Expected: LOW risk, with `merge_student_rows` as the single direct caller and only the UI student-loading flow affected. Stop and warn the user if the refreshed result is HIGH or CRITICAL.

- [ ] **Step 4: Apply cleaned labels and alphabetical snapshot ordering**

Replace the `snapshot` construction in `build_student_report` with:

```python
    snapshot = tuple(
        sorted(
            (
                CourseGrade(
                    course=_display_course_name(course),
                    grade=grade,
                    change=(
                        "+"
                        if course in previous and grade > previous[course]
                        else "-"
                        if course in previous and grade < previous[course]
                        else None
                    ),
                )
                for course, grade in current.items()
            ),
            key=lambda item: item.course.casefold(),
        )
    )
```

Do not change the subsequent score sort or the `low_grades` and `high_grades` slices. The raw `course` loop variable remains the comparison key, while `CourseGrade.course` becomes the cleaned display label.

- [ ] **Step 5: Run the new report tests and verify GREEN**

Run:

```powershell
uv run pytest tests/test_dashboard_data.py::test_student_report_alphabetizes_cleaned_recent_course_labels tests/test_dashboard_data.py::test_student_report_keeps_low_and_high_grades_ordered_by_score tests/test_dashboard_data.py::test_student_report_matches_changes_with_raw_course_names -q
```

Expected: all three tests PASS.

- [ ] **Step 6: Run the complete Python test suite**

Run:

```powershell
uv run pytest -q
```

Expected: the full suite PASS with no failures or warnings introduced by this change.

- [ ] **Step 7: Check formatting and the final change scope**

Run:

```powershell
git diff --check
```

Expected: exit code 0 with no output.

Run:

```text
gitnexus_detect_changes({repo: "PlaywrightScraper", scope: "all"})
```

Expected: the dashboard display helper and `build_student_report` are the only production symbols changed; affected execution flows stay within dashboard student loading and rendering.

- [ ] **Step 8: Commit the integration**

```powershell
git add -- ui/dashboard_data.py tests/test_dashboard_data.py
git commit -m "feat: alphabetize franchise recent grades"
```
