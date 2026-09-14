# Assignment scores and formative/summative categories

Status: approved and implemented locally on 2026-09-13. This document specifies features 3 and 4. Synthetic, browser and portal-only live verification passed with application database access blocked. Deployment remains a separate step.

## Outcome and scope

Collect each included Infinite Campus assignment's score and Formative/Summative category, preserve them through normalization and JSON storage, and show them in the same assignment row as its title and due date.

The shared assignment row appears both under Current grades and in standalone Agenda cards. Both presentations receive the same optional metadata support. This increment adds extraction for Infinite Campus; it does not add category inference or score extraction to other portal engines. Old data and other portals continue to render.

Keep the existing inclusion rules: explicit missing or zero-scored work, scores below 80%, and eligible unscored due work. Do not turn this feature into a complete assignment-history view. Preserve current date filtering, status precedence, course matching, ordering, scrape states, slot separation, and scheduling.

## Evidence

A read-only authenticated check of the supplied CCSD account reached Chemistry H on 2026-09-13. The live course showed:

| Container label | Observed rows | Examples |
| --- | --- | --- |
| Formative | 9 | Solutions, Radiation, & Albedo has no score; CO2 & Climate has `Score 10/10 (100%)` |
| Summative | 1 | Melting Ice & Sea Level has no score |

Observed DOM relationships:

- Category control: `button.divider__header[aria-controls]`.
- Category name: the control's `h5` text, exactly `Formative` or `Summative` in this sample.
- Assignments: descendants of the element whose `id` equals that control's `aria-controls`.
- Assignment row: `.selcat-assignment-row`.
- Score: that row's `.assignment-score__scores--largeScreen` text, sometimes including the accessibility prefix `Score`.
- The full category-button text also contains weight and category totals, such as `78/80 (97.5%)`. Those are not assignment scores and must never be copied to assignment rows.

Sanitized local evidence is in ignored `output/2026-09-13-assignment-metadata-probe.json`; it contains selected academic rows/markup only, no login credentials or browser session state. Committed fixtures must be synthetic. The probe required direct authenticated navigation to the known Grades overview after the existing menu-based helper timed out at the desktop viewport; this was a diagnostic workaround, not a production navigation change. Navigation verification must use a portal-only harness with human-provided inputs; any check requiring a database-backed scheduled job is human-executed.

The screenshot's two unscored Chemistry assignments should therefore show a score placeholder. The Summative assignment was due September 10; a fresh scrape after that date can omit it under the existing past-unscored rule even though the older September 10 snapshot still contains it. Tests must use a fixed reference date when checking both sample shapes together.

Code inspection found three places that currently lose or omit metadata:

1. `scraper/portals/infinite_campus_agenda.py:parse_infinite_campus_course_grades` reads scores to classify rows but emits no score/category.
2. `scraper/agenda_contract.py:normalize_agenda` keeps only title/date/time in stored items.
3. `ui/routes.py:_agenda_slots` shapes only title/status/due fields, and `ui/static/react-dashboard.js:AgendaAssignment` renders only marker/title/date.

Existing `grade_db` Rust persistence accepts bounded JSON and writes primary/secondary agenda snapshots. There is no per-assignment relational schema to migrate.

## Approach and alternatives

**Add two optional scalar fields to the existing agenda item.** A canonical display score string preserves fractions without adding a nested numeric schema. A closed category enum avoids guessing arbitrary teacher labels. Use the bulk category containers already expanded by the collector.

Opening every assignment would add navigation, delay and failure points without being necessary for the observed data. A new assignment table with earned/possible/percentage/category-weight columns would support future analytics but exceeds the requested row display. Neither is required here.

## Shared data contract

Extend both `AgendaRecord` and `StoredAgendaItem` with:

```python
score: NotRequired[str]
category: NotRequired[Literal["formative", "summative"]]
```

Existing required fields remain unchanged. Unknown or invalid optional metadata is omitted, not used to reject an otherwise valid assignment. Example stored row:

```json
{
  "title": "Synthetic practice quiz",
  "dueDate": "2026-09-14",
  "dueTime": null,
  "score": "7.5/10",
  "category": "formative"
}
```

### Score rules

