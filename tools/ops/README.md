# Production operations: reviewed manual apply

These scripts never enable inbox Send, connect intake, or change Shopify/Gorgias
credentials. Root reviews and applies them on srv1766050. Keep the existing
console and Gorgias webhook active; do not run the retired full-root rollback.

## Active Inbox source recovery

The active Inbox is `helpdesk-inbox2.service` on localhost port 8767. Its public
route is https://support.buttonsbebe.com/inbox/. Shared projection and Shopify
modules remain under `console-src/inbox/`; they do not provide a second Inbox UI.

Use [the journaled source release and recovery procedure](../../deploy/cd/README.md)
for source changes on a provisioned host. It preserves application data,
prepared environments, approved configuration, and previously active services.
A fresh host needs separate reviewed dependency, identity, unit, and proxy setup.
The privileged receiver is installed separately from source deployment.

`inbox_runtime.py` targets retired Inbox 1. Every action refuses, including
rollback. Keep that service masked and preserve its backups and customer state.
Do not use an old runtime receipt to restore or start it.

## Caddy and conservative core restrictions

Materialize **only reviewed differences** into the existing live support
fragment; never overwrite secrets with redacted source placeholders.

- Inbox uses `handle /inbox/*` plus explicit `route`: authentication executes
  before stripping `/inbox`, then all active Inbox service routes reach port 8767. This keeps
  original URI/method/Origin available to auth and preserves `/console/api`.
- Historical `/qa` and `/qa/*` return404. Files remain on disk for recovery.
- Existing URI/Referer log redaction remains intact.
- Webhook/processor units add NoNewPrivileges, PrivateTmp and kernel protection,
  while preserving current root runtime paths and existing secret drop-ins.
  They remain privileged legacy components; this is not complete isolation.
- Heartbeat keeps its persistent state outside PrivateTmp. EnvironmentFile is
  intentionally unquoted: systemd's EnvironmentFile parser accepts spaces as
  part of this path and rejects surrounding quotes. Documentation `%` is escaped.

Validate the complete staged Caddy configuration on the actual Linux Caddy
version, then reload it. Apply core units during the coordinated release, not
mid-job. Preserve installed credential-bearing drop-ins. Run Linux
`systemd-analyze verify` and the included Caddy integration test before applying.

After **all** reviewed config changes are applied and verified, root updates the
`deploy/systemd` and `deploy/caddy` directory fingerprints in
`/etc/buttonsbebe-deploy-approved-config.sha256` using the receiver's exact
sorted SHA256 algorithm. Record hashes of actual applied unit/fragment files
separately. Never approve source config before its corresponding live changes
are applied. The CD receiver does not install these units or Caddy fragments.

## Encrypted backups and failure visibility

The backup service reads the existing public recipient certificate
`/etc/buttonsbebe-backup-recipient.pem`; **no private decryption key belongs on
this VPS**. It uses the SQLite backup API, validates each snapshot, encrypts the
archive with OpenSSL CMS AES-256-GCM after gzip compression, and removes temporary plaintext on normal
completion. The six-hour timer writes only its own encrypted files in
`/opt/buttonsbebe/backups/scheduled`; 14-day retention preserves at least three
verified snapshots and never prunes manually created backups.

Before enabling the timer, manually run the service once and decrypt/restore a
copy off-host using the owner's existing protected key. Check integrity and
manifest SHA256 values. Root already has the intended public cert installed;
do not generate a replacement recipient or invent an off-host upload endpoint.
The timer creates local encrypted copies only. Off-host copies must use the
separately reviewed destination/transport and must be observed for freshness.

`/var/lib/buttonsbebe/backup-status.json` records status, last attempt, last
success and only an error class. Unit failure is nonzero and appears in the
journal. Alert on failed status or last success older than eight hours; a
healthy processor does not establish healthy backups. An off-host monitor is
still required to detect complete VPS/network failure. A machine crash can
leave a private `.incomplete-*` directory; inspect/recover it under root rather
than treating it as a completed encrypted backup.

