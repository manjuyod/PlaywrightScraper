# Infinite Campus Production Debug Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduce the authorized student's Infinite Campus agenda problem through the headed production runner, identify its root cause without exposing private data, and deliver the smallest regression-tested fix when a production defect is confirmed.

**Architecture:** The production agenda CLI remains the authoritative reproduction boundary because it exercises canonical student selection, credential-slot routing, Playwright collection, atomic result posting, and job completion. A background PowerShell process keeps Chromium visibly headed while redirecting raw process streams to ignored files; only a bounded exit/category summary is inspected. Production code is not edited until the observed failure has been converted into a deterministic RED test.

**Tech Stack:** Python 3.12, uv, Playwright async API, pytest, Ruff, Rust `grade-db`, PowerShell, GitNexus.

## Global Constraints

- Work directly on `dev` as explicitly authorized.
- Preserve the user's unstaged `MAX_CONCURRENT_AGENDA_WORKERS = 6` change in `scraper/agenda.py`; do not stage, revert, or rewrite it unless a confirmed root cause requires that exact symbol and the user separately approves ownership.
- CRM/SMSS remains SELECT-only with the canonical active-student and configured-portal boundary.
- The single authorized production run may create/renew its Neon agenda job lease, post one atomic agenda result, and complete or fail that job. No other Neon mutation is authorized.
- Keep Chromium headed; do not add `--headless`.
- Never print or persist credentials, URLs, names, identifiers, assignment content, scores, dates, cookies, HTML, or tokens.
- Infinite Campus assignment traversal remains sequential and atomic.
- Do not weaken login-origin validation, credential-slot routing, error sanitization, or database boundaries.
- Do not add arbitrary sleeps, generic selectors, login retries, whole-student retries, or new private logging.
- Limit credential submissions to the initial production run and at most one focused headed diagnostic replay.

---

### Task 1: Preflight the headed single-student production boundary

**Files:**
- Inspect: `scraper/agenda.py`
- Inspect: `scraper/db_cli.py`
- Inspect: `tests/test_agenda_grade_db_boundary.py`
- Preserve: `.superpowers/sdd/2026-08-16-infinite-campus-agenda/`

**Interfaces:**
- Consumes: `scraper.agenda.main(franchise_id: int | None, student_id: int | None)` and the production `GradeDbClient` job boundary.
- Produces: a verified command, privacy-safe output paths, and a clean pre-run process/database boundary.

- [ ] **Step 1: Verify the worktree contains only authorized changes**

Run:

```powershell
git status --short
git diff -- scraper/agenda.py
```

Expected: the only pre-existing code change is `MAX_CONCURRENT_AGENDA_WORKERS` from `2` to `6`; the approved design/plan may appear as documentation commits. Stop if any other tracked change appears.

- [ ] **Step 2: Verify the production command is single-student, headed, and atomic**

Inspect `scraper/agenda.py` and confirm all of the following:

```text
--student is parsed as one integer
GradeDbClient.start_job receives that student_id
chromium.launch uses headless=False
post_result receives one complete agenda_success bundle or one controlled failure
complete_job/fail_job closes the leased job
```

Do not run the job if any condition is false.

- [ ] **Step 3: Establish private process variables without echoing them**

In the active PowerShell session, assign the authorized identifier to `IC_DEBUG_STUDENT_ID`. Use fixed ignored output paths that contain no identity data:

```powershell
$runDir = Resolve-Path ".superpowers/sdd/2026-08-16-infinite-campus-agenda"
$stdoutPath = Join-Path $runDir "production-debug-result.txt"
$stderrPath = Join-Path $runDir "production-debug-stderr.txt"
```

The identifier value must never be written to either file or printed to the console.

---

### Task 2: Run once and classify the observed production behavior

**Files:**
- Runtime only: `.superpowers/sdd/2026-08-16-infinite-campus-agenda/production-debug-result.txt`
- Runtime only: `.superpowers/sdd/2026-08-16-infinite-campus-agenda/production-debug-stderr.txt`
- Update after safe aggregation: `.superpowers/sdd/2026-08-16-infinite-campus-agenda/progress.md`

**Interfaces:**
- Consumes: `uv run python -m scraper.agenda --student $env:IC_DEBUG_STUDENT_ID` and the authorized production write boundary.
- Produces: process exit code plus a bounded category from `agenda_success`, `agenda1_infinite_campus_failed`, `agenda2_infinite_campus_failed`, `agenda_runner_failed`, lease failure, or an unexpected sanitized category.

- [ ] **Step 1: Launch the production runner in the background**

Run from the repository root:

```powershell
$uv = (Get-Command uv).Source
$arguments = @("run", "python", "-m", "scraper.agenda", "--student", $env:IC_DEBUG_STUDENT_ID)
$process = Start-Process -FilePath $uv -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
```

Expected: a visibly headed Chromium window opens. `-WindowStyle Hidden` applies only to the background terminal process.

- [ ] **Step 2: Poll without a blocking wait longer than 60 seconds**

Run repeatedly until completion:

```powershell
$process.Refresh()
[pscustomobject]@{ running = -not $process.HasExited } | ConvertTo-Json -Compress
```

Wait no more than 30 seconds between polls. Never display raw stdout/stderr while the process runs.

- [ ] **Step 3: Extract only a bounded result**

After exit, inspect the files programmatically and report only:

```text
exit_code
stdout_byte_count
stderr_byte_count
sanitized failure code, if one matches the runner's fixed code vocabulary
```

Do not return raw lines. Verify no validation Chrome process or temporary profile remains.

- [ ] **Step 4: Apply the diagnostic decision gate**

Use exactly one branch:

