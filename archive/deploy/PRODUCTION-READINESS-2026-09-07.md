> Archived incident record (2026-09-07 readiness review). Point-in-time
> evidence; the live procedure is PRODUCTION-OPERATOR-RUNBOOK.md.

# Production readiness review — 7 September 2026

**Draft operator handover, awaiting root review and final deployment evidence.**
This is not clearance to activate inbox Send. Evidence cutoff for live state is
6 September 2026, 22:22 UTC; source review includes integration `56ab459` and the
monitor follow-ups `72f18e5` / `abcf435`. The root reviewer must update the final
release SHA, applied configuration fingerprints, verification outcomes and soak
results before calling this the completed handover. Later source commits do not,
by themselves, establish a production deployment.

## Owner entry points and safety boundary

| Entry point | Purpose and current boundary |
|---|---|
| `https://support.buttonsbebe.com/console/` | Existing owner support console; preserved. Existing human action paths are distinct from the locked inbox. |
| `https://support.buttonsbebe.com/inbox/` | Separate inbox; existing console login, localhost backend, dedicated service identity. Send remains locked. |
| `https://support.buttonsbebe.com:8443/` | Receiving workspace; sign in at the existing console first, then return. Existing root-relative receiving assets/APIs stay on this origin. |
| `/console/api/ops` | Authenticated operations summary in reviewed source; requires monitor installation and webhook deployment before availability. |

The exact inbox Send response remains:

```json
{"ok":false,"error":"send_access_inactive","message":"Activate the send access."}
```

`SEND_ACCESS_ENABLED` remains hardcoded false. `SHOPIFY_MUTATIONS_ENABLED`,
`HELPDESK_OUTBOUND_ENABLED`, and `GORGIAS_BRIDGE_ENABLED` remain zero. No live
financial/send mutation was used to validate receiving containment or inbox QA.
Gorgias stays on the existing `/webhook/gorgias/*` receiver on port 8000. The
inbox projection is not a new Gorgias webhook destination.

## Verified live changes by the evidence cutoff

- Caddy URI/Referer logging redaction applied and tested with a synthetic canary.
  This prevents new URI-based disclosure; it does not erase historical logs or
  invalidate the already exposed webhook credential.
- Inbox runs as `bb-inbox` UID 995, isolated under `/opt/buttonsbebe/inbox`, with
  its writable SQLite state outside source. Readiness and the exact Send lock
  were verified. A failed first installation automatically restored the previous
  inbox; a restrictive-umask traversal bug was then fixed and retested.
- Inbox authentication runs before prefix removal, preserving login return to
  `/inbox/`. Owner Cookie/Authorization are removed before proxying to the inbox.
  Public QA exposure returns 404 while on-disk files remain preserved.
- Receiving port 3210 now binds localhost. The dedicated TLS origin requires the
  existing owner session; unsafe methods require its exact Origin before auth.
  Unauthenticated aggregate API access returned 401 and authenticated aggregate
  GET returned 200. No customer response body or financial POST was inspected.
  Framing is denied; owner credentials are stripped before backend forwarding.
- Hermes 9119 and exchange 4100 bind localhost with their existing Caddy entry
  points preserved. The exact deleted-directory preview process on 8099 was
  stopped. Other PM2 processes and firewall policy were not broadly changed.
- A consistent webhook backup was encrypted and copied off-host; decryption,
  exact SHA and SQLite integrity were verified in an isolated directory. The
  new scheduled backup job also completed for both databases; its downloaded
  encrypted artifact was decrypted and both manifest hashes/integrity checks
  passed. Six-hour timer activation must be confirmed in the final evidence.

The receiving `/webhooks/redo` route has **no public authentication exception**.
The existing optional webhook signing secret was not configured in the reviewed
runtime. Untrusted requests are blocked; provider signing and delivery must be
coordinated before restoring automated ingress. Likewise the legacy
reconciliation GET/HEAD route is blocked, including case/trailing-slash variants,
because its handler can change local reconciliation state.

## Reviewed source awaiting final deployment/verification

At the cutoff the main webhook/processor release and replacement deployment
receiver were not deployed, and the integration branch was not published to main.
Do not infer otherwise from this repository containing the fixes.

