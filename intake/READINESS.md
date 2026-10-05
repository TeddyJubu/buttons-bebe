# Owner proof and activation scope (E6)

**Decision on 2026-09-29: NOT READY for live operation. Authorized live scope:
empty.** The isolated intake application has demonstrated its offline contracts
with synthetic cases and a bounded saved Gorgias sample. No production release,
real channel connection or Gorgias retirement has been performed or authorized.

E6 completes the presentation of that evidence and preserves the activation gate.
Completing the A–E checklist does not make the future production prerequisites
below pass. The owner's latest instruction authorizes parallel implementation,
review and completing this local checklist without repeated permission questions;
it does not specify a live channel, provider, release or routing change.

The [cutover runbook](CUTOVER.md) describes a future migration and rollback.
[readiness.json](readiness.json) is a static, redacted decision record with proof
file hashes. It is documentation, never application configuration or an activation
token. Root [AGENTS.md](../AGENTS.md) and the
[tasklist](../INTAKE-TASKLIST.md) retain the operating boundary.

## What the owner can rely on

| Milestone | Demonstrated result | Limit of the result |
| --- | --- | --- |
| A–C: foundations, import and local review | Independent SQLite ticket/message identities; atomic import and edits; repeat-request and stale-edit protection; loopback UI; outbound network/DNS/process denial and production release exclusion tested | The Python guard is defense in depth, not an operating-system security boundary; no production service is installed |
| D1–D3: saved export fidelity | 50 tickets, 211 messages, 22 attachment references and 261 original source records; 2,088 field checks; repeat/restart/interruption rollback passed | Recent bounded sample, not whole-account completeness or ongoing synchronization |
| D4: inbound replay | 135 incoming history duplicates suppressed, 76 history echoes ignored; 206 fabricated follow-ups threaded to 49 saved conversations | Local fabricated input; trusted sender identity and provider behavior are unproven |
| D5: reviewed fake delivery | 57 fake attempts produced 35 local outgoing messages; 11 unknown attempts remained blocked; lost responses, definite rejection and explicit reconciliation exercised | Fake acceptance is not recipient delivery; no real email, CC/BCC, alternate-recipient or outgoing-file support |
| D6: attachments and recovery | 95 assertions; two synthetic attachment references share one 40-byte blob restored byte-for-byte; source history and unresolved attempts preserved | No real attachment bytes; local unencrypted checkpoints do not establish offsite disaster recovery |
| D7: owner acceptance | Seven cases / 31 selected messages, six workflows and 2,388 automated checks; owner accepted the bounded sample with listed gaps | Acceptance is restricted to the offline sample; the original packet remains unchanged |
| E1: isolated Inbox | All 50 conversations / 211 messages / 22 references matched the UI; 46 eligible and four withheld fake reply targets; no stored row changes during viewing | Production Inbox remains separate |
| E2: local team workflow | Three users, 150 per-user read checks, claim/handoff, role and review-owner enforcement, session revocation on restore | Local roles permit workspace-wide reading; production identity, MFA, channel restrictions and recovery are unproven |
| E3: assistance contracts | 261 native source/message bindings, 100 explicit fixture runs, 50 deduplicated repeats and 50 blocked staff-only Use actions; saved business rows unchanged | Canned fixtures and deliberately unavailable context; no model or retrieval-quality proof |
| E4: channel and job contracts | Signed local files, fenced claims, bounded retries, receipt observations, redacted health and restored-work hold; 460 saved-data assertions plus 2,088 fidelity checks | No real receiver, SDK, SMTP client, delivery worker, provider authentication or production credentials |

The E4 historical verification gate passed **145 backend tests**, JavaScript
syntax and whitespace checks. Its synthetic Inbox browser regression reported
zero external requests and zero live-route requests. The separate CLI smoke
completed seven jobs and deduplicated seven repeated events. Any subsequent
review fixes and their final test count are recorded in the tasklist; 145 is the
E4 result, not a claim about later changes.

Final completion verification on 2026-09-29 passed **155 backend tests**, JavaScript
syntax/whitespace checks and **all four synthetic browser suites**, with zero
external browser requests. Independent review found and verified fixes for an
invalid-password substitution edge case and two recovery-integrity gaps: missing
captured jobs and missing completed native messages. Ten new regressions cover
these findings; no material review findings remain.

The executable E5 runbook was also rehearsed in a fresh saved-data workspace:
**2,088 fidelity checks and 460 assertions** passed, including all 206 fabricated
follow-ups. An additional verified checkpoint restored all 421 jobs and 418
messages, retained the intentional unknown delivery/receipt conflict, and held
intake as required. Its restore hold remains in place. The private consolidated
record is `intake/.local/checklist-completion-20260929/verification.json`;
`readiness.json` retains the twelve historical proof hashes plus seven current
verification/report hashes. Earlier proofs were not rewritten or migrated.

