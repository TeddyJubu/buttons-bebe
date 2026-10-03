# Standalone ticket intake — execution checklist

Owner instruction, 2026-09-28: implement a ticket system that can eventually
replace Gorgias; use exported data to prove it works; no real intake or delivery
until proven and explicitly authorized. Production stays untouched.

## How to continue this work

1. Read this file and AGENTS.md at the start of every intake session.
2. Inspect the branch and local changes; reuse this checkout.
3. Work through the next unchecked item. Update this file with changes, commands,
   results and remaining limitations before ending the session.
4. A checked item means its acceptance criteria have been demonstrated. Synthetic
   coverage is not proof against a real export or permission for live operation.
5. Keep implementation and tests outside production code and release inventory.
   Never enable live behavior through an environment flag.

Checkout: `/root/buttonsbebe-ticket-intake`, branch `codex/ticket-intake`.
Base: `73fc4c5` (local origin/main). No push or deployment planned.

## A. Isolation and durable foundations

- [x] A1. Create isolated checkout and persistent checklist; add continuation rules
  to root AGENTS.md. Managed worktree tool could not resolve the `/root` chat;
  used a Git worktree from the known repository instead.
- [x] A2. Add an offline-only application with no credentials, provider clients,
  background polling, live webhook routes or delivery capability. Prove denied
  network connections and denied live actions.
- [x] A3. Add versioned, normalized SQLite storage: independent ticket/message IDs,
  contacts, source mappings, attachments metadata, import batches and audit events.
  Store only in private ignored sandbox directories; never accept a production DB.
- [x] A4. Implement manual test tickets, internal notes and shared status/assignment
  changes with atomic writes, repeat-request protection and stale-edit rejection.

## B. Export ingestion

- [x] B1. Define supported Gorgias JSON shapes and document unsupported CSV or
  partial exports. Require a source-account namespace to prevent ID collisions.
- [x] B2. Preview imports before commit: counts, duplicates, conflicting records,
  missing fields and incomplete message pages. Reject malformed batches atomically.
- [x] B3. Import full conversation snapshots with original timestamps, authors,
  public/private visibility, headers and attachment metadata. Retain original
  records privately. Never fetch attachment URLs or render remote HTML.
- [x] B4. Make repeated imports idempotent across restarts and concurrent requests.
  Never replay imported history into notifications, AI jobs or delivery queues.
- [x] B5. Expose a durable reconciliation report and provenance in the review UI.

## C. Local review workspace

- [x] C1. Build a loopback-only Inbox test screen with persistent sandbox banner,
  search, ticket list, conversation, customer context and activity history.
- [x] C2. Add manual ticket creation, internal notes, assignment/status editing,
  and export preview/import. No send button or live access toggle.
- [x] C3. Verify desktop/mobile, reload persistence, error states and keyboard use.
  Prove imported HTML/images cannot trigger external browser requests.

## D. Proof using exported data (required before live planning)

- [x] D1. Create a private read-only Gorgias export through the existing MCP and
  inspect its structure locally. The owner authorized us to export it ourselves
  on 2026-09-28. Keep this separate from the offline intake application.
- [x] D2. Reconcile source vs imported ticket/message/note/attachment counts,
  sender/recipient fields, timestamps, status, tags, assignment and original IDs.
  Record gaps explicitly. Keep reports with identifiers/customer data ignored.
  Completed for the initial 50-ticket sample; broader coverage and owner review
  remain required. See the exact source gaps below.
- [x] D3. Re-import the same export, restart, and retry interrupted imports; verify
  stable IDs, no duplicate messages, no partial batches and no outbound actions.
- [x] D4. Add offline simulated inbound replay with message/header-based threading,
  same-subject separate conversations, reopen rules, spam and automatic responses.
  Completed: a CLI-only replay file (explicit offline marker), preview/commit,
  durable event/message deduplication, account/mailbox/participant checks, and
  visible review/spam/automatic queues. No HTTP ingress route or provider client.
  Synthetic edge cases and 206 fabricated replies against the saved export passed;
  the original D2/D3 verification workspace and snapshot remain preserved.
  See the D4 evidence and coverage limits below.
- [x] D5. Add fake delivery ledger for human-confirmed simulated replies; prove
  duplicate-send, stale-review and uncertain-delivery behavior. No real provider.
  Completed: frozen review/confirm flow, durable attempt + fake receipt records,
  explicit reconciliation of uncertainty, and reviewed retry only after a proven
  rejection. Local UI and synthetic/saved-export proof passed; no live adapters.
  See the D5 evidence below.
- [x] D6. Rehearse private attachment-file import, backup/restore and recovery.
  Completed: bounded local-file manifests, checksum-verified private SQLite
  attachment bytes, self-contained backups and restore into new workspaces only.
  Corruption/interruption tests, browser checks and a saved-data recovery rehearsal
  passed; unresolved attempts remain blocked. Real attachment bytes are absent
  from the export, so file fidelity remains synthetic coverage only. See D6
  evidence below.
- [x] D7. Review representative real-export conversations with the owner. Obtain
  acceptance of data fidelity and workflows; record unresolved gaps.
  Accepted by the owner on 2026-09-28: "Accept D7 for the offline sample with the
  listed gaps". Seven-case comparison and six workflows accepted within the
  bounded offline sample; 2,388 automated checks passed. All gaps remain recorded.
  Decision: `intake/.local/gorgias-owner-review-20260928-v2/owner-decision.json`.
  Original review packet remains unchanged.

## E. Later implementation — not authorized for activation

- [x] E1. Connect the existing Inbox to the independent ticket API in isolation;
  keep production Inbox unchanged until a separately reviewed release.
  Completed: copied Inbox shell/styles/icons under `intake/inbox/`, dedicated
  offline API client, durable notes/tickets/details and the D5 fake reply flow.
  Synthetic browser workflows and all 50 saved-export conversations passed;
  production source hashes remain unchanged.
- [x] E2. Add per-user authentication, permissions, unread state and team workflow.
  Completed for the offline sandbox: individual local sessions, admin/agent/viewer
  API permissions, verified actors, per-user read watermarks, claim/assignment/
  handoff and recovery revocation. 102 backend tests, three synthetic browser
  suites and the 50-ticket saved-data team proof passed. See E2 evidence below.
  Production identity and activation remain outside this milestone.
- [x] E3. Adapt AI/context interfaces to our ticket IDs with offline fixtures first.
  Completed for offline fixtures: native ticket/contact/message input contracts,
  saved context/evidence, durable fixture outcomes, run/use/dismiss UI, stale-result
  and linked-review checks. 119 backend tests, four synthetic browser suites and
  the 50-ticket saved-data proof passed. Real model/retrieval quality is unproven;
  all model/provider/live traffic remains disabled. See E3 evidence below.
