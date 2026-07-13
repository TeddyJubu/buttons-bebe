---
name: shopify
description: "Shopify Admin REST API: look up orders, products, customers, fulfillments, and refunds for Buttons Bebe support."
version: 1.0.0
author: Buttons Bebe
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Shopify, E-commerce, Orders, Fulfillment, API]
    related_skills: [gorgias, support-agent]
prerequisites:
  envvars: [SHOPIFY_SHOP, SHOPIFY_ADMIN_API_TOKEN]
---

# Shopify Admin REST API

Look up and manage Buttons Bebe order data through the Shopify Admin REST API
using curl from the terminal tool. All endpoints are documented at
https://shopify.dev/docs/api/admin-rest.

## Prerequisites

Two environment variables must be set (load from `~/.hermes/.env` or the
project `.env` file):

| Variable                 | Example                          | Where to get it                              |
|--------------------------|----------------------------------|----------------------------------------------|
| SHOPIFY_SHOP             | `buttonsbebe`                    | Shopify admin URL subdomain                    |
| SHOPIFY_ADMIN_API_TOKEN  | `shpat_...`                      | Shopify Admin > Apps > Develop apps > API token|

### Auth

Shopify Admin REST uses a bearer token or `X-Shopify-Access-Token` header.
The Admin API token goes in the header:

```bash
-H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN"
```

### Base URL

```
https://{{shop}}.myshopify.com/admin/api/2025-04
```

> Use the latest stable API version. Update the version in the BASE variable
> when Shopify deprecates old versions.

### Setup snippet (run at the top of any session)

```bash
# Load credentials
if [ -f "/root/Buttonsbebe Agent/.env" ]; then
  set -a; source "/root/Buttonsbebe Agent/.env"; set +a
fi

API_VERSION="2025-04"
BASE="https://${SHOPIFY_SHOP}.myshopify.com/admin/api/${API_VERSION}"
AUTH="-H \"X-Shopify-Access-Token: ${SHOPIFY_ADMIN_API_TOKEN}\""
```

---

## 1. Orders

### List orders (with filters)

```bash
# Recent orders (50 per page, most recent first)
curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/orders.json?status=any&limit=50" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for o in data.get('orders', []):
    print(f\"#{o['order_number']}  {o['email']}  {o['financial_status']}  {o['fulfillment_status']}  {o['total_price']}\")"

# Filter by fulfillment status
curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/orders.json?fulfillment_status=unfulfilled&limit=50"

# Filter by financial status
curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/orders.json?financial_status=paid&limit=50"

# Search by customer email
curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/orders.json?email=customer@example.com&status=any"
```

### Get a single order (full detail)

```bash
ORDER_ID=1234567890  # Synthetic example ID, not an order number

curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/orders/$ORDER_ID.json" \
  | python3 -c "
import sys, json
o = json.load(sys.stdin)['order']
print(f\"Order #{o['order_number']}  (ID: {o['id']})\")
print(f\"Email: {o['email']}\")
print(f\"Date: {o['created_at']}\")
print(f\"Financial: {o['financial_status']}\")
print(f\"Fulfillment: {o['fulfillment_status']}\")
print(f\"Total: {o['total_price']} {o['currency']}\")
print()
print('--- Line Items ---')
for item in o['line_items']:
    print(f\"  {item['title']} (Variant: {item.get('variant_title','N/A')})  Qty: {item['quantity']}  SKU: {item.get('sku','N/A')}  Price: {item['price']}\")
print()
print('--- Shipping Address ---')
addr = o.get('shipping_address', {})
print(f\"  {addr.get('name','')}\")
print(f\"  {addr.get('address1','')}\")
print(f\"  {addr.get('address2','')}\")
print(f\"  {addr.get('city','')}, {addr.get('province_code','')} {addr.get('zip','')}\")
print(f\"  {addr.get('country','')}\")
print(f\"  Phone: {addr.get('phone','N/A')}\")
print()
print('--- Fulfillments ---')
for f in o.get('fulfillments', []):
    status = f.get('status')
    tracking = f.get('tracking_number', 'N/A')
    url = f.get('tracking_url', 'N/A')
    print(f\"  Status: {status}  Tracking: {tracking}  URL: {url}\")
print()
print('--- Refunds ---')
for r in o.get('refunds', []):
    print(f\"  Refund created: {r['created_at']}  Total: {r.get('transactions',[{}])[0].get('amount','?')}\")"
```

