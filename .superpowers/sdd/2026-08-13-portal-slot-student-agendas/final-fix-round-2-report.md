# Final branch-review fix report: round 2

Date: 2026-08-14

## Scope

This pass addresses both Important findings in `final-review-post-fix.md`.
Work remained offline: no live portal, database, credential, integration flag,
saved browser state, production Rust, or SQL was used or changed.

Changed implementation and regression files:

- `scraper/agenda_contract.py`
- `scraper/portals/parentvue_agenda.py`
- `tests/test_agenda_contract.py`
- `tests/test_parentvue_agenda.py`
- `.superpowers/sdd/2026-08-13-portal-slot-student-agendas/final-fix-report.md`
- `.superpowers/sdd/2026-08-13-portal-slot-student-agendas/final-fix-round-2-report.md`

## Finding 1: deterministic normalization

### Root cause

Canonical course and assignment-title sorting used only `casefold()`. Equal
casefold values therefore retained input order through Python's stable sort.
At the storage boundary, reversing 124 otherwise equal-key rows changed the
one row that was truncated. Duplicate resolution had the same issue: stable-ID
and fallback-identity duplicates with the same status retained whichever
display representative appeared first.

### Focused fix

- Course order is now `(course.casefold(), course)`.
- Assignment order remains date, time, and case-insensitive title, with exact
  title added only as the final tie-breaker.
- Same-status stable/fallback duplicates choose the minimum complete canonical
  representative instead of the first input representative.
- Missing-over-due precedence is unchanged.

The regression uses 124 source-distinct casing variants of one eight-character
title. Forward, reverse, and interleaved inputs serialize identically and
retain the same exact-sorted 123 rows. The omitted title is the exact-value
maximum.

The existing capacity math remains unchanged:

```text
weeks root + week + course + missing array + due array + 123 rows * 4
= 5 + 123 * 4
= 497 nodes per slot

bundle + 2 * (slot object + portal value + weeks)
= 1 + 2 * (1 + 1 + 497)
= 999 maximum bundle nodes
```

RED before implementation:

```text
boundary permutation changed retained output
casefold-equal course order changed with input order
same-status duplicate display representative changed with input order
3 failed, 1 missing-precedence guard passed in 0.09s
```

GREEN after the focused change:

```text
4 passed in 0.03s
```

No budget, canonical category, source-ID deduplication, portal order, or
independent slot fairness behavior changed.

## Finding 2: ParentVUE visibility cascade

### Root cause

Inline style values were compared literally, so `none !important` and
`hidden!important` were not recognized. Visibility was also treated as an
any-ancestor hidden flag, even though a closer descendant can explicitly set
`visibility:visible`.

### Focused fix

Inline property names and values are trimmed and case-normalized, and a
terminal `!important` is removed before comparison. Visibility is resolved
from the nearest explicit declaration while the full ancestor walk continues
to enforce unconditional `hidden`, `aria-hidden=true`, and `display:none`.
The same helper applies to assignment rows and Missing markers.

RED before implementation:

```text
!important assignment rows leaked and !important markers classified missing
visible descendant row/marker could not override a visibility-hidden ancestor
2 failed in 0.23s
```

GREEN after the focused change:

```text
2 passed in 0.18s
complete agenda-contract and ParentVUE files: 20 passed in 0.19s
```

## Fresh verification

Python commands removed CRM, Postgres, database, and supported-portal-named
environment variables, disabled dotenv and bytecode, and did not enable live
or integration flags.

```text
agenda contract + Canvas + ParentVUE + Google Classroom + agenda boundary
+ runner boundary + secret redaction: 66 passed in 0.90s

Task 9 prescribed Python selection:
149 passed, 2 failed in 11.40s

complete Python suite:
205 passed, 2 failed, 1 skipped in 8.91s

Rust contract suite:
7 passed, 0 failed

complete Rust suite:
32 passed, 0 failed; doc-tests contained 0 tests

ruff check .:
All checks passed!

cargo fmt --check:
exit 0

git diff --check:
exit 0
```

The two Python failures are unchanged, unrelated branch baselines in
`tests/test_read_only_dashboard_routes.py`: production-mode overview and jobs
API tests expect 403 but receive 200. No agenda, portal, boundary, redaction,
browser, or Rust test failed.

## GitNexus

Current pre-edit impacts supplied for this review were MEDIUM for
`normalize_agenda`, LOW for `_is_hidden`, and MEDIUM for
`parse_parentvue_agenda`; none was HIGH or CRITICAL.

Pre-stage change detection reported 32 mapped symbols in four files, five
affected processes, and aggregate MEDIUM risk. Every trace was inspected:

- `Main -> _json_value_nodes`
- `Main -> _display_text`
- `Main -> Monday_for`
- `Get_agenda -> _assignment_rows`
- `Get_agenda -> _is_hidden`

The first three are the normal agenda collection path through
`normalize_agenda`; the final two are the ParentVUE collection path through
`parse_parentvue_agenda`. Mapped changes to `_assignment_rows` and later test
symbols are line-shift artifacts rather than edits to those symbols. No
database mutation, dashboard, credential, or unrelated portal flow was
reported.

Final staged detection included the two report files and reported six files,
the same 32 mapped code symbols, the same five affected processes, and MEDIUM
risk. All 25 process steps were inspected again after staging.

## Remaining concerns

The two pre-existing read-only dashboard authorization failures keep the
prescribed and complete Python selections from being wholly green. They are
outside this pass's owned files. The deterministic 497-node cap still omits
the canonical tail of unusually large slots by design; round 2 changes only
which exact-case representative wins an otherwise equal canonical key.
