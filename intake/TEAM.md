# Local users and team workflow (E2)

E2 adds real identity and permission checks **inside the offline sandbox**. It
has no connection to production sign-in, Gorgias, email, invitations or directory
services. All users in one workspace share its ticket history. Different
workspaces have separate identities, passwords, sessions and records.

## Set up accounts

Use a fresh test workspace, with Python 3.12+ from the repository root:

```bash
python3 -m intake --workspace team-review user-set --username alex --name 'Alex' --role admin
python3 -m intake --workspace team-review user-set --username sam --name 'Sam' --role agent
python3 -m intake --workspace team-review user-set --username taylor --name 'Taylor' --role viewer
python3 -m intake --workspace team-review serve --port 8891
```

Each `user-set` prompts twice for a sandbox-only password, hidden from the
terminal. Use 12–1024 characters. There are no seeded users or default passwords.
An unattended local test may use `--password-file /absolute/private/file` instead:
this must be a regular private file with no group/other permissions, outside
source control. Remove that input file after provisioning. Never pass passwords
as command arguments or reuse production credentials.

Open `http://127.0.0.1:8891/inbox/` locally, or through the existing SSH local port
forward instructions in [README](README.md). Sign in using the account just
created. Separate browser profiles can represent different teammates; tabs in
one browser profile share its current account. The lab at `/` requires the same
sign-in and enforces the same permissions.

The operating-system owner manages accounts with the CLI; there is no web user
administration endpoint. To change a role, name or active state while keeping its
password, use `--keep-password`. Every edit revokes that user's sessions:

```bash
python3 -m intake --workspace team-review user-set --username sam --name 'Sam' --role viewer --keep-password
python3 -m intake --workspace team-review user-set --username sam --name 'Sam' --role viewer --disable --keep-password
```

The last active admin cannot be disabled or demoted. Existing assignments to
unavailable users stay visible for reassignment. Re-enable an account by running
`user-set` without `--disable`. Omit `--keep-password` to reset its password.
Users can also select **Password** in the header and supply their current and new
passwords; a successful change signs out every session for that user.

## Permissions

| Action | Viewer | Agent | Admin |
| --- | --- | --- | --- |
| Read/search all workspace tickets and team members | Yes | Yes | Yes |
| Change own read/unread state | Yes | Yes | Yes |
| Create test tickets, save internal notes, change details | No | Yes | Yes |
| Claim, assign, hand off, unassign | No | Yes | Yes |
| Review/confirm a fake reply, inspect fake receipts | No | Yes | Yes |
| Run/use/dismiss an offline assistance fixture | No | Yes | Yes |
| Preview/import saved exports and read import history | No | No | Yes |
| Manage accounts, replay files, copy attachments, backup/restore | OS-owner CLI | OS-owner CLI | OS-owner CLI |
| Real intake, sending, provider reads, AI or notifications | Disabled | Disabled | Disabled |

Roles are enforced by the API and rechecked inside database transactions. A
request cannot select its own identity, audit name, assignee by arbitrary text,
or another user's unread state. There is no shared anonymous API token anymore.
An admin cannot confirm another teammate's frozen reply review. An authorized
teammate may reconcile an existing shared fake receipt; this creates no new
attempt. Reviews made by older anonymous/CLI workflows cannot be confirmed over
HTTP; create a new review after signing in.

E3 adds [saved context and assistance fixtures](ASSISTANCE.md). These use the same
roles and never call a model or provider.

All workspace members can read all its tickets; assignments organize work and
do not restrict visibility. Queue-specific access, custom roles, SSO, MFA,
invitations and production account recovery remain outside this local milestone.

## Working together

- **Team view** offers Everyone, Assigned to me, Unassigned and Unread for me.
  It combines with the existing queue, status and search filters.
- **Assign to me** claims an unassigned ticket atomically. Competing or stale
  claims fail. **Ticket details → Assigned to** selects an active local admin or
  agent, or Unassigned. A handoff is visible to other users after Refresh.
- Source Gorgias assignment remains separately visible and unchanged. Source
  names or IDs are never automatically matched to a local account.
