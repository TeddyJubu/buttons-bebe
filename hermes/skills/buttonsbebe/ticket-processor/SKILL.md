---
name: ticket-processor
description: "Headless ticket processing: read ticket, normalize message, search KB, check returns, classify priority, draft reply, post to Gorgias, output JSON result."
version: 2.0.0
author: Buttons Bebe
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Customer-Support, Classification, Drafting, Headless, Buttons-Bebe]
    related_skills: [gorgias, support-agent]
prerequisites:
  envvars: [GORGIAS_SUBDOMAIN, GORGIAS_API_KEY, GORGIAS_API_EMAIL]
---

# Buttons Bebe Headless Ticket Processor

This skill is invoked by the job processor (orchestrator.py) in one-shot
mode (`hermes --yolo -z "..."`). It processes a single Gorgias ticket
autonomously using the MCP tools available to Hermes.

## Data Sources (MCP Tools)

Three MCP servers are connected and available as tools:

1. **buttonsbebe_gorgias** — Gorgias helpdesk
   - `get_ticket(ticket_id)` — full ticket with messages, customer, tags, priority
   - `get_ticket_messages(ticket_id)` — conversation thread
   - `get_customer(customer_id)` — customer details, Shopify-linked data
   - `search_customer(email)` — find customer by email
   - `list_recent_tickets(limit)` — recent tickets

2. **buttonsbebe_kb** — Knowledge Base (policies, FAQs, products, intents)
   - `search_kb(query, k)` — search KB for policies, FAQs, product details,
     exemplar tickets. Returns passages with `sensitive` flag.

3. **buttonsbebe_redo** — Redo returns/RMA + order shipping system
   - `get_order(order_name)` — full order with shipping address, fulfillment
     status, tracking number, carrier, delivery status, line items, customer
   - `get_returns_for_order(order_name)` — returns for a Shopify order
   - `get_return(return_id)` — single return detail
   - `list_recent_returns(limit)` — recent returns across the store

Use only these authenticated, read-only MCP tools. Never use curl or direct API
credentials. Hermes returns its draft to the processor/console; only a human
console action may write to Gorgias.

## Which MCP Tool to Use for Which Query (Decision Matrix)

After normalizing the customer message (Step 3), use this matrix to
decide which MCP tools to call and in what order:

### Shipping & tracking questions
"Where is my order?" | "Has it shipped?" | "Tracking number?"
1. Redo → get_order(order_name) — tracking, carrier, delivery status
2. Gorgias → get_customer(customer_id) — Shopify fulfillment_status (fallback)
3. KB → search_kb("shipping status") — processing time policy

### Address change requests
"I need to change my address" | "Wrong zip code"
1. Redo → get_order(order_name) — current address + fulfillment status
   (not shipped = CRITICAL, already shipped = HIGH)
2. Gorgias → get_customer(customer_id) — Shopify shipping_address (fallback)
3. KB → search_kb("address change") — intent-10, order changes policy

### Return & exchange requests
"I want to return" | "Can I exchange?" | "How many days?"
1. Redo → get_returns_for_order(order_name) — existing return status
2. Redo → get_order(order_name) — line items, shipping address
3. KB → search_kb("return exchange") — 7-day window, restocking fees

### Wrong / damaged item
"I got the wrong item" | "Item is damaged" | "Wrong size received"
1. Gorgias → get_ticket(ticket_id) — conversation, Gorgias intents
2. Redo → get_order(order_name) — what was ordered, tracking
3. Redo → get_returns_for_order(order_name) — existing return
4. KB → search_kb("wrong item" / "damaged") — intent-15/16, SENSITIVE

### Refund / chargeback / payment dispute
"I want a refund" | "chargeback" | "charged twice"
1. Redo → get_returns_for_order(order_name) — existing refund status
2. Gorgias → get_customer(customer_id) — financial_status, refunds
3. KB → search_kb("refund chargeback") — SENSITIVE, escalate

### Cancellation requests
"Cancel my order" | "I don't want this anymore"
1. Redo → get_order(order_name) — fulfillment status (not shipped = can cancel)
2. Gorgias → get_customer(customer_id) — cancel_reason, cancelled_at
3. KB → search_kb("cancel order") — intent-06, order changes policy

### Order changes (add item, change size)
"Can I add to my order?" | "Change size before it ships"
1. Redo → get_order(order_name) — fulfillment status, current line items
2. KB → search_kb("order change") — intent-08, order changes policy

