# Isolated Inbox (E1–E3)

The isolated Inbox is available at `/inbox/` on the local intake server. It uses
the current production Inbox's copied shell, stylesheet, wordmark and Lucide
icons, with a dedicated offline client. Production assets and services remain
unchanged. `intake/inbox/provenance.json` records the original file hashes.

## Run and inspect

```bash
python3 -m intake --workspace inbox-review user-set --username reviewer --name 'Reviewer' --role admin
python3 -m intake --workspace inbox-review serve --port 8891
```

Open `http://127.0.0.1:8891/inbox/` on the workspace machine. For a remote
workspace, use an SSH local port forward as described in [README](README.md).
The Codex desktop browser cannot open a remote workspace's local file/loopback
address directly. No public route, tunnel or production deployment is configured.
Sign in with the local account just created. [TEAM](TEAM.md) explains roles,
account administration, unread state and handoff. Stop the test server with Ctrl-C.

The server starts with an empty workspace. Import the synthetic Gorgias fixture
with the preview/import commands in [README](README.md), or use **Import & proof
tools** to open the intake lab at `/` on the same server. Both screens use the
same database. The lab retains its export preview/import/history tools; attachment
files, offline replay and backup/restore remain explicit CLI operations.

Keep accepted proof workspaces unchanged. To review the saved real export, make a
fresh workspace with `python3 -m intake.rehearse` rather than reopening an older
proof for editing. Real customer content remains private; screenshots use only
synthetic data.

## Connected workflows

- List, search message text, select a queue, filter open/closed tickets and move
  through 50-ticket pages. URLs identify independent ticket IDs for reopening a
  conversation. All list/detail reads use the canonical `/api/tickets` endpoints.
- **New test ticket** creates a database ticket with a private description.
  **Save note** adds a durable internal note. Neither contacts a customer.
- **Ticket details** opens the context rail for status, priority and
  assignment. Changes persist in the database and are visible in a fresh browser;
  old production browser-storage keys cannot replace the stored ticket state.
- Revision checks reject stale edits. Unsaved notes and detail edits survive
  refresh and ticket navigation within the tab. Closing/reloading the page warns
  when these unsaved edits exist; they are not persisted to browser storage.
- Creation, notes and detail updates retain their operation ID within the tab
  after a lost response. Refresh and retry the unchanged values to inspect/recover
  that result without a duplicate write. A changed payload cannot replace a
  still-unconfirmed operation. There is no automatic mutation retry.
- **Simulate reply…** uses the existing D5 review, confirm and receipt ledger.
  Review freezes sender, recipient, context and reply text. Confirmation records
  only a fake delivery. Stale reviews fail; uncertain acceptance blocks another
  attempt; explicit reconciliation can recover known fake acceptance once.
- Imported historical replies, private notes, replay messages and simulated
  outgoing replies are labeled distinctly. Message bodies are assigned as text
  nodes, preserving stored line endings and keeping markup inert.
- Attachment references display metadata-only or private-copy availability.
  Files and remote images are never loaded or opened. Source references, recorded
  tags and activity appear in the context rail. [E3](ASSISTANCE.md) adds saved test
  context and fixture suggestions with explicit run/use/dismiss and stale-result
  checks. Real model generation and live lookups remain disabled. Local sign-in,
  permissions, named assignments and private read/unread state are in [E2](TEAM.md).

The page keeps the original three-column layout, mobile ticket navigation and
collapsible context rail. The note composer stays at the bottom. On short screens,
the ticket header and composer have bounded, independently scrollable areas so
the message area remains usable. Use **Refresh tickets** after edits from another
browser or CLI; no background provider polling or synchronization exists.

## Offline boundary

The dedicated client checks the server's offline capabilities before loading
tickets, uses only fixed same-origin `/api/` routes, refuses redirects and has no
provider URL or live activation option. The server retains loopback binding,
Host/Origin/token checks, no-store responses, a restrictive CSP and the outgoing
network/DNS/process guard. Its `/inbox/` assets use a fixed filename allowlist.

No production Gorgias sender, console authentication call, AI request, Shopify
worker, Google Font, remote image or external navigation is loaded by this copy.
The copied `styles.css`, `icons.js` and license remain byte-identical to their
recorded originals; `offline.css`, `app.js` and `client.js` own the adaptations.
Local identities and permissions apply to both screens. This does not reuse
production authentication or enable live use.

## Verification

```bash
bash intake/verify.sh
PLAYWRIGHT_MODULE=/path/to/playwright node intake/tests/browser-inbox.mjs
PLAYWRIGHT_MODULE=/path/to/playwright node intake/tests/browser.mjs
PLAYWRIGHT_MODULE=/path/to/playwright node intake/tests/browser-team.mjs
PLAYWRIGHT_MODULE=/path/to/playwright node intake/tests/browser-assistance.mjs
```

The Inbox browser suite starts an empty server, imports only synthetic saved
fixtures and tests creation, notes, field persistence in a fresh browser,
stale/lost responses, search, queues, pagination beyond 50 tickets, fake delivery,
HTML/CRLF handling, errors, short screens, drawer keyboard use and capability
refusal. It saves private synthetic screenshots and stops its server afterward.

For a read-only display proof using an existing fresh saved-export workspace:

```bash
PLAYWRIGHT_MODULE=/path/to/playwright node intake/tests/browser-inbox-saved.mjs WORKSPACE_NAME
```

This requires schema version 7, at most 50 saved tickets, no simulated deliveries
and no previous success report at that workspace. It compares every displayed
message's ID/text/author/email/time, ticket fields, attachment counts and reply
eligibility against the API. It provisions a local proof account with a random
password, signs in, then hashes every database table before/after viewing and
refuses further write requests. It produces no screenshots or raw customer output.
Results stay in the private `inbox-display-reconciliation.json`.

E1 evidence: **90 backend tests**, the lab and Inbox browser suites, and the saved
export's **50 tickets / 211 messages / 22 attachment references** all passed.
All stored rows were unchanged during the real-data display proof. The 46 eligible
and four withheld reply targets matched the existing offline rules. All prior
real-attachment, source-coverage and production-readiness gaps remain. E2 adds the local identity
and team proof described in TEAM.md.
