# Migration, cutover and Gorgias retirement (E5)

This is the runbook for a future replacement of Gorgias. **Today the system is an
offline sandbox. No live cutover is ready or authorized.** The executable section
below uses existing local commands, saved exports and fakes. The production
sequence is a design with explicit entry conditions; it is not a set of available
activation commands. Completing this document does not satisfy those conditions.

The evidence baseline and the owner's bounded D7 acceptance are in
[the tasklist](../INTAKE-TASKLIST.md). [CHANNELS](CHANNELS.md) defines E4's adapter,
job and receipt contracts; [RECOVERY](RECOVERY.md) defines local backup/restore.
Keep their distinction between fake acceptance and actual customer delivery.

## Responsibility and the change record

Before a future cutover, assign a named person and backup to every role. One person
may fill multiple roles, but the migration operator must not be the only reviewer
of reconciliation or the decision to change sending ownership.

| Role | Responsibility and required decision |
| --- | --- |
| Account owner | Approve the exact channels, permitted operations, release, maintenance window, coverage exclusions and rollback plan; accept or reject each transition |
| Migration operator | Preserve exports/captures, run imports, maintain ID mappings, reconcile counts/content and produce the private evidence packet |
| Support lead | Inventory actual workflows, validate representative conversations and late replies, freeze staff changes, resolve review queues and communicate the operational handover |
| Channel operator | Verify provider contracts and mailbox routing, enforce one sending owner, preserve ingress during transitions and collect authoritative submission evidence |
| Recovery/incident operator | Own monitoring, checkpoints, recovery fencing, incident decisions and the append-only record of post-checkpoint activity |
| Independent reviewer | Check evidence against the scope, identify unproven gaps and witness routing/ownership transitions |

Use a private, access-controlled change record. It must contain the release hash
and schema, source account, every channel/mailbox/alias and provider, named roles,
approved actions and time window, source snapshot and checkpoint digests,
reconciliation results, sending-owner epochs, routing before/after evidence,
provider retention/retry horizons, stop conditions and rollback target. Record
timestamps in UTC. Store secrets separately; the record contains references and
digests, not credentials. Public summaries contain aggregate results only.

For each channel, record these states and the evidence for every transition:

| State | Authoritative history | Sending owner | Ingress requirement |
| --- | --- | --- | --- |
| Current operation | Gorgias | Existing Gorgias action path | Existing routing unchanged |
| History rehearsal | Gorgias; isolated copy is evidence only | Existing Gorgias action path | Saved files only reach this sandbox |
| Future maintenance interval | Frozen Gorgias history plus a durable ingress buffer | None | Every new delivery is durably retained or remains unacknowledged for verified retry |
| Future replacement operation | Reconciled native history plus the retained source archive | Exactly one authorized native sender | Selected production adapter owns the channel |
| Future rollback interval | Both immutable stores plus the reconciliation journal | None | Continue durable capture while ownership is unresolved |
| Future returned operation | Reconciled rollback target plus retained native journal | Exactly one verified rollback sender | Routing and late-arrival handling are verified at the rollback target |

The production buffer, cross-host sender fencing and rollback import are
**unimplemented**. The local restore hold and fake key revocation are not evidence
that another host, Gorgias automation or an existing console sender has stopped.

## Establish history completeness

Inventory the account before selecting a migration scope. Include every email
mailbox/alias and non-email channel, open and closed tickets, spam/trash, private
notes, long conversations with paginated messages, merged/deleted records that
remain accessible, contacts, tags, assignments, custom fields and attachment
references/bytes. Inventory rules, macros, automations, SLAs, snooze behavior,
permissions, reports and integrations separately: preserving raw source JSON
does not implement their behavior in the replacement.