### Lost / not received package
"Package was lost" | "Delivered but I didn't get it"
1. Redo → get_order(order_name) — tracking, delivery status
2. KB → search_kb("lost package" / "delivered not received") — SENSITIVE

### Product / sizing / availability
"Do you have this in size X?" | "Is this available?" | "How does it run?"
1. KB → search_kb("product name + size") — 4,246 product files
2. KB → search_kb("sizing guide") — sizing policy, DO NOT GUESS

### Shipping policy / general FAQ
"Do you ship to Canada?" | "What's your return policy?"
1. KB → search_kb("shipping policy" / "return policy") — policies + FAQs

### Urgent / rush delivery
"I need this by Friday" | "It's urgent"
1. Redo → get_order(order_name) — has it shipped?
2. KB → search_kb("urgent rush shipping") — intent-18, CRITICAL

### Customer history
"What orders has this customer placed?"
1. Gorgias → get_customer(customer_id) — full Shopify order history
2. Gorgias → search_customer(email) — if customer ID unknown

### Thank you / survey / no question
"Thanks!" | Survey response
1. Gorgias → get_ticket_messages(ticket_id) — confirm no question
2. Classify as LOW, draft brief acknowledgment

RULE: Always search KB for policy/safety guidance. Use Redo for
shipping/returns/order status. Use Gorgias for ticket context and
customer order history.

## Critical rule: ALWAYS output JSON_RESULT

At the very end of your response, output exactly this line:

```
JSON_RESULT: {"priority": "<critical|high|normal|low>", "reason": "<one sentence>", "action": "<drafted|sensitive_draft|no_kb_match>", "notify_owner": <true|false>, "gorgias_priority_set": <true|false>, "note_posted": <true|false>}
```

The job processor parses this line to decide whether to send a WhatsApp
notification. If you omit it, the processor defaults to escalating.

## Step 1 — Use the authenticated read-only MCP tools

Do not load Gorgias, Shopify, or Redo credentials. Do not call their REST APIs
directly. The MCP tools are already authenticated and are the only allowed
external-data path for Hermes.

## Step 2 — Read the Ticket

Use the `get_ticket` MCP tool (from buttonsbebe_gorgias server) to fetch
the full ticket. This gives you:

- Ticket: subject, status, priority, channel, tags
- Customer: email, name, phone, Shopify-linked data
- Messages: full conversation thread (customer + agent messages)
- Shopify order integration data (if linked)

```
TICKET_ID = <provided in prompt>
Call: get_ticket(ticket_id=TICKET_ID)
```

Extract from the ticket:
- The customer's latest message (the one that triggered this webhook)
- The full conversation thread (all messages, customer vs agent)
- Customer email and ID
- Any order numbers mentioned in subject or messages
- Tags and current priority
- Shopify integration data from customer (order IDs, addresses)

If the ticket has a Shopify order linked in the customer integration
data, note the order number — you'll need it for Redo returns lookup.

## Step 3 — Normalize the Message (CRITICAL — do this before KB search)

Customer messages arrive with significant noise. You MUST clean the
message before searching the KB. Follow these steps:

### 3a. Extract only the customer's new text

Strip ALL of the following from the message:
- Quoted email replies (everything after "On [date], [name] wrote:")
- Forwarded email threads
- Email signatures
- Order confirmation summaries (line items, prices, addresses)
- Tracking URLs and Shopify links
- HTML tags and CSS
- System-generated text (e.g. "In replies all text above this line...")
- Survey/feedback request emails
- Mailer footer text

Keep ONLY the customer's actual words — their question, request, or
complaint.

### 3b. Handle empty or non-message emails

If after cleaning, the message is empty or contains only:
- A satisfaction survey link → classify as LOW, action: "escalated",
  reason: "satisfaction survey — no customer question"
- A "thank you" with no question → classify as LOW, draft a brief
  acknowledgment
- Only an order confirmation (no customer text) → classify as LOW,
  action: "escalated", reason: "order confirmation — no customer question"

### 3c. Correct spelling and normalize phrasing

Fix common misspellings before searching:
- "thist" → "this"
- "recieved" → "received"
- "refunf" → "refund"
- "cancling" → "canceling"
- "exchaneg" → "exchange"
- "shiping" → "shipping"
- "siz" → "size"

Rewrite vague phrasing into a clear search query:
- "I got the wrong thing" → "wrong item received"
- "it didn't come" → "order not received"
- "can I swap" → "exchange request"
- "add to my order" → "add item to existing order"
- "change the size before it goes out" → "size change before shipment"

