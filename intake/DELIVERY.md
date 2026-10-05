# Fake reply delivery (D5)

This is a local simulator. It has no SMTP client, provider SDK, credentials,
delivery worker or real sending switch. Review records, fake receipts and
simulated outgoing messages stay in the private sandbox database.

## Use the local UI

1. Import the synthetic Gorgias fixture or replay the synthetic email fixture
   using the instructions in [README](README.md) and [REPLAY](REPLAY.md).
2. Select an Inbox conversation with a usable incoming email, then choose
   **Simulate reply…**. The mailbox and recipient come from saved message evidence.
3. Write a reply and choose a test outcome. **Review simulated reply** freezes
   the exact text, sender, recipient, subject, parent, scenario and ticket revision.
   It stores a review and audit event, but creates no attempt or outgoing message.
4. Inspect the frozen review and select **Confirm simulation**. Cancel or Edit draft
   cannot dispatch an attempt. Reviews expire after ten minutes. Editing requires
   a new review; a changed ticket requires refreshing and reviewing its context.
5. Inspect **Simulated delivery** in the details panel. Successful local records
   appear in the conversation as **Simulated reply · No email sent**.

| Test outcome | Initial ledger state | What happens next |
|---|---|---|
| Simulated success | Simulated delivery recorded | One local outgoing message; repeated confirmation has no effect |
| Rejected before acceptance | Rejected by simulator | No outgoing message; **Review retry** requires a new review and confirmation |
| Accepted, acknowledgement lost | Outcome uncertain | **Check fake receipt** records the accepted local message exactly once |
| Outcome unavailable | Outcome uncertain | Checking remains unresolved; all further attempts on that ticket stay blocked |

No outcome triggers an automatic retry. Reloading the browser or restarting the
server preserves the ledger. If the HTTP response is lost, close the dialog and
refresh the ticket to inspect the record. The dialog prevents another confirmation
after a failed response. A repeated API confirmation returns the existing attempt
without calling the fake transport again, including when that attempt is unresolved.

## Reply eligibility and duplicate protection

The latest incoming message must be an email in the Inbox queue with one known
sender, one destination mailbox and a usable, uniquely owned Message-ID. Private
notes, self-addressed mail, automatic responses, ambiguous evidence and separate
review/spam/automatic queues cannot be used as reply targets. A Reply-To header
must agree with the source sender; alternate-recipient routing requires future
work. There are no arbitrary recipient/CC/BCC controls or outgoing attachments.

Confirmation checks the current ticket revision, source recipient/mailbox evidence,
review digest, expiry and unresolved attempts in one write transaction. Notes,
status changes and newly received messages invalidate earlier reviews. It then
reserves a durable attempt and advances the revision so two parallel reviews
cannot both proceed. Confirming the same review is idempotent across concurrent
requests and restarts. A new operation/review ID cannot duplicate an already
accepted reply with the same text, envelope and incoming parent. A new incoming
parent permits a new reply; changing only the test scenario cannot bypass deduplication.

A retry is allowed only after a recorded, definitive fake rejection. It must
reference the latest failed attempt for that exact reply and pass a new review
against current context. Editing the reply creates a different intent and clears
the retry reference; unresolved delivery still blocks every intent on the ticket.

Accepted fake replies get independent message IDs and a stable generated
Message-ID. Offline incoming replies can thread against those IDs through the D4
replay engine. References retain up to the latest 99 ancestors plus the parent.
Simulation preserves ticket status and never moves its update timestamp backward.

## Durable failure model

The implementation deliberately uses separate transactions:

1. Persist the reviewed attempt as `attempting` before the fake dispatch.
2. Persist the fake transport's acceptance/rejection evidence separately.
3. Atomically update the ledger, outgoing message, source mapping and audit event.

A crash before dispatch leaves an unresolved reservation. Absence of a receipt
does not establish rejection, so reconciliation keeps it blocked. A crash after
fake acceptance but before the final commit leaves the acceptance receipt intact;
explicit reconciliation materializes the message once. A failed final commit
rolls back both the message and final ledger state. Unknown outcomes do not create
outgoing messages or claim success. Reconciliation reads fake evidence only and
does not dispatch. Conflicting receipt fingerprints leave the attempt unresolved.

Schema version 3 adds `delivery_reviews`, `delivery_attempts`, `fake_dispatches`
and `simulated_outgoing`; existing version 1/2 history is preserved on upgrade.
Imports and replay never create a delivery review or trigger fake dispatch.
Schema version 4 adds private attachment storage and a review-generation field.
Restoring a backup preserves attempts and receipts but invalidates old unconfirmed
reviews. See [recovery instructions](RECOVERY.md) for the checkpoint boundary.

## Local API

All routes retain the loopback Host/Origin/token checks and require the explicit
`mode: "offline_simulation"`. Extra request fields are refused. Production-style
send routes remain denied.

| Route | Required fields besides mode |
|---|---|
| `POST /api/tickets/{id}/simulation-review` | `operation_id`, `revision`, `body`, `scenario`; optional `retry_of` |
| `POST /api/tickets/{id}/simulation-confirm` | `review_id`, `digest`, `confirmed: true` |
| `POST /api/tickets/{id}/simulation-reconcile` | `attempt_id`, `confirmed: true` |
| `GET /api/tickets/{id}` | Returns `reply_context` and the durable `deliveries` ledger alongside the conversation |

Scenario values: `accepted`, `rejected`, `accepted_timeout`, `unknown`. They select
fixed SQLite-only test outcomes; they cannot select a real provider. Receipt
reconciliation and confirmation are separate explicit actions.

## Verification with saved exports

```bash
python3 -m intake.delivery_rehearse intake/exports/gorgias-TIMESTAMP --workspace delivery-proof --account buttonsbebe
```

The rehearsal verifies source hashes and initial fidelity in a fresh private
workspace. It tests stale reviews, each fake outcome, duplicate protection,
explicit retry, restart and full-row stability, using fabricated reply text. It
prints aggregate counts only and saves a private `delivery-reconciliation.json`.
The original export and earlier proof workspaces are preserved.

The initial sample contains 50 tickets/211 messages. Of these, 46 conversations
were eligible. One was self-addressed and three lacked usable latest-message
evidence. The rehearsal produced 35 simulated outgoing messages, 11 definite
rejections followed by reviewed successful retries, and 11 unresolved outcomes
that remained blocked. All original source records and ticket statuses were
preserved; no real delivery occurred.

`bash intake/verify.sh` covers backend behavior, migration, cross-ticket/recipient
checks, simultaneous confirmations, crashes before/after fake acceptance and the
outbound network/process guard. The browser suite additionally covers confirmation,
cancel, stale review, receipt recovery after reload, explicit retry and a lost HTTP
response after commit. Screenshots use synthetic data only.

This proves the local workflow against a fake transport. E2 now binds HTTP review
and confirmation to authenticated local users and permissions. CLI rehearsals
remain explicit local operator simulations. E4 adds signed receipt observations
and redacted monitoring in [CHANNELS](CHANNELS.md); the historical
`simulated_delivered` state represents fake acceptance, not recipient delivery.
Fake receipts do not prove real provider delivery guarantees. There
is intentionally no manual override that turns missing evidence into success or
permits an uncertain retry. Real activation remains a separate future approval.
