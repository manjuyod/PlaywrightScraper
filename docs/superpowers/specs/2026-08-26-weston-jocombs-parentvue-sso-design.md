# Weston J.O. Combs ParentVUE SSO Design

## Context

Franchise 103 student 38231 has the CRM grade portal URL
`https://az-joc.edupoint.com/`. The portal registry cannot currently map that
district root URL to an engine, so the grade runner produces a null portal key
and records `scrape_failed` before attempting authentication.

A non-persisting live probe confirmed the complete browser path:

1. The CRM root URL opens the J.O. Combs Synergy common login page.
2. The site's explicit Microsoft SSO link redirects to the district's Microsoft
   tenant.
3. The existing Microsoft login helper authenticates the student.
4. Edupoint redirects back to `PXP2_Gradebook.aspx`.
5. The existing ParentVUE engine finds `#gb-assignments` and reaches its normal
   gradebook-ready state.

## Goal

Allow the existing ParentVUE engine to recognize Weston's J.O. Combs root URL,
authenticate through the page's Microsoft SSO entry, and reuse the current
ParentVUE grade parser without changing any database data or runner contracts.

## Scope

- Add exact URL recognition for `az-joc.edupoint.com` as `parentvue`.
- Configure the ParentVUE shared login lifecycle to select the J.O. Combs
  Microsoft SSO link when direct username and password inputs are absent.
- Keep direct ParentVUE and StudentVUE username/password login behavior
  unchanged.
- Add focused automated coverage for URL routing and Microsoft SSO metadata.
- Verify Weston through a non-persisting live grade scrape that reports only a
  course count.

## Non-Goals

- Do not change Kayden's or Chloe's portal handling.
- Do not add a generic Clever engine.
- Do not update CRM, Neon, runner configuration, jobs, results, or student
  state.
- Do not create a J.O. Combs-specific portal key or duplicate the ParentVUE
  parser.
- Do not persist credentials, browser storage, screenshots, traces, page HTML,
  course names, or grade values.
- Do not broaden root-URL detection to every `edupoint.com` host in this change.

## Architecture

The change stays within the existing ParentVUE declaration in
`scraper/portals/parentvue.py`:

- Append the exact `az-joc.edupoint.com` host to `ParentVUE.url_patterns`.
- Add `sso_entry_selector='a[href*="sts.windows.net"]'` to
  `ParentVUE.login_config`.
- Enable `microsoft_sso=True` for that shared login configuration.

No runner or registry function changes are required. Portal discovery already
registers class metadata, URL detection already prefers the longest matching
pattern, and the shared login lifecycle already tries direct credentials before
falling back to an available SSO entry. Consequently, existing ParentVUE sites
with visible username/password fields continue down their current path, while
the J.O. Combs root page selects Microsoft SSO.

## Data Flow

1. The read-only grade database boundary returns the CRM student context.
2. `student_from_context` asks the registry to identify the CRM portal URL.
3. The exact J.O. Combs host resolves to `parentvue`.
4. `scrape_one` constructs the existing `ParentVUE` engine.
5. The shared login flow does not find direct ParentVUE credential inputs on
   the common login page, selects the Microsoft SSO link, and invokes the
   existing Microsoft callback.
6. `ParentVUE.validate_login` and `ParentVUE.after_login` navigate to the
   gradebook-ready state.
7. The unchanged ParentVUE grade table parser returns the grade map to the
   unchanged runner result boundary.

## Error Handling and Privacy

- Authentication rejection remains a sanitized `LoginError("portal login
  rejected")` and becomes the existing controlled `bad_login` result.
- Navigation or parsing failures continue through the existing sanitized
  `scrape_failed` result path.
- Logs may contain only the stable portal key, CRM student record ID, event
  name, exception type, and aggregate counts already allowed by the logging
  contract.
- Credentials remain in CRM and process memory only. They must never appear in
  source, tests, documentation, commands, logs, snapshots, or committed files.

## Testing Strategy

Automated tests use test-first red-green-refactor cycles:

1. Add a registry assertion that `https://az-joc.edupoint.com/` resolves to
   `parentvue`.
2. Add a ParentVUE login-contract test proving the configured SSO selector is
   the Microsoft tenant link and that the Microsoft callback is enabled.
3. Preserve the existing tests for direct ParentVUE login, post-login gradebook
   navigation, sanitized timeouts, grade parsing, and agenda support.
4. Run the focused registry and ParentVUE tests, then the complete test suite.

After automated verification, run one non-persisting live Weston grade scrape.
The diagnostic may read Weston's CRM credentials through an
`ApplicationIntent=ReadOnly` connection and may authenticate to the portal, but
it must not start a grade job, post a result, or write to either database. Its
only reported data is success/failure and aggregate course count.

## Non-Destructive Constraints

- Database interaction is read-only without exception.
- No migrations, SQL updates, job starts, result posts, or state changes.
- No credential or browser-session artifacts are written to disk.
- Production changes are limited to ParentVUE metadata and its focused tests.
- Existing user changes in the worktree must be preserved.

## Alternatives Considered

### Dedicated J.O. Combs portal engine

A subclass could isolate host-specific SSO metadata, but it would introduce a
new portal key for the same Edupoint product and complicate grade and agenda
status semantics without adding behavior. Rejected as unnecessary.

### Runner-level URL rewrite

The runner could rewrite the root URL to a direct StudentVUE path. This puts
district-specific portal knowledge in orchestration code and still requires SSO
support. Rejected because portal navigation belongs in the portal engine.

### Generic `edupoint.com` root detection

This would recognize future Edupoint roots automatically, but it expands the
blast radius to unverified districts and could route non-student Synergy entry
pages into ParentVUE. Deferred until another district demonstrates the same
contract.

## Acceptance Criteria

- Weston's CRM root URL resolves to `parentvue`.
- ParentVUE selects the Microsoft SSO entry when direct login inputs are absent.
- Existing direct ParentVUE and StudentVUE login tests remain green.
- Weston reaches the gradebook and returns at least one parsed course during a
  non-persisting live diagnostic.
- No database row, job, result, credential file, browser state, screenshot, or
  trace is created or modified.