- [x] E4. Design production email/channel adapters, authentication, durable intake
  jobs, delivery reconciliation, monitoring and recovery; test against fakes.
  Completed for the offline sandbox: provider-neutral design, signed local fake
  envelopes, bounded durable jobs, receipt observations, redacted health and
  restore holds. 145 backend tests, Inbox browser regression, CLI smoke and the
  saved-data rehearsal passed. No live adapter or production activation exists.
- [x] E5. Create a migration/cutover runbook: history completeness, late replies,
  one sending owner per channel, routing changes, rollback and Gorgias retirement.
  Completed: `intake/CUTOVER.md`, independently reviewed. Its available offline
  rehearsal and additional backup/restore sequence passed on a fresh saved-data
  copy. Production transitions remain explicitly gated, unimplemented and unrun.
- [x] E6. Present proof results and exact activation scope to the owner. Require
  a new explicit instruction before real ingress, egress or any deployment.
  Completed: `intake/READINESS.md` and static `intake/readiness.json` present
  historical/final proof, accepted sample gaps and the exact current scope.
  Decision: NOT READY for live operation; live actions/channels/deployment targets
  are empty. This closes evidence/planning, without granting live activation.

## Session evidence

### 2026-09-28 — first implementation

- Reviewed current Inbox API, local-ticket storage, production release allowlist
  and historical demo intake. Reuse the product model, not retired demo services.
- Completed A–C with synthetic data in `intake/`. Production Inbox, webhook,
  processor, release inventory and runtime services are unchanged.
- `bash intake/verify.sh`: **26 tests passed**, JS syntax and diff whitespace
  checks passed. Covers all-or-nothing failure recovery; repeated/concurrent
  imports; source namespaces; mismatched/duplicate IDs; partial exports; private
  notes and headers; stale edits; API origin/token checks; outbound network/DNS/
  subprocess denial even with live-enabling flags; and release exclusion.
- `PLAYWRIGHT_MODULE=/root/.npm/_npx/e41f203b7505f1fb/node_modules/playwright
  node intake/tests/browser.mjs`: **passed** with the real isolated backend.
  Verified creation, notes, status/assignment, reload persistence, preview without
  writes, repeat imports, stale-edit rejection and note preservation, import
  history, send refusal, keyboard dialog dismissal and search. No page JavaScript
  errors and **zero external browser requests**. Checked widths 320–1440px.
- Inspected synthetic desktop/mobile screenshots. Latest verification artifacts:
  `intake/.local/browser-1790586093694/` (ignored; no real customer data).
- CLI preview → import → repeated import → report passed in ignored workspace
  `cli-1790586185`: 2 tickets, 4 messages, 1 private source note, 1 attachment
  reference; one import batch; no duplicate records on repeat.
- Test HTTP servers were stopped after verification. No server was published,
  no branch was pushed, and no real export was fetched or imported.
- Asked the owner for the path/format of an existing export; none supplied yet.
  D1–D7 and all activation work remain unchecked.

### Known limits of the first milestone

- Only saved full-conversation JSON snapshots; CSV summary exports need a separate
  adapter or a fuller source export. A separate read-only snapshot utility now
  exists outside the offline application; no live provider client is in intake.
- Attachments are metadata only; remote images/URLs are never fetched.
- Changed existing source snapshots are conflicts, not incremental synchronization.
  Use a fresh workspace for a changed export until update semantics are designed.
- One local operator; no production user accounts, provider tools or AI. Snoozed
  is currently a status label, without a timer or automatic reopening.
- No simulated inbound routing or reply delivery yet. The test screen is separate
  from the production Inbox; connecting that Inbox is tracked under E1.
- Python audit-hook denial is defense in depth, not an OS sandbox against hostile
  code. Sample fidelity is now verified below; production readiness and whole-
  account migration completeness remain unproven.

## Next action

All 25 A–E6 milestones are complete within their recorded offline/sample/design
scope. Final verification: 155 backend tests, four synthetic browser suites and
the fresh saved-data/cutover rehearsal passed; no material review findings remain.
The system remains offline. `intake/READINESS.md` records unproven production
prerequisites and `intake/CUTOVER.md` records the future cutover/rollback process.
These are future production gates, not claims that live replacement is ready.
No approval question is pending, and no live transition is authorized or queued.

### 2026-09-28 — authorized read-only export and real-data rehearsal

- The owner asked us to export the data ourselves. Used only the existing
  read-only Gorgias MCP and three verified read-only tools; no credentials copied,
  provider writes, channel changes, public hosting, service restarts or deployment.
- Added the separate `intake_export/` operator utility, outside both the offline
  application and production deployment inventory. It captures bounded test
  samples, follows message pagination, checks ticket timestamps before/after,
  records failures and hashes files. It does not automatically import anything.
- Captured all 50 selected recent tickets without failures: **211 messages,
  22 attachment references, 17 open and 33 closed tickets**. This is a sample,
  not a complete account export. No attachment bytes were downloaded.
- Private export: `intake/exports/gorgias-20260928T093215-921504Z/`.
  Each file is 0600 and the directory is 0700; all are ignored by Git.
- Offline rehearsal: `python3 -m intake.rehearse
  intake/exports/gorgias-20260928T093215-921504Z
  --workspace gorgias-export-review-20260928 --account buttonsbebe`.
  Ran with the sandbox's outbound network/process guard installed.
- **2,088 field checks passed**, including full original ticket/message records,
  source mappings, timestamps, status, assignment, tags, message author/visibility,
  display text, headers and attachment metadata. Counts matched: 50 tickets,
  211 messages, 48 linked contact records and 22 attachment references.
- Injected a storage failure at the end of one real conversation import: all
  tables rolled back to their prior counts. Retried successfully. Reopened the
  database and re-imported all 50 files: no duplicate records or changed IDs.
- Private report: `intake/.local/gorgias-export-review-20260928/reconciliation.json`.
- Coverage gaps: this sample has **no private notes, spam or trashed tickets**;
  private notes remain synthetically tested. **Four email messages lack headers**
  in the provider response. **None of the 50 detail responses supplies
  messages_count**; pagination exhaustion was verified, but no independent source
  message total was available. All 211 messages have retained full text in this
  sample. These limitations must carry into migration/threading acceptance.
- Source data was never printed, included in screenshots or sent to an AI service.
  No real-data browser server was started. The offline application remains unable
  to receive live messages or send anything.
- Final verification: `bash intake/verify.sh` **32 tests passed**, including new
  export pagination, source-change detection, checksum validation and deliberately
  corrupted-import detection. JS syntax, exporter compilation and diff whitespace
  checks passed. Confirmed exports/reports are ignored and permission-restricted.

