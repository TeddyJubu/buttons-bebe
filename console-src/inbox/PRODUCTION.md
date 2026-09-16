# Isolated inbox runtime

This service remains a read-only side room. The existing console and Gorgias
intake are the operational system. The inbox API exposes ticket reads, status,
a capability document, and an unconditionally locked Send response. It does not
expose intake, external providers, drafts, escalation, or handled-state writes.
Disabled capabilities are hidden in the interface and refused by the server.

## Runtime

Python 3.12 or later; install `requirements.lock` with `uv pip sync
--require-hashes` into a dedicated virtualenv. Top-level input is
`requirements.txt`; regenerate locks explicitly with `uv pip compile
--python-version 3.12 --generate-hashes`. Test dependencies have a separate
`requirements-test.lock` whose runtime pins must exactly match the runtime lock.
`run-review.sh` accepts `INBOX_PYTHON`, binds only 127.0.0.1, and defaults to port
8766. It forcibly disables outbound, bridge and Shopify mutations. The server
never loads `.env` files. Caddy must authenticate **every** inbox path, strip the
`/inbox` prefix, and proxy both static assets and `/console/api/helpdesk` to this
service. Never intercept the old console API to make this work.

Use a dedicated unprivileged service account, restrictive umask and a writable
`/var/lib/buttonsbebe-inbox` directory. `HELPDESK_DB_FILE` defaults to
`/var/lib/buttonsbebe-inbox/inbox.sqlite3`. Keep this directory outside code
releases and never replace it during rollback. Runtime code, dependencies and
`static-manifest.json` must be read-only to the service account. The manifest is
an explicit public-asset allowlist; fixtures, source Python and data are absent.

The ASGI server has bounded connection concurrency, body size and body-read
time. Exposed API tools perform local operations only. Static responses set
no-store, a restrictive CSP, nosniff and same-origin referrer policy. Logs omit
request contents and query strings. Caddy remains responsible for identity and
access control; a loopback connection is not an authenticated human identity.

## Legacy migration

Before changing the service account/runtime, inspect whether the old inbox data
directory contains ticket or seen JSON files. If present, stop the old inbox,
back up the originals securely, then run as an operator able to read that path:

```
python console-src/inbox/migrate_store.py \
  --tickets /absolute/old/data/intake_tickets.json \
  --seen /absolute/old/data/seen_messages.json \
  --database /var/lib/buttonsbebe-inbox/inbox.sqlite3
```

The target must not already exist. The importer validates state, commits it in
one SQLite transaction and retains the originals. Invalid/corrupt records fail
visibly, rather than being skipped or replaced with an empty inbox. A seen-only
legacy store is refused until reconciled. Change directory/database ownership
to the dedicated service account before starting the new runtime. When no
legacy state exists, startup creates an empty database; it never adds demos.
The server ignores legacy JSON environment settings, so it cannot accidentally
read stale root-owned files after migration.

## Durability and limits

SQLite stores a versioned whole-inbox snapshot. `BEGIN IMMEDIATE` serializes
read/modify/write operations across processes, reloads the committed state, and
commits tickets, deduplication IDs and the sequence together. Ticket reads and
readiness use a read-only SQLite connection and deferred read transaction; they
do not serialize state or acquire a writer lock. Their busy wait is bounded to
200ms, and local storage work runs outside the ASGI event loop. Failed operations
roll back and refresh the in-memory cache. WAL and FULL synchronization protect
committed state. Startup/readiness fail visibly on corruption. Existing local
workflow helpers now persist flags, but they are not exposed as production
capabilities because no corresponding owner/customer workflow is connected.

This is suitable for the current small isolated store, not a claim of unlimited
throughput: full snapshots scale with retained history. Before adding real
intake, define retention, measure representative state size and migrate to
normalized ticket/message/action tables if needed. Back up through SQLite's
backup API; do not copy a live database file without its transaction state.
Future real intake must use one outer `tickets.transaction()` covering both
remembering the message and creating its ticket. Do not expose individual
helpers as separate intake API operations.

## Verification

```
python -m unittest discover -s console-src/helpdesk-agent/tests
node --test console-src/inbox/test/*.test.js
# The server tests now run with inbox discovery (requirements-test.lock
# venv for httpx/fastapi):
python -m unittest discover -s console-src/inbox/tests -p 'test_*.py'
```

Tests cover restart persistence, operation/commit failure, simultaneous writers,
legacy/corrupt state, malformed JSON types, oversized bodies, static traversal
and symlinks, unsupported capabilities, UI failure behavior, and the Send lock.
The HTTP Send response must retain `send_access_inactive` and exactly
`Activate the send access.`. Inbox `/webhook/gorgias` must return 503.

## Canonical observed history projection

The inbox now reads a separate root-owned snapshot at
`/var/lib/buttonsbebe-inbox-projection/projection.sqlite3`. The existing writable
inbox store is preserved and never overwritten or merged into canonical records.
The operator must create the projection directory mode0750 root:bb-inbox before
starting the supplied projection service/timer. Published files are0640
root:bb-inbox. The inbox account must have read-only access to this directory;
do not put it inside its writable StateDirectory.