For each included category, obtain an authoritative source count or explain why
one is unavailable; retain pagination completion, extraction start/end times,
watermarks and digests. A sample's `sample_capture_complete` flag means that the
bounded sample was captured, not that the account is complete. A changing source
cannot be treated as one atomic snapshot merely because every page was fetched.
Missing totals or unavailable categories require an explicit coverage decision;
they must never silently become zero counts.

The migration packet must reconcile all of the following:

| Evidence | Acceptance condition |
| --- | --- |
| Tickets and messages | Exact source-account/source-ID mapping; no unexplained missing, extra or duplicate records; every message attached to the intended ticket |
| Conversation content | Original timestamps, direction, public/private visibility, authors, sender/recipient headers and text preserved; HTML-only display differences reviewed |
| Ticket state | Status, priority, tags and assignment reconciled; source assignments deliberately mapped to active native users; unsupported fields/workflows recorded |
| Thread continuity | Original RFC message identities and participant/mailbox evidence retained; duplicate/ambiguous/missing identities surfaced for review |
| Attachments | Every reference accounted for; each required file associated with its original message and verified by size/hash; missing bytes identified individually in the private packet |
| Source archive | Original snapshots and their manifests retained independently of the database; a restore plus integrity check and an archive-retrieval exercise succeed |
| Side effects | Import and replay of history create no live notifications, model runs or submissions; the new system has no unreviewed historical work queued for sending |

Native IDs are generated independently from source IDs. Within one workspace,
repeat imports preserve them; a new import into an empty workspace may assign
different native IDs. Select the eventual canonical database once, retain its
`source_records` mapping and preserve it through checkpoints. Do not transplant
ticket links, assignment IDs, assistance results or reviews from another rehearsal
database. Reconcile provider message identities and RFC headers as separate
evidence; do not use subject or customer email alone as a migration identity.

The accepted sample contains 50 tickets, 211 messages and 22 attachment references.
It has no real attachment bytes or private source notes, no representative
spam/trash or long paginated conversation, and four unusable thread anchors. E4
also excluded a self-addressed anchor from its fabricated follow-up proof. D7's
acceptance applies to that offline sample and those recorded gaps. It does not
waive whole-account completeness or production delivery proof.

## Full snapshots, deltas and late replies

The current importer accepts bounded full-conversation JSON: up to 20 MiB and
2,000 tickets per file. It rejects indicated incomplete pages and mismatched
message counts. Batches are atomic, but a collection of files is not one global
transaction. Preserve a manifest across all batches and verify the complete
collection before using it as a migration boundary.

Identical source records are idempotent. Changed content under an existing source
ID is a conflict. Additional messages can be inserted only when all already known
records, including the ticket snapshot, are unchanged; this is not a general delta
updater. Existing contact changes, ticket edits, merges, deletions and changing
source snapshots have no migration reconciliation policy. Never remove source
records, change the account namespace, strip changed fields or edit digests to
force an import to pass.

Choose and prove one future approach before scheduling any cutover:

1. **A bounded maintenance cutover.** Freeze source staff/automation writes,
   preserve all arriving mail in a verified durable buffer, take a final complete
   snapshot, import into an empty selected canonical database, reconcile it, then drain
   the buffer through the production adapter. Any arrival that overlaps the final
   snapshot must deduplicate by stable identity. The buffer and capture boundary
   are prerequisites; a staff freeze alone does not stop incoming mail. Reusing a
   staged mutable database instead requires the tested preserving update/journal
   path from the second approach; the current importer cannot refresh it.
2. **A staged migration with deltas.** Implement and test versioned delta handling
   first, including ticket/contact edits, messages/notes, merges/deletions,
   pagination races, overlap, tombstones and restartable source watermarks. Retain
   immutable originals and conflict evidence. Prove repeated and interrupted
   applications preserve native IDs and newer native state. Then prove a final
   freeze/watermark handover without gaps. This option is not available today.