### 3d. Combine subject + cleaned message

If the cleaned message is very short or vague, incorporate relevant
context from the ticket subject (e.g. order number, "Re: wrong item").

Example:
  Subject: "Re: Order 12345678 confirmed"
  Raw message: "Hi, I would like to add thist to my order size 4 pants..."
  Cleaned query: "add item to existing order size 4 pants"
  KB search query: "add item to existing order"

### 3e. Handle multi-message context

If the ticket has multiple customer messages (repeated follow-ups):
- Use the LATEST customer message as the primary query
- But review ALL customer messages for context
- If 3+ customer messages with no agent reply → bump priority to CRITICAL
  (customer is frustrated)

## Step 4 — Search the Knowledge Base

Use the `search_kb` MCP tool with the CLEANED, NORMALIZED query from
Step 3. Do NOT search with the raw message text.

Try multiple search queries if the first returns nothing:
1. Search with the cleaned customer message
2. If no results, search with broader keywords (e.g. "refund" instead of
   "I want my money back for the blue shirt that was wrong")
3. If still no results, search with the Gorgias intent name (e.g.
   "exchange/request", "shipping/status")

The KB contains:
- Store policies (shipping, returns, refunds, sizing, etc.)
- FAQs derived from real tickets
- 22 customer intent patterns with approved response templates
- Exemplar solved tickets
- 4,246 product files with sizes, prices, and availability

Review the results and their sensitive flags:
- If results have `sensitive: true` → the topic is at least HIGH priority
- If no results after all attempts → draft a generic acknowledgment, set
  `action: no_kb_match`, flag the information gap, and do not guess

## Step 5 — Check Returns (if ticket involves a return/refund/exchange)

If the customer mentions a return, exchange, refund, or damaged/wrong
item, and you have an order number from the ticket:

Use the `get_returns_for_order` MCP tool (from buttonsbebe_redo server):

```
Call: get_returns_for_order(order_name="<order_number>")
```

This returns:
- Return status (approved, rejected, pending, completed)
- Items being returned with SKU, reason, quantity
- Refund amount and type per item
- Customer comments on returns

Use this data to:
- Confirm whether a return has been initiated
- Check the return reason (damaged, wrong item, etc.)
- See if a refund has been processed
- Provide accurate status updates to the customer

If there are no returns for the order, the customer may not have
initiated one yet — mention the return process in your draft.

## Step 5.5 — Check Order & Shipping (if ticket involves an order)

If the ticket mentions an order number (in subject or message), use the
`get_order` MCP tool (from buttonsbebe_redo server) to fetch the full
order with shipping and fulfillment data:

```
Call: get_order(order_name="<order_number>")
```

This returns:
- **Shipping address** — full address (street, city, state, zip, country)
- **Shipping method** — e.g. "Free Shipping"
- **Fulfillment status** — `fulfillments` array (empty = not shipped,
  populated = shipped with tracking)
- **Tracking number** — e.g. "123456789012"
- **Carrier/tracking company** — e.g. "ETA Express"
- **Delivery status** — `deliveredAt` (null = not delivered, date = delivered)
- **Estimated delivery date** — `estimatedDeliveryDate`
- **Line items** — product title, SKU, quantity, price, image URL
- **Customer** — name, email, phone
- **Order total** — totalPrice, subtotalPrice, currency
- **Tags** — e.g. "ETA_delivery"

Use this data to:
- Answer "where is my order?" questions (tracking number + carrier)
- Verify if an order has shipped (fulfillments array populated)
- Check if an order was delivered (deliveredAt not null)
- Get the shipping address for address-change requests
- Determine if an address change is CRITICAL (not shipped) or HIGH (already shipped)
- Provide order status updates with specific tracking info
- Reference line items when discussing wrong/damaged items

## Step 6 — Classify Priority

Use these definitions to classify the ticket:

### CRITICAL — Act within minutes
The test: If we don't act right now, the customer's order gets worse,
stuck, or impossible to fix.

- Address change before shipment
- Wrong size / item correction before shipped
- Pre-shipment cancellation
- Urgent delivery reroute
- Fraud / security risk
- Angry or abusive language
- Repeated follow-ups (3+ messages, no agent reply)

Agent actions: classify critical, draft a reply for console review, and request
owner notification via WhatsApp. Do not write Gorgias priority or notes.

### HIGH — Act within a few hours
The test: This hurts revenue, trust, or reputation, but a short delay
won't make it unfixable.