### 2026-09-28 — D4 offline inbound replay

- Added `replay-preview` and `replay` CLI commands, an explicit `offline_replay`
  file contract and a seven-event synthetic fixture. [Replay instructions](intake/REPLAY.md)
  document supported headers, transaction behavior, queue and reopen rules.
- Added durable event receipts, provider-ID aliases and RFC Message-ID deduplication.
  Changed content under an existing identity fails the complete batch. Preview,
  concurrent/repeated delivery, restart and injected storage failure are tested.
- Header matching requires the same account/mailbox and a known participant.
  Same subject/customer without references creates separate tickets. Missing,
  ambiguous or unknown references create visible Needs review tickets. No fuzzy
  merge or later automatic merge of out-of-order messages is performed.
- Genuine newer replies reopen eligible closed/waiting/snoozed Inbox tickets.
  Late messages, agent echoes, internal notes, spam and automatic responses do not
  reopen work. Timestamp comparisons preserve microseconds. Priority/assignment
  edits do not reset the last status-change barrier.
- Added queue filtering and simulation labels to the local UI. Spam and unmatched
  automatic messages remain inspectable in their own queues. Matched automatic
  replies remain in the thread without changing status. Queue release/merge
  controls, full MIME/SMTP parsing, authentication and timed wake remain future work.
- `bash intake/verify.sh`: **50 tests passed** (18 added since D3); JS syntax and
  diff whitespace checks passed. Replay also executes successfully with outgoing
  network/process operations blocked. HTTP replay/ingress routes are absent.
- Browser integration: **passed**, including CLI preview/commit/repeat and all
  three new queue views, labels and review reasons. Desktop/mobile screenshots
  inspected; widths 320–1440px passed, no JS errors and **zero external requests**.
  Synthetic artifacts: `intake/.local/browser-1790590400112/`.
- Reproducible saved-export proof:
  `python3 -m intake.replay_rehearse
  intake/exports/gorgias-20260928T093215-921504Z
  --workspace gorgias-replay-proof-20260928-v3 --account buttonsbebe`.
  All **265 assertions passed**. Historical replay of **211 messages** suppressed
  **135 incoming duplicates** and ignored **76 echoes/self-addressed messages**;
  no new historical messages, status changes or outbound actions were produced.
- **206 fabricated follow-ups** threaded to the expected **49 original customer
  conversations**, preserving the 50-ticket total. All **33 eligible closed
  tickets reopened**. Preview left every row unchanged; restart and full replay
  left every stored row unchanged. These are local fabricated replies to saved
  headers, not actual customer messages or delivery tests.
- Coverage limits: **four source messages have no usable headers**; **one
  self-addressed message is not a customer reply anchor**. The first rehearsal
  exposed that self-addressed case and the proof now excludes it explicitly;
  it is correctly ignored during historical replay. Spam/private-note edge cases
  remain synthetic, since the real sample contains none. Sender matching is not
  cryptographic authentication and cannot authorize a future live adapter.
- Private report:
  `intake/.local/gorgias-replay-proof-20260928-v3/replay-reconciliation.json`.
  Export, databases, reports and screenshots remain Git-ignored; real-data
  directories are 0700 and files 0600. The original D2/D3 database remains at
  schema version 1 with 50 tickets/211 messages; the new additive schema migration
  is separately tested. No real-data browser was started, no new export made,
  no production service changed and no live intake/delivery enabled.

### 2026-09-28 — D5 fake reply delivery

- Added [fake delivery instructions](intake/DELIVERY.md), a local review/confirm
  dialog and a persistent delivery ledger. Sender, recipient, body, subject,
  incoming parent, scenario and ticket revision are frozen for ten minutes.
  Confirmation rechecks revision and recipient/source evidence. The latest
  incoming message must be an eligible Inbox email; ambiguous/missing headers,
  alternate Reply-To, multiple mailboxes, self-addressed or automatic mail block
  simulation. Arbitrary recipient overrides and extra API fields are refused.
- Durable reservation precedes the SQLite-only fake dispatch. Acceptance evidence
  commits separately from the atomic outgoing-message/ledger/audit transaction.
  Repeated or concurrent confirmation returns one attempt. Fresh operation IDs
  cannot duplicate an accepted reply with the same body, envelope and parent.
- Fixed fake outcomes exercise success, definite rejection, acceptance with a lost
  acknowledgement, and unknown acceptance. No automatic retry exists. Unknown or
  absent evidence blocks further attempts, including after restart. Explicit
  reconciliation can materialize a proven fake acceptance once; only a definite
  rejection permits a new reviewed retry referencing the prior attempt.
- Fake outgoing messages are labeled individually and indexed for subsequent D4
  inbound threading. Ticket statuses and original source records are preserved.
  Schema version 3 upgrades existing version 1/2 workspaces additively; both
  migration paths are tested. No original proof workspace was upgraded.
- `bash intake/verify.sh`: **68 tests passed** (18 added since D4), JS syntax and
  source whitespace checks passed. Includes stale/expired/cross-ticket review,
  source-recipient change without a revision, duplicate and concurrent confirmation,
  explicit retry, ambiguous targets, long reference chains, wrong receipt identity,
  crashes before dispatch and after acceptance, API restrictions and successful
  fake simulation/reconciliation with network/DNS/process operations denied.
- Browser integration: **passed**, including cancel without dispatch, frozen
  review and confirmation, double-click suppression, stale-review rejection,
  uncertainty after reload, explicit receipt reconciliation and reviewed retry.
  Dropped the HTTP response after a committed simulation: refresh recovered the
  ledger and a new attempt for the identical reply was blocked. Unknown evidence
  kept the reply button disabled. No JS errors or external browser requests.
  Desktop/mobile widths 320–1440px passed; inspected synthetic screenshots in
  `intake/.local/browser-1790592021924/`. The test server was stopped afterward.
- Saved-export proof:
  `python3 -m intake.delivery_rehearse
  intake/exports/gorgias-20260928T093215-921504Z
  --workspace gorgias-delivery-proof-20260928-v3 --account buttonsbebe`.
  **385 assertions passed**, using all 50 saved tickets/211 source messages.
  **46 conversations were eligible**: 12 successes, 12 accepted-but-timed-out
  simulations, 11 rejections followed by explicitly reviewed successful retries,
  and 11 unknown outcomes. Result: **57 fake attempts**, **35 simulated outgoing
  messages**, 11 retained rejection records and **11 unresolved records with retry
  blocked**. All source records, ticket totals and ticket statuses stayed intact;
  restart + repeated confirmation/reconciliation changed no stored rows.
