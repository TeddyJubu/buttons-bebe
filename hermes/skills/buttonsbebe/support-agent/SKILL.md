---
name: support-agent
description: "Buttons Bebe support orchestration: combine KB search, Gorgias tickets, and Shopify orders to resolve customer issues."
version: 1.0.0
author: Buttons Bebe
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Customer-Support, Orchestration, Buttons-Bebe, Workflow]
    related_skills: [gorgias, shopify]
prerequisites:
  envvars: [GORGIAS_SUBDOMAIN, GORGIAS_API_KEY, GORGIAS_API_EMAIL, SHOPIFY_SHOP, SHOPIFY_ADMIN_API_TOKEN]
---

# Buttons Bebe Support Agent

This skill orchestrates the three systems that power Buttons Bebe customer
support:

1. **Knowledge Base** (`search_kb` tool) — store policies, FAQs, exemplar tickets
2. **Gorgias** (REST API) — support tickets, messages, macros
3. **Shopify** (Admin REST API) — orders, fulfillments, products, refunds

Hermes is the brain that reads the ticket, searches the KB, pulls order data
from Shopify, drafts a reply, and writes it back to Gorgias as an internal note
for a human to review and send.

## Workflow Overview

```
Customer message → Gorgias ticket
        ↓
  Hermes reads ticket (Gorgias API)
        ↓
  Search KB for policy/answer (search_kb tool)
        ↓
  Pull order data if needed (Shopify API)
        ↓
  Draft reply based on KB + order data
        ↓
  Post draft as INTERNAL NOTE to Gorgias
        ↓
  Human reviews → sends to customer
```

---

## Step 1 — Load Credentials

Always start by loading the project environment:

```bash
if [ -f "/root/Buttonsbebe Agent/.env" ]; then
  set -a; source "/root/Buttonsbebe Agent/.env"; set +a
fi

# Gorgias
GORGIAS_BASE="https://${GORGIAS_SUBDOMAIN}.gorgias.com/api"
GORGIAS_AUTH="-u ${GORGIAS_API_EMAIL}:${GORGIAS_API_KEY}"

# Shopify
SHOPIFY_API_VERSION="2025-04"
SHOPIFY_BASE="https://${SHOPIFY_SHOP}.myshopify.com/admin/api/${SHOPIFY_API_VERSION}"
SHOPIFY_AUTH="-H \"X-Shopify-Access-Token: ${SHOPIFY_ADMIN_API_TOKEN}\""
```

---

## Step 2 — Read the Ticket

```bash
TICKET_ID=<from Gorgias>

# Get ticket + messages in one flow
curl -s $GORGIAS_AUTH "$GORGIAS_BASE/tickets/$TICKET_ID/messages" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('data', []):
    role = m.get('role')
    action = m.get('action')
    sender = m.get('sender', {}).get('email', 'unknown')
    body = m.get('body', {}).get('plain', '(no body)')
    label = 'CUSTOMER' if role == 'user' else 'STAFF' if role == 'agent' else 'SYSTEM'
    print(f'[{label}] {sender}:')
    print(body)
    print('---')"
```

Extract from the ticket:
- Customer email (for Shopify lookup)
- Order number or Shopify order ID (if linked)
- The customer's question / complaint
- Tags and status

---

## Step 3 — Search the Knowledge Base

Before answering anything, search the KB using the `search_kb` tool:

- Query with the customer's actual question or keywords
- Review the top results and their risk labels
- If results are marked `[SENSITIVE -> escalate]`, skip to Step 6 (Escalate)
- Use the KB content as the basis for the answer — do not invent policy

---

## Step 4 — Pull Order Data (if needed)

If the ticket involves an order (shipping, returns, cancellations, refunds,
order changes):

```bash
# If order is linked in Gorgias, find the Shopify order ID
# Otherwise look up by order number
ORDER_NUMBER=<from ticket>

curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$SHOPIFY_BASE/orders.json?name=$ORDER_NUMBER&status=any" \
  | python3 -c "
import sys, json
o = json.load(sys.stdin)['orders'][0]
print(f'Order #{o[\"order_number\"]}  ID: {o[\"id\"]}')
print(f'Financial: {o[\"financial_status\"]}  Fulfillment: {o[\"fulfillment_status\"]}')
print(f'Total: {o[\"total_price\"]}')
for item in o['line_items']:
    print(f'  {item[\"title\"]}  Qty: {item[\"quantity\"]}  {item[\"price\"]}')
addr = o.get('shipping_address', {})
print(f'Ship to: {addr.get(\"address1\",\"\")}, {addr.get(\"city\",\"\")}, {addr.get(\"province_code\",\"\")} {addr.get(\"zip\",\"\")}')"
```

