# Grade Checker CRM Device Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Protect Grade Scraper routes with CRM-origin device authorization, real Rust assertion validation, and uncached grant introspection while preserving unrelated scraper/dashboard work.

**Architecture:** Grade Checker generates state/PKCE and redirects unauthenticated browsers to the fixed CRM device-authorization page. Its backend redeems the resulting code with Rust, validates the Ed25519 assertion, creates a secure Grade session, and introspects the exact Rust grant before every protected data access. The private device key never exists under the Grade origin.

**Tech Stack:** Python 3.11-3.14, Flask 3.1, Requests, PyJWT with crypto support, signed Secure/HttpOnly cookies, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-29-grade-checker-crm-device-auth-design.md`

**Companion plans:** [Rust service](../../../../../crm-device-auth/docs/superpowers/plans/2026-08-29-crm-origin-device-auth-service.md) · [CRM](../../../../../tutoring-club/docs/superpowers/plans/2026-08-29-crm-origin-device-auth-crm.md) · [Coordination index](../../../../../tutoring-club/docs/superpowers/plans/2026-08-29-crm-origin-device-auth-cross-repo.md)

## Global Constraints

- Execute on branch `feat/CRM-auth-integration` in `C:\Users\17026\Documents\Code stuff\reporting-v2\PlaywrightScraper`.
- Fix the Grade origin at `https://grades.tutoringclub.com`, its callback at `https://grades.tutoringclub.com/auth/callback`, the CRM authorization URL at `https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx`, and the Rust public origin at `https://crm-auth.tutoringclub.com`.
- Rust Tasks 1, 5, and 6 from `../../crm-device-auth/docs/superpowers/plans/2026-08-29-crm-origin-device-auth-service.md` and CRM Tasks 1, 4, and 5 from `../../tutoring-club/docs/superpowers/plans/2026-08-29-crm-origin-device-auth-crm.md` are start gates.
- The worktree already contains modified/untracked auth and dashboard files. Inventory/adapt them; never overwrite blindly or stage unrelated `ui/dashboard_data.py` changes.
- Before modifying an existing symbol, run GitNexus upstream impact and report HIGH/CRITICAL risk. Run change detection before every commit.
- Agents never query CRM, Neon, or PostgreSQL. Tests use Flask clients, mocked Rust HTTP, deterministic crypto, and data-call spies.
- Browser JavaScript never receives/stores the CRM-origin private key and never calls Rust directly.
- Grade never accepts trusted role, franchise, permissions, callback, client ID, device ID, or assertion claims from browser input.
- `/auth/start` uses 32 random bytes for state, at least 32 bytes for PKCE verifier, S256, fixed CRM URL, and 600-second transaction.
- Rust codes/challenges last 60 seconds, grants 28,800 seconds, assertions at most 1,500 seconds.
- Grade session never outlives backing grant and every protected request introspects Rust without cross-request active caching.
- Rust unavailable means controlled Grade 503 before private data access; it never affects CRM.
- Role `"2"` requires `dashboard.read`/`students.read`; Role `"3"` has `students.read` only. Franchise comes from validated Rust claims.
- Never log/store codes, verifiers, assertions, cookies, client secrets, keys, nonces, signatures, raw bodies, CRM credentials, or student data.
- Before every commit, inspect `git diff --cached --name-only`; commit only task-owned paths.
- TDD order: failing focused test, observed failure, minimal implementation, focused pass, non-database suite, GitNexus change detection, staged-path inspection, commit.

## File Map

- `ui/auth/config.py`: fixed env/URL/TTL configuration.
- `ui/auth/models.py`: typed claims, introspection, transaction/session records.
- `ui/auth/client.py`: Basic-auth v2 redeem/introspection HTTP.
- `ui/auth/assertions.py`: real EdDSA/JWKS validation.
- `ui/auth/transaction.py`: signed one-use PKCE transaction.
- `ui/auth/session.py`: Grade session cookie envelope.
- `ui/auth/routes.py`: start/callback/logout.
- `ui/auth/guards.py`: per-request introspection/permission/franchise guards.
- `ui/app.py`: blueprint/error/header integration.
- `ui/routes.py`: guarded protected entry points.
- `tests/`: mocked HTTP, crypto, session, guard, and zero-data-access tests.

## Upstream Contract Gate

Before Task 1 verify:

```text
../../crm-device-auth/test-vectors/v2-crm-origin-device-proof.json
POST https://crm-auth.tutoringclub.com/v2/authorization/redeem
POST https://crm-auth.tutoringclub.com/v2/grants/introspect
https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx
```

---

