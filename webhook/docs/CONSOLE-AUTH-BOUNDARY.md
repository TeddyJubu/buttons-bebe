# Console authentication boundary

The configured owner account and password verifier are unchanged. There is no
new customer login or invented staff account. This change makes owner sessions revocable and enforces console access
inside FastAPI as well as at Caddy.

## Route inventory

| Routes | Caller and authorization |
| --- | --- |
| `GET /dashboard/api/messages`, `/stats`, `/tickets`, `/notifications`, `/learning` | Signed and registered owner session |
| `POST /dashboard/api/notifications/read` | Owner session and trusted Origin |
| `POST /dashboard/api/inbox/send-access` | Owner session and trusted Origin; explicit boolean creates/revokes a page-scoped grant; no provider call |
| `POST /dashboard/api/inbox/ticket/{ticket_id}/send` | Owner session, trusted Origin and valid grant for this session; exact review and confirmation before the existing durable console sender |
| `POST /dashboard/api/ticket/{ticket_id}/send`, `/note`, `/rewrite` | Owner session and trusted Origin; action handler still owns confirmation, idempotency, ticket and content validation |
| `POST /dashboard/api/results` | Processor only: direct loopback, no Origin or Forwarded/X-Forwarded headers; never exposed as a browser action |
| `POST /auth/login`, `/auth/logout` | Trusted Origin; login verifies existing owner credentials, logout durably revokes the presented session |
| `GET /auth/session`, `/auth/check`, `/auth/page-check` | Registry-backed session validation; proxy checks also validate Origin for original unsafe methods |
| `GET /health`, `/ready` | Public liveness/readiness; aggregate diagnostics only, no record contents or provider calls |
| `POST /webhook/gorgias/{tenant_id}` | Existing signature/tenant/rate-limit controls unchanged |

The only repository consumer of the internal results endpoint is the processor's
loopback HTTP post. All other dashboard endpoints are owner-facing. The
middleware does not trust an `X-Authenticated-Actor` request header. It sets
`request.state.actor_id`, `actor_role`, and `session_id` from server-validated
claims and registry metadata. Owner actor IDs are `owner:<configured username>`;
the session ID is a SHA256 digest, not the bearer cookie. The producer identity
is `processor`, not a human. Action handlers must never accept a browser-supplied
actor as authority.

## Proxy and CSRF contract

Caddy continues to rewrite `/console/api/*` to the existing internal namespace.
It must retain `Origin` and set `X-Forwarded-Method` and `X-Forwarded-Uri` on its
forward-auth subrequest (the standard Caddy `forward_auth` behavior). The auth
subrequest itself is a GET, so unsafe-method checks use its original-method
header. The actual dashboard middleware always checks its own method.

Only the exact HTTPS origins `support.buttonsbebe.com` and
`srv1766050.hstgr.cloud` are accepted for owner mutations. Host and arbitrary
Forwarded headers do not extend this allowlist. Missing/foreign/null Origin is
rejected; existing browser POST fetches send Origin automatically. Non-browser
administrative callers must deliberately supply the trusted origin as well as
an authorized session. The processor endpoint instead rejects all Origin and
proxy headers. Its loopback boundary assumes application ports are not exposed
through another public proxy.

For unauthenticated inbox API requests, `/auth/page-check` returns JSON 401.
Page requests redirect to login with a validated relative `/console` or `/inbox`
return path. Traversal, external origins, control characters, backslashes and
ambiguous encodings are refused. The login page uses the same route vocabulary.
Authenticated API/auth responses are marked no-store.

## Session migration and revocation

New logins issue v2 HMAC-signed cookies with independent random nonces. The
registry must contain the token digest, owner, original expiry and non-revoked
state. A correctly signed but unregistered v2 cookie is rejected. Existing v1
cookies are accepted only if their signature, canonical encoding, configured
username and original expiry remain valid. Their first use registers the token
with that same expiry; it never extends the session. Canonical base64 checks
prevent alternate token spellings from evading a revocation tombstone.

Logout registers an unseen valid v1 token if necessary and marks its registry
row revoked. `INSERT OR IGNORE` never overwrites a revoked row. A database error
returns 503 and does not claim successful logout or merely erase the browser
cookie. Expired tokens fail cryptographic validation before registration. Keep
revocation rows at least until their expiration; ordinary code rollback must
not roll back this table or session state.

`console_sessions` is an additive table in the existing SQLite database, created
at application startup through the Database helper. No existing table or owner
credential is replaced. Registry lookup failure denies access with 503. This
still represents one configured owner account: it does not identify which
person used shared credentials. Per-staff accounts and least-privilege roles
remain separate work requiring actual owner account definitions.

## Readiness and limits

Readiness performs a real local query and validates critical schema columns.
It reports aggregate pending/processing/failed counts and oldest-job ages.
Missing/unreadable/corrupt databases and missing schema return 503 without
exposing database paths or exception text. Probes do not call a model or an
external provider. Queue-age diagnostics support monitoring, but do not by
themselves certify end-to-end draft quality or operator notification delivery.

Same-origin inbox/console browser authority remains a limitation. Server-side
sessions, Origin checks, the inbox capability allowlist and CSP reduce risk;
they do not make two paths into separate origins or eliminate same-origin XSS.
The owner-authorized Inbox reply flow additionally requires an explicit switch,
a short-lived session-bound grant, and per-reply confirmation. The Inbox service
and its tool invocations have no send authority. The same-origin limitation still
applies: the switch is an additional human-action gate, not an XSS sandbox.

Password verification runs in two dedicated spawned process workers. A global
nonblocking capacity guard refuses additional work with 429 instead of queuing
waiters or hashing on the webhook event loop. Worker completion releases the
capacity even if the HTTP caller disconnects; cancellation cannot create an
unbounded background backlog. Process isolation also covers legacy bcrypt/crypt
implementations that do not release Python's GIL. The existing per-client rate
limit and bounded login request size remain in place. Verification exceptions
return 503 without printing credentials. Production Python 3.12 was checked to
provide `crypt` and BLOWFISH support without reading credential files; a future
Python 3.13+ migration must explicitly preserve bcrypt compatibility.
