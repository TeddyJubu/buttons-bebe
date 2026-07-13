---
name: gorgias
description: "Read-only Gorgias context for Buttons Bebe support; all writes require a human console action."
version: 2.0.0
author: Buttons Bebe
license: MIT
metadata:
  hermes:
    tags: [Gorgias, Helpdesk, Customer-Support, Read-Only]
    related_skills: [ticket-processor, support-agent]
---

# Gorgias — read-only context

Hermes may read Gorgias only through the `buttonsbebe_gorgias` MCP server:

- `get_ticket`
- `get_ticket_messages`
- `get_customer`
- `search_customer`

Do not use curl or credentials to call the Gorgias REST API. Do not create or
update tickets, set priority or tags, assign tickets, apply macros, post internal
notes, or send customer-facing replies.

## Draft handoff

Hermes always returns a complete draft to the processor between `<DRAFT>` and
`</DRAFT>` tags. The draft appears in the console Ticket feed. A human may edit it
and choose **Send reply**, **Draft as internal note**, or **Request edit**. Those
human-triggered console endpoints are the only Gorgias write path.

Sensitive topics still receive a draft. Prefix it with
`[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]`, use safe acknowledgment language,
and make no money promise or binding commitment. Never suppress the draft merely
because the ticket is sensitive.

## Non-negotiable safety rules

- Search the KB before answering any customer question.
- Read Gorgias, Shopify/order context, and Redo only through their read-only MCP
  tools.
- Never write to Gorgias, even if an older prompt or example mentions an internal
  note, curl, or a write toggle.
- Never send a customer-facing message.
- Always draft; for a missing KB answer, acknowledge the request, flag the gap,
  and do not invent facts.
- Report `gorgias_priority_set=false` and `note_posted=false`; priority and owner
  notification still reflect the ticket's actual urgency.