- Four conversations were withheld: one self-addressed message and three latest
  incoming messages without usable reply evidence. This remains a bounded sample.
  Automated confirmations prove the local workflow contract, not human identity;
  fake receipts do not establish real provider delivery guarantees. CC/BCC,
  alternate-recipient routing, attachments and real adapters remain outside D5.
- Private report:
  `intake/.local/gorgias-delivery-proof-20260928-v3/delivery-reconciliation.json`.
  Earlier proof directories and the saved export remain preserved; a terminated
  intermediate D5 rehearsal is retained separately and is not counted as a pass.
  Reports/DBs stay Git-ignored in 0700 directories with 0600 files. No customer
  content was printed or shown in screenshots. No new export, production changes,
  real delivery, deployment or live intake activation occurred.

### 2026-09-28 — D6 private attachments and recovery

- Added [attachment and recovery instructions](intake/RECOVERY.md). Explicit local
  manifests map existing attachment IDs to bounded files with mandatory SHA-256
  checksums. Preview, atomic import, repeat suppression, deduplicated private BLOBs
  and checksum-verified local extraction are implemented. Known source sizes are
  checked; different bytes cannot replace an existing copy. URLs, path traversal,
  links, FIFOs and oversized files are refused. No attachment is downloaded,
  parsed, opened, executed or served over HTTP. The UI displays copy availability.
- Added SQLite online backups with committed WAL state, self-contained database
  files, private manifests and schema/relationship/blob/ledger validation. Private
  staging is synced before publication. Restore accepts only a verified backup
  into a new workspace; existing backups/workspaces cannot be replaced. Corrupt
  or interrupted work is never published as a completed backup/restore. Recovery
  works without the original working database.
- Schema version 4 preserves history and adds attachment storage and review
  generations. Recovery keeps all business rows and stable IDs, adds provenance
  and invalidates unconfirmed reviews predating the restore. Existing attempts
  and receipts remain reconcilable. Known fake acceptance can recover one outgoing
  message; missing evidence stays blocked. Restore never dispatches or retries.
- `bash intake/verify.sh`: **84 tests passed** (16 added since D5), JS syntax and
  diff whitespace checks passed. New cases cover bad hashes/size/identity, unsafe
  paths and files, concurrent attachment imports, all-or-nothing rollback, private
  copy permissions, full-row restore fidelity, corruption, missing/linked backups,
  weakened indexes, failure during publication, WAL data, recovery without the
  original database, invalidated reviews and uncertain/accepted fake receipts.
  Attachment import, backup and restore also run successfully under the outbound
  network/DNS/process guard; HTTP file and recovery routes remain absent.
- Browser integration: **passed**, including attachment preview/import/repeat,
  availability after refresh, inert malicious-looking file bytes, backup/verify/
  restore, recovered byte equality and preservation of unresolved attempts. Private
  file URLs return 404. Desktop/mobile widths 320–1440px passed, with no JavaScript
  errors and **zero external browser requests**. Inspected synthetic screenshots
  in `intake/.local/browser-1790597095594/`; the test server stopped afterward.
  Tightened the browser harness umask to 077 after its synthetic screenshots were
  found at 0644; corrected those artifacts to 0600. Re-ran the browser suite with
  explicit screenshot/report permission assertions: **passed**, artifacts in
  `intake/.local/browser-1790597347690/`. No intake test server remains running.
- Saved-data recovery proof:
  `python3 -m intake.recovery_rehearse
  --source-workspace gorgias-delivery-proof-20260928-v3
  --workspace gorgias-recovery-proof-20260928`.
  **95 assertions passed**. Cloned the D5 database; only the clone was upgraded.
  All 19 non-metadata tables matched their checkpoint rows after restore, including
  all **261 original source records**. Two separate synthetic attachment references
  shared one **40-byte blob**, recovered byte-for-byte. A corrupt backup was
  refused without creating its destination. The source D5 file hash was unchanged.
- The restored proof retains the **11 original unresolved delivery attempts**.
  One additional synthetic accepted-but-timed-out attempt recovered exactly one
  message from its stored receipt. Restart and repeated confirmation/reconciliation
  of all 58 checkpoint attempts made no further changes or fake dispatches.
  A pre-checkpoint review confirmed later in the original clone was correctly
  refused in the restored workspace, where that later attempt was absent.
- Private report:
  `intake/.local/gorgias-recovery-proof-20260928/recovery-reconciliation.json`.
  Restored copy: `intake/.local/gorgias-recovery-proof-20260928-restored/`.
  The intentionally corrupt proof backup is retained separately for inspection.
  Earlier source export/proof workspaces remain preserved; private artifacts stay
  ignored by Git with 0700 directories and 0600 files.
- Coverage limits: the **22 real attachment references remain metadata-only**;
  no real attachment bytes exist in this saved sample. Synthetic checks prove
  the storage/recovery mechanism, not real file fidelity. Backups cover database
  state and stored blobs, not separate export/report/screenshot files. Changes
  after the checkpoint are absent from the restored copy. Local private backups
  are neither encrypted nor offsite. Cross-copy/real-provider recovery requires
  later design. D7 owner acceptance remains unchecked. No new export, provider
  call, live site change, real intake/delivery, public hosting or deployment occurred.

### 2026-09-28 — D7 review prepared; owner decision pending

- Added [owner review instructions](intake/OWNER-REVIEW.md) and
  `python3 -m intake.owner_review`, which verifies the saved snapshot and imports
  into a fresh private workspace. It never records owner acceptance. Earlier
  proofs and source export files are preserved; no provider access is involved.
- Rechecked **50 tickets / 211 messages** with **2,388 passing checks**: 2,088
  source-record/field checks plus 300 displayed-contact, chronological-order,
  message-count and history-only checks. Repeat import and injected rollback also
  passed. All 261 source records are retained; no simulations were added.
- Selected **seven conversations / 31 messages** for structural review: longest
  thread (24 messages, also with attachments), an open reply-eligible ticket,
  a closed staff/customer exchange, missing latest-message evidence, self-addressed
  mail, and two separate tickets from one customer. Selection is not random or a
  claim of whole-account coverage. Full source/imported fields and text appear
  together with derived reply eligibility; customer HTML/Markdown stay inert.
- Current packet:
  `intake/.local/gorgias-owner-review-20260928-v2/START-HERE.md`.
  `owner-review.md` provides a native file-viewable comparison; `owner-review.html`
  provides expandable columns for a browser on the workspace machine. Private
  `owner-review.json` records case mappings, checks, gaps, digest and **pending**
  acceptance. The first HTML-only packet is retained separately; v2 fixes mobile
  wrapping and adds the Codex-readable Markdown comparison. The app refused to
  open a remote file URL in its local browser; nothing was publicly hosted.