- Accept one nonnegative decimal fraction, one nonnegative percentage, or a fraction followed by a parenthesized percentage. Permit whitespace and one leading `Score` label with an optional colon.
- Prefer the fraction when both fraction and percentage are present. Examples: `Score 7 / 10 (70%)` becomes `7/10`; `79.50%` becomes `79.5%`; `0 / 10` remains `0/10`; `12/10 (120%)` becomes `12/10`.
- Strip insignificant decimal zeros, not meaningful precision. Use `Decimal` string formatting; do not introduce binary floating-point artifacts or locale-specific commas.
- A denominator must be greater than zero. Reject negative values, zero denominators, non-finite values, multiple scores, and malformed strings. Extra credit is allowed.
- Limit input to 128 characters and canonical stored output to 32 characters. Non-string values are invalid; in particular, do not stringify objects, booleans or raw HTML.
- Empty scores and excused/exempt/pass-fail/ungraded text yield no score. Never convert absence to zero or use course/category aggregate totals as assignment scores.
- Preserve `_score_percentage` and the existing status rules in this increment. Display metadata does not change classification.

### Category rules

Normalize whitespace and case, then recognize only `Formative` and `Summative`. Use the category control's `h5` label and its explicit container relationship. Never infer a category from assignment title, color, weight, row order or score.

For nested containers use the nearest controlled ancestor. Missing/ambiguous controls, missing labels, unknown category names, duplicate target IDs or conflicting category evidence yield no category for the affected row. Do not let the previous category bleed into the next section. Ordinary missing metadata does not fail the whole course scrape.

### Deduplication and bounds

Preserve the current identity rules (`sourceId` when provided; otherwise canonical course/title/date/time), status priority `missing > low_score > due`, core-row representative choice, and ordering.

Merge optional metadata deterministically within one identity group:

- Select the core representative and winning status using the existing rules.
- For score, consider only valid scores from observations with the winning status. Keep it if there is one distinct nonempty value; omit it on disagreement.
- For category, consider valid categories from all observations of that identity. Keep one distinct nonempty value; omit it on disagreement.
- A metadata-less duplicate must not erase an unambiguous value. Reversing input order must produce identical output.

Keep `MAX_AGENDA_WEEKS_NODES = 497`, Rust's 1,000-node payload limit, depth 8 and 4,096-byte string limit. Count optional fields before prefix truncation. More metadata can reduce the number of assignments fitting an exceptionally large agenda; this change does not increase payload limits or silently drop fields after counting. Both slots and maximum-size cases require regression coverage.

## Presentation

Use this column order in the shared row:

```text
[status]  Assignment title                   Score  Category     Due
[LOW]     Synthetic practice quiz            7/10   Formative    Sep 14
[DUE]     Synthetic project                  —      Summative   Sep 14
```

- Show the canonical fraction or percentage, with `Score: ...` accessible text and tabular numeric alignment.
- Show an em dash with accessible text `Score unavailable` if the score is absent. This avoids claiming an old snapshot is necessarily ungraded.
- Render recognized categories as readable `Formative` or `Summative` text. Omit the category badge when unknown; do not invent a category.
- Preserve existing missing/low/due markers, their meaning, and the due date.
- Keep score and category in the same row, not in a separate details view. Use a shrinking title column; preserve the full title through the existing title attribute and accessible text. Constrain exceptionally long metadata with full-value titles so it cannot force horizontal overflow.
- At 360, 768 and 1440 CSS pixels, there must be no horizontal page overflow; score, category and due date remain visible for normal values. Reduce gaps/padding at narrow widths. Keep full category words for ordinary rows.
- Use normal React text rendering. Do not insert portal strings with `innerHTML`.

The server must validate optional fields when shaping historical/stored rows too. Do not spread arbitrary stored JSON into page data. Keep the new metadata helper independent of Playwright, database access and Flask so collector, normalizer and UI can share validation safely.

## Global constraints

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

## Acceptance and rollout

Tests cover observed category containment and the `Score` prefix with synthetic markup, category totals distinct from assignment scores, no score, zero/decimal/fraction/percentage/extra-credit scores, unknown category, malformed metadata, conflicts and metadata-less duplicates, both slots, old JSON, near-limit payloads, and real rendered rows at three viewport widths.

Deploy the backward-compatible reader/renderer first, then deploy/enable the enriched collector. Existing records render without a migration; metadata appears on the next successful agenda refresh. Agent verification uses synthetic fixtures, mocked persistence and, when human-provided portal inputs are available, a portal-only read-only check with no database connection. Any database-backed smoke test, result-writing job or rescrape is human-executed, with sanitized results supplied to the agent. Do not request a franchise-wide rescrape merely to validate this feature. Reader or producer rollback is safe because the new fields are optional and old readers ignore them.

Implementation plan: [Assignment metadata plan](../plans/2026-09-13-assignment-score-category.md).
