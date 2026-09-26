# Inbox

A support workspace at `/inbox/`, styled with the tokens and component rules in
`DESIGN.md`. It starts read-only on every page load. Reply drafts stay in the
browser. The owner can switch Gorgias to Read & write and review/confirm a
customer reply directly from the Inbox through the authenticated console sender.

## Manual replies

The header switch calls `/console/api/inbox/send-access` to obtain a 30-minute,
session-bound grant held only in page memory. SQLite stores only its hash;
switching off revokes it. Neither toggling nor preparing a reply calls Gorgias.
Reloading, signing out, or expiry returns the page to read-only.

Send reply opens a review with the recipient, customer message and exact reply.
Confirm & send calls `/console/api/inbox/ticket/{id}/send`, which checks the grant,
review identity and confirmation before using the console's durable sender.
The latest customer message must already be synced to the console database;
an AI draft is optional. The provider rechecks recipient and source message
before posting on the customer's existing channel. Status/assignment, ticket
creation and Shopify remain read-only. No background process acquires send access.

Pending or uncertain delivery keeps the draft and blocks a new reply. Check status
uses the existing read-only action-status route and never resends. Confirmed
success alone clears the matching submitted draft; newer edits are preserved.
The additive `inbox_send_grants` table is created on first enable. Expired grants
are cleaned up on subsequent enable; grants and action records are runtime data.

## AI edits

The pencil icon beside Use draft opens an instruction field for revising the
suggested reply. It calls the existing authenticated
`/console/api/ticket/{id}/rewrite` action with the current suggestion, operator
instructions and source message ID. It works while Gorgias is read-only and
never sends a message or enables send access.

Revisions stay in this browser, tied to the exact source suggestion. A new or
superseded suggestion stops using that revision. Cancelled requests and responses
for changed tickets cannot replace the current suggestion. The reply composer
is preserved; choose Use draft after reviewing the updated suggestion.

## Data flow

- `live_api.py` synchronizes Gorgias ticket summaries and retrieves opened tickets
  through the local, read-only Gorgias MCP. Provider credentials are not available
  to this process. Existing inbox projection helpers supply draft context.
- Gorgias webhooks remain the primary Hermes trigger. The processor also scans
  recently updated open tickets through the same read-only MCP and queues the
  latest unanswered public customer message if webhook intake missed it. This
  bounded, idempotent recovery keeps suggestions available during a webhook
  delivery outage; it never sends a customer reply.
- `customer_details.py` records bounded requests for the customer context of
  opened tickets. It uses a separate SQLite database with DELETE journaling so
  the worker can read it through a read-only mount without creating WAL sidecars.
- `shop_worker.py` uses the existing fixed Shopify queries in
  `../inbox/export_shop_rail.py` and publishes atomic, read-only rail snapshots.
  It has no HTTP listener. Successes and misses have separate cache lifetimes;
  failed reads back off before retrying.
- `app.js` refreshes customer context independently of the conversation and reply
  editor. Loading, missing-customer and retry states remain visible. Order history
  can load even when the ticket has no linked order number.

Customer requests are generated from the opened Gorgias ticket, not from arbitrary
browser-supplied customer identifiers. Runtime databases and credentials stay
outside this source tree.

## Installation layout

The checked-in systemd units live in `deploy/systemd/` and the authenticated routes
are in `deploy/caddy/sites/support.caddy`. The release inventory deploys the
Inbox Python service and its allowlisted web assets to their separate roots.
The previous interface and service were retired. This Inbox now uses `/inbox/`
as its canonical route. Bookmarks for `/inbox2/` redirect to
`/inbox/` with query parameters preserved; its old API and asset URLs return
HTTP 410. The shared modules in `../inbox/` remain in use for draft projection
and Shopify context.

The deployed layout uses:

| Source | Installed location |
| --- | --- |
| `index.html`, `app.js`, `styles.css`, `icons.js`, `lucide-LICENSE.txt` | `/var/www/inbox2/` |
| `live_api.py`, `customer_details.py`, `shop_worker.py` | `/opt/buttonsbebe/inbox2/` |
| Shared modules in `../inbox/` | `/opt/buttonsbebe/inbox/console-src/inbox/` |
| Existing Python environment | `/opt/buttonsbebe/inbox/venv/` |
| API state and customer request queue | `/var/lib/buttonsbebe-inbox2/` |
| Worker snapshots | `/var/lib/buttonsbebe-inbox2-shop/` |

`helpdesk-inbox2.service` runs as `bb-inbox` with loopback-only networking.
`buttonsbebe-inbox2-shop.service` uses the existing protected Shopify environment
file and requires read access to the API request database. Its state directory is
readable by `bb-inbox`. Install the units together with their source files, the
Gorgias `list_inbox_tickets` tool, the corrected shared Shopify query and the login
redirect changes. Validate Caddy configuration before reloading it. Keep deployment
backups outside the repository.

## Verification

Run `bash tools/verify_release.sh` from the repository root with the required Python
environments available. It includes the Inbox backend tests and JS syntax checks.
For focused checks:

```sh
python -m unittest discover -s console-src/inbox2/tests -p 'test_*.py'
node --check console-src/inbox2/app.js
node --check console-src/inbox2/icons.js
```

Browser tests use synthetic customers and intercept API calls. Start the
project skill's local preview helper to route /inbox/ to the active assets and
supply a synthetic read-only API:

    python3 skills/buttonsbebe-support-webapp/scripts/serve_inbox_preview.py --port 8878

Then run node console-src/inbox2/tests/layout.mjs and
node console-src/inbox2/tests/customer-loading.mjs. Run
node console-src/inbox2/tests/rewrite-draft.mjs for mocked AI editing, retry,
cancellation and stale-response handling. Run
node console-src/inbox2/tests/send-access.mjs for the mocked manual-send flow and
`PYTHONPATH=webhook/src python -m unittest webhook.test_inbox_send_access` for
server authorization. These tests do not send real customer messages. Install Playwright and its
Chromium browser first, or set PLAYWRIGHT_MODULE to an existing Playwright
module. Screenshots are saved to temporary directories, never the repository.
The plain console-src static-server command no longer works for /inbox/ because
the retired console-src/inbox/index.html was removed.
