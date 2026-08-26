# Weston J.O. Combs ParentVUE SSO Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route Weston's J.O. Combs Edupoint root URL through the existing ParentVUE engine and authenticate via the site's Microsoft SSO entry without any database writes.

**Architecture:** Extend only `ParentVUE` class metadata: add the exact J.O. Combs host to URL detection and opt the existing universal login lifecycle into the page's Microsoft SSO link. Reuse the existing Microsoft callback, ParentVUE post-login navigation, grade parser, agenda behavior, runner, and database boundary unchanged.

**Tech Stack:** Python 3.13, Playwright async API, pytest, portal class registry, GitNexus impact/change analysis.

**Spec:** `docs/superpowers/specs/2026-08-26-weston-jocombs-parentvue-sso-design.md`

## Global Constraints

- Database interaction is read-only without exception.
- Do not start jobs, post results, apply migrations, or update CRM, Neon, runner configuration, or student state.
- Do not change Kayden's or Chloe's portal handling.
- Do not add a Clever engine, a J.O. Combs-specific portal key, or generic `edupoint.com` routing.
- Do not persist credentials, browser storage, screenshots, traces, page HTML, course names, or grade values.
- Preserve direct ParentVUE and StudentVUE username/password login behavior.
- Preserve existing user changes in the worktree.
- Execute inline without subagents.

## File Structure

- `scraper/portals/parentvue.py` — owns ParentVUE URL patterns and shared login metadata; receives the only production change.
- `tests/test_portal_registry.py` — verifies the exact J.O. Combs root URL resolves to `parentvue`.
- `tests/test_parentvue_agenda.py` — verifies ParentVUE declares the Microsoft SSO selector and callback while retaining its existing gradebook and agenda tests.
- `docs/superpowers/specs/2026-08-26-weston-jocombs-parentvue-sso-design.md` — approved requirements and non-destructive constraints; unchanged during implementation unless a contradiction is discovered.

---

### Task 1: Add J.O. Combs routing and Microsoft SSO metadata

**Files:**
- Modify: `tests/test_portal_registry.py`
- Modify: `tests/test_parentvue_agenda.py`
- Modify: `scraper/portals/parentvue.py:15-23`

**Interfaces:**
- Consumes: `registry.get_portal_key_from_url(url: str) -> str | None`, `ParentVUE.url_patterns`, and `ParentVUE.login_config: UniversalLoginConfig | None`.
- Produces: exact root-host routing from `https://az-joc.edupoint.com/` to `parentvue`; `ParentVUE.login_config.sso_entry_selector == 'a[href*="sts.windows.net"]'`; `ParentVUE.login_config.microsoft_sso is True`.

- [ ] **Step 1: Write the failing URL-routing test**

Add this focused test after the existing URL detection test in `tests/test_portal_registry.py`:

```python
def test_jocombs_root_url_uses_parentvue() -> None:
    assert (
        registry.get_portal_key_from_url("https://az-joc.edupoint.com/")
        == "parentvue"
    )
```

- [ ] **Step 2: Write the failing ParentVUE SSO-contract test**

Add this test beside the existing ParentVUE capability and login tests in `tests/test_parentvue_agenda.py`:

```python
def test_parentvue_declares_jocombs_microsoft_sso() -> None:
    config = ParentVUE.login_config

    assert config is not None
    assert config.sso_entry_selector == 'a[href*="sts.windows.net"]'
    assert config.microsoft_sso is True
```

- [ ] **Step 3: Run both new tests and verify RED**

Run:

```powershell
uv run pytest `
  tests/test_portal_registry.py::test_jocombs_root_url_uses_parentvue `
  tests/test_parentvue_agenda.py::test_parentvue_declares_jocombs_microsoft_sso `
  -v
```

Expected: two assertion failures. The root URL currently resolves to `None`; the current ParentVUE login configuration has no SSO entry selector and has Microsoft SSO disabled. If either test errors for import or syntax reasons, fix the test and rerun until it fails for the missing behavior.

- [ ] **Step 4: Implement the minimal ParentVUE metadata change**

Update only the class metadata at the top of `ParentVUE` in `scraper/portals/parentvue.py`:

```python
class ParentVUE(PortalEngine):
    portal_key = "parentvue"
    url_patterns = (
        "parentvue",
        "Login_Parent",
        "Login_Student",
        "az-joc.edupoint.com",
    )
    agenda_capable = True
    login_config = UniversalLoginConfig(
        username_selector="#ctl00_MainContent_username",
        password_selector="#ctl00_MainContent_password",
        sso_entry_selector='a[href*="sts.windows.net"]',
        microsoft_sso=True,
    )