```text
Exit 0 and applied agenda success:
  Inspect the canonical result through a read-only aggregate query. If the stored result is correct, report that the production rerun repaired stale state and make no portal code change.

Controlled Infinite Campus slot failure:
  Trace that slot through one focused headed replay using the ignored diagnostic harness, recording only a bounded phase/category. Do not submit credentials a third time.

Runner/lease/database failure:
  Diagnose the grade-db/job boundary without changing portal code or resubmitting portal credentials.
```

- [ ] **Step 5: Remove raw runtime streams after recording safe evidence**

Resolve both exact paths under `$runDir`, confirm their filenames match `production-debug-result.txt` and `production-debug-stderr.txt`, then delete only those two files. Clear `IC_DEBUG_STUDENT_ID` from the process environment.

---

### Task 3: Convert the confirmed root cause into a RED regression

**Files:**
- Primary test when the collector is responsible: `tests/test_infinite_campus_agenda.py`
- Boundary test when slot/job routing is responsible: `tests/test_agenda_grade_db_boundary.py`
- Production file selected only by the evidence: `scraper/portals/infinite_campus_agenda.py`, `scraper/portals/infinite_campus.py`, or `scraper/agenda.py`

**Interfaces:**
- Consumes: the bounded failure phase, the exact failing call boundary, and the closest existing fake/test scenario.
- Produces: one deterministic test that fails because the confirmed defect is present and passes only when that defect is removed.

- [ ] **Step 1: State the single root-cause hypothesis**

Record this evidence tuple in the ignored progress report before editing:

```text
failing component
last successful boundary
first failed boundary
observable state difference from the working headed run
production mutation the regression must catch
```

Use no private values.

- [ ] **Step 2: Run GitNexus impact before editing**

Run upstream impact for the exact production symbol named by the evidence. Report risk, direct callers, affected processes, and affected modules. If risk is HIGH or CRITICAL, stop and warn the user before editing.

- [ ] **Step 3: Add one minimal behavior test**

Extend the closest existing fake with only the state needed to reproduce the first failed boundary. The assertion must cover the externally visible contract: successful traversal/result when recovery is valid, or sanitized atomic failure when recovery is impossible. Do not assert implementation text or mock existence.

- [ ] **Step 4: Verify RED**

Run the complete owning test file so the new regression fails against an otherwise-green baseline:

```powershell
uv run pytest tests/test_infinite_campus_agenda.py -q
```

If the runner boundary owns the failure, substitute `tests/test_agenda_grade_db_boundary.py`. Expected: FAIL for the observed behavior, not a fixture error. Record the exact mutation that the failure catches.

---

### Task 4: Implement the minimal fix and verify locally

**Files:**
- Modify only the production file selected in Task 3.
- Modify only the corresponding test file selected in Task 3.

**Interfaces:**
- Consumes: the RED regression and confirmed root-cause boundary.
- Produces: a minimal fix that preserves atomicity, privacy, sequential traversal, and database contracts.

- [ ] **Step 1: Implement the smallest root-cause correction**

Change only the failing branch. Do not combine concurrency, classification, selector, retry, logging, or database changes unless the RED test proves that exact concern is causal.

- [ ] **Step 2: Verify GREEN**

Run the exact RED command from Task 3. Expected: PASS.

- [ ] **Step 3: Run focused and shared verification**

```powershell
uv run pytest tests/test_infinite_campus_agenda.py -q
uv run pytest tests/test_infinite_campus_agenda.py tests/test_infinite_campus_login.py tests/test_agenda_contract.py tests/test_agenda_grade_db_boundary.py tests/test_portal_registry.py tests/test_secret_redaction.py -q
uv run ruff check scraper/agenda.py scraper/portals/infinite_campus.py scraper/portals/infinite_campus_agenda.py tests/test_infinite_campus_agenda.py tests/test_infinite_campus_login.py tests/test_agenda_grade_db_boundary.py
git diff --check
```

Expected: all focused/shared tests and Ruff pass with no diff-integrity errors.

- [ ] **Step 4: Run full verification**

```powershell
uv run pytest -q
cargo test --manifest-path grade_db/Cargo.toml -q
```

Only the two pre-approved non-development dashboard authorization failures may remain in the Python suite. Any other failure is a regression.

- [ ] **Step 5: Run GitNexus change detection and commit only owned files**

Confirm the change detector reports only the evidence-selected agenda/runner flow and tests. Stage explicit paths; never use `git add -A`. Preserve the user's runner-limit change as a separate unstaged edit unless the user explicitly asks to include it.

---

### Task 5: Prove the production outcome headed

**Files:**
- Runtime only: the same two ignored production-debug stream files from Task 2.
- Update: `.superpowers/sdd/2026-08-16-infinite-campus-agenda/progress.md`

**Interfaces:**
- Consumes: verified code on `dev` and the authorized one-student production runner.
- Produces: one applied atomic result plus aggregate-only validation evidence.

- [ ] **Step 1: Repeat the Task 2 background headed command once**

Expected: visibly headed Chromium, one selected student, and no raw terminal output.

- [ ] **Step 2: Verify the applied result and cleanup**

Require exit code `0`, a completed job, one applied atomic agenda result, no unexpected failure category, no remaining validation Chrome process, and no temporary profile. Inspect stored agenda only through aggregate counts/status vocabulary.

- [ ] **Step 3: Record safe evidence and remove runtime artifacts**

Append only bounded counts/status categories and verification command summaries to the ignored progress report. Delete the two exact raw stream files and clear the identifier environment variable.

- [ ] **Step 4: Re-run affected verification after the live result**

Repeat the focused/shared tests, Ruff, `git diff --check`, full Python suite, and Rust suite from Task 4 before claiming completion.