- **88 backend tests passed**, including four owner-review tests for pending
  acceptance/no live authorization, unchanged source/refused workspace overwrite,
  hostile markup escaping, corrupt-source refusal and display mismatch rejection.
  The updated Markdown fence handling also passed the four focused review tests.
  The real packet's browser check passed at widths 320–1440px: seven cases and 31
  messages, expanding comparisons, no scripts/media/forms, no JavaScript errors,
  **zero external requests** and **zero screenshots**. Browser evidence is private.
- The review explicitly retains the 22 missing real attachment files, absent
  private notes/spam/trash, four unusable-header messages, four withheld reply
  targets, missing independent message counts, bounded/long-thread coverage and
  later authentication/adapters/recovery/cutover work. Six workflow behaviors are
  presented for acceptance. This is a sample review, not live activation approval.
- D7 remains unchecked until the owner reviews the packet and records acceptance
  or requested corrections. No live site change, provider call, real intake/send,
  new export, production service operation, push or deployment occurred.

### 2026-09-28 — D7 owner acceptance recorded

- Exact owner instruction: **"Accept D7 for the offline sample with the listed
  gaps"**. Marked D7 complete for the reviewed seven-case sample and six workflows.
  The acceptance covers the bounded offline sample, not whole-account migration
  completeness or any live activation.
- Saved the exact statement, recording timestamp, scope, packet digest, source
  manifest digest, workflows, retained gaps and original artifact hashes in
  `intake/.local/gorgias-owner-review-20260928-v2/owner-decision.json`.
  `OWNER-DECISION.md` provides the readable decision. Both remain private and
  Git-ignored. All seven existing packet/evidence files are unchanged; their
  original pending status is historical and superseded by this separate decision.
- No runtime changes or new tests were needed for recording the decision. The
  existing 2,388 source/display checks and 88 passing backend tests remain the
  evidence. E1 is the next implementation task; all E items remain unchecked.
  Live intake, sending, provider access and deployment remain disabled/unauthorized.

### 2026-09-28 — E1 isolated Inbox integration

- Added [Inbox run instructions](intake/INBOX.md). Reused the current Inbox shell,
  wordmark, stylesheet and Lucide icons under `intake/inbox/`; recorded original
  hashes in `provenance.json`. The dedicated client uses the canonical offline
  ticket API, checks offline capabilities and contains no live sender, AI,
  authentication or Shopify routes. No production UI/runtime file was changed.
- `/inbox/` is served only by the existing loopback sandbox with fixed asset
  allowlists, token/origin protections and its network/process denial. The intake
  lab remains at `/` with the same database. There is no public route or service.
- Connected database-backed ticket creation, private notes, status/priority/
  assignment, search, 50-row pagination, queue views, source/activity details and
  the existing fake delivery review/confirm/receipt ledger. Browser-local
  production ticket keys are ignored. Draft edits stay in page memory; revision
  conflicts preserve notes; a lost response retains the operation for explicit,
  unchanged retry without a duplicate write. No automatic write retry exists.
- Preserved the three-column Inbox, bottom composer and mobile rail/navigation.
  Short-screen header/composer limits retain usable conversation space. All
  message text and line endings render directly through text nodes; imported
  markup and attachment references cannot load remote content. Missing/ambiguous
  reply evidence leaves simulation disabled. AI and customer/order lookups are
  explicitly unavailable; authentication/team workflow remains E2.
- `bash intake/verify.sh`: **90 tests passed**, JS syntax and whitespace checks
  passed. Added HTTP asset/isolation and copied-source provenance checks. Existing
  fake delivery, replay, attachment/recovery and owner-review tests remain green.
- New Inbox browser suite: **passed**, including a fresh second browser reading
  the same ticket/notes/details, stale-note preservation, lost-response retry,
  production-localStorage isolation, 59-ticket pagination, text search, queue
  views, all D5 delivery outcomes, server errors/recovery, literal markup/CRLF,
  null source tags, offline capability refusal and keyboard-accessible mobile rail.
  No external or live-route requests and no JS errors. Checked widths 320–1440px
  and short heights down to **320×568** with the composer visible.
- Inspected synthetic desktop/mobile screenshots in
  `intake/.local/inbox-1790619837490/`; browser evidence is retained alongside them.
  The original lab browser suite also passed, with artifacts in
  `intake/.local/browser-1790619734051/`. Test servers stopped after verification.
- Reconciled the saved snapshot into a fresh workspace:
  `python3 -m intake.rehearse
  intake/exports/gorgias-20260928T093215-921504Z
  --workspace gorgias-inbox-proof-20260928 --account buttonsbebe`.
  **2,088 field checks**, interrupted rollback and repeat import passed. Then ran
  `node intake/tests/browser-inbox-saved.mjs gorgias-inbox-proof-20260928`
  with the existing Playwright installation. All **50 tickets / 211 messages /
  22 attachment references** matched the new Inbox display. **46 eligible / four
  withheld reply targets** matched the API. Every stored table was unchanged;
  **zero external requests, zero write requests and zero screenshots**.
- The saved-data check exposed browser normalization of CRLF during HTML
  rendering; direct text assignment fixed it and a synthetic regression now
  covers CRLF/standalone carriage returns and literal script text. Also fixed a
  concurrent refresh race that hid list errors and a short-screen message-area
  limitation. Failed intermediate proof outputs remain private and are not
  counted as success. Final report:
  `intake/.local/gorgias-inbox-proof-20260928/inbox-display-reconciliation.json`.
- Earlier proofs, D7 acceptance and the saved source export remain preserved.
  Private runtime evidence stays Git-ignored with 0700 directories/0600 files.
  No live site/service changes, provider calls, new exports, real messages,
  public hosting, branch push or deployment occurred. E2 is next; E2–E6 remain
  unchecked and the accepted source-coverage gaps remain unresolved.


### 2026-09-28 — E2 local users, permissions and team workflow

- Replaced the anonymous per-server API token with individual local sign-in on
  both `/inbox/` and the import lab. Added scrypt passwords, random HttpOnly /
  SameSite=Strict session cookies, session-bound CSRF protection, generic login
  failures and local throttling. Sessions expire after eight hours and are bound
  to the current server process. No default accounts, production credentials,
  external identity provider or email invitation path exists.
- Added CLI account creation/edit/reset/disable, last-active-admin protection,
  self-service password changes and sign-out. Role/name/password/active changes
  revoke all of that user's sessions. Browser sign-out/account switching clears
  other tabs; revoked sessions cannot write. Roles are checked server-side and
  rechecked inside storage transactions, including before cached operation results.