The E4 saved-data proof finished with 421 completed jobs, 50 tickets and 418
messages. These include the original 211 messages, 206 fabricated follow-ups
and one local fake outgoing message. Its restored checkpoint finished with 417
completed jobs, 50 tickets and 417 messages: the four receipt jobs and one fake
outgoing message were added only in the original proof after the checkpoint.
The restored proof exercised all 206 follow-ups again with old claims fenced.
All 261 original source records remained unchanged.

E4 deliberately retains **one unresolved fake attempt and one conflicting receipt
group**. Monitoring reports both. A passed fault rehearsal means these states
were held and surfaced correctly; it does not mean every message was delivered
or that the health report is clear.

## Accepted sample gaps, still open for production

The owner's recorded statement is: “Accept D7 for the offline sample with the
listed gaps”. The original seven review artifacts still match the hashes in the
separate decision record. The packet's historical pending label is superseded
by that decision, not rewritten.

- Only 50 recent tickets are covered. This cannot prove complete history,
  ongoing delta capture, deletions, edits or a whole-account reconciliation.
- The 22 real attachment references have no file bytes. There are no real private
  notes or spam/trash cases in the sample; those behaviors have synthetic coverage.
- Four source messages lack usable headers. Four conversations have withheld
  reply targets; one self-addressed message cannot anchor a customer follow-up.
- The largest saved conversation has 24 messages. None of the 50 detail responses
  supplies an independent `messages_count`. Exhausted pagination proves the saved
  request chain ended, not a source-independent total or long-thread coverage.
- Subsequent E2/E3/E4 work demonstrates local identity, assistance fixtures and
  fake adapter contracts. It does not resolve D7's production integration gaps.
- Real mail routing, outgoing attachments, CC/BCC, alternate recipients, timed
  snooze, queue release/merge and channel parity still require a scoped product
  decision and implementation where required for replacement.
- Recovery is local and unencrypted. Post-checkpoint external activity,
  cross-host sending ownership, offsite durability and real disaster recovery
  remain unproven.

## Evidence retained privately

These paths refer to ignored workspace artifacts. Open them locally only; they
may contain customer data. The static decision record includes only their hashes
and selected aggregate results. It contains no source ticket/message IDs,
customer addresses, message text, attachment filenames or signing keys.

| Proof | Private artifact under `intake/.local/` |
| --- | --- |
| D2/D3 fidelity | `gorgias-export-review-20260928/reconciliation.json` |
| D4 replay | `gorgias-replay-proof-20260928-v3/replay-reconciliation.json` |
| D5 delivery | `gorgias-delivery-proof-20260928-v3/delivery-reconciliation.json` |
| D6 recovery | `gorgias-recovery-proof-20260928/recovery-reconciliation.json` |
| D7 review and decision | `gorgias-owner-review-20260928-v2/owner-review.json`, `owner-decision.json` |
| E1 display | `gorgias-inbox-proof-20260928/inbox-display-reconciliation.json` |
| E2 team | `gorgias-team-proof-20260928/team-saved-proof.json` |
| E3 assistance | `gorgias-assistance-proof-20260928/assistance-saved-proof.json` |
| E4 channel/recovery | `gorgias-channel-proof-20260929/channel-reconciliation.json` |
| E4 CLI and browser | `e4-cli-20260929/cli-proof.json`, `inbox-1790667638968/inbox-browser-result.json` |

Read-only inspection for this packet confirmed preserved database versions:
D2 schema 1, D7/E1 schema 4, E2 schema 5, E3 schema 6, E4 and its restored copy
schema 7. No old proof was opened with `Store`, migrated or imported again.
Later implementation tests must use fresh workspaces and leave these historical
proofs and the original saved export intact.

## Exact current scope

| Action | Current permission |
| --- | --- |
| Modify/review/test the isolated checkout | Allowed; preserve the offline boundary and production release exclusion |
| Import already saved data into a fresh private test workspace | Allowed; explicit preview/commit and no remote attachment access |
| Start a loopback test UI; create local users and ticket changes | Allowed; local sandbox credentials and fake actions only |
| Sign/admit fake files; run local jobs, fixtures, backups and restored proofs | Allowed; no external side effects and original evidence preserved |
| Reuse the prior read-only export authorization for ongoing synchronization | Outside this scope; the earlier export was a separate bounded operation |
| Real ingress, egress, provider/model calls, external notifications | Disabled and unauthorized |
| Public endpoint/tunnel, production routing or service changes, deployment | Unauthorized |
| Push/merge to main, retire Gorgias or change a live channel | Unauthorized |

