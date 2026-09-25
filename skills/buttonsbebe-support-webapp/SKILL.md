---
name: buttonsbebe-support-webapp
description: Run, inspect, test, or edit the Buttons Bebe support webapp at support.buttonsbebe.com (/inbox/ and /console/) and its backing services in the buttons-bebe repository. Use for this support app, not the storefront or unrelated operations apps.
---

# Buttons Bebe support webapp

Use this skill for the authenticated support Inbox, console, and the services that supply their ticket data and AI drafts. The Inbox read path, console human-action path, and release path have different owners and permissions.

## Locate the source and establish the current state

- In this installation, the source checkout is /root/buttonsbebe-inbox2-commit-20260925. In another workspace, find the repository containing AGENTS.md, console-src/inbox2/live_api.py, and deploy/cd/source_release.py; run commands from that root.
- Read the current root AGENTS.md before editing. It records operational constraints and may change. This skill is a route map, not authority over newer code or deployment state. Check git status --short --branch and the current files before assuming a branch matches production.
- Root README.md describes an older design. Some historical notes in AGENTS.md still mention a deleted console-src/inbox/run-review.sh. For the active Inbox, use console-src/inbox2/README.md, console-src/inbox/PRODUCTION.md, and the files named below.

## Find the right owner

| Task | Start here | Why |
| --- | --- | --- |
| Inbox page, responsive layout, local composer | console-src/inbox2/index.html, app.js, styles.css, icons.js, DESIGN.md | These are the public /inbox/ assets. |
| Inbox ticket reads and Gorgias sync | console-src/inbox2/live_api.py, tools/gorgias_mcp.py | The local API accepts four named read operations; the MCP owns provider reads. |
| Customer and order rail | console-src/inbox2/customer_details.py, shop_worker.py, console-src/inbox/export_shop_rail.py, shop_rail.py | A separate worker reads Shopify and publishes a snapshot; the Inbox process has no Shopify credentials. |
| AI draft shown in Inbox | console-src/inbox/projection.py, export_projection.py, processor/, webhook/ | The Inbox reads a snapshot; the processor and webhook own draft production and persistence. |
| Console UI, sign-in, theme | console-src/index.html, login.html, support-theme.css, tools/build_support_theme.py, tools/sync_console_brand.py | The console is a separate SPA, and some theme styles are generated into its HTML. |
| Console authentication and human actions | webhook/src/bb_webhook/routers/, console_auth.py, console_actions.py | Caddy authenticates public routes before the FastAPI console API handles them. |
| Routes, units, releases | deploy/caddy/sites/support.caddy, deploy/systemd/, deploy/cd/ | These define the production boundary and allowlisted release inventory. |

Read [architecture](references/architecture.md) for data flow and the reason these parts are separated. Read [development](references/development.md) before running or changing the app. Read [release and operations](references/release.md) before a production-facing change.

## Working rules

- Preserve the current Inbox's read-only provider boundary. Its reply editor is local browser state; it does not send to Gorgias. Only the authenticated console's explicit human action flow may send or post a note. Do not give Hermes, Inbox, or the Shopify rail an implicit provider write path.
- Use synthetic data for a local UI preview: run python3 skills/buttonsbebe-support-webapp/scripts/serve_inbox_preview.py, then open http://127.0.0.1:8878/inbox/. The helper binds loopback, serves the current Inbox assets, and answers its API with one synthetic ticket. It never calls production. For real data or full-stack operation, follow the production/service docs rather than supplying secrets to this preview.
- Match verification to the files changed. For an Inbox UI change, check JS syntax and inspect the page in a browser; for API/worker changes, run focused Python tests; before a production release, run the repository's offline gate. Commands and prerequisites are in the references.
- A successful push to main triggers production deployment after CI verification. Treat a push/merge to main as a release, and use deploy/cd/README.md for its prerequisites. The skill itself grants no deployment or provider-write permission.
