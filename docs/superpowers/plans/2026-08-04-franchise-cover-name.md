# Franchise Cover Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the normalized CRM franchise name on the direct Franchise Page cover instead of `Franchise {id}`.

**Architecture:** Add a dedicated, parameterized read-only CRM lookup for `dbo.tblFranchies.FranchiesName`, normalize the returned display name in the dashboard data boundary, and expose it as `franchiseName` on the existing Franchise Page payload. Only the React `FranchisePage` cover consumes the new field; the HTML title and all other UI labels remain unchanged.

**Tech Stack:** Python 3.12, Flask, pyodbc, React without JSX, pytest.

## Global Constraints

- Do not access a live database during implementation or verification.
- Preserve the existing numeric label everywhere except the direct Franchise Page cover.
- Keep names that already contain `Tutoring Club of` (case-insensitive); otherwise prefix `Tutoring Club of `.
- Fall back to `Franchise {id}` for missing, null, or blank CRM names.
- Do not commit changes unless the user asks.

---

### Task 1: Franchise name data boundary

**Files:**
- Modify: `ui/dashboard_data.py`
- Test: `tests/test_dashboard_data.py`

**Interfaces:**
- Produces: `load_franchise_name(franchise_id: int) -> str`, returning a normalized cover label.
- Consumes: existing `_crm_connection_string()` and the same injectable pyodbc connector pattern as `read_crm_students()`.

- [ ] Add failing tests for prefixed, standalone, case-insensitive, whitespace, missing, and blank franchise names, plus the parameterized read-only lookup contract.
- [ ] Run the focused dashboard-data tests and confirm they fail because the new interface does not exist.
- [ ] Add `CRM_FRANCHISE_NAME_SQL`, `read_crm_franchise_name()`, `_format_franchise_name()`, and `load_franchise_name()` with safe cleanup and `DashboardDataError` wrapping.
- [ ] Run the focused dashboard-data tests and confirm they pass.

### Task 2: Franchise Page cover consumer

**Files:**
- Modify: `ui/routes.py`
- Modify: `ui/static/react-dashboard.js`
- Test: `tests/test_read_only_dashboard_routes.py`
- Test: `tests/test_read_only_dashboard_frontend.py`

**Interfaces:**
- Consumes: `dashboard.load_franchise_name(franchise_id)`.
- Produces: Franchise Page payload field `franchiseName: str`; `FranchisePage` passes it to `Header.title`.

- [ ] Add failing route and frontend behavior tests asserting the payload and cover use the normalized name while the payload's existing numeric `title` remains unchanged.
- [ ] Run the focused route/frontend tests and confirm the expected failures.
- [ ] Load `franchiseName` in `franchise_view`, add it to `page_data`, and render `data.franchiseName` in the React cover.
- [ ] Run focused tests, then the complete Python test suite, and confirm all pass.
- [ ] Run `gitnexus_detect_changes(scope="all")` and verify only the planned dashboard data, franchise route, and FranchisePage cover flows are affected.
