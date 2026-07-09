# Gorgias API & Feature Research (Haiku agent, 2026-07-10)

> Source: developers.gorgias.com + docs.gorgias.com. Used to make Fable's data model
> Gorgias-compatible (easy migration) and to define the feature-parity checklist.

## 1. Data model

### Ticket
- `id`, `status` ("open"|"closed"), `priority` (critical|high|normal|low)
- `channel` (email, chat, whatsapp, sms, facebook, instagram, ...), `via` (api, email, shopify, rule, ...)
- `from_agent` (bool), `subject`, `language` (ISO 639-1), `summary` (AI summary obj)
- `tags` [{id, name, decoration}], `customer` {id, email, name, firstname, lastname}
- `assignee_user`, `assignee_team`, `custom_fields`, `messages` []
- Timestamps: `created_datetime`, `opened_datetime`, `last_received_message_datetime`,
  `last_message_datetime`, `updated_datetime`, `closed_datetime`, `snooze_datetime`
- `satisfaction_survey`, `is_unread`, `spam`, `external_id`

### TicketMessage
- `id`, `ticket_id`, `public` (bool — FALSE = internal note), `channel` (email|chat|internal-note|...)
- `via`, `source` {type, from, to, cc, bcc}, `sender`, `receiver` (omit for internal notes)
- `from_agent`, `subject`, `body_text`, `body_html`, `stripped_text`, `stripped_html`
- `attachments` [{url,name,size,content_type}], `macros`, `intents`, `rule_id`, `imported`
- Timestamps: `created_datetime`, `sent_datetime`, `processed_datetime`, `opened_datetime`
- `last_sending_error`, `is_retriable`

**Internal note** = `channel: "internal-note"`, `public: false`, `from_agent: true`, no receiver.
**Public reply** = `public: true`, `from_agent: true`, receiver = customer, source.from = integration email.
**Incoming** = `from_agent: false`, sender = customer, `public: true`.

### Customer
- `id`, `email`, `firstname`, `lastname`, `name`, `external_id`
- `channels` [{type, address}], `language`, `timezone`, `note`, `meta`
- `integrations` — Shopify data keyed by integration id:
  `{"6": {"__integration_type__": "shopify", "customer": {...}, "orders": [...]}}`

## 2. Endpoints (base `https://{domain}.gorgias.com/api`, Basic auth email:api_key)

| Endpoint | Method | Notes |
|---|---|---|
| `/tickets` | GET | cursor pagination: `cursor`, `limit` (max 100, default 30), `customer_id`, `external_id`, `order_by` |
| `/tickets/{id}` | GET/PATCH/DELETE | PATCH: status, tags, assignee, custom fields |
| `/tickets` | POST | create with initial messages |
| `/tickets/{id}/messages` | POST | create message (payload above), returns 201 |
| `/messages` | GET | all messages, paginated |
| `/customers` | GET/POST | `email`, `external_id` filters |
| `/customers/{id}` | GET/PATCH/DELETE | |
| `/tags`, `/tickets/{id}/tags` | CRUD | POST add, PATCH replace, DELETE remove |
| `/macros` | CRUD | canned replies with `{{ticket.customer.firstname}}` vars + actions |
| `/rules` | CRUD | JS-like condition code, `event_types`, `priority` |
| `/views`, `/views/{id}/items` | GET | saved filters |
| `/surveys` | CRUD | satisfaction |
| `/custom-fields` | CRUD | |

Pagination envelope: `{"data": [...], "object": "list", "meta": {"prev_cursor", "next_cursor", "total_resources"}}`

## 3. Webhooks (HTTP integration)
- Events: `ticket-created`, `ticket-updated`, `message-created`, `ticket-closed`
- Payload: `{"event_type": "...", "data": {"ticket": {...}, "message": {...}}}`
- HMAC-SHA256 signature, header `X-Gorgias-Signature`, key = webhook secret, msg = raw body.

## 4. Feature-parity checklist (what Fable must replace)
- Views/filters (saved ticket lists by status/assignee/tag/date)
- Tags (colored labels, filterable)
- Macros (canned replies with variable substitution + attached actions)
- Rules/automation (auto-tag, auto-assign, auto-close on events, priority-ordered)
- Custom fields
- Multi-channel unified inbox (email, chat, WhatsApp, social)
- Assignment & routing (agents/teams)
- Status lifecycle (open/closed + snooze)
- Customer profiles w/ Shopify order history sidebar
- Internal notes
- Satisfaction surveys (CSAT)
- Search (tickets/messages/customers)
- Stats (ticket volume, first-response time, resolution time, CSAT)
- Bulk operations
- SLA / auto-escalation via rules

## 5. Migration out of Gorgias
- Paginate `GET /api/tickets?limit=100&cursor=...` until `next_cursor` null; same for
  `/customers`, `/tags`, `/macros`, `/rules`, `/custom-fields`, `/messages`.
- Preserve source ids in `external_id`; mark messages `imported: true`; keep `sent_datetime`.
- Attachments are CDN URLs — download and re-host.
- 429s: exponential backoff.
- Gaps: survey responses + full audit log not cleanly exportable.

## Local code that talks to Gorgias today (tools/gorgias_mcp.py)
- GET `/tickets?limit=`, `/tickets/{id}`, `/tickets/{id}/messages?limit=`,
  `/customers/{id}`, `/customers?email=` — these 5 reads are the compat surface the
  Gorgias emulator must serve so existing tools keep working unmodified.
- Write path (VPS processor): POST `/api/tickets/{id}/messages` with `channel=internal`.
