# Offline assistance and context interfaces (E3)

E3 connects the independent ticket system to a versioned assistance contract and
a **fixed local fixture adapter**. It exercises the workflow without a model,
provider lookup, production processor or external tool. `aiGeneration`,
`providerReads`, real delivery and notifications remain disabled.

The fixture result is a canned test response, not an AI-generated answer or a
verified statement about a customer. The Inbox and lab label it accordingly.
This milestone proves interface behavior; it does not prove model quality,
retrieval quality, policy correctness or production readiness.

## Try the six synthetic cases

Run from the repository root, choosing a new workspace:

```bash
python3 -m intake.assistance_demo --workspace assistance-review
python3 -m intake --workspace assistance-review user-set --username reviewer --name 'Reviewer' --role admin
python3 -m intake --workspace assistance-review serve --port 8891
```

The account command prompts for a sandbox-only password. Open
`http://127.0.0.1:8891/inbox/`, sign in, select a case and click **Run fixture**.
The demo explicitly seeds six synthetic conversations: ready reply, missing
staff facts, no reply needed, timeout, malformed output and conflicting identity.
Normal server startup still seeds nothing. The demo refuses existing workspaces.

The same fixture state appears in the lab at `/`. Agents and admins may run,
use and dismiss suggestions; viewers may inspect them. Importing fixtures is an
OS-owner CLI operation, with no HTTP upload/result-ingress endpoint.

- **Saved test context** shows customer, order, return, product and knowledge
  records with capture/expiry times and explicit available/empty/unavailable/
  failed states. Evidence is plain text. No links, images or file bytes load.
- **Run fixture** creates a durable local run. Reading or opening a ticket never
  generates anything. A lost response reuses its operation ID; it does not start
  another run. A new explicit run supersedes the older suggestion.
- **Use test suggestion** checks the current ticket and fixture again before
  copying a ready result into the fake reply editor. Existing handwritten text
  stays intact; **Start a blank manual reply** explicitly clears the editor and
  its previous suggestion association.
- The existing **Review simulated reply → Confirm simulation** flow remains
  mandatory. The reviewed reply records the assistance run it came from, including
  human edits. Confirmation checks that association again. No real email is sent.
- **Needs staff input** exposes missing facts and a staff next step, with no
  customer-facing body. Its Use button is disabled. Manual composition remains
  available when the existing reply-routing rules allow it.
- **No reply needed** has no draft body and never closes a ticket. **Timeout**
  and **invalid output** have no fallback acknowledgment or automatic retry.
- **Dismiss suggestion** is a shared, durable workspace action. Other teammates
  see it after Refresh. A new explicit run can produce a new suggestion.

## Native-ID contract

The new contract keeps the production interface's useful concepts—draft state,
source message, review requirement, missing facts and staff next step—while using
our independent IDs throughout. Production projection readers, customer lookup
queues and processor imports are not used by this module.

| Interface | Identity and behavior |
| --- | --- |
| `intake-assistance-input-v1` | Independent ticket/contact/message IDs; current revision, status/priority/queue, local assignee, identity consistency, full stored conversation and provenance |
| `intake-assistance-fixture-v1` | Explicit local ticket ID and input digest, labeled canned response, bounded context/evidence, capture and expiry times |
| `intake-assistance-request-v1` | Durable run ID, per-run response token, input snapshot/digest and fixture digest/context |
| `intake-assistance-result-v1` | Exact request/run/ticket/source-message/input/fixture bindings and validated response metadata |

Gorgias account/external IDs are retained only as provenance. They are never
accepted as a substitute for the independent ID, looked up through a provider,
or used to select a different customer's context. Same external IDs in different
saved accounts retain their separate independent identities.

The input is hashed over the stored conversation and relevant ticket/contact
state. Read/unread changes do not affect that hash; notes, messages, assignment,
status/priority, identity, tags and attachment availability do. Attachments carry
local IDs, names, availability and digests; bytes and remote URLs are excluded.
Message bodies remain untrusted data, including apparent instructions or result
markers. They cannot supply output metadata or trigger a tool.

A request includes at most 1,000 complete messages and 2 MiB of serialized input.
An oversized conversation is refused; no silent truncation is presented as a
complete basis for a reply. Existing ticket reading remains available.

## Explicit fixture preparation

Obtain a private input document using the independent ID shown in the Inbox URL:

```bash
python3 -m intake --workspace review assistance-input --ticket-id INDEPENDENT_TICKET_ID
```

The command writes a new mode-0600 JSON file under the workspace's private
`assistance-inputs/` directory and prints only its path, input digest and message
count. It does not send the input anywhere. Authenticated readers can also use
`GET /api/tickets/ID/assistance-input` on the local API.

Create a fixture file in ignored private storage. Its exact top-level fields are:

```json
{
  "format": "intake-assistance-fixture-v1",
  "mode": "offline_fixture",
  "fixture_id": "a-new-local-fixture-id",
  "label": "Offline context test",
  "ticket_id": "INDEPENDENT_TICKET_ID",
  "input_digest": "DIGEST_FROM_INPUT_DOCUMENT",
  "context": {
    "identity": {"contact_id": "LOCAL_CONTACT_ID", "email": "customer@example.test", "state": "consistent"},
    "captured_at": "2026-09-28T10:00:00Z",
    "expires_at": "2026-09-28T18:00:00Z",
    "sections": {
      "customer": {"state": "unavailable", "reason": "No supplied snapshot.", "records": []},
      "orders": {"state": "unavailable", "reason": "No supplied snapshot.", "records": []},
      "returns": {"state": "unavailable", "reason": "No supplied snapshot.", "records": []},
      "products": {"state": "unavailable", "reason": "No supplied snapshot.", "records": []},
      "knowledge": {"state": "unavailable", "reason": "No supplied snapshot.", "records": []}
    }
  },
  "outcome": "result",
  "response": {
    "state": "needs_staff",
    "body": "",
    "reason": "No saved context was supplied; no model was called.",
    "missing_facts": ["Verified order and policy facts"],
    "staff_next_step": "Review the missing facts before composing a reply.",
    "cited_evidence_ids": [],
    "priority": "normal",
    "review_required": true
  }
}
```