- Admins can import saved exports; agents/admins can work tickets and simulate
  replies; viewers can read and change only their own read state. Verified names
  and stable local IDs identify audit actors; operation IDs are user-scoped.
  Fake reply confirmation requires the same user who reviewed it. Shared receipt
  reconciliation retains the existing uncertainty and duplicate protections.
- Local assignments reference active named admins/agents. Claim is atomic;
  stale/competing claims fail. Handoff/unassignment use ticket revision checks.
  Imported source assignment remains separate and unchanged. Inbox team views
  include Everyone, Assigned to me, Unassigned and Unread for me.
- Read/unread state is per user and explicit, using a displayed-message watermark
  plus a separate read-state version. New messages outside the displayed snapshot
  stay unread; an older tab cannot undo a newer mark; shared ticket revisions and
  other users' unread state are unaffected. All message kinds count; status and
  assignment changes do not. Refresh reads other teammates' changes.
- Schema 5 adds identity/session/assignment/read/review-owner tables. Additive
  migration leaves historical records intact. The frozen schema-4 definition
  preserves validation and restoration of existing backups. Recovery retains
  users/password hashes/assignments/read state, clears every session and login
  throttle record, and invalidates pending old reviews.
- `bash intake/verify.sh`: **102 tests passed**, including all original 90 and
  12 E2 tests. JS syntax and whitespace checks passed. Coverage includes anonymous
  refusal, CSRF/origin checks, direct viewer/agent permission bypasses, forged
  actors, user-isolated idempotency, stale/future/foreign read watermarks,
  concurrent claims, password change/disable/logout/expiry/restart revocation,
  role revocation inside transactions, throttling, review ownership, migration
  and backup/restore. Production source hashes and release exclusion passed.
- Synthetic browser suites all passed with real authenticated local backends:
  `browser-inbox.mjs`, `browser.mjs`, `browser-team.mjs`. The team suite used
  three separate admin/agent/viewer browser contexts and checked claim races,
  handoff, individual unread state, concurrent arrivals, role restrictions,
  verified note authors, browser-storage absence, cross-tab logout with a draft,
  revoked-user write refusal and password changes. No external requests or page
  errors. Inspected screenshots at desktop and 320px; widths 320–1440px passed.
  Private synthetic artifacts:
  `intake/.local/inbox-1790625509116/`,
  `intake/.local/browser-1790625712251/`,
  `intake/.local/team-1790625621846/`.
- Fresh saved-export workspace `intake/.local/gorgias-team-proof-20260928/`:
  **50 tickets, 211 messages, 22 attachment references, 2,088 field checks**;
  repeat/restart/rollback proof passed. Authenticated display reconciliation
  matched all 50 conversations, retained 46 eligible/four withheld reply targets,
  and changed no stored rows during viewing. Login/account provisioning precede
  that read-only baseline. No customer screenshots or raw customer output.
- Saved-data team proof passed **150 per-user read checks** across three users,
  plus named handoff, stale claim refusal, viewer write refusal, agent import
  refusal and cross-user fake-confirmation refusal. Original source/message rows
  stayed unchanged. The pending ownership-test review was never dispatched.
  Private reports: `reconciliation.json`, `inbox-display-reconciliation.json`,
  `team-saved-proof.json`. Checkpoint `e2-team-checkpoint` restored to new workspace
  `gorgias-team-proof-20260928-restored`: users, 50 read watermarks and one local
  assignment preserved; **zero restored sessions**, pending reviews invalidated.
- Accepted D7/E1 proof workspaces and decision artifacts were not reopened or
  modified. Test servers stopped. No live reads, ingress, egress, provider/AI
  calls, notifications, service changes, public hosting, push or deployment.
- Scope retained: all users can read all tickets within their workspace;
  assignments are workflow ownership, not per-ticket access restrictions.
  Production SSO/MFA/TLS, custom/team-specific permissions, account recovery,
  realtime collaboration and activation remain unproven/out of scope. E3 is next.


### 2026-09-28 — E3 native-ID assistance and context fixtures

- Read the existing production draft/context projection contracts without
  importing their runtime or changing production files. Preserved draft state,
  source message, priority/review metadata, missing facts and staff next step in
  new versioned input/fixture/request/result contracts using independent ticket,
  contact and message IDs. External Gorgias IDs remain provenance only.
- Added explicit private input export and preview/digest-confirmed fixture import
  through the CLI. No HTTP fixture/result-ingress endpoint, model/provider client,
  dynamic adapter URL, credential/config loader or production processor import.
  Existing live capabilities remain false; assistanceFixtures explicitly identifies
  this local test capability. Startup/opening a ticket does not run a fixture.
- Context carries exact local identity binding, capture/expiry times, independent
  customer/orders/returns/products/knowledge availability and evidence IDs.
  Missing/conflicting identity, expired/replaced context and unavailable facts
  cannot be treated as current evidence. Attachment IDs/availability/digests are
  included, while remote URLs and bytes are excluded. All conversation bodies
  remain untrusted data. Inputs above 1,000 messages/2 MiB are refused intact.
- Added durable fixture runs with response-token/native-ID/input/fixture binding,
  validated outcomes, user-scoped idempotency and verified audit actors. Ready,
  needs_staff, no_reply, timeout/malformed failure, stale and dismissed states are
  exercised. Missing staff facts never become customer-facing text; failures never
  produce a fallback acknowledgment; no_reply never closes tickets. Sensitive
  priority is preserved and suggestions do not change actual priority/status.
- Inbox and lab now show saved test context, cited test suggestions and explicit
  Run fixture / Use test suggestion / Dismiss actions. Viewers can inspect but
  cannot act. Existing handwritten replies are preserved until explicitly cleared.
  All fixture/body/evidence text remains inert, including literal markup.
- Use validates inside its write transaction before cached operation results.
  Any used suggestion stays linked to the frozen fake reply review, including
  human edits. Confirmation checks it again: changed messages/assignment/context,
  expiry, replacement, dismissal or recovery blocks an old suggestion. The D5
  review/confirm/receipt workflow and same-reviewer requirement remain in force.
  No implicit execution, delivery, retry, alert or status mutation is introduced.
- Schema 6 adds fixture/run/dismissal/review-association tables. Schema-4 and
  schema-5 backups remain supported through frozen schemas. Recovery validates
  fixture digests, request bindings and cross-ticket associations; preserves
  evidence; invalidates old suggestions/linked unconfirmed reviews; and revokes
  sessions. Pending runs do not automatically resume after crash or restore.