### Task 1: Reconcile v2 configuration, models, HTTP client, and assertion validation

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `ui/auth/config.py`
- Modify: `ui/auth/models.py`
- Modify: `ui/auth/client.py`
- Modify: `ui/auth/assertions.py`
- Create: `tests/test_auth_config.py`
- Create: `tests/test_auth_client.py`
- Create: `tests/test_auth_assertions.py`
- Create: `tests/fixtures/v2-crm-origin-device-proof.json`

**Interfaces:**

- `RustAuthClient.redeem_authorization_code(code, code_verifier) -> AuthClaims`.
- `RustAuthClient.introspect_grant(grant_id, device_id) -> GrantIntrospection`.
- `validate_assertion(raw, jwks, config) -> AuthClaims` with real Ed25519 verification.
- Removes temporary fake authorization/challenge/assertion methods.

- [ ] **Step 1: Inventory current dirty auth work**

```powershell
git status --short
git diff -- ui/auth tests/test_auth_routes.py tests/test_auth_transactions.py tests/test_device_auth_browser_contract.py tests/test_device_auth_javascript.py
```

Record useful intent and ensure unrelated `README.md`, `ui/app.py`, `ui/dashboard_data.py`, and `ui/routes.py` edits are not staged in this task.

- [ ] **Step 2: Run GitNexus impact for existing auth symbols**

Analyze `AuthConfig`, `RustAuthClient`, `AuthClaims`, and assertion helpers before editing.

- [ ] **Step 3: Copy canonical v2 fixture byte-for-byte with `apply_patch`**

Source: `../../crm-device-auth/test-vectors/v2-crm-origin-device-proof.json`.

- [ ] **Step 4: Write failing config/client/assertion tests**

```python
def test_redeem_and_introspection_shapes_are_exact(http_stub, config):
    client = RustAuthClient(config, session=http_stub)
    claims = client.redeem_authorization_code("opaque-code", "v" * 43)
    assert http_stub.last_path == "/v2/authorization/redeem"
    assert http_stub.last_json == {
        "authorization_code": "opaque-code",
        "code_verifier": "v" * 43,
    }
    result = client.introspect_grant(claims.grant_id, claims.sub)
    assert http_stub.last_path == "/v2/grants/introspect"
    assert result.active is True
```

Assertion tests use real fixture-signed Ed25519 JWTs and reject unverified decode, algorithm substitution, wrong issuer/audience, invalid times/types, unknown permissions, and missing claims.

- [ ] **Step 5: Run and observe failure**

```powershell
uv run pytest tests/test_auth_config.py tests/test_auth_client.py tests/test_auth_assertions.py -q
```

- [ ] **Step 6: Implement exact v2 config/models/client**

Rename launch config to `crm_device_authorize_url`, load `CRM_DEVICE_AUTHORIZE_URL`, validate fixed HTTPS values, use server-side Basic auth, 3-second connect/read timeout, bounded JSON, and controlled `ClientError(code)` without raw details.

```python
@dataclass(frozen=True)
class GrantIntrospection:
    active: bool
    grant_id: str | None = None
    device_id: str | None = None
    crm_role: str | None = None
    franchise_id: int | None = None
    permissions: tuple[str, ...] = ()
    expires_at: int | None = None
```

Add `PyJWT[crypto]>=2.10,<3`, update lockfile, and exclude actor reference from the Grade session model.

- [ ] **Step 7: Implement real assertion validation**

Use Rust JWKS and require `EdDSA`; remove every `verify_signature=False` path. Validate exact fixed claims/types/times/permissions and return typed `AuthClaims` only after success.

- [ ] **Step 8: Run focused tests/lint**

```powershell
uv run pytest tests/test_auth_config.py tests/test_auth_client.py tests/test_auth_assertions.py -q
uv run ruff check ui/auth tests/test_auth_config.py tests/test_auth_client.py tests/test_auth_assertions.py
```

- [ ] **Step 9: Detect changes, verify staging, commit**

```powershell
git add pyproject.toml uv.lock ui/auth/config.py ui/auth/models.py ui/auth/client.py ui/auth/assertions.py tests/test_auth_config.py tests/test_auth_client.py tests/test_auth_assertions.py tests/fixtures/v2-crm-origin-device-proof.json
git diff --cached --name-only
git commit -m "feat: add Grade Checker v2 auth client"
```

---

### Task 2: Implement PKCE transaction, callback, and Grade session

**Files:**

- Modify: `ui/auth/transaction.py`
- Modify: `ui/auth/routes.py`
- Create: `ui/auth/session.py`
- Modify: `ui/templates/auth_error.html`
- Remove from worktree after inventory: `ui/templates/device_auth.html`
- Modify: `tests/test_auth_transactions.py`
- Modify: `tests/test_auth_routes.py`
- Create: `tests/test_auth_session.py`

**Interfaces:**

