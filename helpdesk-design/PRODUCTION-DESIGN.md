> **Historical design snapshot:** This file describes the earlier Inbox 1
> interface and review server. It is not the active Inbox specification; use
> `AGENTS.md` and the current `console-src/inbox2/` sources for present behavior.

# Buttons Bebe support interface

The inbox, support console (including its KB, notification and settings views),
and login share the inbox's warm neutral palette, IBM Plex typography, 6–8px
corners, dark primary actions, and restrained terracotta accents. The existing
console remains the operational console; its actions, API and authentication
are unchanged. It links to the separate `/inbox/` room.

`console-src/support-theme.css` is the shared source. Run
`python3 tools/build_support_theme.py` after editing it. The generated inline
styles allow the existing single-file console deployment to keep working.
`--check` checks that all three pages match the source.

The production review server forces `HELPDESK_PRODUCTION=1`. It starts without
seed tickets, never probes the test shop, and does not supply fixture orders,
mailbox messages, generated demo drafts or macros. Browser API failures show
an unavailable state. Existing persisted intake is preserved. Test fixtures
remain for offline tests but are not loaded by the production browser and
are not served by the preview server. No persisted inbox tickets existed at
cleanup time; there was no customer data to delete.

The new inbox is not connected to the production Gorgias intake. Customer
support continues in the original console. Connecting a real intake or order
provider is separate work, not implied by removing demo data.

Send remains hardcoded off and returns `Activate the send access.` Shopify
mutations, outbound email and the Gorgias bridge remain disabled.

## Live apply

Back up `/var/www/console/index.html`, `/var/www/console/login.html`, and the
specific inbox source files before applying. Copy only changed files into
`/root/Buttonsbebe Agent/` and the two HTML pages into `/var/www/console/`.
Restart only `helpdesk-inbox`. Do not run the full deployment receiver for
this focused change: the PR24 receiver was already rolling back when this
work began. Keep the authenticated `/inbox/*` Caddy route on port 8766 and
all existing routes unchanged. Record applied file hashes and retain backups.
