# Architecture and file map

This describes the active source layout in the September 2026 checkout. Confirm the current code and AGENTS.md before making a change.

## Public routes and processes

| Public route | Owner | Internal route or source | Purpose |
| --- | --- | --- | --- |
| / | Caddy | Redirect to /console/ | Entry point. |
| /console/login | Caddy static | console-src/login.html | Public sign-in bootstrap. |
| /console/ | Caddy static after page auth | console-src/index.html | Operations console: draft review, human actions, KB, notices, notifications. |
| /console/api/* | Caddy forward auth | FastAPI :8000 /dashboard/api/* | Console data and human action API. |
| /console/kbapi/*, /console/waapi/* | Caddy forward auth | KB admin :8087, WhatsApp :8085 | Console's specialized APIs. |
| /inbox/ | Caddy static after page auth | /var/www/inbox2/ from console-src/inbox2 | Current ticket workspace. |
| /inbox/api/helpdesk | Caddy forward auth | helpdesk-inbox2 :8767 | Read-only ticket operations. Caddy strips Cookie and Authorization before proxying. |
| /inbox2/ | Caddy | Redirect page requests to /inbox/; old assets/API return 410 | Old public URL only. |
| /webhook/gorgias/* | Caddy | Webhook FastAPI :8000 | Inbound provider webhook. |

deploy/caddy/sites/support.caddy is the route source. deploy/systemd/helpdesk-inbox2.service runs the Inbox API as bb-inbox, localhost-only. deploy/systemd/buttonsbebe-inbox2-shop.service runs the separate Shopify worker. The backend retains inbox2 service and storage names despite the public /inbox/ rename.

## Ticket read path

    Gorgias
      -> read-only Gorgias MCP (:8079, tools/gorgias_mcp.py)
      -> console-src/inbox2/live_api.py (:8767)
           -> local SQLite summary cache /var/lib/buttonsbebe-inbox2/
           -> opened-ticket detail and messages
           -> draft projection (shared console-src/inbox/projection.py)
           -> customer rail snapshot (customer_details.py)
      -> authenticated /inbox/api/helpdesk
      -> console-src/inbox2/app.js

The API allows helpdesk.capabilities, helpdesk.list_tickets, helpdesk.get_ticket, and helpdesk.get_messages. It selects only list_inbox_tickets, get_ticket, and get_ticket_messages MCP tools. The browser needs no provider credentials. Ticket summaries are synchronized locally; opened tickets can fetch current detail. The composer and draft dismissal are browser-local. The owner can explicitly enable manual replies for one page and confirm each reply through the separate authenticated console sender; the Inbox service stays read-only. app.js currently renders status, priority, and assignee as observed read-only values.

Customer context crosses a separate trust boundary: customer_details.py queues a bounded lookup derived from the opened ticket; shop_worker.py uses fixed queries in console-src/inbox/export_shop_rail.py, reads protected Shopify configuration, and atomically publishes read-only snapshots under /var/lib/buttonsbebe-inbox2-shop/. The bb-inbox API reads those snapshots; it does not possess Shopify credentials.

## AI draft and human action path

    Gorgias webhook -> webhook/src/bb_webhook/ -> webhook/data/webhook.db job_queue
      -> processor/orchestrator.py -> processor/hermes_runner/
      -> read-only Gorgias, Redo, KB MCP tools (:8079, :8078, :8077)
      -> result in webhook DB -> console ticket feed / projection export
      -> human reviews in /console/ -> explicit Send, Note, or Rewrite via :8000

Hermes produces drafts; it has no Gorgias write tool. Sensitive work is marked for human review and owner alerting. The console's explicit send/note endpoints, session auth, revision checks, and audit trail live under webhook/src/bb_webhook/routers/ and console_actions.py. Rewrite returns text for review. See root AGENTS.md and webhook/docs/CONSOLE-AUTH-BOUNDARY.md before changing that boundary.

The KB source lives in kb/; kb/scripts/ builds and queries its LanceDB index. kb-admin/server.js owns console KB editing. The Notice Board is an immediate override layer. Runtime DBs, credentials, KB index, generated lessons, and customer data are outside the versioned release inventory. deploy/cd/source_release.py lists deployable source files.

## Repository component index

| Path | Responsibility and reason it exists |
| --- | --- |
| console-src/inbox2/ | Current standalone Inbox page, read-only API, customer request queue, and Shopify snapshot worker. |
| console-src/inbox/ | Shared projection and Shopify rail readers/exporters plus their pinned Python runtime; retained after the old Inbox page was removed. |
| console-src/index.html, login.html, support-theme.css, brand/ | Console SPA, sign-in page, and visual system. Generated style embeds preserve the single-file console deployment. |
| webhook/src/bb_webhook/ | Inbound Gorgias webhook, SQLite job/result records, signed console sessions, and console API/action handlers. |
| processor/ and hermes/ | Job polling, bounded Hermes execution, draft extraction/cleaning, and the assistant's read-only behavior/configuration templates. |
| tools/ | Read-only Gorgias and Redo MCP services, release checks, and operational utilities. |
| kb/ and kb-admin/ | Support policies/search corpus and index code; separate authenticated editor API. Corpus edits are data work, not ordinary code deployment. |
| whatsapp-connect/ | Owner alerts and pairing bridge; separate Node service and session state. |
| feedback/ | PII masking and legacy feedback compatibility around the learning path. |
| testing/ | Scenario fixtures, live-model rubric, and test harnesses for draft behavior. |
| deploy/ and .github/workflows/ | Caddy/systemd contracts, sealed CD receiver, release checks, and auto-deploy trigger. |
| console-src/helpdesk-agent/, gorgias-webhook/, teddy/ | Historical or demo code; inspect AGENTS.md and deployment inventory before treating any of it as live. |

## Common routing mistakes

- console-src/inbox/ contains shared projection and Shopify helpers; its old page/server were retired. Do not revive them to fix /inbox/.
- console-src/inbox2/ is the current UI and API source despite the public URL's lack of 2.
- console-src/index.html is the console, not Inbox. Its ticket feed and the Inbox show related data through different read paths.
- Root README.md, INCONSISTENCIES.md, and DEV-ISSUES.md are older background. For present behavior, prefer root AGENTS.md, current code, console-src/inbox2/README.md, and deploy/cd/README.md.