- Signed `__Host-grade_checker_auth_tx` with state/verifier/return/600-second expiry.
- `/auth/start`, `/auth/callback`, `/auth/logout`.
- Secure, HttpOnly, host-only `__Host-grade_checker_session` not exceeding grant expiry.

- [ ] **Step 1: Run GitNexus impact**

Analyze existing auth route/transaction functions.

- [ ] **Step 2: Write failing transaction/route/session tests**

Pin one state/S256 challenge, fixed CRM URL, exact cookie attributes, callback uniqueness, one-use transaction, redeem then introspect order, minimum session claims, grant clamp, and fixed logout.

```python
def test_start_uses_fixed_crm_url_and_one_state(client, config):
    response = client.get("/auth/start")
    location = response.headers["Location"]
    assert location.startswith(config.crm_device_authorize_url)
    assert location.count("state=") == 1
    assert location.count("code_challenge=") == 1
    assert "code_challenge_method=S256" in location
```

- [ ] **Step 3: Run and observe failure**

```powershell
uv run pytest tests/test_auth_transactions.py tests/test_auth_routes.py tests/test_auth_session.py -q
```

- [ ] **Step 4: Implement signed transaction**

```python
@dataclass(frozen=True)
class AuthTransaction:
    state: str
    code_verifier: str
    return_path: str
    expires_at: int
```

Use a dedicated signing salt and allowlist relative return paths; reject absolute/user-supplied destinations.

- [ ] **Step 5: Implement callback/session**

Verify/consume transaction, compare state constant-time, redeem Rust code, validate assertion, introspect matching grant/device/claims, then create Grade session. Clear transaction cookie on every terminal outcome.

- [ ] **Step 6: Remove obsolete device template**

After preserving useful copy in auth error tests, remove the currently untracked `ui/templates/device_auth.html`; no Grade page should generate/sign a private key.

- [ ] **Step 7: Run focused tests/lint**

```powershell
uv run pytest tests/test_auth_transactions.py tests/test_auth_routes.py tests/test_auth_session.py -q
uv run ruff check ui/auth tests/test_auth_transactions.py tests/test_auth_routes.py tests/test_auth_session.py
```

- [ ] **Step 8: Detect changes, verify staging, commit**

```powershell
git add ui/auth/transaction.py ui/auth/routes.py ui/auth/session.py ui/templates/auth_error.html tests/test_auth_transactions.py tests/test_auth_routes.py tests/test_auth_session.py
git diff --cached --name-only
git commit -m "feat: establish Grade sessions through CRM device auth"
```

---

### Task 3: Add uncached introspection guards

**Files:**

- Create: `ui/auth/guards.py`
- Modify: `ui/app.py:1-35`
- Create: `tests/test_auth_guards.py`
- Modify: `tests/test_read_only_dashboard_routes.py`

**Interfaces:**

- `current_claims() -> AuthClaims`, cached only in Flask `g` for one request.
- `require_permission(permission, api=False)`.
- `require_franchise(franchise_id)`.
- Each request calls Rust introspection before returning claims.

- [ ] **Step 1: Run GitNexus impact**

Analyze `app`, blueprint registration, and any existing guard imports; report HIGH/CRITICAL results.

- [ ] **Step 2: Write failing guard tests**

```python
def test_each_request_introspects_without_cross_request_cache(client, rust_stub):
    rust_stub.active = True
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 200
    assert rust_stub.introspection_calls == 2

def test_unavailable_introspection_prevents_data_access(client, rust_stub, data_spy):
    rust_stub.raise_unavailable = True
    assert client.get("/").status_code == 503
    assert data_spy.calls == 0
```

Add inactive clearing, permission denial, API JSON denial, exact claim match, and franchise mismatch tests.

- [ ] **Step 3: Run and observe failure**

```powershell
uv run pytest tests/test_auth_guards.py tests/test_read_only_dashboard_routes.py -q
```

- [ ] **Step 4: Implement guards**

Decode/validate Grade session, call `introspect_grant`, require exact grant/device/role/franchise/permission equality, then place typed claims in `flask.g`. Cache only within request.

- [ ] **Step 5: Run focused tests/lint**

```powershell
uv run pytest tests/test_auth_guards.py tests/test_read_only_dashboard_routes.py -q
uv run ruff check ui/auth/guards.py ui/app.py tests/test_auth_guards.py
```

- [ ] **Step 6: Detect changes, verify staging, commit**

```powershell
git add ui/auth/guards.py ui/app.py tests/test_auth_guards.py tests/test_read_only_dashboard_routes.py
git diff --cached --name-only
git commit -m "feat: enforce live Rust grant introspection"
```

---

### Task 4: Enforce route/data boundaries and remove Grade-origin key code