- Ticket revision checks reject stale notes and detail/assignment changes.
  Assignment history and notes identify the verified local user. Audit records
  include the stable local user ID as well as a name snapshot. Operation IDs
  are scoped to that user, so one user's retry cannot reuse another's result.
- **Mark read / Mark unread** is explicit. Opening a ticket does not mark it read.
  Unread means messages have been stored since that user's last explicit mark,
  or that user manually marked it unread. All message kinds, including notes
  and simulated replies, count. Status/priority/assignment changes do not.
- A mark-read request includes the highest message sequence actually displayed.
  A later message remains unread even if it arrived during that request. Read
  updates also have their own per-user version, preventing an older tab from
  undoing a newer manual mark. Marking a ticket does not change its shared
  revision or anyone else's unread state.
- **Refresh tickets** shows other users' changes and CLI replay arrivals. There
  is no background synchronization, presence indicator, realtime push or alert.

## Session and recovery behavior

Passwords use a separate random salt and Python's scrypt (`N=32768`, `r=8`, `p=1`).
Session cookies are random, HttpOnly, SameSite=Strict and scoped to `/`; only
session-token hashes are stored in SQLite. Cookies are named per local port.
They are intentionally for loopback HTTP and lack Secure; production use requires
a separately designed TLS/session boundary. The per-session CSRF token stays in
page memory. Host, Origin and fetch-site validation also apply to login.

Sessions last eight hours and are bound to this workspace server process.
Restarting the server, editing an account, changing its password, disabling it or
logging out invalidates the relevant sessions. Five failed logins for one
username or twenty across the workspace within a minute temporarily block login.
Errors do not reveal whether a username exists. Local database/OS access is
trusted administration; browser roles do not restrict the OS owner.

Sign-out/account switching clears other open tabs through a same-origin browser
channel. Expiry clears the page on its timer; revoked sessions are rejected at
the next API call. Drafts and CSRF tokens are never persisted to browser storage.
A missing network response remains uncertain; the client does not resend writes
automatically.

Schema 5 adds local identity, session, assignment, read-state and review-owner
tables. Opening an older supported workspace performs an additive migration;
keep accepted evidence intact by using a fresh copy. Old schema-4 snapshots
remain verifiable/restorable with the frozen schema definition.

Backups contain password hashes, users, assignments and read watermarks, so they
remain private. Restore preserves those records, **deletes every session and
login throttle record**, and invalidates unconfirmed old reply reviews. Sign in
again in the restored workspace. No pending operation dispatches on restore.
This does not establish production identity, account recovery or disaster
recovery readiness.

## Verification

```bash
bash intake/verify.sh
PLAYWRIGHT_MODULE=/path/to/playwright node intake/tests/browser-team.mjs
PLAYWRIGHT_MODULE=/path/to/playwright node intake/tests/browser-inbox.mjs
PLAYWRIGHT_MODULE=/path/to/playwright node intake/tests/browser.mjs
```

The team browser test provisions three synthetic users through the same CLI,
uses separate browser contexts, and covers the role boundaries, read isolation,
claim race, handoff, stale watermark, cross-tab logout, revoked write and password
change. Screenshots contain only synthetic data at widths 320–1440px. Every test
server binds loopback and is stopped afterward.

A fresh saved-data proof first runs `intake.rehearse` and
`browser-inbox-saved.mjs`, then:

```bash
PLAYWRIGHT_MODULE=/path/to/playwright node intake/tests/browser-team-saved.mjs NEW_PROOF_WORKSPACE
```

This explicitly modifies only that proof copy's local users, read/assignment
state, audit and pending review records, then backs up and restores to a new
workspace. It preserves source/message rows, never confirms or dispatches its
pending review, makes no provider calls and takes no customer screenshots.
Its report contains aggregate counts, not customer identities.

E2 evidence on 2026-09-28: **102 backend tests** and all three synthetic browser
suites passed. A fresh saved-export copy passed **2,088 fidelity checks**, all
**50 tickets / 211 messages / 22 attachment references** in authenticated display,
and **150 per-user read checks** across three users. Source/message rows remained
unchanged; handoff and role boundaries passed; the restored copy had zero
sessions with users/read state preserved and old reviews invalidated. Full
artifact locations and retained gaps are in [the checklist](../INTAKE-TASKLIST.md).
