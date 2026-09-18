# Production operator runbook

This is the live operator procedure. The dated [readiness report](PRODUCTION-READINESS-2026-09-07.md)
and the [CD unblock record](CD-UNBLOCK-2026-09-15.md) are archived incident
records; their pending fields are not evidence that anything is installed or
enabled. Related keeper runbooks:

- [Recovery pack](RECOVERY-PACK.md) — explicit encrypted recovery, beyond the scheduled backup
- [WhatsApp dependency switch](WHATSAPP-DEPENDENCY-SWITCH.md) — manual scoped node_modules replacement
- [Inbox network isolation](INBOX-NETWORK-ISOLATION.md) — connect-denial evidence and constraints for the dedicated inbox
- [Hermes MCP 2 field compatibility](HERMES-MCP2-COMPAT.md) — SDK field-name repair, dry-run-by-default
- [Dependency readiness](DEPENDENCY-READINESS.md) — npm-ci lock deploy procedure for whatsapp-connect

## Owner checks without business mutations

1. Sign in at `https://support.buttonsbebe.com/console/login`. Verify the existing
   console still renders and its APIs remain on port 8000.
2. Open `/inbox/`; confirm observed list/thread/rail or an honest empty/incomplete
   history state. A stale/error banner is not an empty mailbox. The latest
   projection may not be installed at the report cutoff; check runtime hashes.
3. Click Send only to verify the locked explanation. It must say exactly
   `Activate the send access.` Do not change any lock/bridge environment flags.
4. For receiving, return to `https://support.buttonsbebe.com:8443/` after the
   existing console login. A 401 before login is expected. Do not test a refund,
   return-processing, send, or unsigned provider webhook to prove availability.
5. After the monitor/webhook deployment, view `/console/api/ops`. Missing, stale,
   unavailable and attention are distinct failure states; an all-green summary
   proves only its listed checks, not successful provider transactions.

Loopback verification on the reviewed VPS (the first POST exits at the hardcoded
lock and the second must hit the disabled inbox bridge):

```sh
curl -sS -X POST http://127.0.0.1:8766/console/api/helpdesk \
  -H 'content-type: application/json' \
  -d '{"tool":"helpdesk.send_reply","arguments":{"ticketId":"deployment-lock-probe","text":"Hi","confirmed":true}}'
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  http://127.0.0.1:8766/webhook/gorgias
curl -fsS http://127.0.0.1:8766/ready
```

Expected: exact three-field Send lock JSON, bridge HTTP 503, and healthy `/ready`
only with usable storage and a fresh projection. Do not substitute the live
Gorgias receiver URL for the bridge test. Never paste session cookies or full
service/environment output into tickets or chat.

## If the inbox projection becomes stale

Check the sanitized operations summary and projection timer/service state. The
canonical receiver remains the source of truth; do not repair freshness by
editing timestamps, inserting examples, resetting the inbox database, or pointing
Gorgias at the inbox. Inspect exporter errors privately without copying customer
payloads. Verify root ownership, `root:bb-inbox` directory 0750/file 0640, source
hashes, free space and read-only access. Run the reviewed exporter oneshot only
after its source/schema compatibility is confirmed. A successful initial export
must precede starting a newly projection-aware inbox.

Do not interpret observed history as complete Gorgias history. Its 90-day,
100-message-per-ticket bounds and lineage withholding are deliberate.
See [local monitoring](LOCAL-MONITOR.md) for freshness thresholds and limitations.

## If inbox views show unknown status or an empty Assigned to me

That is the correct reading until the Gorgias HTTP Integration body carries the
ticket state fields and a ticket receives a new message event. Follow
[the Integration template note](GORGIAS-WEBHOOK-TEMPLATE.md); it covers the
exact keys to add, the presence check on the stored payload, and the operator
address the inbox compares against. Do not repair a view by editing the
projection, re-exporting older events, or pointing the inbox at Gorgias.

## Deployment and rollback

Follow [the deployment receiver contract](cd/README.md). Before any main push,
confirm the installed receiver **and** source helper match the reviewed release;
old full-root rollback can overwrite accepted data. Do not invoke it.

- Run the full offline gate and confined Linux recovery harness. Prepare hashed
  dependencies before an outage; never overwrite dependency receipts to bypass
  preparation. Check source-to-live drift and backward-compatible schema first.
- Apply Caddy/systemd manually after validation. Keep private backups of active
  files. Update both source-directory and actual applied-file approvals in
  `/etc/buttonsbebe-deploy-approved-config.sha256` only after verification.
  Redacted Caddy source is not a deployable credentials file.
- The reviewed receiver owns source files only. Its inventory journal excludes
  credentials, DB/WAL/SHM, venvs, WhatsApp sessions, KB corpora and live index.
  Hold its host lock and preserve which services/timers were already active.
- For projection-aware releases, pause the active timer, refuse an in-flight
  export, refresh a snapshot after canonical schema readiness, and require real
  inbox `/ready` plus the hardcoded Send lock. A failed export/readiness must
  trigger source recovery, never database reset.