For either approach, the final boundary must have a recorded last source watermark
and first new-provider capture identity, with an overlap reconciliation proving
that each event belongs to history, buffered intake or both with one outcome.
Define how the source identifies updates that occur during extraction; wall-clock
timestamps alone are insufficient if the selected source cannot provide a stable
ordering or completeness guarantee.

Late replies may reference messages sent through Gorgias long before cutover.
Retain all usable historical Message-ID/References/In-Reply-To evidence and the
corresponding channel/mailbox participants. Inventory every historical reply
address, forwarding alias and provider-owned address. Verify whether each address
can keep delivering to the new owner; the ability to receive at a public support
address does not prove old provider-owned reply addresses still work. Unroutable
legacy addresses are a retirement blocker unless an explicit supported continuity
plan is implemented and verified.

Rehearse delayed messages, duplicate arrivals through old/new routes, replies to
closed cases, concurrent follow-ups and missing/ambiguous headers. Preserve
ambiguous mail for staff review without guessing a thread or silently merging
cases. Define who owns that queue and the response-time budget. Old-route arrivals
remain recoverable until the selected provider's retry/retention horizon and the
agreed late-reply observation interval have elapsed; an arbitrary short quiet
period is not evidence that no more mail can arrive.

## Executable offline rehearsal

These commands are available now. Run from this checkout with no production
environment loaded. Use unused workspace names; retain earlier evidence unchanged.
`channel_rehearse` requires both its workspace and its `-restored` sibling to be
absent. Keep names short enough for the 48-character limit including that suffix.

```bash
bash intake/verify.sh
python3 -m intake.channel_rehearse intake/exports/gorgias-20260928T093215-921504Z --workspace e5-local-proof-20260929 --account buttonsbebe
python3 -m intake --workspace e5-local-proof-20260929 verify-workspace
python3 -m intake --workspace e5-local-proof-20260929-restored verify-workspace
python3 -m intake --workspace e5-local-proof-20260929 jobs-health
```

The rehearsal imports a fresh saved copy, checks fidelity, authenticates fabricated
history/follow-ups, proves deduplication and threading, restores queued/leased work
under a hold, resumes fake jobs and exercises signed fake receipts. It writes
`intake/.local/e5-local-proof-20260929/channel-reconciliation.json` privately.
Its final original workspace intentionally retains an unknown delivery and a
conflicting receipt group; those visible attention signals are expected test
outcomes, not a clean production readiness report. No real sender is involved.

For an additional local checkpoint, the following are existing commands. Replace
`VERIFIED_DIGEST` with the actual digest from `backup-verify`; do not assume a
digest from an earlier checkpoint is interchangeable.

```bash
python3 -m intake --workspace e5-local-proof-20260929 backup --name e5-checkpoint
python3 -m intake --workspace e5-local-proof-20260929 backup-verify --name e5-checkpoint
python3 -m intake --workspace e5-local-proof-20260929-recover restore --from-workspace e5-local-proof-20260929 --backup e5-checkpoint --expected-digest VERIFIED_DIGEST
python3 -m intake --workspace e5-local-proof-20260929-recover verify-workspace
python3 -m intake --workspace e5-local-proof-20260929-recover jobs-health
python3 -m intake --workspace e5-local-proof-20260929-recover jobs-resume
```

The last command only inspects the recovery plan. An exact inspected digest is
required to resume local fake work. Restore revokes sessions and fake keys, fences
old claims and invalidates unconfirmed reviews. It preserves existing attempt IDs
and uncertainty. Restoring a checkpoint excludes later activity and does not merge
stores. Neither sequence proves provider compatibility, full-account migration,
post-cutover rollback, offsite recovery, production identity or real delivery.

## Future production sequence — blocked until implemented and proven

Do not translate this table into live commands until there is a separately
reviewed production implementation, an exact scope record and the required owner
instruction. All currently available adapters are fakes. No environment setting
or sandbox CLI can authorize this sequence.

