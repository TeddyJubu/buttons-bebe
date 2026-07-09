# Shopify Admin API Research (Haiku agent, 2026-07-10)

> Source: shopify.dev. The emulator must be drop-in identical for these endpoints.

## What the EXISTING code actually calls (must-emulate, exact)
From `kb/scripts/sync_products.py`:
1. `POST https://{shop}/admin/oauth/access_token`
   body: `{"client_id", "client_secret", "grant_type": "client_credentials"}`
   → `{"access_token": "...", "scope": "...", "expires_in": 86399}`
2. `POST https://{shop}/admin/api/{ver}/graphql.json` — products query (id, title, handle,
   body/description, variants w/ price/sku, onlineStoreUrl, status, tags), paginated via
   `pageInfo { hasNextPage, endCursor }`.
Token header on all calls: `X-Shopify-Access-Token`.

## REST Admin API (legacy since 2024-10 but stable; emulate anyway for order lookups)
Format: `https://{store}.myshopify.com/admin/api/{version}/{resource}.json` (current ver 2026-07)

### Orders
- `GET /orders.json` — params: `status` (any|cancelled|...), `email`, `name` (#1001),
  `created_at_min/max`, `limit` (1-250, default 50), `page_info`, `fields`.
  Envelope: `{"orders": [...]}`.
- `GET /orders/{id}.json` → `{"order": {...}}`
- Order fields: `id`, `name` ("#1001"), `email`, `created_at`/`updated_at` (ISO8601 w/ offset),
  `currency`, `total_price` (string!), `subtotal_price`, `total_tax`, `total_discounts`,
  `financial_status` (paid|pending|refunded|partially_refunded), `fulfillment_status`
  (null|fulfilled|partial), `customer` {id, first_name, last_name, email},
  `line_items` [{id, product_id, variant_id, title, quantity, sku, price, price_set, fulfillment_status}],
  `shipping_address`/`billing_address` {first_name, last_name, address1, address2, city, province,
  province_code, zip, country, country_code, phone},
  `fulfillments` [{id, status, tracking_info/tracking_number/tracking_url, line_items, created_at}],
  `admin_graphql_api_id` ("gid://shopify/Order/{id}")
- Pagination: `Link: <...page_info=X&limit=N>; rel=next` response header.

### Customers
- `GET /customers.json`, `GET /customers/{id}.json`, `GET /customers/search.json?query=email:...`
- Fields: `id`, `first_name`, `last_name`, `email`, `phone`, `orders_count`, `total_spent` (string),
  `state`, `verified_email`, `tags` (comma string), `currency`, `addresses` [], `default_address`,
  `admin_graphql_api_id`.

### Products
- `GET /products.json` — params: `status` (active|archived|draft), `limit`, timestamps, `page_info`.
- Fields: `id`, `title`, `body_html`, `vendor`, `product_type`, `handle`, `status`, `tags` (comma
  string), `variants` [{id, product_id, title, price (string), sku, inventory_quantity,
  inventory_item_id, option1..3, barcode, weight}], `options`, `images` [{id, src, ...}].

## GraphQL Admin API
- `POST /admin/api/{version}/graphql.json`, body `{"query": "...", "variables": {...}}`
- Response `{"data": {...}, "extensions": {"cost": {"requestedQueryCost", "actualQueryCost",
  "throttleStatus": {"maximumAvailable": 1000.0, "currentlyAvailable", "restoreRate": 50.0}}}}`
- camelCase fields (`lineItems`, `financialStatus`); ids are GIDs.
- Errors return HTTP 200 with `{"errors": [{"message", "extensions": {"code"}}]}`
  (e.g. MAX_COST_EXCEEDED, ACCESS_DENIED).

## Rate limits & errors (emulate for resilience tests)
- REST: 40 req/min bucket, leak 2/s. Header `X-Shopify-Shop-Api-Call-Limit: 32/40`.
- 429: `Retry-After: 2.0` + `{"errors": "Exceeded 2 calls per second..."}`
- 401: `{"errors": "[API] Invalid API key or access token..."}`
- 404: `{"errors": "Not Found"}` ; 422: `{"errors": ["..."]}`

## Webhooks (emulator → help desk, optional)
- Topics: `orders/create`, `orders/fulfilled`, `orders/updated`, `customers/create`, ...
- Headers: `X-Shopify-Topic`, `X-Shopify-Hmac-Sha256` (base64 HMAC-SHA256 of raw body, key =
  client_secret), `X-Shopify-Shop-Domain`, `X-Shopify-Webhook-Id`, `X-Shopify-Event-Id`,
  `X-Shopify-API-Version`, `X-Shopify-Triggered-At`.
- Payload = bare order JSON (no envelope).

## Precision notes
- REST = snake_case wrapped (`{"order": {...}}`); GraphQL = camelCase under `data`.
- Money values are STRINGS in REST ("199.99").
- Timestamps ISO 8601 with offset (REST) / Z (webhooks).

## Redo (returns) API used by tools/redo_mcp.py
- Base `https://api.getredo.com/v2.2/stores/{store}`, Bearer auth.
- GET returns list / by order name / by return id. Emulate a minimal version for tests.
