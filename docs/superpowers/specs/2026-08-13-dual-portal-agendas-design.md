# Portal-Slot Student Agendas Design

## Goal

Collect a complete actionable agenda independently for Portal 1 and Portal 2, store the result as one slot-separated weekly snapshot, and present the two agendas side by side on the student report. The agenda number always follows the configured credential slot: Portal 1 supplies Agenda 1 and Portal 2 supplies Agenda 2, regardless of portal type.

For the authorized rollout case, the job is scoped through the existing franchise/student runner filters. The implementation must not hardcode a franchise, student ID, username transformation, or credential. Development and verification use fixtures and mocks only: no manual database access, no live writes, no saved browser state, and no persisted authentication tokens.

## Approved Product Behavior

- Agenda 1 uses Portal 1's detected portal name and credentials.
- Agenda 2 uses Portal 2's detected portal name and credentials.
- Each agenda-capable portal worker includes both missing assignments and upcoming/due assignments.
- A missing credential slot, unsupported portal, or portal without agenda parsing leaves that agenda's content blank; it is not a collection failure.
- Items stay separated by slot. An assignment visible through both configured slots remains visible in both cards.
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

### 1. One slot-separated JSON snapshot in the existing column — selected

Preserve the configured slot identity and store `agenda1` and `agenda2` beneath fixed top-level keys. Each slot records its detected portal key and canonical weeks. Reuse the existing result boundary and `weekly_agenda` column.

This provides one atomic current-state snapshot, preserves both slot identity and portal provenance, permits the same portal type in both slots, and requires no schema migration or additional database operation. Fixed slot keys also avoid relying on JSONB object ordering.

### 2. Merge and deduplicate both portals into one agenda

This would reduce repeated-looking assignments but discard provenance and could incorrectly merge similar work posted differently by the school systems. Leadership explicitly prefers both complete source views.

### 3. Add one column or table per portal

This makes portal separation explicit at the database level but adds migrations, write paths, and operational scope for data that already fits the existing JSONB boundary. It conflicts with the lightweight-mutation goal.

## Stored Snapshot Contract

`weekly_agenda` remains the only persisted agenda field. New two-slot snapshots use this shape:

```json
{
  "agenda1": {
    "portal": "canvas",
    "weeks": {
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
  },
  "agenda2": {
    "portal": "parentvue",
    "weeks": {
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
    }
  }
}
```

The contract has these rules:

- `agenda1` and `agenda2` are always present and map directly to Portal 1 and Portal 2.
- `portal` is the lowercase registry key detected from that slot's URL. It is `null` when the slot is absent or cannot be safely identified.
- `weeks` is always an object. It remains empty when no agenda-capable worker is available or a successful worker returns no dated assignments.
- Week keys are ISO dates for Monday, calculated from each assignment's due date.
- Course names remain portal-supplied display labels; they are not merged across portals.
- `missing` and `due` are always arrays when a class bucket is present.
- `title` and `dueDate` are required strings. `dueTime` is a normalized local display time or `null`.
- Assignments without a usable due date are omitted because they cannot be placed into a canonical week.
- Empty slot collections retain the detected portal key with an empty `weeks` object. A completed collection producing no assignments is valid data, not a failure.
- The snapshot contains no credential, session identifier, access token, portal URL, student name, or student ID.

JSONB replaces the entire snapshot, so resolved missing assignments disappear on the next successful run. Historical accumulation and assignment-level database mutations are out of scope.

## Credential and Portal Resolution

The runner examines each configured credential slot independently, determines its portal from its URL, and retains its original slot number. It never reorders slots by portal type.

Each engine receives only the username/password associated with its own URL slot. The Canvas username is passed exactly as stored; the implementation must not append a domain or synthesize another login identifier.

Portal engines expose an explicit agenda-capability signal. A configured slot receives a worker only when its resolved engine supports the complete agenda contract. Canvas and Google Classroom retain their existing agenda capability, and ParentVUE gains it in this feature. Other portal types are labeled from their registry key but intentionally produce an empty `weeks` object until an agenda parser is added. A future portal becomes collectable by implementing the same contract; no storage or UI redesign is required.

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

### Existing Google Classroom collector

Google Classroom remains agenda-capable. Its existing Assigned and Missing tab parsing is normalized into the same complete week/class/status contract, so placing Google Classroom in either configured slot does not regress to a blank card.

### Duplicate handling

There is no cross-slot deduplication, even when both slots resolve to the same portal type.

Within one portal response, exact repeated records are collapsed using a remote assignment identifier when available. If no stable identifier exists, the fallback identity is normalized course, title, due date, and due time. If the same within-portal assignment is classified as both missing and due, missing wins so one source card does not repeat the row.

## Atomic Data Flow

For each student:

1. Resolve Portal 1 and Portal 2 independently while retaining their slot numbers.
2. Create workers only for slots whose resolved engines advertise complete agenda support.
3. Run available workers using separate pages in the job's ephemeral browser context.
4. Normalize successful results into the portal/week/class contract.
5. Build one object containing `agenda1` and `agenda2`; unsupported, unconfigured, and successfully empty slots receive empty `weeks` objects.
6. Post one `agenda_success` outcome through the existing grade database client.
7. Let the existing grade database boundary replace `weekly_agenda` in one update.