| Transition | Action and responsible role | Evidence required before proceeding |
| --- | --- | --- |
| P0: establish readiness | Owner and independent reviewer approve the complete change record; support lead verifies every required workflow or an explicitly accepted alternative | Whole-scope reconciliation; supported provider/identity contracts; measured capacity, ingress lag and recovery budgets; tested monitoring; encrypted offsite restore; an executable rollback journal/replay procedure; all mandatory gaps closed |
| P1: prepare maintenance | Channel and recovery operators verify durable ingress capture, checkpoint both sides and identify every sender including automations, console actions, integrations and scheduled work | Successful capture/retry failure drills; independently verified checkpoints; complete sender inventory; operator coverage and incident contact path |
| P2: establish no-sender interval | Support lead freezes edits; channel operator disables and fences old sending authority and cancels/reconciles queued sends | Auditable denial of an old sender's attempted submission; each in-flight attempt classified as accepted, proven rejected or held unknown; no unknown attempt eligible for retry |
| P3: close history boundary | Migration operator takes the final snapshot/delta boundary and drains only reconciled captured input into the chosen database | Exact IDs/counts/content verified; overlaps deduplicated; no unexplained missing event; attachment and late-reply continuity gaps resolved; fresh verified checkpoint |
| P4: transfer ingress | Channel operator changes only approved routes/aliases and validates old/new arrival paths while sending remains disabled | Before/after route evidence; controlled permitted test arrivals each produce one durable capture and one intended ticket outcome; wrong-account and duplicate events are handled correctly |
| P5: transfer sending ownership | Channel operator records a new ownership epoch and enables only the approved native sender; support lead performs the separately approved controlled reviewed submission | Previous owner demonstrably fenced; recipient/content/actor/attempt evidence retained; acceptance/delivery reconciled by selected provider evidence; no duplicate submission and tested disable path |
| P6: observe and accept | Recovery operator watches agreed monitoring and audit budgets; support lead checks real workflow outcomes; owner records acceptance | No unexplained data gaps, routing loss or duplicate sends; no aged unresolved attempts/dead work outside agreed budgets; late arrivals reconcile; tested backup recovery remains within RPO/RTO |

Routing changes may include mailbox forwarding, provider subscriptions, domain
records, historical aliases or the application's public routes. Choose the exact
changes from the selected providers' verified contracts; no generic DNS or Gorgias
command is supplied here. Record previous values and reversibility. A route change
alone never transfers sending authority. Overlapping ingress is acceptable only
after cross-route identity/deduplication is proved; overlapping sending is not.

Before P0, fill in numerical limits and observation durations for queue age,
unresolved submission age, service/storage headroom, backup age, RPO/RTO, alert
acknowledgement and rollback completion. Defaults from the fake monitoring tests
are not production SLOs. The target for acknowledged inbound-event loss and
unexplained duplicate submissions is zero. Missing evidence, an undefined budget
or an unavailable responsible operator prevents the next transition.

Stop the transition immediately for an identity/recipient mismatch, missing
acknowledged capture, unexplained duplicate, conflicting source history, unknown
sender ownership, authentication failure, lost audit evidence, corrupt recovery
checkpoint or broken required legacy reply address. Stop new submissions on any
uncertain acceptance; reconcile the original attempt rather than retrying it.
An operator investigates other backlog/delivery signals under the pre-agreed
budget; automated monitoring must not switch providers or resend messages.

## Rollback preserves activity; it is not just restoring yesterday's database

Before any production activation, implement and rehearse the following journal
and recovery procedure with the selected provider. Current local backups cannot
perform it. If the journal, replay path or old-owner fencing is unavailable,
production cutover remains blocked.

1. **Contain and capture.** The incident operator records the decision and time,
   stops/fences all submissions on the failing owner, retains ingress durably and
   freezes staff writes at a documented boundary. Keep unacknowledged deliveries
   eligible for verified retry. Preserve the latest failed database intact and
   take a verified checkpoint when possible; never replace it with an older copy.