- On ordinary failure the receiver restores journaled source and verifies prior
  service readiness. After power loss or a hard kill, traps cannot run: identify
  the exact journal and affected services, stop those services, use the reviewed
  helper's `rollback --journal PATH`, then restart/verify in documented order.
  Never restore the complete application directory or a stale database as a code
  rollback. Do not discard a journal reporting incomplete recovery.

For dedicated inbox runtime installation/recovery use the reviewed
`tools/ops/inbox_runtime.py` helper with the expected current unit SHA. It validates
prepared source/dependency receipts and keeps application state outside code.
Its `rollback --backup PATH` restores source/unit, not customer state. Review
schema compatibility before choosing a prior runtime. Preserve all original data
and credentials when investigating a failed installation.

For listener changes use [the containment runbook](LISTENER-CONTAINMENT.md).
A failed verification keeps the localhost binding; automatically restoring the
old public bind would reopen exposure. Fix startup/proxy continuity while
contained. Do not broadly restart PM2, change the firewall, or kill a reused PID.

## Encrypted backup and isolated restore verification

The scheduled job uses SQLite's backup API, a compressed archive, CMS
AES-256-GCM encryption and a public recipient certificate on the VPS. The private
recovery key remains off-host. The six-hour timer and local retention do not
substitute for off-host transfer or a restore drill. Preserve manual recovery
backups; scheduled pruning applies only to its own verified naming scheme.

Before restoring anything, establish a private workspace on a trusted recovery
machine with sufficient space. Keep the encrypted artifact, its receipt, the
trusted public certificate and the matching private key outside the repository.
Set `umask 077`. Validate the artifact SHA against the trusted recorded receipt.
Decrypt to a **new private temporary archive**, never into a live source/data
path. A template command, with privately selected paths, is:

```sh
openssl cms -decrypt -binary -inform DER \
  -in /PRIVATE/ENCRYPTED-SNAPSHOT.cms \
  -recip /PRIVATE/RECIPIENT-CERTIFICATE.pem \
  -inkey /PRIVATE/RECOVERY-KEY.pem \
  -out /PRIVATE/NEW-RESTORE-DIRECTORY/snapshot.tar.gz
```

Treat nonzero exit or authentication failure as failure, even if an output file
exists. Do not print archive/database contents. Inspect member metadata before
extracting: only regular `manifest.json`, `webhook.sqlite3`, and optional
`inbox.sqlite3` are expected; reject absolute paths, traversal, links or unexpected
members. Extract into the private temporary directory only. For each database:

1. Compare SHA256 to its manifest entry, without printing data rows.
2. Open the isolated copy and run `PRAGMA integrity_check`; require exactly `ok`.
3. Record only artifact/checksum match, database count, integrity result and time
   in a sanitized restore receipt. No message content, credentials or key paths.
4. Remove the temporary plaintext after recording proof, retaining the encrypted
   artifact and protected recovery material according to the retention plan.

A successful drill does not authorize replacing production. Real data recovery
needs a separate incident decision: identify the lost interval, stop all writers,
preserve current DB/WAL/SHM and source evidence, choose a compatible snapshot,
reconcile post-snapshot accepted events/actions, and define duplicate-send risk.
Never replay historical jobs or ambiguous human sends automatically. A code
rollback must not become an implicit database restore.

## Escalation and remaining decisions

The local monitor emits no external messages. Whole-host/network failure needs
an independently configured off-host monitor and approved notification route.
The WhatsApp heartbeat dead-man's switch (`buttonsbebe-heartbeat.timer`,
`deploy/HEARTBEAT-INSTALL.md`) is installable but not yet verified active on
the VPS — the readiness evidence does not establish that its unit and timer are
installed and enabled. Verify `systemctl is-enabled buttonsbebe-heartbeat.timer`
before counting on alert coverage. Even once live it shares the host/bridge
failure domain; do not count its transport acknowledgment as proof the owner
received an alert.

Keep the inbox Send lock until a separately reviewed activation decision verifies
real provider identity/routing, exact recipient/context binding, duplicate and
ambiguous-action handling, audit/recovery procedures and owner confirmation.
Passing this runbook's lock test is evidence that sending remains disabled.

Coordinate the exposed historical webhook credential with the existing Gorgias
integration before rotation. Coordinate authenticated receiving provider ingress
before allowing `/webhooks/redo` publicly. Do not reopen the state-changing
reconciliation GET simply because the old UI calls it. These are recorded
operational limitations, not permission to bypass controls.

The live Hermes brain loads its `SOUL.md` from its home directory,
`/root/.hermes/SOUL.md` — the file the process actually consumes. The
`/root/Buttonsbebe Agent/SOUL.md` checkout copy is a hand-managed mirror of
the repo's `hermes/SOUL.md` (CD does not deploy `hermes/`). The repo copy now
points the "single source of truth" at `AGENTS.md` (CLAUDE.md was merged into
it); at the next Hermes maintenance, apply the same one-line re-point to
**both** VPS copies — `/root/.hermes/SOUL.md` first, then the checkout mirror —
so the live brain is not left pointing at the deleted `CLAUDE.md`.
