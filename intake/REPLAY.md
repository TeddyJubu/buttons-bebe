# Offline inbound replay

D4 adds email intake simulation from a saved local JSON file. The CLI installs
the existing outbound network/process guard before it opens storage. There is no
HTTP replay endpoint, provider client, mailbox poller or delivery adapter.

## Try it with synthetic messages

```bash
python3 -m intake --workspace replay-demo replay-preview intake/fixtures/replay-synthetic.json
python3 -m intake --workspace replay-demo replay intake/fixtures/replay-synthetic.json --expected-digest DIGEST_FROM_PREVIEW
python3 -m intake --workspace replay-demo serve --port 8891
```

Preview runs the transaction and rolls it back, including messages, contacts,
receipts and audit events. Commit verifies the file digest and rechecks the current
database in a serialized transaction. The digest identifies input, not a frozen
database plan; outcomes may change if someone commits another file after preview.
Preview ticket/message IDs are provisional. Conflicts reject the whole batch.
CLI output contains counts and the digest, without customer content or addresses.

The fixture creates five tickets and six messages from seven events: two unrelated
conversations with identical subjects, one threaded reply with a changed subject,
one message needing review, one spam message, one automatic response and an ignored
agent echo. Repeating it creates no additional records.

## Replay file contract

```json
{
  "mode": "offline_replay",
  "account": "synthetic-demo",
  "mailbox": "support@example.test",
  "events": [{
    "event_id": "delivery-1",
    "provider": "simulation",
    "subject": "A test question",
    "spam": false,
    "message": {
      "id": "provider-message-1",
      "channel": "email",
      "public": true,
      "from_agent": false,
      "created_datetime": "2026-09-01T12:00:00Z",
      "sender": {"name": "Example Customer", "email": "customer@example.test"},
      "source": {
        "from": {"address": "customer@example.test"},
        "to": [{"address": "support@example.test"}]
      },
      "headers": {"Message-ID": "<message-1@example.test>"},
      "body_text": "Synthetic question. Never delivered.",
      "attachments": []
    }
  }]
}
```

Limits: 20 MiB and 1–500 events. The mode marker, account, mailbox, event ID,
provider, subject, message ID, direction/visibility and timestamp are required.
Only email is supported in this milestone. Incoming recipients must include the
declared mailbox. Messages use the saved Gorgias message shape; `source.from/to`
take precedence over the `sender/receiver` fallback. Original fields and attachment
metadata remain private in SQLite. No attachment URL is fetched.

Use the original source account and `provider: "gorgias"` when replaying a saved
Gorgias message. New fabricated follow-ups normally use `provider: "simulation"`.
No field authorizes provider access or external delivery.

## Matching and lifecycle rules

| Input | Result |
|---|---|
| New Message-ID, no reply references | Separate Inbox ticket, even with the same subject or customer |
| In-Reply-To/References resolve to one ticket in the same account/mailbox, with a matching participant | Append to that conversation |
| Missing/unsupported headers, absent parent, multiple matching tickets or different participant | Separate Needs review ticket with a reason |
| Spam flag | Separate Spam ticket; existing work is not reopened |
| Auto-Submitted other than `no`, or a delivery-status multipart report | Append if confidently matched, otherwise Automatic responses queue; never reopen |
| Agent echo, private note or mail from the mailbox to itself | Durable ignored receipt; no incoming message or ticket |
| Previously recorded event | Return its existing result without modifying records |
| Previously recorded provider message ID or RFC Message-ID | Suppress a matching duplicate; conflicting content or multiple owners aborts the batch |

Header names and email addresses are matched without case sensitivity. Message
identifiers retain case. All recognized references must resolve to at most one
ticket; unknown earlier references can coexist with a known parent. Subject and
customer address alone never join conversations. Duplicate transport aliases are
retained across restarts, including when two providers report the same RFC ID.
Duplicate comparison uses sender, recipients, display body, attachments, direction,
parsed references, RFC ID and automatic/header-validity classification. Transport
timestamps and unrelated header metadata may differ; reuse of the same event ID
requires the exact original event content.

A genuine customer reply can reopen a closed, waiting or snoozed **Inbox** ticket
only when its timestamp is newer than both the latest saved message and the last
status change. Delayed messages remain in history without reopening or moving
the ticket timestamp backward. Assignment/priority edits do not reset this status
barrier. A reply arriving before its unknown parent stays separately reviewable;
adding the parent later never silently merges it. Review/spam/automatic tickets
remain in their queues on subsequent replies. Queue release/merge controls and
timed snooze wake are not implemented in D4.

The UI defaults to Inbox + needs review. Its queue selector exposes Spam,
Automatic responses and All queues. Simulated messages are labeled individually,
including when appended to an imported historical conversation. All accepted
events have durable receipts; new messages also have ticket activity entries.

The conservative parser is informed by [RFC 5322 message identification](https://www.rfc-editor.org/rfc/rfc5322#section-3.6.4)
and [RFC 3834 Auto-Submitted](https://www.rfc-editor.org/rfc/rfc3834#section-5).
It intentionally sends unsupported ID syntax, duplicate header fields and
ambiguous senders to review. It is not a full MIME/SMTP adapter. Matching a sender
address is **not email authentication**; future real adapters need a trusted
transport envelope, authentication/spam evidence and separate activation review.

## Saved-export proof

```bash
python3 -m intake.replay_rehearse intake/exports/gorgias-TIMESTAMP --workspace replay-proof --account buttonsbebe
```

This requires a fresh private workspace. It verifies snapshot hashes, imports the
saved history, rehearses historical redelivery, generates fabricated replies to
usable source headers, checks each expected original ticket, then restarts and
repeats everything while comparing every stored row. It never calls the exporter
or a provider. Only aggregate results are printed; the report is saved privately
as `.local/<workspace>/replay-reconciliation.json`.

The initial 50-ticket sample passed 206 follow-up probes across 49 conversations.
All 33 eligible closed tickets reopened. Historical redelivery suppressed 135
incoming duplicates and ignored 76 echoes/self-addressed messages. Four messages
lacked usable headers; one self-addressed message was excluded as a customer reply
anchor. Synthetic tests cover spam, notes, automatic replies, ambiguous references,
out-of-order delivery, storage failure and concurrent retries. This bounded proof
does not establish production readiness or authorize real ingress/egress.
