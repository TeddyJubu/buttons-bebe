# Shared inbox data modules

Inbox 1 was retired on 25 September 2026. Its interface, browser modules, server,
launcher and systemd unit were removed. `/inbox`, `/inbox/` and `/inbox/index.html`
redirect to `/inbox2/`, preserving ticket and view parameters. Other legacy inbox
routes return HTTP 410. The old `helpdesk-inbox.service` is stopped and masked.

This directory retains the projection reader/exporter, Shopify rail reader/exporter,
data migration utility and dependency locks required by Inbox 2 and its existing
projection timer. Runtime databases and browser-local drafts are preserved.
See [Inbox 2](../inbox2/README.md) for the active application and deployment layout.
