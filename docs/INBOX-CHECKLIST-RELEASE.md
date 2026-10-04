# Inbox checklist release — 5 October 2026

This release combines P1–P6 from the approved Inbox fix plan. The owner authorized
implementation, merge and deployment. It does not include the separate draft
reliability PR #90.

| Checklist area | Result | Verification |
| --- | --- | --- |
| Clean email text and preview | Intake owns one standard-library policy. Display history, current AI text, original evidence and truncation are separate. Consumers accept the versioned contract. | Retained-content, canonical, reconciliation, projection and message-display tests; synthetic full-stack launcher. |
| Reply box and AI suggestion | History scrolls independently. Editor/actions stay visible; long suggestions expand within their allocated space. Both side panels can collapse. | Layout and local-controls browser tests at desktop, phone width and 200% zoom. |
| Views and filters | Assigned, Unassigned, All, Open, Snoozed, Closed, Trash and Spam use observed provider state. Priority, assignee, tag and channel filters have matching provider counts and stable pages. | Read contract and ticket-view SQLite tests; full-stack category fixtures. |
| Local ticket controls | Rename, status, priority, assignment, snooze and read markers persist in this browser. Observed provider values remain visible. New messages invalidate old read markers. | Local-state and local-controls tests, refresh/caret and reply-isolation checks. |
| Bulk actions and New ticket | Selection covers the visible page. Bulk organization and unique `local:<UUID>` tickets stay in the browser. Local tickets cannot send. | Local-controls tests and captured request assertions. |
| Redo details | A separate worker reads the existing fixed localhost MCP tool for Shopify-verified order identities. It retains bounded reviewed fields, timestamps and explicit pending/empty/partial/stale/unavailable states. | Identity, queue, snapshot, transport and Redo browser tests. |

The provider field definitions were checked against the [Gorgias ticket
object](https://developers.gorgias.com/reference/the-ticket-object) and
[ticket-list contract](https://developers.gorgias.com/reference/list-tickets).
Missing category fields are unavailable, rather than invented. Assigned to me
requires `INBOX_OPERATOR_GORGIAS_EMAIL`; missing operator identity is disclosed.
Trash summaries are bounded to 1,000 cached rows. Browser overrides filter the
loaded provider page; the interface distinguishes shown rows from provider
totals. Browser state does not synchronize across devices.

Original source data can already be missing or truncated. Old retained records
are derived on read without replaying jobs, changing drafts, sending alerts or
altering provider records. A separate historical write-back is unnecessary for
this release. If later requested, a derivation pass must first count available
sources in a read-only dry run, record per-ID cleanup versions and resumable
checkpoints in a separate local ledger, and preserve original records/results.

The existing customer-reply grant, recipient/source checks, operation IDs and
final confirmation remain required. Gorgias, Shopify and Redo otherwise remain
read-only. The browser test dependency lock is CI-only and is not deployed.

The checked-in fixture launcher runs the actual Inbox API with synthetic
provider reads, temporary SQLite stores, blocked console mutations and a
non-loopback network guard. The browser release gate starts its own synthetic
preview, runs every Inbox browser test and cleans up its processes.

Operational closeout requires separate evidence: all 48 current production-model
cases and explicit grading, installed source hashes and service checks, phone
pairing/recipient selection, and the next 100 terminal production results.
Phone pairing needs the owner's device. A real owner test alert requires an
exact request to send it. Future results remain open until 100 exist. Unspecified
Gorgias custom-field parity is not a completed claim.

Source-only rollback and manual shared-package/Redo service preparation follow
`deploy/cd/README.md`. Runtime databases, credentials, WhatsApp sessions and KB
corpora are excluded from deployment and rollback.
