# Franchise 57 Agenda Reliability Design

## Goal

Make every agenda-capable franchise 57 portal produce either a validated agenda or a controlled, non-destructive failure, without accessing SMSS during diagnosis and without treating ambiguous pages as successful empty agendas.

## Confirmed live behavior

- Neon contains three tracked franchise 57 students and six configured portal slots. Four slots are agenda-capable: two Google Classroom, one ParentVUE, and one Canvas.
- The first Google Classroom account delegates authentication to the student's configured GPS portal. The agenda worker currently omits both the canonical pictograph answers and the opposite slot's GPS username. The Google and GPS usernames differ.
- The second Google Classroom account can remain on an `accounts.google.com` challenge. The current login method swallows that timeout and incorrectly proceeds to agenda navigation. A prior successful snapshot contained 14 assignments, so this is a login/readiness regression rather than evidence of an empty agenda.
- ParentVUE reaches an authenticated gradebook whose `#gb-assignments` container contains a `.no-data` marker. Its zero-assignment result is valid for this account, but the parser currently also returns zero for ambiguous shells and malformed candidate rows.
- Canvas begins on an approved `*.instructure.com` tenant, traverses an HTTPS identity-provider route to Microsoft, and must return to a positively identified Canvas page. The current login wrapper collapses navigation, trust, timeout, and cleanup failures into a generic credential rejection. Agenda API calls use the configured entry origin rather than the verified final Canvas origin.
- The canonical `students_grades_20262027.auth_answers` value is empty for the GPS-delegated account, while one legacy Neon `student_auth` row contains three answers. The user authorized a one-time canonical sync. Runtime fallback to legacy authentication data is explicitly out of scope.

## Data boundary

- Do not connect to SMSS, CRM, or SQL Server during implementation or validation.
- Neon inspection is read-only except for one authorized canonical update.
- Legacy `student.id` and canonical `crmstudentid` have no relational key. The authorized one-time mapping therefore requires two independently unique proofs: exactly one tracked franchise 57 GPS→Google source with one populated legacy authentication row, and exactly one canonical CRM target appearing in franchise 57 agenda job history with `agenda2_google_classroom_failed`. The update must strictly parse exactly three bounded nonempty answers, require the canonical JSON array to still be empty, retain the franchise/job-history predicate in the write, update exactly one canonical row, and commit atomically.
- Because the source and target cannot be joined relationally, the transaction report and a post-write read-only audit must preserve the uniqueness evidence for both sets. No runtime code may infer or repeat this compatibility mapping.
- Do not print student IDs, names, usernames, passwords, authentication answers, portal hostnames, URLs, HTML, cookies, tokens, screenshots, traces, or assignment content.
- The production runner continues to receive credentials and canonical authentication configuration through the existing grade-db boundary. No runtime legacy-table fallback is added.

## Agenda collection boundary

`_collect_slot` will use the same 15-second action and navigation timeouts as the ordinary grade runner. It will pass a defensive, validated copy of canonical `auth_images` only to Google Classroom, whose approved GPS delegation needs it. It will pass the opposite portal slot as alternate configuration so a slot-2 Google account can use slot-1 GPS credentials after an origin-bound redirect.

The alternate configuration is not a general credential fallback. A portal may use it only after proving that the current normalized HTTPS origin equals the configured alternate origin and that the detected portal key matches the alternate configuration.

Cleanup errors must not replace a successful collection or its primary collection error. Page and context cleanup are best effort and expose only a bounded cleanup status to diagnostics.

## Google Classroom

Google login succeeds only when the exact normalized origin is `https://classroom.google.com` and the authenticated Classroom main-menu control is visible. A normal Classroom URL is matched with an exact-host predicate rather than the current literal Playwright glob.

If the flow redirects to the exact configured alternate GPS origin, it may delegate once using the alternate GPS username/password and canonical authentication answers. Unknown origins, mismatched alternate origins, loops, and missing alternate configuration fail closed before alternate credentials are submitted.

Remaining on `accounts.google.com`, CAPTCHA, MFA/account challenge, or ambiguous post-submit timeout is a controlled login failure. Whole-login retry after password submission is not allowed. Agenda navigation waits explicitly for Main Menu, To-do, Assigned, and Missing controls with bounded portal-local timeouts; it does not use one-second probes followed by assertions.

## ParentVUE

An empty ParentVUE agenda is valid only when the authenticated gradebook-scoped `#gb-assignments .no-data` marker is visible and no assignment candidates are present. Zero candidates without that marker is an ambiguous failure. Candidate rows with zero accepted records are also a failure.

Course lookup walks through intermediate `.gb-class-row` ancestors until it finds a course-bearing section, fixing the nested-row case. The authenticated workflow waits for either a no-data marker or assignment candidates before parsing.

## Canvas

Canvas changes stay local to `CanvasEngine`; shared universal login helpers are not modified.

The configured entry URL and every main-frame authentication hop must be normalized HTTPS URLs with no userinfo and default port. Route policy is selected by the exact configured Canvas tenant and contains explicit entry, transit, identity-provider, and return hosts. Hostnames are never approved by substring and are never learned from a redirect.

Only pre-submit navigation and login-surface readiness may retry. Credential submission occurs at most once. Recognized rejection maps to sanitized `LoginError`; timeouts and trust failures retain distinct internal types and never mark credentials bad.

Canvas login success requires both an approved final `*.instructure.com` return host and positive Canvas DOM evidence. Negative URL heuristics are removed. The verified final origin is frozen on the engine and is the only origin allowed for agenda API and pagination requests.

## Diagnostics

External failure codes and atomic snapshot preservation remain unchanged. Internal diagnostics use a dedicated helper with an allowlist of bounded enums and counts: portal, slot, phase, mapped exception type, retry attempt, approved host class, candidate count, accepted count, and cleanup status. They never include exception messages, tracebacks, URLs, credentials, authentication answers, HTML, or assignment data.

## Verification

- Test-driven unit coverage for credential ownership, alternate-origin delegation, Google challenge handling, ParentVUE tri-state parsing, Canvas route policy, verified API origin, cleanup isolation, and secret redaction.
- Existing agenda atomic-failure behavior remains unchanged.
- Targeted portal and boundary tests run before the broader non-live suite. The two previously acknowledged unrelated failing tests are not blockers.
- Live validation reads franchise 57 credentials from Neon in a read-only transaction, runs the four agenda-capable slots sequentially with output limited to portal/status/counts, and performs no agenda or job writes.
- Completion requires: ParentVUE validated explicit empty; both Google Classroom agendas authenticated and collected; Canvas authenticated and collected from its verified final origin.
