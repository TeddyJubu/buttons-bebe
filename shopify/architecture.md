# Shopify Module — Architecture

Independent entity. The main agent calls into this folder for anything Shopify-related.
Nothing in this folder knows about Gorgias, Telegram, or the KB. It only knows Shopify.

---

## What this module does

Provides a single clean interface: give it a customer email or order number, get back
structured order data. The caller decides what to do with that data.

---

## File layout (to be built)

```
shopify/
  shopify.py          — the entire module; one function per capability
  architecture.md     — this file
```

One file. No sub-modules, no classes, no database.

---

## Capabilities (functions to build)

### 1. `get_orders_by_email(email) -> list[Order]`
Calls Shopify API: `GET /admin/api/2024-01/orders.json?email=<email>&status=any`
Returns all orders for that customer, newest first.

### 2. `get_order_by_number(order_number) -> Order | None`
Calls Shopify API: `GET /admin/api/2024-01/orders.json?name=<#1234>&status=any`
Returns the single matching order, or None.

### 3. `get_order_status(order) -> OrderStatus`
Given an Order object, extracts:
- fulfillment status (unfulfilled / partially_fulfilled / fulfilled)
- tracking number and tracking URL (if shipped)
- estimated delivery (if available)
- financial status (paid / refunded / partially_refunded)
- line items (product names, quantities)
- created date

### 4. `format_status_reply(order_status) -> str`
Given an OrderStatus, returns a plain English sentence the LLM (or the agent directly)
can use as a reply. Example:
> "Your order #1234 (Blue Bunny Romper x1) was shipped on June 25 via DHL.
>  Track it here: https://track.dhl.com/xyz. Expected delivery: June 28–30."

---

## Data flow (when called from main agent)

```
main agent receives "where is my order" ticket
  │
  ├─ extract customer email from Gorgias ticket
  │
  ├─ call shopify.get_orders_by_email(email)
  │     ├─ found →  shopify.get_order_status(latest_order)
  │     │               ├─ shipped   → shopify.format_status_reply() → auto-send ✓
  │     │               ├─ processing → "Your order is being prepared, ships soon" → auto-send ✓
  │     │               └─ refunded  → escalate to human (edge case)
  │     └─ not found → escalate to human (never guess)
  │
  └─ no email in ticket → escalate to human
```

---

## Confidence rule

This module auto-sends ONLY when:
- Customer email is present in the ticket
- At least one Shopify order exists for that email
- Order has a clear, unambiguous status (not partially_fulfilled with missing tracking)

All other cases → return `escalate=True` to the main agent.

---

## Config (via .env)

```bash
SHOPIFY_STORE=your-store-name          # the part before .myshopify.com
SHOPIFY_ACCESS_TOKEN=shpat_...         # Admin API access token (read-only scopes: orders, customers)
```

Read-only access token. This module never writes to Shopify.

---

## Dependencies

```
requests    # already in requirements.txt
```

No new packages. Shopify Admin REST API requires only HTTP + JSON.

---

## Shopify API scopes needed

When creating the access token in Shopify Admin → Apps → API credentials:
- `read_orders` — to fetch order list and details
- `read_customers` — to look up by email

No write scopes. This module is read-only by design.

---

## What this module does NOT do

- Does not write anything to Shopify
- Does not cancel orders, issue refunds, or modify anything
- Does not handle webhooks from Shopify
- Does not know about Gorgias, the KB, or Telegram
- Does not store any data locally

All of that stays in the main agent or future dedicated modules.

---

## Future capabilities (not in scope yet)

- `cancel_order()` — cancel an unfulfilled order (requires write scope)
- `create_refund()` — issue a refund (requires write scope, human approval gate)
- `get_product_inventory()` — check if an item is in stock
- `get_customer_history()` — all orders + lifetime value for a customer
