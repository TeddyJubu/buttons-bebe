> Historical Inbox1 evidence. The service is retired and masked. Do not execute its setup or rollback instructions. Use [active source recovery](cd/README.md) and [Inbox](../console-src/inbox2/README.md).

# Inbox connection isolation

The dedicated inbox reads local SQLite snapshots and files. All outbound integrations are disabled. It does not require outbound TCP or UNIX connections. The `helpdesk-inbox` unit denies the `connect` syscall with EPERM; bind/listen/accept and writes on accepted Caddy sockets remain allowed. Existing localhost address restrictions remain in place.

This closes the gap where a compromised inbox process could connect to other local services using the shared loopback network. It does not make the existing same-origin browser deployment a separate security origin, or defend against a kernel compromise. Do not weaken this restriction to add integrations; route future capabilities through separately authenticated, scoped services after review.

A disposable systemd 255 transient unit ran as bb-inbox with the actual installed ASGI runtime and root-owned synthetic projection on 2026-09-07. No production state or live service unit was changed. Evidence:

- TCP connect to a harmless loopback canary: EPERM; zero accepted canary connections.
- UNIX socket connect: EPERM.
- asyncio-compatible socketpair: passed.
- Actual ASGI static page: HTTP 200.
- Readiness with synthetic SQLite state and projection: HTTP 200.
- Projected ticket list: empty synthetic list.
- Send response: `{"ok":false,"error":"send_access_inactive","message":"Activate the send access."}`.
- Transient unit stopped and all synthetic files removed.

Apply manually through the existing approved-config fingerprint procedure; CD does not install this unit. After applying, repeat static/readiness/locked-Send checks and inspect the effective unit properties. Future runtime changes need the same acceptance and connection-denial checks.