To stop scheduling, disable the backup timer; leave all existing backups and
keys intact. A backup is never automatically restored by deployment or by this
job. `sqlite_backup.py` is also available for one-off private snapshots.

## Listener containment inventory

`listener_inventory.py` reports selected ports, loopback binding, PID, known
service name, and whether an unmanaged Python preview has a deleted working
directory. It never prints command lines, environment variables or token paths.

Observed before remediation: Hermes9119 is owned by
`buttonsbebe-hermes-dashboard.service`; exchange4100 by `exchange-proxy.service`;
Python8099 is an unmanaged deleted-directory preview; redo3210 is separate and
unmanaged by systemd. Bind Hermes/exchange only after reviewing their exact
launch code and preserving Caddy reachability. Revalidate PID/start identity
before stopping the stray preview. Do not blindly stop redo3210: determine its
login/API protection and legitimate consumers first. No listener is stopped or
firewall changed by the inventory tool.

## Receiving service containment (port 3210)

The reviewed receiving process was externally reachable without a session.
Source inspection found unguarded financial POST handlers and a public aggregate
GET confirmed the API boundary, without reading customer bodies or invoking a
mutation. This is a separate legacy application; its business code is not moved
into this repository.

The new `sites/receiving.caddy` origin is
`https://support.buttonsbebe.com:8443`. It preserves all root-relative assets and
APIs. `/auth/session` checks the existing console cookie without expanding the
console's trusted Origin list. Caddy rejects unsafe methods unless Origin is
exactly that 8443 origin, **before authentication**. Missing/expired sessions get
401 JSON for APIs and a friendly sign-in link for pages. Sign in at the existing
443 console, then return to receiving; no new credential or login form exists.

`GET/HEAD /api/reconciliation` is blocked because its recomputation changes
persistent reconciliation classifications. It needs an explicit reviewed POST
workflow before re-enabling through this origin. Other inspected GET handlers
read records, aggregate statistics, labels or configuration; `snapshotFees` is a
read operation despite its name. No provider financial mutation was found in a
GET handler. This does not certify the underlying app's entire business logic.

1. Install the reviewed receiving fragment and add its import while retaining
   **all existing imports**. Validate/reload Caddy; verify existing 443 HTTPS and
   HTTP-to-443 redirect behavior as well as the new8443 listener.
2. Prove unauthenticated API401, unauthenticated page401 with sign-in link,
   rejected missing/cross-origin writes, and an authenticated **aggregate-only**
   GET before changing port3210. Use a private root-owned cookie file; never
   print it or put its value in a command argument.
3. Record the named PM2 process, current PID/start ticks, and server.js SHA256
   without dumping PM2's environment. Invoke `contain_receiving.py apply` with
   `--process-name`, `--expected-pid`, `--expected-start-ticks`,
   `--expected-source-sha256`, and `--proxy-cookie-file`. It changes exactly the
   single `app.listen` host to127.0.0.1, validates syntax, and restarts only the
   named process. It omits `--update-env`, preserving the existing environment.
4. Independently verify external3210 cannot connect, authenticated8443 still
   works, all other services remain active, and no source/credential/data file
   other than this one bind argument changed. No financial POST is an acceptance
   test. Update applied Caddy fingerprints after successful review/verification.

The previous `/webhooks/redo` handler used an optional secret that was not
configured in the inspected runtime/environment file. There is **no public
exception** for this unverified webhook on the new origin. Provider signing and
callback coordination are required before introducing a public route; do not
weaken the session/origin gate to restore unauthenticated input. Existing
Gorgias `/webhook/gorgias/*` routing is unrelated and remains unchanged.

