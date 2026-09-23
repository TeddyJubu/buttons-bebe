 # Ticket feed vs Inbox parity map

 Date: 2026-09-23. Prod checked via ssh chaim: webhook :8000 ready, inbox :8766 ready, projection timer active.

 ## What you see

 Both surfaces show the same Gorgias tickets, so they look like duplicates. They are not the same pipe.

 - Ticket feed is /console/ tab tickets from console-src/index.html. Reads GET /console/api/tickets rewritten to /dashboard/api/tickets on :8000. Joins parsed messages with job queue and processor results including live draft text, job status, owner alert state.
 - Inbox is /inbox/ from console-src/inbox/index.html served by console-src/inbox/review_server.py on :8766. Reads only the exported snapshot via POST /console/api/helpdesk after Caddy strips /inbox. Data comes from /var/lib/buttonsbebe-inbox-projection/projection.sqlite3 plus shop-rail.sqlite3. Local workflow store /var/lib/buttonsbebe-inbox/inbox.sqlite3 stays tiny and is never merged into canonical records.
 - Console nav links out to /inbox/. Inbox nav links back to /console/. That cross-link makes the duplication obvious.

 ## File by file

 - List: ticketsView plus row in console-src/index.html vs js/boot.js, js/inbox.js, js/view-model.js, js/tissues/list.js in console-src/inbox. Console filters by job and risk. Inbox views All, Mine, Unassigned, Closed and search over observed summary fields.
 - Detail: console detail pane in row shows customer message, editable draft textarea, Request edit, Send reply, Draft as internal note, learning checkbox, Check last action status. Inbox thread js/tissues/thread.js plus composer.js shows observed messages and the latest stored processor draft read-only with Send locked to Activate the send access.
 - Draft write path: processor on :8000 writes ticket_results via POST /dashboard/api/results. Inbox never writes drafts. CAPABILITIES in review_server.py keeps draftReply, summarizeThread, macros, escalate, intake false. Only list, get, capabilities, projection status, write gate status, bridge status are exposed.
 - Send and note path: routers/console.py POST /dashboard/api/ticket/id/send, note, rewrite with confirm, idempotency operation id, Gorgias write, learning lesson. Inbox helpdesk.send_reply is refused before schema validation even with extra args. /webhook/gorgias on :8766 returns 503 bridge disabled.
 - Learning loop: webhook/src/bb_webhook/learning.py writes lesson files on every console action, nightly promotion to indexed exemplars. Inbox has no learning write.
 - Customer and order rail: console has no Shopify rail. Inbox rail js/tissues/customer.js, order.js, returns.js, order-history.js renders from local shop-rail.sqlite3 written by root-owned export_shop_rail.py through fixed GraphQL. Inbox never calls Shopify and never loads credentials.
 - Auth: /console/api uses forward auth check on :8000 and proxies to :8000 with identity. /inbox uses forward auth page check on :8000, strips prefix, proxies to :8766 with Cookie and Authorization removed. Loopback alone is not identity.
 - Deploy: console files deploy to /var/www/console from console-src/index.html and login.html. Inbox deploys to /opt/buttonsbebe/inbox/console-src from console-src/inbox plus console-src/helpdesk-agent per deploy/cd/source_release.py. DB directories stay outside releases and are never reset on rollback.
 - Ops: console Overview polls :8000 ops summary including ticket processing, inbox history, backup checks, connections, storage. Inbox readiness checks local store plus projection staleness older than 180 seconds.

 ## What must not break

 - Human-gated Send, Note, Rewrite on :8000 with confirm click, source draft revision check, unresolved action guard, and audit trail.
 - Processor ingestion, job queue states pending, processing, done, failed, owner WhatsApp alerts, 48-scenario gate in testing/scenarios.json.
 - Inbox locks: no .env loading, Shopify mutations off, outbound off, bridge off, static allowlist static-manifest.json, bounded body, no-store plus CSP, logs omit request contents.
 - Projection exporter behavior: 90-day observed tickets, 100 messages each, 60-second timer, atomic replace, stale marker on failure, never calls Gorgias, never decides who me is except via INBOX_OPERATOR_GORGIAS_EMAIL.
 - Caddy separation: inbox /console/api/helpdesk must never hit dashboard /console/api. Direct /dashboard stays 404.

 ## Safe swap plan

 1. Keep legacy feed default. Add default-off inbox preview inside the Ticket feed tab as an iframe to /inbox/. No Caddy change, no new routes, no write path change.
 2. Verify reads match: same ticket appears in both, counts differ only by window and job states. Projection stale shows stale, never empty as healthy.
 3. Verify writes still work only from legacy detail: Send confirm, Note staff-only, Rewrite returns text, learning checkbox only after confirmed delivery.
 4. Run offline gate bash tools/verify_release.sh, inbox node tests, inbox python tests, plus local run-review.sh on 127.0.0.1:8766.
 5. Only then hide legacy list behind the flag default. Keep /dashboard/api/tickets for ops and rollback. Remove old render code last.

 ## How to use the preview in this checkout

 - Open Ticket feed. Toggle Legacy feed versus Inbox preview. Choice persists in bb-ticket-feed-v1. ?feed=inbox forces inbox once. ?feed=legacy forces legacy once.
 - Preview is read-only. Send, Note, Rewrite, learning approval remain in Legacy feed until cutover.
