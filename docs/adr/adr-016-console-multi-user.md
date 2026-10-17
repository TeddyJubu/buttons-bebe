# ADR-016: Console multi-user accounts (admin / staff)

- Status: Accepted
- Date: 2026-10-17
- Scope: webhook console auth, Inbox Assigned-to-me operator identity,
  Caddy forward_auth header copy, console Settings user management

## 1. Context

Console authentication was a single configured owner
(`CONSOLE_USERNAME` + `CONSOLE_PASSWORD_HASH`). Sessions were already
revocable rows in `console_sessions`, and Inbox send grants were bound to
actor + session, but login and identity resolution still compared against one
env username and always returned `actor_role=owner`.

Assigned to me used one process env var
(`INBOX_OPERATOR_GORGIAS_EMAIL`) on the credential-free Inbox read API.
Staff cannot share one Gorgias identity without false positives.

## 2. Decision

### 2.1 Users table, env as bootstrap only

Add `console_users` in the existing webhook SQLite database. On startup, if
the table is empty and the env owner credentials are configured, insert that
account as `admin` (optional Gorgias email from `INBOX_OPERATOR_GORGIAS_EMAIL`).
After bootstrap, env credentials are a seed only; password and role changes
happen in the table.

Roles are exactly `admin` and `staff`. Actor IDs are `{role}:{username}`.
Role and disabled state are re-read on every authenticated request so
revocation and demotion take effect without changing the cookie format.

### 2.2 Privilege split (Inbox-first staff)

- Staff: Inbox UI/API and the existing Inbox send grant → confirm → send path.
- Admin: full `/console/` (KB, Notice Board, Connections/WhatsApp, Settings)
  plus user management APIs.
- Hermes, MCP tools, Shopify, and the Inbox read process remain without send
  authority. Safety model §2 is unchanged except that human senders may be
  staff, not only the former single owner.

`/auth/page-check` and `/auth/check` refuse staff for `/console` destinations
(except login). Staff attempting a console next-path after login are sent to
`/inbox/`.

### 2.3 Per-staff Assigned to me

Each user row stores `gorgias_email` (casefolded). Forward-auth responses
emit `X-Authenticated-Actor` and `X-Operator-Email`. Caddy copies those
headers to the Inbox upstream and strips Cookie plus client-supplied copies of
those headers. Inbox `list_tickets` / capabilities use the header when present
and keep the env var as a preview/fallback only.

### 2.4 Admin user management

Admin-only CRUD under `/dashboard/api/users` (list, create, update role/email,
disable, reset password). The last active admin cannot be disabled or demoted.
A thin Settings panel in the console SPA calls these routes.

## 3. Consequences

- Existing `owner:<username>` actor strings in tests and historical audit rows
  are superseded by `admin:` / `staff:`. In-flight send grants from the old
  format expire naturally (30 minutes).
- Caddy site fragments must be installed for header copy; repository sources
  alone do not change production routing.
- `.env` password rotation after bootstrap does not update the admin row;
  use the users API or a deliberate DB update.
- Same-origin XSS limitations documented in CONSOLE-AUTH-BOUNDARY.md remain.

## 4. Alternatives rejected

- External IdP / SSO: heavier ops; not needed for a small staff set.
- Baking role into the session cookie: would delay disable/demotion until
  expiry or force a token-format break.
- Client-side Assigned-to-me filtering: forgeable and wrong for totals.
- Giving Inbox the webhook DB path: breaks the credential-free Inbox boundary.
