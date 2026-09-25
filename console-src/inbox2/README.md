# Inbox

A read-only support workspace at `/inbox/`, styled with the tokens and component
rules in `DESIGN.md`. The browser reads tickets through an authenticated local API.
Reply text stays in the browser; this implementation does not send replies.

## Data flow

- `live_api.py` synchronizes Gorgias ticket summaries and retrieves opened tickets
  through the local, read-only Gorgias MCP. Provider credentials are not available
  to this process. Existing inbox projection helpers supply draft context.
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

Browser tests use synthetic customers and intercept API calls. Start a static
server with `python3 -m http.server 8878 --bind 127.0.0.1 --directory console-src`,
then run `node console-src/inbox2/tests/layout.mjs` and
`node console-src/inbox2/tests/customer-loading.mjs`. Install Playwright and its
Chromium browser first, or set `PLAYWRIGHT_MODULE` to an existing Playwright module.
Screenshots are saved to temporary directories, never the repository.