- Rollback is clearing the toggle. No data migration.

## Phase 1 shipped 2026-09-23: rewrite via parent, dry-run harness

- Inbox draft strips show Regenerate plus an instruction box only when embedded in the console preview. Standalone inbox is unchanged and locked.
- Clicking Regenerate posts a same-origin intent to the console page. The console page maps the inbox ticket to its feed row and calls the existing authed rewrite door. The inbox never calls it directly and holds no session.
- Rewrite never sends anything by design on the server. The dry-run dispatcher defaults on: send and note kinds are refused in code with no network call, so later phases cannot accidentally post.
- Proven in headless Chrome against the real shipped files: console toggle, preview frame, handshake, button, instruction, parent mapping, rewrite fetch, fresh draft rendered in the strip. Standalone inbox still shows no Regenerate button.
- Local manual try: Ticket feed, Inbox preview, open the demo ticket, type an instruction, Regenerate. With no local processor backend the preview shows the loop working end to end up to the rewrite call.
 - Rollback: clear the preview toggle. No data migration, no new routes, no new secrets.

## Send and close 2026-09-23: local close only after confirmed delivery

- Send and close is back in the preview. Its confirm names the close and states plainly that Gorgias stays as-is and only this inbox view changes.
- The ticket is marked closed in the first-party browser store only after the server confirms delivery. Dry runs, failures, and ticket switches never close anything.
- Proven in the browser loop with a store check that dry runs leave local state untouched.
- Rollback: clear the preview toggle. No data migration, no new routes, no new secrets.

## Phase 3 shipped 2026-09-23: customer send via parent, dry-run by default

- The composer Send button works inside the console preview. Clicking opens a confirm showing the exact recipient and the exact reply text, plus a learning checkbox that defaults off. The reply box locks while confirming so the text cannot drift.
- On confirm the console page maps the ticket, proves the draft is current with a hash check, mints a one-time operation id, and calls the existing send door with the learning choice. Known server refusals surface as plain guidance, including draft-changed, missing recipient, and unresolved previous action.
- Live sending arms only with ?live=1 in the console address bar. Without it every send stops after validation and reports dry run. Standalone inbox keeps both buttons locked exactly as before, and Send and close stays legacy-only in this phase.
- Nothing was sent anywhere during tests. The browser loop proved rewrite plus note plus send dry-run end to end, including recipient display and learning default-off.
- Rollback: clear the preview toggle. No data migration, no new routes, no new secrets.

## Phase 2 shipped 2026-09-23: internal note via parent, dry-run by default

- The draft strip shows Post as note only inside the console preview. Clicking opens an inline confirm with the exact text and a staff-only warning. Confirm posts through the console page, never from the inbox service.
- The console page maps the ticket, proves the draft is current with a hash check, mints a one-time operation id, and calls the existing note door. Live posting arms only with ?live=1 in the console address bar. Without it every note stops at validation and reports dry run.
- Nothing was sent anywhere during tests. The browser loop proved rewrite plus note dry-run end to end, and the standalone inbox still shows no note or regenerate controls.
- Rollback: clear the preview toggle. No data migration, no new routes, no new secrets.