- `bash intake/verify.sh`: **119 backend tests passed** (102 existing + 17 E3),
  JS syntax and whitespace checks passed. E3 coverage includes source-account
  collisions, independent-ID-only lookups, private export, atomic preview/import,
  invalid/unknown contracts, wrong identity/citations, staff-text withholding,
  explicit use/review/confirm, priority preservation, repeat requests, late results,
  changed/expired/oversize inputs, invalid result bindings, dismissal, role checks,
  unchanged read-state hashes, recovery invalidation and corrupted associations.
  The existing outbound-guard subprocess proof now also runs assistance fixtures
  while network/DNS/process creation are denied. Production source hashes and
  exclusion from the release inventory passed.
- All **four synthetic browser suites passed**: the new
  `browser-assistance.mjs` and existing Inbox, lab and E2 team suites. Six E3 cases
  cover ready/missing-facts/no-reply/timeout/malformed/conflicting identity, native
  input IDs, existing manual draft preservation, new-context refusal at final
  confirmation, fake accepted-timeout reconciliation, lost-response deduplication,
  viewer restrictions, durable dismissal and inert result/context markup.
  Zero page errors/external requests. Inspected synthetic screenshots at desktop
  and 320px; widths 320–1440px passed. Private artifacts:
  `intake/.local/assist-browser-1790632217688/`,
  `intake/.local/inbox-1790632417754/`,
  `intake/.local/browser-1790632417729/`,
  `intake/.local/team-1790632417877/`.
- Created fresh `intake/.local/gorgias-assistance-proof-20260928/` from the saved
  export: **50 tickets, 211 messages, 22 attachment references, 2,088 fidelity
  checks**; repeat/restart/rollback passed. Prepared explicitly unavailable context
  fixtures, without inventing real customer/order/return/product/policy facts.
  Authenticated browser proof passed **261 source/message bindings**, **100
  explicit fixture runs**, **50 deduplicated repeat requests** and **50 blocked
  staff-only Use requests**. All saved business rows, messages, status and priority
  stayed unchanged; no simulated outgoing messages or delivery attempts were made
  on this real-data copy. Zero model calls, external requests or screenshots.
- Private saved-data reports: `reconciliation.json`,
  `assistance-preparation.json`, `assistance-saved-proof.json`. Checkpoint
  `e3-assistance-checkpoint` restored to new workspace
  `gorgias-assistance-proof-20260928-restored`: **100 runs preserved, zero restored
  sessions**. The synthetic recovery tests additionally prove old suggestions and
  cached Use results are refused after restore.
- Added `ASSISTANCE.md` with the contract, operator workflow, synthetic demo,
  fixture shape, readiness rules, recovery behavior and proof commands. The demo
  is explicit and refuses an existing workspace. Intermediate failed synthetic
  setup remains private and is not counted as proof.
- Earlier accepted D7/E1/E2 workspaces, decisions and exports stayed intact.
  All temporary test servers stopped. No live site/services, new exports,
  provider reads/writes, real traffic, model calls, alerts, hosting, pushes or
  deployments occurred. Genuine model/retrieval quality and production integration
  remain unproven. D7 gaps remain; E4 is next.

### 2026-09-29 — E4 fake channel boundary, durable jobs and recovery

- Added the production boundary design and current fake contract in
  `intake/CHANNELS.md`: authenticated channel/account/mailbox registration,
  provider-specific compatibility gates, separate staff/workload identities,
  durable acknowledgement, reviewed submission and delivery reconciliation,
  monitoring ownership and recovery fencing. Provider/vendor selection, real
  authentication integration and activation remain future work.
- Implemented only a locally signed file adapter. Generated workspace-only fake
  keys support overlap/revocation; signatures bind raw body, channel, key, purpose,
  delivery ID and signing time. Invalid signatures, stale/future timestamps,
  cross-account/mailbox/provider bodies, changed identities, malformed Unicode,
  capacity excess and unsupported/live payloads fail closed. Preview does not
  write; admission commits captures and jobs together before returning success.
  There is no receiver route, SDK, SMTP, provider credential or activation flag.
- Schema 7 adds durable jobs with fenced 60-second leases, one active worker per
  channel, three automatic attempts, 30/120-second delayed retries, redacted
  failures, dead jobs and an explicitly reviewed local retry. Ticket/message
  changes, deduplication receipt and completion commit atomically. Other channels
  remain claimable; delayed/unknown parents retain the existing review behavior.
  Fixed new-contact identity to include provider as well as account/mailbox/event
  so two providers reusing an event ID cannot collide.
- Added signed fake delivery observations tied to reviewed digest, attempt,
  transport receipt, account and mailbox. Late accepted/deferred observations
  cannot erase delivery/bounce/complaint evidence. Contradictory delivered/bounced
  evidence reports conflict. Observations never send, resubmit or clear uncertainty;
  D5 still needs explicit reconciliation from durable fake acceptance evidence.
- Added `jobs-health` aggregate counts/ages/error codes and attention signals,
  with no identifiers/customer content, polling or alerts. CLI-only admission,
  key, worker, retry and resume commands cannot be invoked under browser identity.
  The existing staff Inbox API is unchanged.
- Backups validate signed capture → immutable job → receipt/observation bindings.
  Restore revokes sessions and fake keys, fences old claims, invalidates old reply
  reviews and holds both intake admission and job processing until the exact local
  recovery-plan digest is reviewed. Schema 6 is frozen for historical verification;
  schema-4/5/6 backups remain supported. Older restores acquire the intake hold too.
- Added **26 focused E4 tests** covering authentication, namespaces, rotation,
  capacity, concurrent workers, receipt timing/conflicts, atomic failure rollback,
  lost success responses, exhausted leases, bounded retries, dead-job recovery,
  redacted health, restore holds/revocation and corruption/migration checks. The
  existing offline boundary test now runs signed intake jobs under denied network,
  DNS, datagram and subprocess access, with live-enabling environment flags set.
- Final `bash intake/verify.sh`: **145 backend tests passed**, including all
  26 E4 tests; JavaScript syntax and diff whitespace checks passed. Additional
  whitespace checks covered the new, still-untracked E4 files.
- Synthetic Inbox browser regression **passed**, including fake reply workflows,
  durable ticket/note edits, stale/lost responses, queues, search, paging and
  responsive behavior; zero external requests. Private synthetic artifacts:
  `intake/.local/inbox-1790667638968/`. Its local server was stopped afterward.
- Full CLI smoke **passed** in `intake/.local/e4-cli-20260929/`: generated local
  channel/key, private signed file, preview, seven admitted and completed jobs,
  seven repeated events without new jobs, clean health and integrity verification.
  Report: `cli-proof.json`; no signing secret was printed.