The success boundary is all-or-nothing for workers that actually start. If any agenda-capable worker encounters a login, request, parser, or normalization failure, the runner posts a controlled failure outcome and does not post a partial agenda. The existing stored snapshot therefore remains unchanged. A slot without a worker is an intentional blank, not a failure. Successful empty workers and a run with no capable workers still post the complete two-slot bundle.

Lease expiration, heartbeat failure, and database-client failure keep their existing job-level cancellation behavior.

## Errors, Logging, and Secret Handling

Failures expose a controlled slot/portal collection code or the existing job-level boundary codes. Logs may contain the agenda slot, portal key, safe failure code, exception type, and aggregate counts.

Logs and result payloads must not contain:

- usernames or passwords;
- cookies, bearer tokens, CSRF values, or session identifiers;
- raw URLs containing session/query material;
- student names;
- assignment titles or course names;
- raw HTML or JSON response bodies.

Browser contexts and pages close in `finally` paths. The workflow does not call Playwright storage-state export and does not write authentication artifacts to disk.

## Dashboard Projection

The Flask dashboard boundary validates the nested object and projects it into a presentation-oriented structure rather than asking React to parse raw storage JSON. Each slot projection contains its safe portal label, ordered weeks, classes, status buckets, assignment title, and due display values.

Presentation ordering is deterministic:

- `agenda1` is always rendered as Agenda 1 and uses Portal 1's safe registry display name.
- `agenda2` is always rendered as Agenda 2 and uses Portal 2's safe registry display name.
- Within a week, class names sort case-insensitively.
- Within a class, missing rows appear before due rows.
- Rows within a status sort by due date/time and then title.
- Week headings prioritize the current week, then recent past weeks newest-first, then future weeks soonest-first. This keeps current work and recent missing items ahead of distant dates.

The existing legacy agenda projection remains available for old single-portal snapshots. Students with the new slot-separated shape receive the two-card layout. This prevents the feature from making pre-existing legacy agenda data unreadable while records transition through future jobs.

All display strings retain the dashboard's current defensive length limits. Malformed portal, week, class, bucket, or row values are skipped rather than rendered.

## Student Page Layout

The Report tab becomes two stacked responsive grids.

### Grade row

- Current Grades is on the left and Grade History is on the right at desktop width.
- Both cards use one shared bounded height and align at the top and bottom.
- Grade History's list is the internal scroll region and uses the existing `tc-scrollbar` treatment.
- Current grade rows, synced badge, update timestamp, grade-history week cards, typography, and colors reuse current assets.

### Agenda row

- Agenda 1 / Portal 1 is on the left and Agenda 2 / Portal 2 is on the right. Each heading uses the detected portal display name, such as `Agenda 1 · Canvas` or `Agenda 2 · ParentVUE`.
- Both cards use one shared bounded height even when their item counts differ.
- Each card header and Missing/Upcoming legend stays visible.
- The week/class content is the only internal vertical scroll region and uses the existing scrollbar asset.
- Each week displays collapsible class rows. The class header shows the assignment count.
- Expanded classes show missing and due assignments together. Missing uses a red `M`; upcoming uses a neutral `DUE` marker plus its date/time.
- A slot without an agenda worker, an unconfigured slot, or a successfully empty result leaves the card's scrollable content area blank. The card heading still identifies its agenda number and detected portal when available; the blank is not presented as a scrape error.
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

- Credential slots resolve by URL without being reordered, including two slots with the same portal type.
- Portal display names stay attached to their original agenda numbers.
- Every run posts exactly one `agenda_success` bundle when all started workers succeed.
- One successful and one failed worker posts no partial success and preserves the prior snapshot through the failure path.
- Unsupported, unconfigured, workerless, successfully empty, and both-empty slot combinations are accepted and retain both slot objects.
- Existing Google Classroom agenda parsing is dispatched from either slot.
- Lease and database-client failures retain existing cancellation behavior.
- Captured logs and posted outcomes contain no supplied secrets, session material, names, or assignment content.

### Dashboard tests

- New storage JSON projects into two ordered slot agenda cards with dynamic portal names.
- Malformed nested data is safely skipped.
- Legacy single-portal agenda data still projects through the legacy path.
- Current Grades and Grade History receive the equal-height structure and Grade History's internal scrollbar.
- Both agenda cards receive equal-height structure, independent scroll regions, slot-correct portal labels, blank unsupported states, collapsible classes, assignment counts, and accessible missing/due text.
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
- No cross-slot assignment merging, even when both slots identify the same portal type.
- No historical agenda ledger beyond the existing current JSONB snapshot.
- No live production scrape during implementation or automated verification.
- No new agenda parser beyond ParentVUE; Canvas and Google Classroom are normalized from their existing collectors, and other portal slots remain blank until their own parsers exist.
- No redesign of the Heatmap tab or franchise page.
