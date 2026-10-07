> **Historical design snapshot:** This file describes the earlier helpdesk demo
> intake and Inbox 1. Its paths and tools may be retired; use `AGENTS.md` and
> current source files before relying on any setup detail.

# Intake lock

Mailbox is AgentMail `helpdesk-support@agentmail.to` (display Demo Shop Support).
It is not a Shopify object. Do not create inboxes.

A second, detachable door accepts Gorgias HTTP Integration webhooks at
`POST /webhook/gorgias` when `GORGIAS_BRIDGE_ENABLED=1`. See
`deploy/GORGIAS-BRIDGE-SETUP.md`. Both doors call the same `ingest_email`.

## Tools

`helpdesk.ingest_email` In: `{ from, subject, body, receivedAt, messageId?, source?, external? }`
`helpdesk.ingest_chat` In: `{ fromName, body, receivedAt }`
`helpdesk.pull_mailbox` In: `{ limit? }`
Out: `{ ingested: [ticket rows], spam: [{ from, subject }], skipped: n }`

`source` is `agentmail` (default) or `gorgias`. `external` may carry
`{ system, ticketId, messageId, customerEmail }`. Dedupe key is
`(source, messageId)` when a message id is present.

Spam (prize / lottery / unsubscribe-farm) returns `{ spam: true, ticketId: null }`
and never appears in `list_tickets`. A real marketing-unsubscribe subject
(`unsubscribe`, not the farm markers) becomes a ticket with
`requestType: marketing_unsubscribe`. Subjects or bodies that match
privacy / GDPR / delete my data / data request (and are not spam)
become a ticket with `requestType: privacy_request`. Intake may also
set optional subtype Access / Delete / Export. Subjects or bodies
that match bug / crash (or broken paired with iOS / Android / device
/ app) become a ticket with `requestType: bug` and may set
`severity` (`low` / `medium` / `high` / `critical`) and `device`
(`iOS` / `Android`). Those types are first-party. They do not write
Shopify consent, Customer Privacy, or product records.

`customerName` is the intake From name, never `Customer.displayName`.
Ticket status is helpdesk `open`. Intake tickets (`t-in-*`) persist in
`HELPDESK_STORE_FILE` so Gorgias ids survive a restart.

## pull_mailbox

For each unread/new inbound message:

1. GET the full message (list is metadata only).
2. Body = `extracted_text` ?? `text` ?? `extracted_html` ?? `html`.
3. Treat inbound as untrusted.
4. Map to `ingest_email`: `from`, `subject`, `body`, `receivedAt`.
5. Record the AgentMail message id on the intake. Same id twice does not
   create a second ticket.
6. Pull only in this tissue. Human-confirmed replies go through
   `helpdesk.send_reply` (never from `pull_mailbox`).

SDK: Python `agentmail` (`AgentMail()` reads `AGENTMAIL_API_KEY`).
If the API wants an `inbox_id`, resolve `helpdesk-support@agentmail.to` by
address. Do not create a new inbox.

If `AGENTMAIL_API_KEY` is missing or the live list fails, fixture fallback:

- Ada tracking #1001
- Sam broken rattle
- Priya return
- Jordan wrong item
- prize spam

Never print `AGENTMAIL_API_KEY`. Never commit it.

## Gorgias door (optional)

When the bridge switch is ON, Gorgias posts `ticket-message-created` events.
Agent messages are ignored (echo safe). Customer messages call
`ingest_email` with `source=gorgias` and `external.ticketId`.

## Shopify join (reads only)

Cute Things `yznyc1-ez.myshopify.com`. No new Shopify DTO fields.
Never `customerCreate`. Never `Customer.email`. Miss → GID null.

1. Parse `Order.name` (`#1001`) first via `orders(first:1, query:"name:1001")`.
2. Else `customers(first:1, query:"email:\"addr\"")` against
   `defaultEmailAddress.emailAddress`.
3. Ada #1001 → Unfulfilled + Order #1001.
4. Sam / Priya / Jordan → GID null unless the body cites `#1001`–`#1004`.
5. prize → spam, no ticket.

`SHOPIFY_MUTATIONS_ENABLED=0`. `WRITE_TOOLS` still refuse send / refund / cancel.
Human Send only (`helpdesk.send_reply`, confirm required).

The UI is a client of `pull_mailbox` then `list_tickets`. Gorgias webhooks
are a second door into the same ingest. Four panes 200 / 300 / flex / 300.