| Original finding | Reviewed remediation / remaining deployment evidence |
|---|---|
| 1, 7, 16: rollback/data loss, drift, outage/concurrency | Component/source inventory, host lock, prepared dependencies, source-only rollback, component lifecycle and projection ordering. Independent Linux rollback harness passed; install receiver, verify approved applied config and rehearse final release. |
| 2: intake crash window | Event, parsed message and queue insertion committed atomically; failure/replay tests. No automatic historical replay. |
| 5: stranded jobs and new result durability gap | Bounded periodic singleton recovery and retry exhaustion; durable exact result confirmation before completion; retry reuses stored result. Owner alert transport state distinguishes accepted from uncertain. |
| 6: unsafe learned knowledge promotion | Explicit approved actions and authoritative revisions; rewrites/internal-only work excluded from automatic customer-answer promotion; durable lesson/ledger handling. Historical lessons require separate review. |
| 8: duplicate/ambiguous human actions | Durable action intents bound to operation/context; uncertain outcomes need review rather than automatic resend. Real provider/routing reconciliation remains an activation prerequisite. |
| 9: credential/root boundaries | Inbox isolation is live; child environment allowlists reviewed. Core services/Hermes still retain root/home authority. |
| 10: same-origin authority | Credential stripping is live, but inbox and console still share a browser origin. This is not a separate browser permission boundary. |
| 11, 12, 13: server/persistence/truthful UI | ASGI/SQLite isolation partly live; latest read-only canonical projection, lineage, stale/error/empty states and interaction changes require final runtime/browser verification. |
| 14: weak readiness/observability | DB/schema/queue diagnostics and local monitor with authenticated sanitized summary. Missing/stale/failed status cannot appear healthy. Monitor remains local, not off-host paging. |
| 15: incomplete release gate | Broader suite discovery and failure tests. Full final gate and live-model campaign were still in progress at the cutoff. |
| 17: rewrite process handling | Bounded process groups, timeouts/output, environment allowlist and guarded extraction; requires final deployment. |
| 18: sessions/attribution | Revocable sessions, server-side identity, exact Origin checks and bounded password work; requires final deployment and owner login verification. |
| 19: inbox login return | Live Caddy auth-before-strip and credential stripping verified; latest session changes still need final combined verification. |
| 20: shared appearance vs contract | Shared visual work is not permission/data unification. Final browser checks must verify list/thread/rail, truthful empty/stale state and locked Send. |

## Read-only inbox projection contract

The projection is a bounded view of records observed by the existing receiver,
not a complete mailbox import: newest 500 observed tickets in a 90-day window,
up to 100 observed messages per ticket. It explicitly reports incomplete history
and truncation. Generation-aware pagination refuses inconsistent snapshots;
stale projections are not presented as fresh. Superseded draft lineage is
withheld instead of presenting an old draft as current.

A root-owned exporter opens the canonical database read-only/query-only, applies
a five-second SQL deadline and bounded source-field budget, and atomically writes
`/var/lib/buttonsbebe-inbox-projection/projection.sqlite3`. The directory is
`root:bb-inbox` 0750 and file 0640. The inbox cannot write this directory or open
the root canonical DB. Refresh is every 60 seconds; age beyond 180 seconds makes
inbox readiness fail. Initial export must precede starting the projection-aware
inbox. The exporter unit must disable bytecode writes to preserve source hashes.

## Remaining production work and activation blockers

1. Complete and record the final offline gate, isolated model QA, deployed runtime
   hashes, authenticated browser checks and sustained health observation. At the
   cutoff no full model run was complete. The actual verified model was
   `gpt-5.6-luna-900k` through `openai-codex`; older GLM documentation is stale.
2. Verify real ticket/recipient/channel identity and ambiguous Gorgias action
   reconciliation under an explicitly reviewed test before considering future
   Send capability. The current campaign does not enable it.
3. Coordinate rotation of the webhook credential that exists in historical logs.
   Current API permissions returned 403 for HTTP integration inventory. Do not
   invalidate live ingress or delete evidence without a coordinated recovery plan.
4. Establish reviewed off-host monitoring/destination and scheduled restore drills.
   A local timer, WhatsApp bridge, or successful local ciphertext write cannot
   report complete VPS/network failure or prove off-host disaster recovery.
5. Plan dedicated identities/filesystem access for core processor, webhook and
   Hermes. Environment filtering does not prevent a root process reading files.
6. Resolve receiving provider webhook authentication and blocked reconciliation
   workflow deliberately; do not add a public bypass to make an integration green.
7. Review retention/deletion across ticket payloads, historical lessons, logs,
   provider histories and backups. This campaign has not established complete
   deletion propagation, historical incident frequency or all third-party CVEs.

## Final evidence to fill in before handover

| Evidence | Root reviewer result |
|---|---|
| Final reviewed release SHA / PR / main state | Pending |
| Applied source and Caddy/systemd fingerprint approval | Pending |
| Full offline release gate | Pending |
| Isolated model QA result and limitations | Pending |
| Projection timer, freshness and owner list/thread/rail | Pending |
| Owner login / original console / receiving continuity | Pending |
| Exact Send lock and inbox Gorgias bridge 503 | Pending final check |
| Monitor timer / authenticated summary / soak interval | Pending |
| Backup timer and off-host restore receipt | Restore proof recorded above; confirm final timer state |
| Explicit Send decision | Remains disabled; no activation clearance |

See [the operator runbook](PRODUCTION-OPERATOR-RUNBOOK.md) for verification,
rollback and isolated recovery procedures.