### Look up order by order number (the customer-facing number)

```bash
ORDER_NUMBER=1001

curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/orders.json?name=$ORDER_NUMBER&status=any" \
  | python3 -c "
import sys, json
orders = json.load(sys.stdin).get('orders', [])
if orders:
    o = orders[0]
    print(f\"Found: #{o['order_number']}  ID: {o['id']}  Status: {o['financial_status']}/{o['fulfillment_status']}\")
else:
    print('Order not found')"
```

---

## 2. Updating Orders

> **SAFETY:** Do not auto-apply order mutations (cancel, refund, address change)
> without human approval. Use these endpoints to prepare the action and show the
> human what will happen.

### Update shipping address

```bash
curl -s -X PUT -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  -H "Content-Type: application/json" \
  "$BASE/orders/$ORDER_ID.json" \
  -d '{
    "order": {
      "shipping_address": {
        "address1": "123 New Street",
        "city": "Los Angeles",
        "province": "CA",
        "zip": "90001",
        "country": "US"
      }
    }
  }'
```

### Cancel an order

```bash
# reason: customer, inventory, fraud, declined, other
curl -s -X POST -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  -H "Content-Type: application/json" \
  "$BASE/orders/$ORDER_ID/cancel.json" \
  -d '{"reason": "customer", "email": true}'
```

### List order risks (fraud check)

```bash
curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/orders/$ORDER_ID/risks.json"
```

---

## 3. Fulfillments

### List fulfillments for an order

```bash
curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/orders/$ORDER_ID/fulfillments.json" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for f in data.get('fulfillments', []):
    print(f\"ID: {f['id']}  Status: {f['status']}  Tracking: {f.get('tracking_number','N/A')}\")
    for item in f.get('line_items', []):
        print(f\"  Item: {item['title']}  Qty: {item['quantity']}\")"
```

### Create a fulfillment (mark items as shipped)

```bash
# HUMAN ONLY — do not auto-fulfill
curl -s -X POST -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  -H "Content-Type: application/json" \
  "$BASE/orders/$ORDER_ID/fulfillments.json" \
  -d '{
    "fulfillment": {
      "tracking_number": "1Z9999...",
      "tracking_url": "https://tracking.example.com/1Z9999",
      "notify_customer": true,
      "line_items": [{"id": 1234567890}]
    }
  }'
```

---

## 4. Products

### List products

```bash
curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/products.json?limit=50" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for p in data.get('products', []):
    print(f\"#{p['id']}  {p['title']}  Status: {p['status']}  Vendor: {p['vendor']}\")"
```

### Get a product (detail with variants)

```bash
PRODUCT_ID=1234567890

curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/products/$PRODUCT_ID.json" \
  | python3 -c "
import sys, json
p = json.load(sys.stdin)['product']
print(f\"Title: {p['title']}\")
print(f\"Vendor: {p['vendor']}\")
print(f\"Type: {p['product_type']}\")
print(f\"Status: {p['status']}\")
print(f\"Description: {p.get('body_html','')[:200]}\")
print()
print('--- Variants ---')
for v in p.get('variants', []):
    print(f\"  {v['title']}  Price: {v['price']}  SKU: {v.get('sku','N/A')}  Available: {v.get('available','?')}\")"
```

### Search products by title

```bash
curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/products.json?title=infant%20dress" \
  | python3 -c "
import sys, json
for p in json.load(sys.stdin).get('products', []):
    print(f\"#{p['id']}  {p['title']}\")"
```

---

## 5. Customers

### Get customer detail (includes addresses + order count)

