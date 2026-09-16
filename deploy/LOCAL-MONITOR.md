# Local operational visibility

The webhook's public `/health` is process liveness. Its `/ready` checks local
DB/schema/configuration and queue diagnostics; it intentionally makes no provider
or model calls. Neither endpoint establishes that the entire draft pipeline is
healthy. Inbox `/ready` additionally requires its stored data and fresh read-only
projection. Keep those distinctions when configuring uptime checks.

`tools/ops/monitor.py` adds a separate read-only operations view. It checks critical
service/timer activity, localhost MCP/WhatsApp/KB-admin socket availability,
webhook/inbox readiness, processor loop progress, disk headroom, and encrypted
backup freshness. TCP availability is only a listener check, not a successful
provider API or KB search. The monitor does not use credentials, load `.env`,
write application databases, run models, or send alerts/messages.

Processor progress requires a real INFO `Processor idle heartbeat` or `Job
completed` record in the last ten minutes. Startup/error output does not count.
An idle heartbeat confirms the loop is polling; completed jobs confirm durable
results, not owner receipt or customer delivery. A pending/processing queue older
than thirty minutes needs attention. Missing counts are unavailable, never zero.
Current failed-job totals remain in webhook diagnostics for operator review;
the monitor does not replay or clear them.

The backup timer runs every six hours; missing, failed, corrupt, future-dated, or
older-than-eight-hour backup status needs attention. This verifies the last local
backup job reported success. It does not prove off-host retention or a successful
restore. Keep scheduled restore drills and off-host recovery material separate.
Disk attention begins below 1GiB free or 5% available on `/var/lib`.

Root installs the reviewed script and `buttonsbebe-monitor.service`/`.timer`
manually, validates units, reloads systemd, starts the service once, and then
enables the timer after inspecting its result. No deploy receiver implicitly
copies units. The timer runs every minute; checks have short independent timeouts
and the service has a forty-second bound. Status is atomic/private at
`/var/lib/buttonsbebe/ops-status.json`; failed checks exit nonzero so systemd
records failure. No automatic repair is performed.

The owner can read the summary after console login at `/console/api/ops`.
Dashboard session middleware protects the backing `/dashboard/api/ops`; responses
are not cached. The endpoint accepts only predefined check names/status values,
returns no raw errors or file paths, and marks snapshots older than three minutes
stale. A missing/corrupt status file is explicit, never a healthy empty result.

This provides **local visibility only**. A dead VPS/network or disabled monitor
cannot notify anyone by itself; the authenticated summary will be unreachable or
stale. `processor/heartbeat.sh` is the **live** dead-man's switch
(`buttonsbebe-heartbeat.timer`, every 5 min; see `deploy/HEARTBEAT-INSTALL.md`)
that alerts the owner's WhatsApp when the processor hangs or dies. It shares
the same host failure domain as this monitor, and transport acceptance is not
proof of owner receipt. Off-host monitoring and a reviewed notification
destination remain an operational follow-up.

Runtime secret boundaries improved: inbox runs as its dedicated account with no
root environment, owner cookies/Authorization stripped after proxy auth, and a
root-owned read-only projection. Processor/rewrite child environments explicitly
allow model variables rather than copying commerce/session credentials. Core
services and Hermes still retain root identity/home access; environment filtering
is not filesystem isolation. Moving those services requires a measured separate
runtime/credential migration, not an untested overnight identity change.
