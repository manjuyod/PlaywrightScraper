# Franchise 57 Agenda Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all franchise 57 agenda-capable portal slots return a validated agenda or a controlled atomic failure, then prove the live Google Classroom, ParentVUE, and Canvas paths work without SMSS access.

**Architecture:** Keep portal-specific trust and readiness logic inside each portal engine. The agenda boundary owns slot credential routing, canonical auth-image propagation, timeouts, cleanup isolation, and safe diagnostics. A separately authorized one-row Neon transaction synchronizes legacy GPS answers into the canonical JSON field; runtime fallback is prohibited.

**Tech Stack:** Python 3.11+, Playwright async API, BeautifulSoup, pytest, SQLAlchemy/psycopg, Rust grade-db contract (unchanged), GitNexus.

## Global Constraints

- Do not access SMSS, CRM, or SQL Server.
- Neon is read-only except for the explicitly authorized one-row canonical authentication-answer sync.
- Never print or log credentials, authentication answers, names, raw student IDs, portal hostnames, URLs, HTML, assignment content, cookies, tokens, screenshots, HARs, or traces.
- Preserve existing external agenda failure codes and atomic prior-snapshot behavior.
- Retry only pre-submit/idempotent readiness work; never retry a whole login after password submission.
- Run `gitnexus_impact` before editing every production symbol and `gitnexus_detect_changes` before committing.
- Ignore the two previously acknowledged unrelated failing tests when judging completion.

---

### Task 1: Guarded canonical authentication-answer sync

**Files:**
- No repository files modified.
- Verify: `public.student`, `public.student_auth`, `public.students_grades_20262027`

**Interfaces:**
- Consumes: franchise ID `57`, legacy `student_auth.answers`, canonical `auth_answers` JSONB.
- Produces: exactly one canonical JSON array containing three validated strings.

- [ ] **Step 1: Select and validate the exact target in a read-only transaction**

Run an inline SQLAlchemy script that selects the single tracked franchise 57 student whose configured slots are GPS and Google Classroom and verifies one populated legacy row. Separately require exactly one distinct canonical CRM candidate in franchise 57 agenda job history whose result code is `agenda2_google_classroom_failed`, verify its canonical `auth_answers = []`, and print only bounded counts/booleans. Do not join legacy `student.id` to canonical `crmstudentid`; the live schema has no such relationship.

- [ ] **Step 2: Strictly parse without printing values**

Accept exactly three trimmed strings, reject empty strings, duplicates, more than 64 characters per answer, control characters, and any ambiguous legacy format. Do not use loose braces stripping.

- [ ] **Step 3: Perform one conditional transaction**

Execute a parameterized update equivalent to the job-history-guarded statement below. The independently unique legacy source supplies the strictly parsed payload, while the write atomically constrains the canonical target:

```sql
UPDATE public.students_grades_20262027 AS canonical
SET auth_answers = CAST(:answers_json AS jsonb),
    auth_type = COALESCE(canonical.auth_type, :auth_type)
WHERE canonical.crmstudentid = :student_id
  AND canonical.auth_answers = '[]'::jsonb
  AND EXISTS (
      SELECT 1
      FROM public.grade_scrape_results AS result
      JOIN public.grade_scrape_jobs AS job ON job.id = result.job_id
      WHERE result.crmstudentid = canonical.crmstudentid
        AND job.franchise_id = 57
        AND job.kind = 'agenda'
        AND result.payload->>'kind' = 'failure'
        AND result.payload->>'code' = 'agenda2_google_classroom_failed'
  )
RETURNING canonical.crmstudentid
```

Require one returned row before commit; otherwise roll back. Print only `updated_rows=1`.

- [ ] **Step 4: Verify canonically in a read-only transaction**

Assert `jsonb_array_length(auth_answers) = 3` for the exact job-history target, the legacy GPS→Google source remains unique and tracked, the job-history target remains unique, the update returned one row, and no trigger can update additional canonical rows.

### Task 2: Agenda slot credential and cleanup boundary

**Files:**
- Modify: `scraper/agenda.py`
- Test: `tests/test_agenda_grade_db_boundary.py`

**Interfaces:**
- Consumes: `student` mapping with primary/alternate slot credentials and canonical `auth_images`.
- Produces: portal engines whose primary credentials belong to the active slot and whose alternate configuration belongs to the opposite slot.

- [ ] **Step 1: Add failing constructor-routing tests**

Add tests that capture engine constructor arguments and assert a slot-2 Google engine receives slot-2 credentials as primary, slot-1 GPS URL/username/password as alternate, and a defensive copy of the three canonical auth images. Add a mutation assertion proving the student list is unchanged.