- Refund / chargeback / cancellation request (post-fulfillment)
- Damaged / wrong / missing item
- Payment dispute
- Order not received (fulfilled, no delivery)

Agent actions: classify high, draft a reply for console review, and request owner
notification via WhatsApp. Do not write Gorgias priority or notes.

### NORMAL — Queue or auto-draft
The test: This is informational, routine, or tied to an active order
problem.

- Order status question
- General shipping delay inquiry
- Product / sizing question (info available in KB)

Agent actions: classify normal and return a draft for console review.

### LOW — Queue or auto-draft
The test: This is generic informational, not tied to any active order
problem.

- Policy FAQ
- Thank you message
- General product inquiry
- Newsletter / opt-out request

Agent actions: classify low and return a draft for console review.

## Step 7 — Return the priority classification

Classify the result as critical, high, normal, or low for the processor and
console. Do not update the Gorgias ticket. Always report
`gorgias_priority_set=false`.

## Step 8 — Draft the Reply (ALWAYS draft — sensitive or not)

The agent ALWAYS generates a draft and returns it to the processor for display in
the console. The human is the safety gate and decides whether to send, edit, post
as an internal note, request a rewrite, or discard it. Never skip drafting.

### If KB results found and NOT sensitive:
Draft a reply based on the KB content + returns data (if applicable).
Follow the store's voice and agent-core-rules:
- Be direct and helpful
- Address the specific question
- If product info is unavailable, say so
- Reference the specific order if the ticket mentions one
- If a return was found in Redo, include the return status

### If KB results found and SENSITIVE:
Draft a SAFE ACKNOWLEDGMENT reply. Prefix the draft with:
```
[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]
```
Then use safe language from KB intent templates:
- Damaged item (intent-16): apologize, ask for photos, mention checking replacement
- Wrong item (intent-15): apologize, ask for photo of item received, say we'll fix it
- Refund review (intent-14): say we're reviewing the details, will get back to them
- Lost package: help file carrier claim, say escalating internally

FORBIDDEN words in ANY sensitive draft:
- "refund", "money back", "compensate", "reimburse",
  "credit your account", "issue a refund", "we will refund"

SAFE alternatives:
- "we'll make it right", "we're reviewing", "we'll get back to you",
  "we're looking into this", "we want to make sure everything is correct"

The draft acknowledges the issue and sets expectations, but the MONEY
DECISION is always left to the human reviewing the console draft.

### If no KB results found:
Draft a generic acknowledgment:
```
[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]
Hi! Thanks for reaching out. We're reviewing your message and will
get back to you shortly.
```
Set action to "no_kb_match" so the human knows there was no KB match.

## Step 9 — Return the draft to the console

Output the complete draft exactly once between these tags:

```text
<DRAFT>
...complete human-review draft...
</DRAFT>
```

Do not post the draft anywhere. The processor captures it and the console shows it
to a human. Always report `note_posted=false`.

## Step 10 — Output JSON Result

Output exactly this line at the very end:

```
JSON_RESULT: {"priority": "<critical|high|normal|low>", "reason": "<one sentence>", "action": "<drafted|sensitive_draft|no_kb_match>", "notify_owner": <true for critical/high, false for normal/low>, "gorgias_priority_set": false, "note_posted": false}
```

## Safety Rules

- NEVER send an external reply or post an internal note. Return the draft to the
  console; send/note/rewrite are human-triggered console actions only.
- ALWAYS draft a reply — even for sensitive topics. Use the
  [SENSITIVE — REVIEW CAREFULLY BEFORE SENDING] tag and safe
  acknowledgment language. The human reviews before sending.
- Search the KB before answering. Do not invent policy.
- For sensitive topics, use safe acknowledgment language from KB
  intent templates. Never use forbidden money words (refund, money
  back, compensate, reimburse, etc.) in the draft.
- If no KB match, draft a generic acknowledgment — do not guess
  policy or leave the customer without a draft.
- ALWAYS normalize the message before KB search (Step 3).
- If the message is empty after cleaning, draft a generic
  acknowledgment and classify as LOW.
- If the message is a survey/thank-you with no question, classify as LOW.
- Use MCP tools (get_ticket, search_kb, get_returns_for_order) for reading data.
  Never use curl or direct APIs for external reads or writes.
- Product info is in the KB — search_kb will find
  sizes, prices, and availability. Do not ask for a Shopify API.
- The human agent is the final safety gate. The AI's job is to draft
  the best possible reply it can — including for sensitive topics —
  and let the human decide whether to send, edit, or discard it.