```

Do not modify `PortalEngine`, `universal_login_flow`, the registry algorithm, ParentVUE navigation/parsing methods, the runner, or database code.

- [ ] **Step 5: Run both new tests and verify GREEN**

Run:

```powershell
uv run pytest `
  tests/test_portal_registry.py::test_jocombs_root_url_uses_parentvue `
  tests/test_parentvue_agenda.py::test_parentvue_declares_jocombs_microsoft_sso `
  -v
```

Expected: both tests pass.

- [ ] **Step 6: Run focused ParentVUE and registry regression coverage**

Run:

```powershell
uv run pytest tests/test_portal_registry.py tests/test_parentvue_agenda.py -q
```

Expected: all tests in both files pass, including direct-login retry behavior, sanitized failures, gradebook navigation, grade parsing, and agenda behavior.

---

### Task 2: Verify the complete non-destructive Weston flow

**Files:**
- Verify: `scraper/portals/parentvue.py`
- Verify: `tests/test_portal_registry.py`
- Verify: `tests/test_parentvue_agenda.py`

**Interfaces:**
- Consumes: production `student_from_context`, portal registry discovery, `scrape_one`, the existing ParentVUE parser, and CRM credentials read with `ApplicationIntent=ReadOnly`.
- Produces: fresh automated verification, a live aggregate course count greater than zero, a GitNexus change report limited to the expected symbols/flows, and one implementation commit.

- [ ] **Step 1: Run the complete automated test suite**

Run:

```powershell
uv run pytest -q
```

Expected: the full suite exits zero with no failures.

- [ ] **Step 2: Run one non-persisting live Weston grade scrape**

Run this exact diagnostic from the repository root. It obtains Weston's context through a read-only CRM connection, keeps credentials in process memory, invokes `scrape_one` directly without a `GradeDbClient`, and prints only status plus aggregate course count:

```powershell
uv run python -c 'import asyncio,json; from dotenv import load_dotenv; load_dotenv(); import pyodbc; from playwright.async_api import async_playwright; from ui.dashboard_data import _crm_connection_string; from scraper.portals import get_portal_key_from_url; from scraper.runner import scrape_one
async def verify():
 c=pyodbc.connect(_crm_connection_string(),timeout=10); c.autocommit=False; q=c.cursor(); q.execute("SET TRANSACTION ISOLATION LEVEL SNAPSHOT"); q.execute("SELECT GradePortalURL,GradePortalUser,GradePortalPwd,FirstName FROM dbo.tblStudents WHERE Id=?",(38231,)); row=q.fetchone(); c.rollback(); q.close(); c.close()
 if row is None: return {"status":"failed","reason":"missing_crm_record","course_count":0}
 student={"db_id":38231,"portal":get_portal_key_from_url(str(row[0])),"login_url":str(row[0]),"id":str(row[1]),"password":str(row[2]),"student_name":str(row[3]),"auth_images":[]}
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled"])
  try:
   result=await scrape_one(browser,student); grades=result.get("parsed_grades"); count=len(grades) if isinstance(grades,dict) else 0; return {"status":"passed" if count else "failed","course_count":count}
  except Exception as e: return {"status":"failed","exception_type":type(e).__name__,"course_count":0}
  finally: await browser.close()
out=asyncio.run(verify()); print(json.dumps(out)); raise SystemExit(0 if out["status"]=="passed" and out["course_count"]>0 else 1)'
```

Expected: exit zero and JSON shaped like `{"status":"passed","course_count":N}` where `N > 0`. The output must not contain names, credentials, URLs, course titles, or grade values. This command must not be replaced with `python -m scraper.runner`, because the runner starts and persists a database job.

- [ ] **Step 3: Inspect the patch for scope and secret safety**

Run:

```powershell
git diff --check
git diff --stat
git diff -- scraper/portals/parentvue.py tests/test_portal_registry.py tests/test_parentvue_agenda.py
git status --short
```

Expected: only the approved ParentVUE metadata and two focused tests appear. No credentials, unrelated files, database code, migrations, job code, or browser artifacts appear.

- [ ] **Step 4: Run GitNexus change detection before committing**

Run `gitnexus_detect_changes({repo: "PlaywrightScraper", scope: "all"})`.

Expected: changed symbols are limited to `ParentVUE` and the two new tests, with no unexpected execution flows. Stop and review before committing if the report is HIGH or CRITICAL or names any runner/database flow outside ParentVUE portal dispatch.

- [ ] **Step 5: Commit the verified implementation**

Run:

```powershell
git add -- scraper/portals/parentvue.py tests/test_portal_registry.py tests/test_parentvue_agenda.py
git commit -m "feat: support J.O. Combs ParentVUE SSO"
```

Expected: one commit containing only the three approved files.

- [ ] **Step 6: Confirm the final repository state**

Run:

```powershell
git status --short
git log -2 --oneline
```

Expected: no uncommitted implementation changes; the newest commit is the Weston implementation and the preceding commit is the approved Weston design.
