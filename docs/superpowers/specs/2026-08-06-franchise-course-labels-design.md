# Franchise Course Label Cleanup Design

## Goal

Make course lists on the Franchise Page easier to scan by removing leading numeric period prefixes from displayed course names and alphabetizing the Recent Grades list.

## Scope

The change applies to the course labels shown in Recent Grades, Low Grades, and High Grades. Only Recent Grades changes its ordering. Low Grades and High Grades retain their existing score-based selection and ordering.

The scraper output and the historical `weeklydata` JSON stored in Neon remain unchanged.

## Server-side behavior

Add a small dashboard display helper that removes a leading integer followed by a colon, allowing surrounding whitespace. Examples:

- `1: ENGLISH 8` becomes `ENGLISH 8`.
- `3 : SPANISH IB` becomes `SPANISH IB`.
- `12:  ADVANCED CHOIR` becomes `ADVANCED CHOIR`.
- `HISTORY 8-A` remains unchanged.
- `101 ALGEBRA` remains unchanged because it has no colon delimiter.

If removing the prefix would produce an empty label, retain the stripped original label so the page never receives a blank course name.

## Data flow and ordering

`build_student_report` continues to use each raw stored course key when comparing the latest snapshot with the preceding snapshot. This preserves the existing exact-name semantics for the `+` and `-` change indicators.

After calculating a course's change indicator, the report uses the cleaned display label in its `CourseGrade` value.

- `grades_snapshot` is sorted case-insensitively by cleaned course label for the Recent Grades column.
- `low_grades` is still selected as the three lowest numeric grades and remains lowest-first.
- `high_grades` is still selected as the two highest numeric grades and retains its current ascending order within that selected pair.
- Equal numeric grades inherit the alphabetical snapshot order, making ties deterministic.

The route payload and React rendering contract remain unchanged: each grade item still contains `course`, `grade`, and `change`.

## Edge cases

- Only a numeric prefix at the beginning of the label is removed.
- Course names containing numbers elsewhere are not changed.
- Raw names remain the identity used for cross-week comparisons, so display cleanup cannot merge historical records.
- Two different raw course names may produce the same display label; both grade entries remain present.

## Testing

Add focused dashboard-data tests that verify:

1. Numeric colon prefixes are removed while ordinary numeric course names are preserved.
2. Recent Grades is alphabetized by the cleaned label.
3. Low and High Grades retain score-based ordering.
4. Change indicators still compare exact raw course names across weeks.
5. A prefix-only label does not become blank.

Run the focused dashboard-data tests, then the complete Python test suite. Before completion, run GitNexus change detection and confirm only the expected dashboard report flow is affected.