The root exporter opens the canonical webhook database read-only/query_only and
runs no network/provider calls. A consistent snapshot includes all observed tickets from90days,100 observed
messages each,20000characters per text field. The browser initially loads100
tickets and offers Load more in batches of100; there is no500-ticket cap.
Source SQL is interrupted after5seconds rather than holding a long read snapshot.
A temporary SQLite database is validated and fsynced before atomic replacement;
readers already using the old inode complete normally. Failure retains previous
data and creates a content-free error marker. Timer refresh is60seconds;
older-than180seconds or any failed export appears stale. Successful export clears
that marker. The service never receives owner cookies or credentials.

Messages are only the webhook history already observed by the canonical system,
not complete Gorgias history. Assignment/status/order context remain unknown.
The latest stored processor draft is displayed read-only and explicitly not sent.
All workflow mutations and Send remain disabled. No intake route is changed.
After staging these files, the operator must manually apply the units, inspect
export counts, verify permissions as bb-inbox, and update approved config hashes.

## Shopify customer and order panel

The separate `export_shop_rail.py` exporter reads Shopify through fixed GraphQL
queries and publishes `shop-rail.sqlite3` alongside the ticket projection. The
inbox only reads that local snapshot; its network restrictions and Send lock
remain in place. The exporter needs the existing app to have `read_customers`,
`read_orders`, and `read_returns`. Product-only access cannot populate this panel.
Older orders remain subject to Shopify's granted order-history access window.

The new `buttonsbebe-inbox-shop-rail.service` and timer are operator-installed
units. Install only after verifying the required Shopify permissions. Run as
root:bb-inbox; publish files as 0640 in the existing root:bb-inbox 0750 projection
directory. The service reads the existing root environment; credentials never
enter the inbox service or browser. It does not change any Shopify records.

Each run makes at most 25 GraphQL requests and prioritizes recent tickets.
The timer retries every minute, gradually filling older tickets. Successful
matches are cached for six hours, misses for thirty minutes; skipped or failed
refreshes retain their original timestamps. Order names must match exactly and
the order must match the ticket customer's email or Shopify customer ID.
Conflicting identities are not joined. Customer, order, returns, and history
render from one snapshot, with its refresh time and stale state visible.

2026-09-08 pre-install validation: the existing app returned `ACCESS_DENIED` for
both customer and order reads. Its granted scopes were `read_products` and
`write_products`. A private staged run produced no usable matches. The live
inbox and its service configuration were not changed by this validation.

The identified app is **Aside Catalog Reader**. Pending operator approval is
limited to adding `read_customers`, `read_orders`, and `read_returns`; no new
write scope is requested. Panel rendering and lookup regression tests passed.
All offline release checks passed on an isolated working-source snapshot, with
classifier parity and the final JavaScript suites completed separately after
attaching the original Git history required by the parity test. No commit or
push was made to the user's checkout, and no live service was installed.

2026-09-08 completion: the user approved those three read scopes. Released
`inbox-read-context-20260908` (version 1119418777601) for Aside Catalog Reader
and accepted the store's permission update. A fresh token confirmed all three
scopes. The bounded validation export loaded nine customers and nine orders,
including two with returns. Installed six inbox runtime files and the new
snapshot service/timer under the deployment lock with hash checks and rollback
backup `/opt/buttonsbebe/backups/shop-rail-20260907T220436Z`; recorded runtime
hashes and approved unit hashes. Browser verification showed the selected
order 10319148, customer profile, product images, shipment tracking, and open
return. Inbox readiness and webhook health passed; Send returned
`send_access_inactive`. The snapshot timer is active and warms further tickets
in bounded batches. Source changes remain uncommitted in this checkout.

### Customer rail UI update — 2026-09-08

The live Shopify snapshot rail now uses a compact customer profile, two-column
order/spend summary, active return context before the order, separate payment
and fulfillment badges, and tracking above products. Unfetched gift-card,
invoice, warranty and empty monetary rows are omitted. A return record without
line-item details is labelled as a return, not an inferred item count. Section
toggles keep keyboard focus and rail scroll position.

Installed five static UI files under the deployment lock; rollback copies are
at `/opt/buttonsbebe/backups/rail-ux-20260907T221653Z`. The source manifest records
the updated hashes. No Git commit or push was made. Validation: 141 inbox JS
tests passed, `git diff --check` passed, live inbox readiness and webhook health
returned 200, and the live browser rendered the populated rail without console
errors at 1117px. The browser viewport override did not change the actual width,
so additional responsive breakpoints were not verified. Send access remains
false and the Shopify snapshot timer remains active.


### Load more — 2026-09-09

Removed the 500-ticket export and API offset caps. The list initially loads100
and offers a persistent Load more control with loaded/total counts, busy state,
retry on failure, and an explicit end state. Each expansion re-reads the desired
prefix, retrying once if the snapshot generation changes; it never mixes pages
from different generations. Only the list repaints, preserving reply text and
the conversation pane. The 90-day window,100 messages per ticket,32MB export
size guard,5second SQL deadline and read-only source access remain in place.

Private production export succeeded with2979 tickets. Five runtime files were
installed under the deployment lock with backup
`/opt/buttonsbebe/backups/load-more-20260908T211328Z`; source-manifest hashes were
updated. Inbox readiness and webhook health returned200. The projection timer
was restarted.143 inbox JS tests and17 Python projection/Shopify tests passed;
no Git commit or push was made.
