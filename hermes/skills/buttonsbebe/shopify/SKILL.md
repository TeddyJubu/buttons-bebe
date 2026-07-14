---
name: shopify
description: "Read-only Shopify order and product context through the approved Buttons Bebe MCP tools."
version: 2.0.0
author: Buttons Bebe
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Shopify, E-commerce, Orders, Products, Read-Only]
    related_skills: [gorgias, support-agent, ticket-processor]
---

# Shopify Context — Read Only

Hermes must never access Shopify directly. Do not load Shopify credentials, read
`.env` files, invoke curl, or call Shopify Admin REST/GraphQL endpoints. Do not
update an address, cancel an order, create a fulfillment, change inventory, or
create/inspect a refund through a direct API.

## Approved data paths

- Use `buttonsbebe_redo.get_returns_for_order(order_name)` and
  `buttonsbebe_redo.get_return(return_id)` for return/RMA context.
- Use `buttonsbebe_gorgias.get_ticket`, `get_ticket_messages`, `get_customer`,
  and `search_customer` for help-desk/customer context and synced Shopify order,
  fulfillment, address, line-item, and tracking details when Gorgias provides them.
- Use `buttonsbebe_kb.search_kb` for product catalog information, sizes,
  variants, availability, policies, FAQs, and approved reply exemplars.

All approved paths above are authenticated read-only MCP calls. If a tool lacks the
needed fact, say it needs human review; do not create a direct API fallback.

## Response rules

1. Treat returned order/product data as context, not authority to mutate it.
2. Never promise an address change, cancellation, refund, fulfillment, stock
   change, or other Shopify operation.
3. For change requests, draft a concise acknowledgment for human review and use
   the applicable KB policy.
4. Sensitive requests still receive a `[SENSITIVE — REVIEW CAREFULLY BEFORE
   SENDING]` draft. Hermes returns it to the console and performs no write.
5. Only a human-triggered Gorgias console action may send or post text. There is
   no Hermes-triggered Shopify write path.