**Files:**

- Modify: `ui/routes.py:306-420`
- Modify: `ui/app.py`
- Remove from worktree after inventory: `ui/static/device-auth.js`
- Remove from worktree after inventory: `tests/test_device_auth_javascript.py`
- Remove from worktree after inventory: `tests/test_device_auth_browser_contract.py`
- Modify: `README.md`
- Modify: `tests/test_read_only_dashboard_routes.py`
- Create: `tests/test_auth_data_boundary.py`

**Interfaces:**

- Dashboard/jobs require `dashboard.read`.
- Student/franchise views require `students.read` plus exact validated franchise.
- No private data function executes before session validation and Rust introspection.

- [ ] **Step 1: Run HIGH-impact GitNexus analysis before editing routes**

Analyze `index`, `jobs_api`, `franchise_view`, `student_view`, `load_students`, `load_student`, and `load_jobs`. Report all direct callers/affected processes and preserve unrelated dashboard/agenda edits.

- [ ] **Step 2: Write failing zero-data-access tests**

Use monkeypatched data spies for anonymous, inactive, unavailable, Role 3 dashboard, and cross-franchise requests. Authorized tests prove loaders receive only validated Session franchise.

- [ ] **Step 3: Run and observe failures**

```powershell
uv run pytest tests/test_auth_data_boundary.py tests/test_read_only_dashboard_routes.py -q
```

- [ ] **Step 4: Apply guards at route entry**

Keep auth out of SQL/data helpers. Decorate each protected route and replace route-parameter trust with `require_franchise`; preserve unrelated shaping logic.

- [ ] **Step 5: Remove Grade-origin key WIP and update config docs**

Remove the three inventoried untracked browser-key files and confirm absence with `git status --short`. Update Replit Secrets/README to `CRM_DEVICE_AUTHORIZE_URL` and v2 server-to-server routes.

- [ ] **Step 6: Run complete Grade gate**

```powershell
uv run pytest tests/test_auth_config.py tests/test_auth_client.py tests/test_auth_assertions.py tests/test_auth_transactions.py tests/test_auth_routes.py tests/test_auth_session.py tests/test_auth_guards.py tests/test_auth_data_boundary.py tests/test_read_only_dashboard_routes.py tests/test_read_only_dashboard_frontend.py -q
uv run ruff check ui tests/test_auth_config.py tests/test_auth_client.py tests/test_auth_assertions.py tests/test_auth_transactions.py tests/test_auth_routes.py tests/test_auth_session.py tests/test_auth_guards.py tests/test_auth_data_boundary.py
```

Exclude Docker/PostgreSQL/CRM/Neon tests.

- [ ] **Step 7: Detect changes, verify staging, commit**

```powershell
git add ui/routes.py ui/app.py README.md tests/test_read_only_dashboard_routes.py tests/test_auth_data_boundary.py
git diff --cached --name-only
git commit -m "feat: protect Grade data with CRM device grants"
```

---

### Task 5: Owner browser and rollout acceptance

**Files:** No source changes unless acceptance reveals a spec violation; fixes return to Tasks 1-4 with red/green tests.

**Interfaces:** Validates deployed CRM/Rust/Grade integration after their local plans pass.

- [ ] **Step 1: Verify Grade feature disabled behavior**

Grade protected routes remain closed/controlled while CRM daily operations remain unchanged.

- [ ] **Step 2: Verify Role 2 and Role 3 flows**

Role 2 receives dashboard/students; Role 3 receives students only; cross-franchise URLs fail before data access.

- [ ] **Step 3: Verify multiple devices and cleared CRM site data**

Two browser profiles remain independent. Clearing `tutoraid.net` data creates a new device without replacing the other browser.

- [ ] **Step 4: Verify immediate revocation**

Open Grade in two tabs, logout CRM, issue a protected request in each tab, and require both next requests to fail while another registered device remains active.

- [ ] **Step 5: Verify Rust outage**

Introspection outage yields controlled 503 and zero private data calls; it does not redirect-loop or affect CRM.

- [ ] **Step 6: Observe one full grant window**

Grade session never outlives 28,800-second grant; Rust assertions never exceed 1,500 seconds; no refresh loop occurs.

## Completion Evidence

- Real EdDSA/JWKS validation has no unverified decode path.
- PKCE transaction/callback is fixed, one-use, and state-checked.
- Grade session is Secure/HttpOnly/host-only and grant-clamped.
- Every protected request performs uncached Rust introspection.
- Permission/franchise guards execute before all private data access.
- Rust unavailable fails Grade closed with controlled 503.
- Grade-origin private-key code is absent.
- Existing unrelated dashboard changes remain preserved.
- All agent-run checks pass without live database access.