---

## Step 5 — Draft the Reply

Based on the KB search results and order data, compose a draft reply. Follow
the store's voice and the agent core rules:

- Be direct and helpful
- Address the specific question
- If an action was taken (e.g. address change), mention it was done
- If you cannot answer (product info not available, unknown policy), say so

### Draft template

```
Hi [Customer Name],

[Answer based on KB content]

[If order-related: reference the specific order and its status]

[If action taken: e.g. "I've updated your shipping address to..."]

Let me know if there's anything else I can help with!

Best,
Buttons Bebe Support
```

### Post the draft as an internal note

```bash
DRAFT_TEXT="Hi [Customer], ...

DRAFT REPLY — for human review before sending."

# Escape for JSON
ESCAPED=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$DRAFT_TEXT")

curl -s -X POST $GORGIAS_AUTH "$GORGIAS_BASE/tickets/$TICKET_ID/messages" \
  -H "Content-Type: application/json" \
  -d "{
    \"role\": \"agent\",
    \"action\": \"internal_note\",
    \"body\": {\"plain\": $ESCAPED}
  }"
```

---

## Step 6 — Escalate (when required)

Escalate when the KB flags the topic as sensitive, or when the core rules say
to escalate:

- Refunds, chargebacks, payment disputes
- Damaged / wrong / missing items
- Cancellations
- Address changes (post-shipment)
- Angry or upset customer
- Product-specific questions where info is unavailable (sizing, fabric, fit)
- Final sale exception requests

### Escalation procedure

1. Add the `escalated` tag to the ticket
2. Post an internal note explaining why it was escalated and what KB content
   was found
3. Assign to a human agent

```bash
# Add escalation tag
curl -s -X PUT $GORGIAS_AUTH "$GORGIAS_BASE/tickets/$TICKET_ID" \
  -H "Content-Type: application/json" \
  -d '{"tags": ["escalated", "needs-human"]}'

# Post escalation note
NOTE="ESCALATION — This ticket requires human review.

Reason: [reason from KB risk label or core rules]

KB content found:
[summary of search_kb results]

Order context:
[summary of Shopify order data if applicable]

Recommended action:
[what the agent suggests, if anything]"

ESCAPED=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$NOTE")

curl -s -X POST $GORGIAS_AUTH "$GORGIAS_BASE/tickets/$TICKET_ID/messages" \
  -H "Content-Type: application/json" \
  -d "{
    \"role\": \"agent\",
    \"action\": \"internal_note\",
    \"body\": {\"plain\": $ESCAPED}
  }"
```

---

## Decision Flowchart

```
Read ticket from Gorgias
    │
    ├─ Is there a customer question?
    │   ├─ YES → search_kb(query)
    │   │   ├─ Results found, risk = low → Draft reply → Post as internal note
    │   │   ├─ Results found, risk = sensitive → ESCALATE
    │   │   └─ No results → ESCALATE (do not guess)
    │   └─ NO (just an order lookup) → Pull from Shopify → Post summary as note
    │
    ├─ Does the ticket involve an order?
    │   ├─ YES → Pull order from Shopify
    │   │   ├─ Need to change address / cancel / refund?
    │   │   │   ├─ Pre-shipment → Prepare action, show human, do NOT auto-apply
    │   │   │   └─ Post-shipment → ESCALATE
    │   │   └─ Just a status question → Answer from Shopify data → Draft reply
    │   └─ NO → Continue with KB answer
    │
    └─ Is the customer angry / upset?
        └─ YES → ESCALATE
```

---

## Core Rules (from KB — agent-core-rules)

1. **Order identification** — If the order is linked in Gorgias, do NOT ask the
   customer for the order number. Only ask if it cannot be identified.

2. **Action before response** — When possible, complete or draft the action
   first (address change, size swap, etc.), then respond to the customer.

3. **Do not guess product information** — Only answer product-specific questions
   (sizing, fabric, fit, measurements) if the info comes from the product page,
   description, vendor data, or internal notes. Otherwise escalate.

4. **When to escalate** — Wrong item, damaged item, urgent shipping, unknown
   sizing/fit, unknown fabric, final sale exceptions, refund disputes, and
   anything the KB marks as sensitive.

5. **Never auto-send** — All replies are drafted as internal notes. A human
   reviews and sends them to the customer.