- Fresh saved-export rehearsal **passed** with **2,088 fidelity checks plus 460
  E4 assertions**. Command:
  `python3 -m intake.channel_rehearse intake/exports/gorgias-20260928T093215-921504Z --workspace gorgias-channel-proof-20260929 --account buttonsbebe`.
  Original sample: **50 tickets / 211 messages / 22 attachment references**.
  Signed history produced **135 duplicate incoming messages and 76 ignored
  historical echoes**, with business rows unchanged. **206 fabricated follow-ups
  threaded to 49 original tickets**, and all 206 passed again after restoring a
  checkpoint containing one leased and 205 queued jobs. Repeated admission and
  restart produced no new business rows. All **261 original source records** stayed
  unchanged. Gaps: four unusable thread anchors and one self-addressed anchor.
- The saved-data proof also exercised two fabricated delivery attempts, four
  signed observations and explicit reconciliation: one local outgoing row after
  proven fake acceptance, one unknown attempt still blocked, one contradictory
  receipt group visible in monitoring, and no redispatch. Final original proof:
  **421 completed jobs / 50 tickets / 418 messages**; restored checkpoint proof:
  **417 completed jobs / 50 tickets / 417 messages**. Health intentionally shows
  `unresolved_delivery` and `receipt_conflict` in the original proof.
- Private report:
  `intake/.local/gorgias-channel-proof-20260929/channel-reconciliation.json`.
  Verified backup: `backups/e4-queue-checkpoint/`; restored proof:
  `intake/.local/gorgias-channel-proof-20260929-restored/`.
  Both current databases passed final integrity checks. Runtime directories and
  DBs remain 0700/0600 and ignored. No customer content/identifiers/screenshots were
  printed. D7/E1/E2/E3 proof databases retain their original schema versions.
- All work used saved data and fakes. No provider reads/writes, real intake/reply,
  AI call, notification, public endpoint, deployment, production service change,
  push or merge occurred. Real vendor signature/idempotency/delivery guarantees,
  production identity, mail authenticity/deliverability, MIME/file quarantine,
  multi-host ownership, production load/SLOs, encrypted offsite recovery, model
  quality and D7 source/attachment completeness gaps remain unproven.

### 2026-09-29 — E5/E6 completion with implementation and review agents

- Owner instruction: "create subagents for implementation and review. then you
  continue till the full tasklist is done, don't ask me again". Created separate
  implementation, evidence and independent review subagents. Continued through
  E5/E6 and verified review fixes without asking further questions. The original
  offline-only/no-live-site boundary remained in force.
- Added `intake/CUTOVER.md`: concrete roles and private change records; complete
  history/field/file reconciliation; stable native/source IDs; full-snapshot and
  delta limitations; final frozen boundary and late replies; one sending owner;
  routing transitions; stop conditions; rollback preserving post-checkpoint
  activity and uncertain submissions; and Gorgias retirement/dependency removal.
  Available local commands are separated from future production transitions.
  Current full snapshots require an empty canonical database unless a tested
  preserving update/journal path exists. No delta/buffer/provider-rollback or
  cross-host fencing implementation is invented or claimed.
- Added `intake/READINESS.md` and static redacted `intake/readiness.json`: owner
  proof results, D7's bounded acceptance and preserved gaps, exact permitted local
  scope, eight unproven production gates and an empty future bounded-change
  template. Decision remains **NOT READY FOR LIVE**; authorized live actions,
  channels and deployment targets are empty. The JSON is documentation, never
  runtime configuration or an activation token. README links both deliverables.
- Independent review found and verified three material fixes:
  - Password matching now retains input validity separately from the replacement
    text used for hash work. Empty/short/invalid-type inputs cannot authenticate
    or change a password even if the stored password equals that replacement
    literal. Two HTTP/direct regressions passed; valid credentials still work.
  - Recovery now checks every signed captured event retains its deduplicated job,
    including captures whose duplicate jobs belong to an earlier envelope.
  - Completed inbound results must retain the native message/ticket, correct
    ownership/content, original replay/import provenance, alias evidence where
    applicable and unchanged receipt payload. Missing/altered records cannot be
    certified by an otherwise matching receipt JSON. Eight recovery regressions
    cover deletion, substitution, corruption and valid repeat/import behavior.
- The reviewer independently reproduced the original failures and the fixes,
  reviewed the final code/tests and both E5/E6 documents, and reported **no
  remaining material findings**. Root also checked the final implementation.
- Final `bash intake/verify.sh`: **155 backend tests passed**, including all
  34 channel tests and the two new authentication regressions; JavaScript syntax
  and whitespace checks passed. Current private record/log:
  `intake/.local/checklist-completion-20260929/verification.json` and
  `backend-verification.log`. The record hashes reviewed source/test files.
- All four fresh synthetic browser suites **passed** with **zero external
  requests**; local test servers stopped afterward:
  - Lab: `intake/.local/browser-1790668385661/browser-result.json`.
  - Inbox: `intake/.local/inbox-1790668385661/inbox-browser-result.json`.
  - Team: `intake/.local/team-1790668299872/team-browser-proof.json`.
  - Assistance: `intake/.local/assist-browser-1790668300041/assistance-browser-proof.json`.
- Executed E5's saved-data command into fresh
  `intake/.local/e5-local-proof-20260929/`: **2,088 fidelity checks and 460
  assertions passed**, with 135 historical incoming duplicates, 76 ignored echoes
  and 206 fabricated follow-ups to 49 original tickets. The restored checkpoint
  repeated all 206 follow-ups. Original proof ended with 421 jobs/418 messages;
  restored checkpoint had 417 jobs/417 messages. All 50 tickets and 261 original
  source records remained preserved; all source/sample gaps remain unchanged.
  Report: `channel-reconciliation.json`; the original E4 proof was untouched.
- Executed the runbook's additional `e5-checkpoint` backup, verification and
  restore to `intake/.local/e5-local-proof-20260929-recover/`. All **421 jobs and
  418 messages** verified; the one intentionally unknown attempt and conflicting
  receipt group remained visible, and restored intake remained held. Inspected
  the local resume plan without releasing the hold. Private report:
  `runbook-recovery-proof.json`. No automatic submission/retry occurred.
- Independently verified all twelve historical proof hashes and seven original
  D7 packet artifacts. The E6 record now retains **19 evidence hashes**: twelve
  historical and seven current reports. Historical schema versions and accepted
  proof files remain unchanged. The stronger recovery validator also passed both
  original E4 databases read-only. New proof directories/files are private and
  ignored; no customer content, source IDs or real screenshots were published.
- All A–E6 boxes are now checked for their stated offline/sample/design scope.
  The system has no live activation switch. Real provider/authentication/migration/
  attachment/model/operations/recovery requirements remain in the readiness gate.
  No real intake, outgoing message, provider/model call, alert, public endpoint,
  deployment, live service/routing change, new export, push or merge occurred.
