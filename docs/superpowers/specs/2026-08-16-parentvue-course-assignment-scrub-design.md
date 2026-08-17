# ParentVUE Current-Period Assignment Scrub Design

**Date:** 2026-08-16

## Goal

Extend ParentVUE agenda collection beyond the overview so it sequentially inspects every visible course in the current grading period and returns upcoming, explicitly missing, and low-scoring assignments without inferring missing status.

## Live Evidence

The authenticated franchise 57 ParentVUE Gradebook exposes two overview structures that the current parser does not recognize:

- Upcoming Assignments uses visible `tr.gb-upcoming-assignment` rows with `data-guid` identifiers.
- Recent History is a `.gb-student-assignments-grid` immediately following the `Recent History` heading and contains unclassed table rows.

Jordan's live overview contained three upcoming rows and 28 recent-history rows. Recent History does not reliably expose whether work is completed, missing, or low-scoring, so it must not be used to infer assignment status. Current-period course detail pages are the authoritative source for missing and low-score classification.

## Selected Approach

Use a sequential UI scrub through each visible current-period course. This is preferred over undocumented ParentVUE endpoints because it follows the authenticated interface and provides explicit assignment status and score evidence. It is preferred over Recent History inference because the overview cannot distinguish completed, missing, ungraded, and low-scoring work.

Collection is atomic. If any course cannot be recognized and fully parsed, the ParentVUE agenda slot fails instead of returning a partial snapshot.

## Collection Flow

1. Log in and open the current ParentVUE Gradebook.
2. Wait for the overview shell and recognize either live upcoming rows, current-period course rows, or an explicit empty state.
3. Parse visible Upcoming Assignments rows as `due`.
4. Enumerate visible courses in the current grading period. Hidden courses and old-term content are excluded.
5. Open each course sequentially.
6. Require a recognizable assignment table or an explicit course-level empty state.
7. Classify every assignment in the course:
   - Explicitly marked missing becomes `missing`.
   - Otherwise, a numeric percentage below 80 becomes `low_score`.
   - If no percentage is present but earned and possible points are numeric and the denominator is positive, calculate the percentage; values below 80 become `low_score`.
   - Exactly 80 or higher is excluded.
   - Ungraded, excused, pass/fail, blank, or otherwise nonnumeric scores are excluded unless the assignment is explicitly missing.
8. Return to the Gradebook between courses without resubmitting credentials.
9. Deduplicate records with precedence `missing` over `low_score` over `due`.
10. Return the normalized result only after every course succeeds.

## Agenda Contract

Add `low_score` as a third `AgendaStatus`. Each normalized course/week bucket contains:

```text
missing: list[StoredAgendaItem]
low_score: list[StoredAgendaItem]
due: list[StoredAgendaItem]
```

Low-score assignments retain the existing stored item shape: title, due date, and optional due time. The numeric score is used only for classification and is not stored or exposed.

Stable source identifiers come from ParentVUE GUIDs or stable assignment-link identifiers when available. Duplicate representations of the same assignment use the status precedence above.

Existing stored agenda payloads that omit `low_score` remain readable. The dashboard projection treats an absent `low_score` bucket as an empty list.

## Frontend

The dashboard projects and sorts `missing`, `low_score`, and `due` assignments together within the existing week/course layout.

- Marker text: `LOW`
- Accessible label: `Low-scoring assignment`
- Styling: a dedicated `tc-agenda-marker--low_score` treatment distinct from Missing and Due

The numeric score is not displayed.

## Failure and Safety Boundaries

- Course traversal is sequential; it does not increase agenda worker concurrency.
- No whole-login retry or credential resubmission is introduced.
- A course must expose either a recognized assignment structure or an explicit empty marker.
- Ambiguous pages, malformed rows, navigation failures, and partially parsed courses fail the entire ParentVUE slot.
- Diagnostics remain allowlisted and must not include assignment content, credentials, URLs, HTML, or exception messages.
- SMSS access remains limited to the existing Rust-boundary reads. No new database writes are required.

## Verification

Development follows test-driven development with failing regressions captured before production edits. Coverage includes:

- Live `gb-upcoming-assignment` overview markup.
- Sequential traversal across multiple current-period courses.
- Explicit Missing classification.
- Numeric percentages below 80.
- Calculated point ratios such as `7 / 10`.
- Exactly 80 and higher exclusions.
- Ungraded, excused, and nonnumeric exclusions.
- Missing precedence over Low and Due.
- Low precedence over Due.
- Explicit-empty course handling.
- One-course failure preventing partial output.
- Legacy agenda payloads without `low_score`.
- Dashboard projection, `LOW` marker, styling, and accessible label.

After focused and full automated verification, validate the parser against Jordan's preserved authenticated ParentVUE session without another login submission.
