# Infinite Campus Production Debug Design

## Goal

Reproduce and fix the Infinite Campus agenda failure for one explicitly authorized active student by using the headed production agenda runner, while preserving privacy, atomic agenda behavior, and database boundaries.

## Scope

- Work directly on `dev` as explicitly authorized.
- Run exactly one student through `uv run python -m scraper.agenda --student <authorized-id>` at a time.
- Keep Chromium headed. The production runner already launches with `headless=False`.
- Preserve sequential traversal within the Infinite Campus collector. The six-worker limit may schedule independent portal slots or students, but must not parallelize one student's assignments.
- Do not change classification rules, date ownership, login-origin validation, credential routing, or database schemas.

## Data and privacy boundaries

- CRM/SMSS access remains SELECT-only and must retain the active-student and configured-portal boundary.
- The authorized production run may create/renew a Neon agenda job lease, post one atomic agenda success or controlled failure result for the selected student, and complete or fail that job.
- No other Neon mutation is authorized.
- Do not print or persist credentials, portal URLs, names, student identifiers, assignment content, scores, dates, cookies, HTML, or tokens.
- Diagnostic output is limited to bounded phase/category, portal enum, aggregate counts, cleanup state, and process exit status.
- Do not query legacy or defunct student rows outside the production runner's canonical selection path.

## Diagnostic flow

1. Confirm `dev` contains only the explicitly authorized six-worker change and this diagnostic work, then inspect the production runner's single-student boundary.
2. Run the authorized student once through the production agenda CLI in a background process while Chromium remains visibly headed.
3. Poll at intervals shorter than 60 seconds and inspect only sanitized runner diagnostics and aggregate job status.
4. If the run fails, trace the controlled failure from the runner boundary into the relevant portal phase without exposing private payloads.
5. Perform at most one additional headed credential submission when a focused reproduction is necessary to distinguish competing hypotheses.
6. State one evidence-backed root-cause hypothesis before modifying production code.

## Fix and verification

- Before editing any indexed symbol, run upstream GitNexus impact analysis and warn before proceeding if risk is HIGH or CRITICAL.
- Write a minimal regression test that fails for the observed production behavior and identify the production mutation it catches.
- Implement only the root-cause fix, inline, with no retries, concurrency changes, arbitrary sleeps, database changes, or private logging.
- Run the focused regression, the Infinite Campus agenda suite, the shared agenda/database/security boundary suite, Ruff, `git diff --check`, the full Python suite, and Rust tests.
- Run one final headed single-student production agenda job. Success requires an applied atomic result, clean browser cleanup, and no private output.
- Run GitNexus change detection before any commit and keep the change scope within the confirmed portal/runner flow and its tests.

## Failure handling

- A portal failure must remain sanitized and atomic; no partial agenda snapshot is posted.
- If the first production run reveals an unrelated failure, stop guessing, capture only its bounded category, and reproduce once before writing the RED regression.
- If the same hypothesis fails three times, stop and reassess the architecture with the user.