- [ ] **Step 2: Add failing timeout and cleanup tests**

Assert agenda contexts use 15,000 ms action/navigation timeouts. Assert a successful agenda remains successful if `page.close()` or `context.close()` fails, while a primary collection failure retains its existing public slot failure code.

- [ ] **Step 3: Implement the minimum boundary change**

Resolve the opposite `AgendaSlot`, pass its URL/credentials through `alt_portal_url`, `alt_student_id`, and `alt_password`, and pass a bounded defensive `auth_images` copy only when constructing Google Classroom. Set both context defaults to 15,000 ms. Separate collection from best-effort cleanup so cleanup cannot replace the primary result.

- [ ] **Step 4: Run boundary tests**

Run:

```powershell
uv run pytest tests/test_agenda_grade_db_boundary.py -q
```

Expected: all tests pass.

### Task 3: Google Classroom origin-bound login and readiness

**Files:**
- Modify: `scraper/portals/google_classroom.py`
- Test: `tests/test_google_classroom_agenda.py`

**Interfaces:**
- Consumes: primary Google credentials, configured alternate GPS origin/credentials, canonical auth images.
- Produces: authenticated Classroom state or a controlled failure; at most one approved delegation.

- [ ] **Step 1: Add failing login-state tests**

Cover a full `https://classroom.google.com/...` URL, an unresolved `accounts.google.com` challenge, an unknown redirect, a configured GPS redirect, an origin-mismatched GPS redirect, and a delegation loop.

- [ ] **Step 2: Add failing credential-ownership tests**

Assert GPS delegation receives `alt_sid`/`alt_pw`, never the Google credentials, and receives a defensive auth-image copy. Assert no alternate engine is constructed on origin mismatch.

- [ ] **Step 3: Implement normalized origin helpers and single delegation**

Add module-local HTTPS origin normalization requiring no userinfo and default port. Treat exact `classroom.google.com` plus visible Main Menu as success. Delegate only when the current origin equals configured `alt_portal_url` and the configured alternate portal key matches the redirected portal. Use alternate credentials and current validated redirect URL. Fail closed otherwise.

- [ ] **Step 4: Replace brittle agenda probes**

Wait explicitly and locally for Main Menu, To-do, Assigned, and Missing controls. Replace `assert exists(...)` with controlled `GoogleClassroomAgendaError` boundaries. Preserve Assigned+Missing atomicity.

- [ ] **Step 5: Run Google and boundary tests**

Run:

```powershell
uv run pytest tests/test_google_classroom_agenda.py tests/test_agenda_grade_db_boundary.py -q
```

Expected: all tests pass.

### Task 4: ParentVUE explicit-empty and parser integrity

**Files:**
- Modify: `scraper/portals/parentvue.py`
- Modify: `scraper/portals/parentvue_agenda.py`
- Test: `tests/test_parentvue_agenda.py`

**Interfaces:**
- Consumes: authenticated ParentVUE gradebook HTML.
- Produces: normalized records, a validated empty list, or `ParentVueAgendaError`.

- [ ] **Step 1: Add failing tri-state parser tests**

Cover gradebook shell without candidates, authenticated `#gb-assignments .no-data`, candidates plus no-data inconsistency, all malformed candidates, hidden no-data, and a nested intermediate `.gb-class-row` whose outer ancestor owns the course title.

- [ ] **Step 2: Implement explicit-empty semantics**

Return `[]` only for a visible gradebook-scoped no-data marker with zero candidates. Raise when zero candidates are ambiguous, when no-data conflicts with candidates, or when candidates exist but none are accepted.

- [ ] **Step 3: Fix course ancestor traversal**

Walk all `.gb-class-section`/`.gb-class-row` ancestors until a `data-course-title` or `.course-title` is found, then fall back to the row's labeled course cell.

- [ ] **Step 4: Wait for ParentVUE agenda readiness**

In `after_login`, wait for `#gb-assignments` and then for either a no-data marker or an assignment candidate before returning control to the parser.

- [ ] **Step 5: Run ParentVUE tests**

Run:

```powershell
uv run pytest tests/test_parentvue_agenda.py -q
```

Expected: all tests pass.

### Task 5: Canvas trusted route and verified agenda origin

**Files:**
- Modify: `scraper/portals/canvas.py`
- Modify: `scraper/portals/canvas_agenda.py` only if exact-origin enforcement is not already sufficient.
- Test: `tests/test_canvas_agenda.py`
- Test: `tests/test_canvas_engine.py` if a focused engine test file is needed.

**Interfaces:**
- Consumes: exact configured Canvas entry tenant and an explicit tenant-specific authentication route policy.
- Produces: `_canvas_origin: str` only after positive authenticated Canvas verification; agenda requests remain on that exact origin.

