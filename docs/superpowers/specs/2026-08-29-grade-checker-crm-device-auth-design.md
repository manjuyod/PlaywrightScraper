# Grade Checker CRM-Origin Device Authorization Design

**Status:** Approved design

**Repository:** `PlaywrightScraper`

**Branch:** `feat/CRM-auth-integration`

**Companion specifications:**

- `../../tutoring-club/docs/superpowers/specs/2026-08-29-crm-origin-device-auth-design.md`
- `../../crm-device-auth/docs/superpowers/specs/2026-08-29-crm-origin-device-auth-service-design.md`

## 1. Purpose

Protect the Grade Scraper at `https://grades.tutoringclub.com` with CRM-derived,
device-bound authorization while leaving the browser private key under the
`https://tutoraid.net` origin. Grade Checker redirects to CRM only when it needs
to establish a session, redeems the resulting one-time code through its backend,
and asks Rust whether the backing grant remains active before every protected
request.

Grade Checker never authenticates CRM passwords, selects a franchise, creates a
trusted role, stores a device private key, or queries the authorization database.

## 2. Responsibility boundary

### Browser on Grade Checker origin

- Holds only Grade Checker's signed transaction cookie and application session
  cookie.
- Never generates, imports, receives, or stores the CRM-origin private key.
- Never calls the Rust service directly.
- Follows top-level redirects to CRM for device proof when no Grade session exists.

### Grade Checker backend

- Generates OAuth-style state and PKCE verifier/challenge.
- Redirects only to the fixed CRM device-authorization URL.
- Redeems one-time codes through Rust using confidential-client authentication.
- Validates Ed25519 assertions against Rust JWKS and fixed claims.
- Creates its own secure application session.
- Introspects the backing grant through Rust on every protected request.
- Enforces Rust-provided permissions and franchise scope before data access.

### CRM and Rust

CRM authenticates the human and operates the browser key. Rust registers the
public key, verifies proof, maps permissions, issues codes/assertions, and owns
revocation. Grade Checker trusts neither browser claims nor CRM query parameters.

## 3. Route flow

### 3.1 Start authentication

```http
GET /auth/start
```

The backend generates:

- a cryptographically random 32-byte state;
- a PKCE verifier with at least 32 random bytes;
- the unpadded base64url S256 challenge;
- a fixed allowlisted return destination within Grade Checker;
- a ten-minute signed `__Host-grade_checker_auth_tx` cookie.

It redirects to:

```text
https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx
  ?state=<state>
  &code_challenge=<challenge>
  &code_challenge_method=S256
```

No role, franchise, actor, permission, callback, client ID, device ID, or public
key is placed in this redirect.

### 3.2 CRM device authorization

The CRM page performs login continuation, device registration, challenge signing,
and proof submission under `tutoraid.net`. Grade Checker is not involved until
the browser returns to the fixed callback:

```text
https://grades.tutoringclub.com/auth/callback?state=<state>&code=<code>
```

### 3.3 Callback and code redemption

```http
GET /auth/callback
```

The backend requires exactly one `state` and one `code`, loads and consumes the
transaction cookie, compares state in constant time, and rejects expired or
replayed transactions.

It calls Rust server-to-server:

```http
POST https://crm-auth.tutoringclub.com/v2/authorization/redeem
Authorization: Basic <client credentials>
Content-Type: application/json

{
  "authorization_code": "<opaque-code>",
  "code_verifier": "<PKCE-verifier>"
}
```

Before establishing a session, the backend:

1. validates the Rust assertion signature with the current JWKS;
2. requires `alg=EdDSA` and rejects algorithm substitution;
3. requires the exact issuer and audience;
4. validates `iat`, `nbf`, `exp`, `jti`, device ID, grant ID, CRM role,
   franchise ID, and exact known permission strings;
5. calls Rust introspection for the assertion's grant/device pair;
6. requires introspection claims to match the signed assertion exactly.

The transaction cookie is cleared on success and controlled failure.

## 4. Grade Checker application session

The Rust assertion is an input to session creation, not a browser-readable API
token. Grade Checker creates a Secure, HttpOnly, host-only session cookie with:

```text
Name: __Host-grade_checker_session
Secure: true
HttpOnly: true
Path: /
SameSite: Lax
```

The signed session envelope contains only the minimum controlled claims needed to
identify the Rust grant and enforce scope:

- device ID;
- grant ID;
- CRM role;
- franchise ID;
- permissions;
- session issued/expiry times.

It contains no CRM password, actor reference, private/public key, assertion,
authorization code, verifier, nonce, signature, or student data. Its expiry is
never later than the Rust grant expiry.

The 1,500-second Rust assertion is valid only for establishing a matching Grade
session. A valid offline signature does not override a revoked grant.

Fixed lifetime view:

| Artifact | Lifetime |
|---|---:|
| Grade auth transaction | 600 seconds |
| Rust device challenge | 60 seconds, one attempt |
| Rust authorization code | 60 seconds, single-use |
| Rust assertion | 1,500 seconds, clamped to grant expiry |
| Backing device grant | 28,800 seconds |
| Device registration | Durable until explicit revocation |

## 5. Immediate revocation through introspection

Every protected Grade Checker request performs a backend-to-Rust call:

```http
POST https://crm-auth.tutoringclub.com/v2/grants/introspect
Authorization: Basic <client credentials>

{
  "grant_id": "<grant UUID>",
  "device_id": "<device UUID>"
}
```

Rules:

- Do not cache an active response.
- Require the returned grant/device/role/franchise/permissions to match Session.
- `active=false`: clear the Grade session and begin a fresh auth flow.
- Rust timeout/unavailable: fail closed with a controlled 503 page; do not query
  private Grade data and do not redirect-loop.
- Introspection occurs before every route or data function that can expose
  protected student, dashboard, job, or scraper information.

Revocation becomes effective on the next protected request in every tab. An HTTP
request already executing is not retroactively recalled.

## 6. Authorization guards

Central guards enforce:

| Capability | Required permission |
|---|---|
| Dashboard and job views | `dashboard.read` |
| Student/grade views | `students.read` |

Every data access receives the trusted franchise from the validated session.
Routes, forms, JSON, query strings, cookies, and JavaScript cannot override it.
Role 3 has `students.read` only and cannot reach dashboard/jobs behavior.

Authentication and introspection complete before any private data lookup or
scraper job begins.

## 7. Grade Checker logout

```http
GET or POST /auth/logout
```

Logout clears the Grade Checker session and transaction cookies and redirects to
a fixed public destination. It never accepts an arbitrary return URL.

CRM logout revokes the Rust grant. Because every protected Grade request
introspects Rust, a stale Grade cookie becomes unusable immediately on the next
request. Grade logout alone may clear only the Grade session; durable device and
grant revocation remain CRM/Rust responsibilities.

## 8. Multiple devices and cleared storage

Grade Checker does not enumerate or choose devices. Each browser profile has an
independent key under `tutoraid.net` and therefore an independent Rust device ID.
Multiple devices for the same account and franchise are valid.

If CRM site data is cleared, the next CRM authorization creates a new device.
Grade Checker treats it as an ordinary new authorization and never attempts to
reuse the old device ID or public key. Old Grade sessions fail when their grant is
revoked/expired or when their device no longer proves possession during a future
authorization.

## 9. Fixed configuration

Replit Secrets/configuration provide:

```text
CRM_AUTH_BASE_URL=https://crm-auth.tutoringclub.com
CRM_AUTH_CLIENT_ID=grade-checker
CRM_AUTH_CLIENT_SECRET=<protected high-entropy value>
CRM_AUTH_ISSUER=https://crm-auth.tutoringclub.com
CRM_AUTH_AUDIENCE=grade-checker
CRM_AUTH_JWKS_URL=https://crm-auth.tutoringclub.com/.well-known/jwks.json
CRM_DEVICE_AUTHORIZE_URL=https://tutoraid.net/GradeCheckerDeviceAuthorize.aspx
GRADE_CHECKER_CALLBACK_URL=https://grades.tutoringclub.com/auth/callback
GRADE_CHECKER_COOKIE_SECRET=<protected high-entropy value>
AUTH_TRANSACTION_TTL_SECONDS=600
```

