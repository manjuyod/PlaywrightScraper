# Dual-Portal Student Agendas Design

## Goal

Collect a complete actionable agenda from both ParentVUE and Canvas for a student whose two configured portal slots contain those services, store the result as one portal-separated weekly snapshot, and present the two agendas side by side on the student report.

For the authorized rollout case, the job is scoped through the existing franchise/student runner filters. The implementation must not hardcode a franchise, student ID, username transformation, or credential. Development and verification use fixtures and mocks only: no manual database access, no live writes, no saved browser state, and no persisted authentication tokens.

## Approved Product Behavior

- Agenda 1 represents ParentVUE.
- Agenda 2 represents Canvas.
- Each portal agenda includes both missing assignments and upcoming/due assignments.
- Items stay separated by portal. An assignment visible in both portals remains visible in both cards.
- Within each portal, assignments are organized by canonical ISO week start, then class.
- Class sections are collapsible and contain assignment rows.
- Missing rows display a compact red `M`; upcoming/due rows use a neutral due treatment.
- The two agenda cards share one bounded height and independently scroll inside their content regions.
- The portal/card heading remains visible while the week and class content scrolls.
- Current Grades and Grade History form an equal-height top row. Grade History scrolls internally rather than growing the page.
- The layout becomes one column on narrower screens while retaining the bounded, internally scrolling cards.

## Current Behavior

The agenda runner selects one credential slot and one portal, accepts either `upcoming` or `missing`, and posts one non-empty `weekly_agenda` object. Canvas currently scrapes dashboard List View and ignores the requested target. Google Classroom switches between its Assigned and Missing tabs. ParentVUE has no agenda collector.

The result boundary already replaces `students_grades_20262027.weekly_agenda` atomically with one JSONB value. The dashboard reads that value and flattens the legacy `due date -> assignment tuples` shape into one generic Agenda card.

The student page currently places Current Grades and Agenda in the left column and lets Grade History occupy the entire right column. Long grade history therefore determines the page height.

## Considered Approaches

### 1. One portal-separated JSON snapshot in the existing column — selected

Collect both portals before posting one result and store each portal beneath its own top-level key. Reuse the existing result boundary and `weekly_agenda` column.

This provides one atomic current-state snapshot, preserves portal provenance, and requires no schema migration or additional database operation.

### 2. Merge and deduplicate both portals into one agenda

This would reduce repeated-looking assignments but discard provenance and could incorrectly merge similar work posted differently by the school systems. Leadership explicitly prefers both complete source views.

### 3. Add one column or table per portal

This makes portal separation explicit at the database level but adds migrations, write paths, and operational scope for data that already fits the existing JSONB boundary. It conflicts with the lightweight-mutation goal.

## Stored Snapshot Contract

`weekly_agenda` remains the only persisted agenda field. New dual-portal snapshots use this shape:

```json
{
  "parentvue": {
    "2026-08-10": {
      "Algebra II": {
        "missing": [
          {
            "title": "Example missing assignment",
            "dueDate": "2026-08-11",
            "dueTime": null
          }
        ],
        "due": [
          {
            "title": "Example upcoming assignment",
            "dueDate": "2026-08-14",
            "dueTime": "23:59"
          }
        ]
      }
    }
  },
  "canvas": {
    "2026-08-10": {
      "English 11": {
        "missing": [],
        "due": [
          {
            "title": "Example reading response",
            "dueDate": "2026-08-16",
            "dueTime": null
          }
        ]
      }
    }
  }
}
```

The contract has these rules:

- Portal keys are lowercase registry keys.
- Week keys are ISO dates for Monday, calculated from each assignment's due date.
- Course names remain portal-supplied display labels; they are not merged across portals.
- `missing` and `due` are always arrays when a class bucket is present.
- `title` and `dueDate` are required strings. `dueTime` is a normalized local display time or `null`.
- Assignments without a usable due date are omitted because they cannot be placed into a canonical week.
- Empty portal collections are stored as empty objects. A completed collection producing no assignments is valid data, not a failure.
- The snapshot contains no credential, session identifier, access token, portal URL, student name, or student ID.

JSONB replaces the entire snapshot, so resolved missing assignments disappear on the next successful run. Historical accumulation and assignment-level database mutations are out of scope.

## Credential and Portal Resolution