2. **Preserve the post-boundary journal.** Save all captures/jobs and outcomes,
   tickets/messages/notes, edits/assignments/read-state policy, source mappings,
   attachments/blobs, human confirmations, delivery attempts, provider keys/receipt
   identifiers, authenticated observations and audit records since the rollback
   checkpoint. Include the last committed operation/watermark from both systems
   and authoritative provider activity after it. Keep secrets in their protected
   store; retain non-secret key identifiers in the journal. A database snapshot
   without the provider's post-checkpoint submission evidence is insufficient.
3. **Reconcile every submission.** Join by immutable attempt, account/mailbox,
   reviewed digest and authoritative provider identity. Accepted submissions must
   appear once in the recovered history; proven rejected submissions require a
   new human review before any retry. Unknown, contradictory or unsupported
   outcomes remain held across both systems. Missing list/search results, an old
   checkpoint and a later bounce cannot prove that resending is safe.
4. **Build a separate candidate.** Restore a verified checkpoint into an isolated
   candidate, revoke sessions/credentials, invalidate reviews and fence prior
   workers. Apply the reconciled journal through the tested migration path,
   preserving mappings and original timestamps. Deduplicate incoming captures
   before admitting new work. Keep both originals immutable for comparison.
5. **Validate the chosen target.** If returning to Gorgias, use an explicitly
   authorized, tested history-import path that preserves post-cutover messages
   as history without notifying customers; no such writer exists here. If the
   provider cannot represent a record, retain a linked authoritative archive and
   obtain the agreed support-workflow treatment before reopening that channel.
   If recovering native service, compare the candidate's business/ledger records
   to the combined journal and prove cross-host fencing. Never assume the
   current Gorgias export importer can merge either target.
6. **Transfer ownership once.** The independent reviewer checks completeness,
   unresolved holds, routing and restore evidence. Only then may the owner record
   the rollback scope and channel operator transfer ingress/sending authority to
   one target. Keep every affected unknown attempt blocked. Verify a controlled
   permitted arrival/submission and monitor against the same budgets as cutover.

If lossless return cannot be established, keep submissions disabled and ingress
retained while repairing the candidate or reconciling the journal. Do not conceal
data loss by declaring a restore successful. A forward repair may preserve more
history than a provider rollback; the incident record must state the selected
target, the evidence and any unresolved support work.

## Retire Gorgias only after continuity is proved

Gorgias retirement is a separate owner-approved change after the replacement's
observation window. It must not coincide with deleting the only rollback copy or
breaking an old reply address. Before cancelling access or deleting anything,
the owner and independent reviewer require:

- Whole-scope history and required file completeness accepted; the private
  original archive, native mappings and final reconciliation remain accessible.
- All required channels, roles, automations and staff workflows have a tested
  replacement or an explicit accepted disposition, including unsupported fields
  and current local-only behaviors such as snooze without a scheduler.
- Old-route late replies and provider retries are accounted for over the verified
  retention/retry horizon and agreed observation period; historical reply-address
  continuity remains viable after retirement.
- Every in-flight old/new submission is reconciled or explicitly held in the
  authoritative system; no old automation, integration or operator can send.
- Dependencies are removed through their own reviewed changes: existing Gorgias
  read/sync/projection paths, console send/note actions, webhook/processor inputs,
  assistance/retrieval tools and downstream reporting. Retiring Gorgias must not
  silently break the rest of the support application.
- A final read-only archive and verified recovery exercise are complete; access,
  retention, deletion and incident ownership are recorded under the business's
  approved policy. Billing cancellation, API credential revocation, webhook
  removal and data deletion each have their own exact scope and order recorded.

Keep the final sending-owner record, cutover/rollback evidence and archive
retrieval instructions available after retirement. Completion of E5 documents
these responsibilities and gates; it does not claim they have been executed.