Replace the example identity with the input document's exact `identity` object
and choose accurate capture/expiry timestamps; the example timestamps are not
freshness defaults. A ready response must cite evidence actually present in the
fixture and requires consistent identity. Available sections need one or more
records shaped as `{ "id": "order:example", "title": "Test order", "text":
"Explicitly supplied test facts" }`. IDs are unique across sections. Conflicting
or missing identity cannot attach customer evidence. Unavailable, failed and
empty sections contain no records. Blank/unknown facts are never filled in by
matching a subject, email or external ticket number.

`outcome` can also be `timeout` or `malformed`, with `response: null`, to exercise
failure handling. Unknown fields, credentials, runtime modes, unsupported result
states, changed IDs/digests and invalid evidence references are rejected.

Review and import that exact local file:

```bash
python3 -m intake --workspace review assistance-preview /absolute/private/fixture.json
python3 -m intake --workspace review assistance-import /absolute/private/fixture.json --expected-digest DIGEST_FROM_PREVIEW
```

Preview does not save records. An unchanged repeated import does not duplicate
its fixture; reusing its ID for changed content is rejected. Use a new ID for a
replacement. Most recent import wins; reimporting an older fixture never makes
it current again. Imports do not create messages or change ticket revisions.

## Staleness, failures and human review

Every run, Use action and linked reply confirmation checks the current basis.
New messages or facts, a replaced fixture, expired context, dismissal, a newer
run, or recovery invalidate old results. A late result cannot replace a newer
run. Stale customer evidence and stale reply text are withheld from the current
view rather than shown as current facts.

Ready results may be inspected even when a closed/snoozed ticket or an already
answered conversation prevents Use. A currently open customer message and the
existing D5 sender/mailbox/queue/receipt checks are required to use a suggestion.
A sensitive ticket's suggested priority cannot be lowered, and high/critical
suggestions require review. Suggested metadata never changes the actual ticket
priority/status, posts notes or creates alerts.

After a rejected fake delivery, the ticket's revision has changed. Import/run a
fresh fixture before reusing an assisted reply. Using a current suggestion that
matches the same rejected body/parent selects that rejection for the explicit
reviewed retry; the review identifies it as a retry. There is no automatic send
or resend. Manual replies continue through the normal D5 checks.

A crash after reservation can leave a run pending. Its operation ID stays
reserved; retrying that request does not execute it again. An explicit new run
may supersede it. There is no background worker, retry timer, provider lease,
model execution or live context refresh in E3.

## Storage and recovery

Schema 6 adds fixture, run, dismissal and reply-association tables. Fixtures,
inputs, results and audit events persist locally. Inputs may contain customer
content and must remain private. Recovery validation checks fixture digests,
request bindings and cross-ticket associations as well as the existing ledger.
Schema-4 and schema-5 snapshots remain supported through their frozen schemas.

Backup/restore preserves the assistance records and their evidence. The fresh
recovery generation makes old suggestions unusable, including cached Use results
and unconfirmed linked reviews. Run a still-current fixture again explicitly;
expired or changed context needs a new import. No run or reply dispatches during
restore, and all user sessions are revoked as in E2.

## Proof and remaining limits

```bash
bash intake/verify.sh
PLAYWRIGHT_MODULE=/path/to/playwright node intake/tests/browser-assistance.mjs
```

The synthetic browser proof covers all six cases, native IDs, hand-edited draft
preservation, changed-context confirmation refusal, malformed output, inert
markup, lost-response deduplication, viewers, durable dismissal and fake receipt
reconciliation. Screenshots contain only synthetic data at widths 320–1440px.
The earlier Inbox, lab and team browser suites also pass.

For the existing saved export, create a new reconciliation workspace first,
then prepare explicitly unavailable-context fixtures and run the proof:

```bash
python3 -m intake.rehearse /absolute/private/saved-snapshot --workspace NEW_PROOF_WORKSPACE --account SOURCE_NAMESPACE
python3 -m intake.assistance_rehearse --workspace NEW_PROOF_WORKSPACE
PLAYWRIGHT_MODULE=/path/to/playwright node intake/tests/browser-assistance-saved.mjs NEW_PROOF_WORKSPACE
```

The proof refuses older accepted evidence workspaces, preserves all saved
business rows, checks complete native-ID/message/provenance mappings, runs only
canned staff-input results, blocks Use and verifies idempotency and recovery.
It takes no customer screenshots or raw customer output.

On 2026-09-28, **119 backend tests**, four synthetic browser suites and the saved
sample passed: **50 tickets, 211 messages, 261 source/message bindings, 100 explicit
fixture runs, 50 deduplicated repeat requests and 50 blocked staff-only Use
requests**. Business rows stayed unchanged; 100 runs survived restore and all
sessions were revoked. Model calls and external requests were zero. Detailed
private report paths are in [the checklist](../INTAKE-TASKLIST.md).

Real customer/order/return/product/policy retrieval, genuine model outputs,
semantic grounding, prompt-injection resistance of a real model and production
latency/retry behavior remain unproven. D7's accepted source-coverage gaps remain.
The current work does not authorize live integration or deployment.
