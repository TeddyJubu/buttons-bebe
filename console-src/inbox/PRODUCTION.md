# Shared inbox data modules

Inbox is served at `/inbox/`. The retired `/inbox2/` page routes redirect there,
preserving ticket and view parameters; its former API and static assets return
HTTP 410. The original `helpdesk-inbox.service` remains stopped and masked. The
active backend keeps its established `helpdesk-inbox2` service and data paths so
the existing databases and projection timer remain in place.

This directory retains the projection reader/exporter, Shopify rail reader/exporter,
data migration utility and dependency locks required by Inbox and its existing
projection timer. Runtime databases and browser-local drafts are preserved.
See [Inbox](../inbox2/README.md) for the active application and deployment layout.