No feature switch, environment variable, JSON artifact, passing test or checked
task can convert these local actions into production permission. A new production
implementation needs its own bounded release and evidence; the sandbox is not
activated by removing its guard.

## Future production gates

Every gate below is **unproven**. The runbook explains the sequence and rollback;
this table states the evidence needed to reconsider the decision.

| Gate | Required evidence and scope |
| --- | --- |
| P1: migration completeness | Source inventory and independent totals for the named account/channels/time range; representative long history, private notes and files; immutable export manifests; reconciled unknown/deleted/edited records; implemented baseline/delta/final-fence strategy. Current changed-source imports deliberately conflict and cannot apply migration deltas |
| P2: provider/channel compatibility | Chosen provider and exact mailboxes; current official signing, retry, acknowledgement, event identity, attachment and idempotency contracts; isolated implementation and provider-fixture tests; separately scoped integration evidence. Passing local HMAC tests does not establish vendor compatibility |
| P3: required workflow coverage | Explicit replacement feature matrix for required email/other channels, recipients, attachments, spam/automation, notes, routing, snooze and permissions; implemented and reviewed behavior for every required item; no unsupported feature silently dropped at cutover |
| P4: production access and message trust | Selected staff identity/MFA/TLS, secure sessions, account lifecycle/recovery, role/channel enforcement and separate workload identities; provider credential custody/rotation; sender authenticity and anti-abuse policy; MIME/file quarantine tests. A signed provider event does not prove customer identity |
| P5: assistance scope | Keep model/context providers disabled unless separately included. To include them: authorized retrieval, native-ID identity/evidence binding, real quality and prompt-injection evaluations, missing/stale fact behavior, priority safety and failure/latency tests. Fixtures alone cannot pass this gate |
| P6: operations | Measured peak/retry-storm capacity, queue/backlog/retention limits and SLOs; named operator and escalation owner; authorized redacted alert destination with failure tests; capacity/backup-age monitoring and ownership fencing appropriate to deployment topology |
| P7: recovery and one sending owner | Agreed RPO/RTO, encrypted offsite checkpoints and restore drill; durable journal/reconciliation of post-checkpoint messages and possibly accepted submissions; previous receiver/worker/sender fenced across hosts; unresolved attempts stay blocked; ownership-transfer and rollback rehearsal with exact provider evidence |
| P8: exact release/change approval | Reviewed build/configuration and proof digests, channel allowlist, read/receive/send/deploy actions, limits and window, rollback triggers/owner, and a new explicit owner instruction for that specific change. Rollback external actions must be included in that scope |

If any required gate fails or remains unknown, real activation remains held.
Any future scope that omits assistance must keep model/retrieval calls disabled;
that exclusion cannot waive history, identity, provider, sending-owner or recovery
requirements for the channels being activated.

## Future bounded change record

Prepare a concrete record when the preceding evidence exists. This is an empty
template, not a request or an instruction to execute a change. A blank field means
the proposed change is not ready. Do not add secret values or customer content.

```yaml
decision: not_ready
proposal_id: null
release_commit_and_artifact_digest: null
configuration_and_proof_packet_digests: null
environment_and_deployment_target: null
account_and_exact_channel_mailbox_allowlist: []
permitted_actions: []  # Enumerate read, receive, human-confirmed send, deploy, route.
permitted_routing_and_service_changes: []
traffic_limit_and_observation_window: null
history_cutoff_delta_watermark_and_gap_report: null
required_feature_matrix_and_exclusions: null
production_identity_and_secret_references: null
single_sending_owner_and_fencing_evidence: null
in_flight_attempt_reconciliation: null
monitoring_thresholds_alert_destination_and_operator: null
backup_digest_recovery_objectives_and_restore_proof: null
rollback_triggers_steps_owner_and_external_actions: null
gorgias_retention_and_retirement_conditions: null
owner_instruction_record: null
activation_authorized: false
```

A future owner instruction must identify the completed proposal and its allowed
actions. It authorizes only those actions in the named scope and window; a change
to the release, routing, channels, recipients, automation or recovery assumptions
requires a revised record. Consent to finish this offline checklist, D7's sample
acceptance and earlier production permissions belonging to the existing Gorgias
system do not transfer to the replacement.

Until a bounded proposal passes its gates and receives that new instruction,
the decision stays **NOT READY**, `activation_authorized` stays `false`, and the
live action/channel lists stay empty. No permission question is pending for
completion of the offline checklist.