The runner examines both configured credential slots and determines each portal from its URL rather than assuming that Canvas is always primary or ParentVUE is always secondary.

When both Canvas and ParentVUE are present, the student uses dual-portal collection. Each engine receives only the username/password associated with its own URL slot. The Canvas username is passed exactly as stored; the implementation must not append a domain or synthesize another login identifier.

Existing single-portal behavior outside this combination remains supported. Google Classroom is not migrated to the new complete dual-source collector as part of this feature.

## Portal Collection

### Canvas

Canvas uses the authenticated browser session and same-origin JSON endpoints, not a separately stored API token.

- Read missing work from `/api/v1/users/self/missing_submissions`, following pagination.
- Read upcoming/due work from `/api/v1/planner/items` for the actionable forward window, following pagination.
- Read active courses from `/api/v1/courses` to resolve course identifiers to display names.
- Normalize the remote due timestamp into `dueDate` and optional `dueTime` before calculating the Monday week key.
- Interpret timestamps in the authenticated Canvas/account timezone; do not derive the displayed date or week by truncating a UTC timestamp.
- Ignore planner objects that are not assignment work or lack a usable due date.

The actionable forward window starts on the current local date and extends one year. This avoids the current seven-day List View truncation while keeping the request bounded. Pagination ensures the configured window is not silently cut off by `per_page` limits.

### ParentVUE

ParentVUE uses its existing authenticated Grade Book session.

- Read missing work from the Grade Book's class assignment data and missing-assignment structures.
- Read due work from the full Upcoming Assignments section.
- Use unambiguous selectors or an authenticated same-origin Grade Book request so duplicate hidden navigation labels cannot select the wrong element.
- Normalize class, title, due date, and optional due time into the common portal snapshot shape.

The collector processes all dated actionable assignments exposed in those Grade Book sections. It does not infer assignments from grade percentages or missing counts when no assignment row is available.

### Duplicate handling

There is no cross-portal deduplication.

Within one portal response, exact repeated records are collapsed using a remote assignment identifier when available. If no stable identifier exists, the fallback identity is normalized course, title, due date, and due time. If the same within-portal assignment is classified as both missing and due, missing wins so one source card does not repeat the row.

## Atomic Data Flow

For each dual-portal student:

1. Resolve Canvas and ParentVUE credential slots by URL.
2. Log into and collect both portals using separate pages in the job's ephemeral browser context.
3. Normalize both successful results into the portal/week/class contract.
4. Build one object containing both top-level portal keys, including empty portal objects when appropriate.
5. Post one `agenda_success` outcome through the existing grade database client.
6. Let the existing grade database boundary replace `weekly_agenda` in one update.

The success boundary is all-or-nothing. If either login, request, parser, or normalization path fails, the runner posts a controlled failure outcome and does not post a partial agenda. The existing stored snapshot therefore remains unchanged. A successful empty collection from either or both portals still posts the complete two-key bundle.

Lease expiration, heartbeat failure, and database-client failure keep their existing job-level cancellation behavior.

## Errors, Logging, and Secret Handling

Failures expose controlled codes such as `agenda_canvas_failed`, `agenda_parentvue_failed`, or the existing job-level boundary codes. Logs may contain the portal key, safe failure code, exception type, and aggregate counts.

Logs and result payloads must not contain:

- usernames or passwords;
- cookies, bearer tokens, CSRF values, or session identifiers;
- raw URLs containing session/query material;
- student names;
- assignment titles or course names;
- raw HTML or JSON response bodies.

Browser contexts and pages close in `finally` paths. The workflow does not call Playwright storage-state export and does not write authentication artifacts to disk.

## Dashboard Projection

The Flask dashboard boundary validates the nested object and projects it into a presentation-oriented structure rather than asking React to parse raw storage JSON. Each portal projection contains ordered weeks, classes, status buckets, assignment title, and due display values.

Presentation ordering is deterministic:

- ParentVUE is Agenda 1 and Canvas is Agenda 2.
- Within a week, class names sort case-insensitively.
- Within a class, missing rows appear before due rows.
- Rows within a status sort by due date/time and then title.
- Week headings prioritize the current week, then recent past weeks newest-first, then future weeks soonest-first. This keeps current work and recent missing items ahead of distant dates.