A dated root-only source backup and exact hashes are recorded by the helper.
It deliberately does not automatically reopen the public listener after a failed
post-restart probe. Restore operation with `--acknowledge-public-reopen` is only
for a separately reviewed recovery with alternative network containment, because
restoring the old bind would otherwise recreate the known unauthenticated
financial exposure. Refuse stale rollback when source has subsequently changed.

### Temporary owner rewrite maintenance gate

`rewrite_maintenance.py install|remove --expected-sha256 SHA` is a manual root-only
helper for a coordinated Hermes maintenance window. It only changes the imported
support Caddy fragment. It does not stop producers, patch Hermes, change the
approved deploy-config fingerprints, or touch webhook intake, login, console
pages, customer sends, or application data. Pause deployment/configuration work
while this temporary gate exists; remove it before normal deployment resumes.

Review the SHA of the effective `/etc/caddy/sites/support.caddy` target first.
The installed layout uses a `sites` directory symlink; the helper resolves the
exact target and requires the supported four-import entrypoint plus unique site
and console-API anchors. Both support and srv hostnames share that site block.
The gate matches only `/console/api/ticket/<one-path-segment>/rewrite` and its trailing
slash form, returning 503 with Retry-After. Public `/dashboard` remains governed
by its existing deny routes; there is no new alias or authentication bypass.

The helper creates a new private backup for each operation, validates a private
copy of the complete imported configuration and the rollback configuration,
rechecks all source bytes, then atomically replaces the fragment and reloads
Caddy. A reload failure revalidates/restores/reloads the prior source. Failed
rollback is an operator incident; do not assume the previous active state. Raw
Caddy output is never printed because it can include private configuration.
Successful output contains only backup location and source fingerprints. Remove
requires the current installed SHA and the exact unmodified maintenance block.

For a Hermes cache maintenance window, separately inventory and quiesce the
processor, Hermes dashboard, WhatsApp bridge, root **user** Hermes gateway, and
any QA run; drain already-running webhook rewrite children before modifying
Hermes. Keep webhook intake and owner console pages running. Restore only the
previously active producers and remove the gate after compatibility checks.
The helper deliberately does none of these service operations automatically.

### Production Hermes discovery proof during maintenance

`verify_live_mcp.py --profile-sha256 REVIEWED_SHA` is a manual root-only proof
for the installed `/usr/local/lib/hermes-agent`, lexical
`/usr/local/lib/hermes-agent/venv/bin/python`, and actual `/root/.hermes` profile.
Run only after the coordinated producer pause, reviewed MCP adapter patch,
three-entry cache invalidation, and local MCP service update. This helper does
not pause, patch, invalidate, change trust, or call a model. Discovery itself
rebuilds Hermes' normal schema cache; do not run against the live cache before
that maintenance boundary. Read the profile hash privately immediately before
running; its contents are never printed.

Prerequisites are fail-closed hashes embedded in the helper: patched
`tools/mcp_tool.py`, installed `model_tools.py`, installed `mcp_schema_cache.py`,
and the repository's reviewed bounded process helper. Source/profile hashes are
rechecked after every subprocess. The actual profile must have exactly the
three Buttons Bebe MCP groups at `127.0.0.1:8077/8078/8079/mcp`, no commands or
disabled groups. No test profile or QA fixture module is imported.

Three separate processes (each at most 90 seconds plus bounded cleanup) list
actual endpoint metadata, discover through installed Hermes, then prove a fresh
process registers all three groups through Hermes' cache-registration function.
Every phase requires exactly ten tools, true readonly hints and nonempty object
schemas. Schema comparison permits only the reviewed string/null-union to
`nullable: true` normalization; all other fields remain exact. Socket audit and
HTTP guards permit only local MCP initialization/listing, never `tools/call`,
model requests, or subprocess execution within discovery children. No model or
commerce credential is inherited. Raw child output remains private in bounded
memory; public output is counts, booleans and fingerprints only. Failed proof
means keep producers paused for review, not weaken the schema or trust checks.
