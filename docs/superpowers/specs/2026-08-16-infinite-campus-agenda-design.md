# Infinite Campus Sequential Agenda Design

## Goal

Add Infinite Campus as an agenda-capable portal using the existing canonical
Monday-week, class, and status structure. Collection must be complete for the
active Infinite Campus student being processed, sequential within one browser
page, bounded by the existing agenda result contract, and atomic at the
student level.

## Confirmed Live Structure

The authorized active-student login succeeds in one submission and exposes a
`main-workspace` iframe. The portal menu contains a dedicated Assignments view
with built-in Missing and Current Term toggles.

The observed Current Term view contains 91 logical assignments. Infinite
Campus renders two responsive representations per assignment, but only
`.selcat-assignment-row` contains the canonical title, course, and score data.
The separate large-screen row must not be collected as a second assignment.

The Missing filter exposes five logical assignments. The unfiltered current
term contains 86 numeric scores and 13 scores below 80; the five Missing
assignments overlap those low scores. Assignment list links share a generic
route and therefore are not stable source identifiers.

Opening an assignment detail exposes explicit Start Date and End Date values.
The list does not expose a reliable per-assignment due date. Calendar weekly
mode groups assignments by date, but it is not a reliable complete source for
the full current term. The collector therefore uses sequential assignment
details rather than Calendar inference or an undocumented network API.

No live assignment names, course names, dates, scores, URLs, credentials, or
HTML are persisted by this design or emitted in logs.

## Collection Flow

1. Require the authenticated Infinite Campus student view and its
   `main-workspace` iframe.
2. Open Assignments and enable Current Term.
3. Enable Missing and capture normalized title-plus-course keys for the
   canonical scored rows.
4. Disable Missing and capture the complete ordered current-term row list.
5. For every captured assignment, reacquire its canonical row by ordinal and
   verify that normalized title and course still match the captured values.
6. Open the assignment detail sequentially in the same iframe.
7. Parse explicit Start Date and End Date values, then return with the Back
   control.
8. Wait for Assignments and Current Term readiness before reacquiring the next
   row.
9. Classify the complete validated set and return canonical `AgendaRecord`
   values to the shared normalizer.

No assignment details are opened concurrently. No additional page, context,
worker, or direct database access is introduced.

## Canonical Identity and Responsive Deduplication

Only `.selcat-assignment-row` participates in collection. The duplicate
responsive representation is ignored.

Because live assignment links do not expose a stable per-assignment route,
the collector does not invent a `sourceId`. The shared agenda normalizer uses
its existing normalized course, title, and End Date fallback identity. During
navigation, the collector uses normalized title plus course as its temporary
row key and validates the original ordinal after every return.

Duplicate title-plus-course keys in the same current-term list are ambiguous
before End Date is available and fail the student atomically rather than
causing the wrong detail row to be opened.

## Status and Filtering Rules

Classification precedence is:

1. `missing`
2. `low_score`
3. `due`

Rules:

- A row present under the portal's explicit Missing filter becomes
  `missing`, regardless of score.
- A non-Missing row with a valid numeric percentage below 80 becomes
  `low_score`.
- When no percentage is present, a valid earned-points/possible-points ratio
  below 80 may classify the row as `low_score`; a zero denominator is not a
  numeric score.
- Scores exactly 80 or above are excluded.
- Excused, exempt, pass/fail, not-graded, and ungraded score states are never
  labeled Low.
- An unscored assignment with an End Date on or after the current local date
  becomes `due`.
- Historical blank or nonnumeric assignments are excluded.
- Numeric scores and points are classification inputs only and are never
  stored in agenda output or displayed by the dashboard.

The End Date is the due-date source. Start Date is parsed only as structural
validation and is never substituted for a missing End Date.

## Date and Ambiguity Rules

Infinite Campus detail dates use a local month/day/year and 12-hour time
format. They are parsed as local portal dates without UTC conversion. The
canonical `dueDate` comes from End Date and `dueTime` is normalized to
24-hour `HH:MM`.

An explicit blank End Date on an otherwise neutral assignment is valid and
causes the assignment to be excluded. A Missing or Low assignment without a
parseable End Date cannot be placed into the weekly contract and fails the
student atomically. A nonblank malformed Start Date or End Date also fails the
student.

## Atomicity and Error Handling

Infinite Campus follows the existing per-student agenda boundary:

- Any unexpected list shape, duplicate temporary row key, row reorder,
  missing iframe, failed detail navigation, missing Back control, or malformed
  required detail aborts the Infinite Campus slot.
- A failed capable slot rejects the student's complete two-slot agenda bundle,
  preserving the previously stored snapshot.
- Other students in the franchise run continue.
- Login and collection errors remain sanitized; logs contain only controlled
  event names, portal/slot context, counts, phases, and exception categories.

## Code Boundaries

- Add `scraper/portals/infinite_campus_agenda.py` for pure parsing,
  classification, and sequential assignment traversal.
- Modify `scraper/portals/infinite_campus.py` only to declare
  `agenda_capable = True` and delegate `get_agenda()` after its existing login
  and student selection.
- Reuse `AgendaRecord`, `normalize_agenda`, result bounding, agenda runner
  atomicity, and the existing dashboard. No frontend, Rust schema, Neon
  schema, or CRM query changes are required.

## Test Strategy

Synthetic tests must cover:

- the canonical scored row selector and responsive duplicate exclusion;
- Current Term and Missing filter sequencing;
- strict sequential detail navigation and row reacquisition;
- Missing precedence over Low and Due;
- below, exactly, and above 80 percent;
- earned/possible point ratios and zero denominators;
- excused, exempt, pass/fail, ungraded, and blank scores;
- unscored future work as Due and historical blank work exclusion;
- explicit neutral blank End Date exclusion;
- required missing/low End Date failure and malformed date failure;
- duplicate title-plus-course ambiguity;
- row reorder/count changes and failed Back navigation;
- canonical Monday-week/class/status normalization and result bounding;
- `InfiniteCampus.agenda_capable` plus delegated `get_agenda()` behavior;
- controlled errors and absence of academic content in diagnostics.

Final live validation uses the authorized active student, performs one bounded
login, traverses sequentially, and reports only aggregate assignment, status,
week, and class-group counts.

## Out of Scope

- Infinite Campus Calendar scraping.
- Undocumented Infinite Campus APIs.
- Parallel assignment-detail navigation.
- Inferring due dates from Start Date, list order, or text outside explicit
  detail fields.
- Persisting scores or adding new dashboard labels.
- Changing credentials, CRM data, Neon data, or runner concurrency.