The existing legacy agenda projection remains available for old single-portal snapshots. Students with the new portal-separated shape receive the two-card layout. This prevents the feature from making pre-existing legacy agenda data unreadable while records transition through future jobs.

All display strings retain the dashboard's current defensive length limits. Malformed portal, week, class, bucket, or row values are skipped rather than rendered.

## Student Page Layout

The Report tab becomes two stacked responsive grids.

### Grade row

- Current Grades is on the left and Grade History is on the right at desktop width.
- Both cards use one shared bounded height and align at the top and bottom.
- Grade History's list is the internal scroll region and uses the existing `tc-scrollbar` treatment.
- Current grade rows, synced badge, update timestamp, grade-history week cards, typography, and colors reuse current assets.

### Agenda row

- Agenda 1 / ParentVUE is on the left and Agenda 2 / Canvas is on the right.
- Both cards use one shared bounded height even when their item counts differ.
- Each card header and Missing/Upcoming legend stays visible.
- The week/class content is the only internal vertical scroll region and uses the existing scrollbar asset.
- Each week displays collapsible class rows. The class header shows the assignment count.
- Expanded classes show missing and due assignments together. Missing uses a red `M`; upcoming uses a neutral `DUE` marker plus its date/time.
- Empty portals show a calm portal-specific empty state inside the same-height card.
- At narrower widths, the two grade cards and two agenda cards stack to one column without losing their internal scrolling behavior.

The Heatmap tab is unchanged.

## Accessibility

- Class controls use native `button`/disclosure semantics or native `details`/`summary`, with programmatic expanded state.
- Status is conveyed by visible text (`M`, `DUE`, and accessible labels), not color alone.
- Scroll regions are keyboard reachable when necessary and have descriptive accessible labels.
- Focus uses the existing dashboard focus-ring treatment.
- Long course and assignment text truncates visually only when needed; full text remains available through accessible text/title behavior.

## Testing

All tests are fixture-driven and must not connect to a live portal or database.

### Portal unit tests

- ParentVUE parses sanitized Grade Book HTML containing missing and upcoming sections, duplicate navigation labels, empty sections, malformed rows, and undated rows.
- Canvas parses mocked missing, planner, and course JSON, follows pagination, filters non-assignment items, normalizes timestamps, and preserves the stored username unchanged through login construction.
- Both collectors produce canonical Monday week keys and stable class/status buckets.
- Within-portal duplicates collapse and missing wins; identical-looking cross-portal assignments remain in both bundles.

### Runner and boundary tests

- Credential slots resolve by URL in either order.
- Both successful portals post exactly one `agenda_success` bundle.
- One successful and one failed portal posts no partial success and preserves the prior snapshot through the failure path.
- Empty ParentVUE, empty Canvas, and both-empty successful collections are accepted.
- Lease and database-client failures retain existing cancellation behavior.
- Captured logs and posted outcomes contain no supplied secrets, session material, names, or assignment content.

### Dashboard tests

- New storage JSON projects into two ordered portal agenda cards.
- Malformed nested data is safely skipped.
- Legacy single-portal agenda data still projects through the legacy path.
- Current Grades and Grade History receive the equal-height structure and Grade History's internal scrollbar.
- Both agenda cards receive equal-height structure, independent scroll regions, visible portal labels, collapsible classes, assignment counts, and accessible missing/due text.
- Responsive structure stacks the cards without removing bounded scrolling.

### Verification

- Run focused agenda portal, runner-boundary, dashboard-data, and read-only route tests.
- Run JavaScript syntax checks and frontend contract tests.
- Run the complete Python suite and the Rust grade-database tests without configuring live database credentials.
- Run GitNexus change detection and review every affected execution flow.
- Use a local fixture-backed student payload for Playwright at desktop and mobile widths. Verify equal card heights, independent scroll behavior, fixed card headings, accordion interaction, status presentation, keyboard operation, and the unchanged Heatmap tab.

## Non-Goals

- No database migration, new column, new table, or manual data edit.
- No portal API token storage.
- No cross-portal assignment merging.
- No historical agenda ledger beyond the existing current JSONB snapshot.
- No live production scrape during implementation or automated verification.
- No rollout of the complete portal bundle collector to portals other than Canvas and ParentVUE in this change.
- No redesign of the Heatmap tab or franchise page.