Production rejects non-HTTPS origins, wildcard callbacks, fragments, userinfo,
alternate issuer/audience/client ID, and callback mismatch. Secrets never appear
in source, logs, templates, JavaScript, error pages, or process arguments.

## 10. Failure behavior

- Missing/invalid Grade session: begin `/auth/start` once.
- CRM login required: CRM owns the login UI and fixed continuation.
- Callback state/code missing, duplicated, expired, or replayed: controlled auth
  error with no raw values.
- Rust redeem/introspection unavailable: controlled 503, no private data access.
- Invalid assertion or permission: controlled 403 and clear Session.
- Revoked/expired grant: clear Session and begin a fresh authorization flow.
- Insufficient permission: 403 without revealing whether protected data exists.
- Repeated failure must not create redirect loops; the error page offers an
  explicit retry action.

## 11. Logging and privacy

Allowed fields:

- request ID;
- controlled event/outcome;
- client ID;
- device ID;
- grant ID;
- role;
- franchise ID;
- HTTP status;
- timestamp.

Never log or persist authorization codes, PKCE verifiers, transaction cookies,
Rust assertions, Grade session cookies, client secrets, private/public keys,
nonces, signatures, raw request/response bodies, CRM credentials, or student data.

Templates use no-store responses and never embed authorization material beyond
the minimum callback handling performed server-side.

## 12. Migration from the current branch work

The current uncommitted authentication files are treated as work in progress and
must be reconciled rather than overwritten blindly.

The v2 design removes Grade-origin device key generation. In particular:

- `ui/static/device-auth.js` must not generate or store a private key;
- placeholder challenge/verify responses are replaced by real backend Rust calls;
- duplicate `state` query construction is eliminated;
- the callback requires both state and code and consumes its transaction;
- `RustAuthClient` uses exact v2 redeem and introspection contracts;
- route/data guards introspect before protected work;
- existing unrelated scraper/dashboard changes remain untouched.

No authentication implementation file is assumed disposable solely because it is
uncommitted; the implementation plan must review and adapt the branch's current
work file by file.

## 13. Testing without live database access

Agent-run tests must not query CRM, Neon, or PostgreSQL databases.

Required coverage uses Flask test clients, mocked Rust HTTP, deterministic keys,
and browser-contract/source checks:

- `/auth/start` produces one state, one S256 challenge, and fixed CRM destination;
- callback rejects missing, duplicate, wrong, expired, and replayed state/code;
- exact v2 redeem request and assertion response contract;
- Ed25519 signature/issuer/audience/time/claim validation;
- introspection on every protected route before data access;
- active claim mismatch, inactive grant, timeout, and unavailable behavior;
- role 2 versus role 3 permission enforcement;
- franchise cannot be overridden by any browser input;
- revoked grants clear the Grade session on the next request;
- cookies have exact Secure/HttpOnly/host-only/SameSite/Path attributes;
- logs/templates contain none of the forbidden secrets or artifacts;
- auth failure performs no private data or scraper operation;
- existing non-auth dashboard/scraper regression tests remain green.

Owner-run end-to-end tests cover real CRM, Rust, PostgreSQL, Replit Secrets, DNS,
TLS, and browser redirects after all unit/contract checks pass.

## 14. Acceptance criteria

1. Grade Checker never owns the CRM-origin private key.
2. Unauthenticated users redirect to CRM through state and S256 PKCE.
3. The callback accepts only the fixed one-time code flow.
4. Grade Checker validates Rust assertions and then introspects the grant.
5. Every protected request introspects without an active-result cache.
6. Grant revocation blocks the next protected request immediately.
7. Rust unavailability fails Grade Checker closed without affecting CRM.
8. Role and franchise always come from Rust-validated claims.
9. Multiple CRM-origin devices work independently.
10. Grade session cookies never outlive the backing grant.
11. No protected data is queried before auth and introspection succeed.
12. Agent-run verification performs no live database queries.