```bash
CUSTOMER_ID=12345

curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/customers/$CUSTOMER_ID.json" \
  | python3 -c "
import sys, json
c = json.load(sys.stdin)['customer']
print(f\"Name: {c['first_name']} {c['last_name']}\")
print(f\"Email: {c['email']}\")
print(f\"Phone: {c.get('phone', 'N/A')}\")
print(f\"Orders: {c.get('orders_count', 0)}\")
print(f\"Total spent: {c.get('total_spent', '0')}\")
print(f\"Created: {c['created_at']}\")
print()
print('--- Addresses ---')
for a in c.get('addresses', []):
    print(f\"  {a.get('address1','')}  {a.get('city','')}, {a.get('province_code','')} {a.get('zip','')}\")"
```

### Search customers by email

```bash
curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/customers.json?email=customer@example.com"
```

---

## 6. Refunds

### List refunds for an order

```bash
curl -s -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  "$BASE/orders/$ORDER_ID/refunds.json" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data.get('refunds', []):
    print(f\"Refund ID: {r['id']}  Created: {r['created_at']}\")
    for t in r.get('transactions', []):
        print(f\"  Transaction: {t['kind']}  Amount: {t.get('amount','?')}  Status: {t.get('status','?')}\")
    for line in r.get('refund_line_items', []):
        print(f\"  Refunded: {line.get('line_item',{}).get('title','?')}  Qty: {line.get('quantity','?')}\")"
```

### Calculate a refund (preview — no money moved)

```bash
# Preview what a refund would look like
curl -s -X POST -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  -H "Content-Type: application/json" \
  "$BASE/orders/$ORDER_ID/refunds/calculate.json" \
  -d '{
    "refund": {
      "shipping": {"amount": "0.00", "full_refund": false},
      "refund_line_items": [{"line_item_id": 1234567890, "quantity": 1, "restock_type": "no_restock"}]
    }
  }'
```

### Create a refund

```bash
# HUMAN ONLY — do not auto-refund
curl -s -X POST -H "X-Shopify-Access-Token: $SHOPIFY_ADMIN_API_TOKEN" \
  -H "Content-Type: application/json" \
  "$BASE/orders/$ORDER_ID/refunds.json" \
  -d '{
    "refund": {
      "shipping": {"amount": "0.00", "full_refund": false},
      "refund_line_items": [{"line_item_id": 1234567890, "quantity": 1, "restock_type": "return"}],
      "transactions": [{"amount": "29.99", "kind": "refund", "gateway": "shopify_payments"}]
    }
  }'
```

---

## Quick Reference

| Action                  | Method | Endpoint                              |
|-------------------------|--------|---------------------------------------|
| List orders             | GET    | `/orders.json`                        |
| Get order               | GET    | `/orders/{id}.json`                   |
| Update order            | PUT    | `/orders/{id}.json`                   |
| Cancel order            | POST   | `/orders/{id}/cancel.json`             |
| List fulfillments       | GET    | `/orders/{id}/fulfillments.json`      |
| Create fulfillment      | POST   | `/orders/{id}/fulfillments.json`      |
| List products           | GET    | `/products.json`                      |
| Get product             | GET    | `/products/{id}.json`                 |
| Get customer            | GET    | `/customers/{id}.json`                |
| Search customers        | GET    | `/customers.json?email=...`           |
| List refunds            | GET    | `/orders/{id}/refunds.json`           |
| Calculate refund        | POST   | `/orders/{id}/refunds/calculate.json` |
| Create refund           | POST   | `/orders/{id}/refunds.json`           |

## Safety rules (from agent-core-rules)

- **Never auto-apply** order mutations (cancel, refund, address change,
  fulfillment). Prepare the action and present it to a human for approval.
- If the order is already connected to a Gorgias ticket, do NOT ask the
  customer for the order number — look it up.
- Do not guess product information (sizing, fabric, fit). If it is not in the
  product title, description, or vendor data, escalate to a human.
- Search the KB (`search_kb` tool) before answering policy questions.
