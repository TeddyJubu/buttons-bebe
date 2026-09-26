---
name: gorgias
description: Use when working with the Buttons Bebe Gorgias integration, its ticket/message contract, or current Gorgias API documentation.
version: 2.1.0
author: Codex-reviewed from the user-provided gorgias.skill
license: MIT
metadata:
  source_archive_sha256: f56ebe7a8cfb56de89080a314da199d15c07ebdf1a492f6ec4460d5aa4032c4e
  official_docs: https://developers.gorgias.com/reference/introduction
---

# Gorgias support integration

Use the official Gorgias documentation as the API contract. The user-provided
archive was a useful starting point, but it referenced a missing helper,
retired configuration, and an Hermes-side write path. Those parts are not
adopted here.

## Buttons Bebe safety boundary

For the Buttons Bebe repository:

- Hermes uses the `buttonsbebe_gorgias` MCP server for read-only context only:
  `list_recent_tickets`, `get_ticket`, `get_ticket_messages`, `get_customer`,
  and `search_customer`.
- Hermes must not post internal notes, send customer replies, change ticket
  state, set priority/tags, assign tickets, apply macros, or call the Gorgias
  REST API directly.
- Human-confirmed console actions are the write boundary. Keep that boundary
  explicit in code and audit logs.
- Never load or print production credentials from source, chat, or retired
  paths such as `/root/gorgias-webhook/config.json`.

## Current official API facts

- The API is REST and uses JSON request/response bodies.
- Private apps use an API key; public apps must use OAuth2.
- The API base is `https://{domain}.gorgias.com/api`.
- Creating a ticket message uses
  `POST /tickets/{ticket_id}/messages`. A created message may be delivered
  asynchronously; a successful create response is not proof that the message
  was sent. Check `sent_datetime`, `failed_datetime`, and
  `last_sending_error` when delivery status matters.
- A public outgoing message requires the correct channel/routing data,
  `from_agent=true`, and a sender representing an existing user. For email,
  `source.from.address` must be an email integration connected to Gorgias.
- The documented outgoing examples use `via="api"` alongside the selected
  `channel`; do not assume `via` should repeat the channel name.
- Internal notes are private and use `channel="internal-note"`; they are not
  sent to customers.
- The ticket-scoped list endpoint `GET /tickets/{ticket_id}/messages` is
  deprecated. New reads should use `GET /messages?ticket_id={id}` with the
  documented `limit`/`cursor` pagination. Do not rely on undocumented
  `per_page` parameters.
- API-key integrations are documented at 40 requests per 20 seconds. Honor
  `Retry-After` and the documented rate-limit headers when handling 429s.
- Gorgias HTTP integrations are configured account-side. Treat repository
  HMAC headers or query-string secrets as local conventions unless the account
  configuration explicitly establishes them; do not present them as a native
  Gorgias REST contract.
- Gorgias has announced an email-message retention change for 8 September
  2026: prefer `stripped_text`/`stripped_html` when available and account for
  `body_text`/`body_html` becoming nullable or archived.

## Verification rules

When reviewing or changing the integration:

1. Read the exact official endpoint page, not only a code example.
2. Confirm authentication, endpoint, required fields, response status, and
   asynchronous delivery behavior.
3. Prefer the repository's existing read-only MCP boundary for Hermes.
4. For any human send flow, verify the resulting message and its delivery
   fields; do not report a send as delivered merely because the API accepted
   the create request.
5. Keep secrets in the protected runtime environment and never expose them in
   logs or responses.

Official references:

- https://developers.gorgias.com/reference/introduction
- https://developers.gorgias.com/reference/authentication
- https://developers.gorgias.com/reference/create-ticket-message
- https://developers.gorgias.com/reference/list-messages
- https://developers.gorgias.com/reference/limitations
- https://developers.gorgias.com/reference/the-ticketmessage-object
- https://developers.gorgias.com/changelog/new-email-message-data-retention-policy

## Draft reliability and human input

Classify the newest customer request before inherited subjects or intents. Pure
acknowledgments receive no new draft or alert and do not resolve an existing
sensitive case. Ordinary knowledge gaps require normal staff review, not HIGH.

Each actionable reply needs a supported answer, one necessary customer
clarification, or a verified customer action. Staff-only facts require authenticated
review_required, missing_facts, and a specific staff_next_step outside customer
text. A necessary customer clarification can remain usable while a later staff
check waits for their answer. Never claim work started or promise a follow-up.

Return the exact run-token draft and JSON_RESULT markers required by the runtime
prompt. A generation failure displays AI draft unavailable with bounded retries;
never manufacture a generic acknowledgment. Hermes remains read-only. Only the
existing human-confirmed console Send flow may send a customer reply.