- [ ] **Step 1: Add failing trust-policy tests**

Reject HTTP, userinfo, non-default ports, substring lookalikes, unknown transit hosts, and `login.microsoftonline.com.evil.example`. Accept only the explicit Canvas entry→transit→Microsoft→Canvas return route.

- [ ] **Step 2: Add failing retry/submission tests**

Prove pre-submit preparation can retry without any fills or presses. Prove post-submit timeout performs exactly one submission and propagates without restarting login.

- [ ] **Step 3: Add failing authenticated-origin tests**

Require positive Canvas DOM markers and an approved `*.instructure.com` final host. Prove an arbitrary non-login URL is not authenticated. Prove agenda collection uses the verified final origin and refuses to run without it.

- [ ] **Step 4: Implement Canvas-local route policy**

Add normalized URL/origin validation and an explicit policy keyed by the franchise 57 Canvas tenant. Validate every main-frame authentication hop before credential entry. Keep Microsoft detection exact and local; do not edit shared `universal_login_flow` or `use_sso_login`.

- [ ] **Step 5: Split preparation, submit-once, and verified return**

Move retry to a pre-submit helper. Submit credentials once. Sanitize recognized rejection, preserve timeout/trust distinctions internally, remove generic username/password text from error detection, and require positive Canvas evidence before storing `_canvas_origin`.

- [ ] **Step 6: Use the verified origin for agenda APIs**

Pass `_canvas_origin` to `collect_canvas_agenda`. Keep exact scheme/host/effective-port enforcement for API and pagination requests.

- [ ] **Step 7: Run Canvas tests**

Run:

```powershell
uv run pytest tests/test_canvas_agenda.py tests/test_secret_redaction.py -q
```

Expected: all tests pass.

### Task 6: Safe diagnostics and regression verification

**Files:**
- Modify: `scraper/agenda.py`
- Modify: `scraper/config/logging.py` only if a dedicated allowlisted helper cannot be isolated in `agenda.py`.
- Test: `tests/test_logging_config.py`
- Test: `tests/test_secret_redaction.py`

**Interfaces:**
- Consumes: portal/slot/phase enums, mapped exception type, bounded counts, cleanup status.
- Produces: safe diagnostic records with unchanged public failure codes.

- [ ] **Step 1: Add negative logging tests**

Prove exception messages, tracebacks, URLs, credentials, auth images, HTML, unknown extra fields, and assignment data are absent. Prove only the approved enum/count fields survive.

- [ ] **Step 2: Implement the allowlisted diagnostic boundary**

Use fixed event names and explicitly constructed allowed fields. Do not pass `exc_info` or arbitrary exception objects. Bind slot context per task and reset it in `finally`.

- [ ] **Step 3: Run the focused non-live suite**

Run:

```powershell
uv run pytest tests/test_agenda_grade_db_boundary.py tests/test_google_classroom_agenda.py tests/test_parentvue_agenda.py tests/test_canvas_agenda.py tests/test_logging_config.py tests/test_secret_redaction.py -q
```

Expected: all focused tests pass.

### Task 7: Review loop, live validation, and publication

**Files:**
- Review all changed files.
- No live diagnostic artifacts saved.

**Interfaces:**
- Consumes: reviewed implementation and canonical Neon auth answers.
- Produces: verified live result for all four agenda-capable slots and a pushed commit.

- [ ] **Step 1: Run two-stage subagent review**

Dispatch a specification reviewer and a security/code-quality reviewer. Resolve every actionable finding and repeat the relevant reviewer until it reports no blocking findings.

- [ ] **Step 2: Run broader verification**

Run the focused suite, then the repository's broader pytest suite. Record the two user-approved unrelated failures separately if they remain; do not mask any new failure.

- [ ] **Step 3: Live-test franchise 57 sequentially**

Read credentials and canonical auth answers from Neon inside `SET TRANSACTION READ ONLY`. Run only the four agenda-capable slots, one at a time, with approved origins and no persistence. Emit only portal enum, success/failure phase, assignment count, nonempty-week count, and bounded navigation status.

- [ ] **Step 4: Require the completion criteria**

Confirm ParentVUE returns a validated explicit empty, both Google accounts reach Classroom and collect valid agendas, and Canvas returns to a verified Canvas origin and collects its agenda. If an external MFA/CAPTCHA challenge remains, report it as the sole blocker without retrying credentials.

- [ ] **Step 5: Analyze and publish**

Run `gitnexus_detect_changes(scope="all")`, inspect `git diff --check`, set git identity to `manjuyod <manjuyod@gmail.com>`, commit the reviewed scope, and push the current `dev` branch.
