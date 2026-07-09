# Fable API Contract — the shared blueprint (Wave 1 agents build EXACTLY to this)

All services: Python 3.10+, FastAPI + uvicorn, stdlib + fastapi/uvicorn/httpx/pydantic only
(no DB ORM — use sqlite3 stdlib, WAL mode). All bind 127.0.0.1. All JSON. All timestamps ISO 8601 UTC.

## Ports
| Port | Service | Root path |
|---|---|---|
| 9600 | Fable help desk server + console static files | `/` console, `/fable/api/*` native API, `/api/*` Gorgias-compat |
| 9601 | Shopify emulator | `/admin/*` exact Shopify paths, `/emulator/*` test controls |
| 9602 | Redo emulator | `/v2.2/stores/{store}/*`, `/emulator/*` |
| 9603 | Mailbox emulator | `/simulate/*`, `/outbox`, `/send` |

Env config in `fable/.env.fable` (all defaults work with zero setup):
```
FABLE_DB=fable/server/data/fable.db
FABLE_BRAIN=mock                # mock | anthropic | hermes
SHOPIFY_BASE=http://127.0.0.1:9601
SHOPIFY_SHOP=buttons-bebe.myshopify.com
SHOPIFY_CLIENT_ID=test-client-id
SHOPIFY_CLIENT_SECRET=test-client-secret
REDO_BASE=http://127.0.0.1:9602
REDO_API_KEY=test-redo-key
REDO_STORE_ID=bb-store-1
MAILBOX_BASE=http://127.0.0.1:9603
SUPPORT_EMAIL=care@buttonsbebe.com
```

## 1. Fable native API (server, :9600, prefix `/fable/api`)

### Tickets
- `GET /fable/api/tickets?status=open|closed|snoozed|all&channel=email|chat|whatsapp|all&sensitive=true&q=<search>&limit=50&cursor=<id>`
  → `{"tickets": [TicketSummary], "next_cursor": int|null, "counts": {"open":n,"closed":n,"snoozed":n,"sensitive_open":n}}`
- `GET /fable/api/tickets/{id}` → `{"ticket": Ticket}` (full, with messages[], customer, order_context, draft)
- `PATCH /fable/api/tickets/{id}` body any of `{"status","assignee","tags":[str],"snooze_until"}` → `{"ticket": Ticket}`

### Ticket actions (mirror the VPS console verbs)
- `POST /fable/api/tickets/{id}/send`   body `{"text": str}` → sends customer-facing reply via the ticket's channel transport; 409 if ticket closed. Response `{"ok":true,"message":Message}`
- `POST /fable/api/tickets/{id}/note`   body `{"text": str}` → internal note (never leaves Fable)
- `POST /fable/api/tickets/{id}/rewrite` body `{"instruction": str}` → brain rewrites draft → `{"draft": Draft}`
- Every action appends to `audit_log` table (who=console, what, ticket_id, ts).

### Intake (channels POST here; also used by tests)
- `POST /fable/api/intake/email`    body `{"from_email","from_name","subject","body_text","message_id"}`
- `POST /fable/api/intake/chat`     body `{"session_id","name","email"(opt),"body_text"}`
- `POST /fable/api/intake/whatsapp` body `{"phone","name","body_text"}`
  All → find-or-create customer → find-or-create open ticket (same customer+channel within 7d)
  → store message → **enqueue AI pipeline job** → `{"ticket_id": int, "message_id": int}` (202)

### Chat widget long-poll
- `GET /fable/api/chat/{session_id}/messages?after=<msg_id>` → `{"messages":[{id,from_agent,body_text,created_at}]}`

### Other
- `GET /fable/api/customers/{id}` / `GET /fable/api/customers?email=` / `?q=`
- `GET /fable/api/stats` → `{"tickets_today","open","avg_first_response_minutes","drafts_accepted_pct","by_channel":{...}}`
- `GET /fable/api/audit?limit=100`
- `GET /fable/api/health` → `{"ok":true,"brain":"mock","db":"...","queue_depth":n}` (all services expose `/health` — emulators at `/health` too)
- `GET /fable/api/macros` (P1, may stub `{"macros":[]}`)

### Core objects
```jsonc
TicketSummary: {"id":1,"subject":str,"status":"open","channel":"email","sensitive":false,
  "sensitive_reason":null,"customer":{"id":1,"name":str,"email":str},"preview":str,
  "has_draft":true,"is_unread":true,"tags":[str],"last_message_at":iso,"created_at":iso}

Ticket: TicketSummary + {"messages":[Message],"draft":Draft|null,
  "order_context":{"orders":[ShopifyOrderTrimmed],"returns":[RedoReturnTrimmed]}|null,
  "audit":[{"action","detail","at"}]}

Message: {"id":int,"ticket_id":int,"from_agent":bool,"public":bool,          // public=false → internal note
  "channel":"email|chat|whatsapp|internal-note","body_text":str,"created_at":iso,
  "sender_name":str,"via":"customer|console|ai"}

Draft: {"id":int,"ticket_id":int,"body_text":str,"risk":"low|sensitive",
  "risk_reason":str|null,"brain":"mock","kb_refs":[str],"created_at":iso,
  "status":"proposed|sent|noted|superseded"}
```

## 2. AI pipeline (inside server; runs per intake, synchronous worker thread polling a jobs table)

Steps (each logged): fetch customer's orders from Shopify emulator (REST, by email) → returns
from Redo emulator → risk classify → brain drafts → store Draft on ticket.

**Risk classifier (deterministic, code not LLM):** sensitive if body matches (case-insensitive)
refund|chargeback|dispute|damaged|broken|wrong item|missing|never arrived|lost|scam|lawyer|
angry-signals (!!!+, ALL-CAPS ≥ 6 words) — else low. Extendable word list in `server/app/risk.py`.

**Brain interface (`server/app/brains/base.py`):**
```python
class Brain(Protocol):
    name: str
    def draft(self, ctx: DraftContext) -> DraftResult: ...
    def rewrite(self, ctx: DraftContext, current_draft: str, instruction: str) -> DraftResult: ...
# DraftContext: ticket subject/messages, customer, orders, returns, kb_snippets, risk
# DraftResult: body_text, kb_refs, notes
```
`MockBrain` (default): template-based, deterministic — greets by first name, answers
order-status questions with real tracking data from ctx.orders, uses polite fallback otherwise.
Deterministic given same ctx (tests rely on this). `anthropic`/`hermes` adapters: stub files
raising NotImplementedError with clear TODO.

## 3. Gorgias-compat layer (server, :9600, prefix `/api`, Basic auth accepted-but-ignored)
Envelope `{"data":[...],"object":"list","meta":{"next_cursor","prev_cursor","total_resources"}}`.
- `GET /api/tickets?limit=` , `GET /api/tickets/{id}` , `GET /api/tickets/{id}/messages?limit=`
- `GET /api/customers/{id}` , `GET /api/customers?email=`
- `POST /api/tickets/{id}/messages` `{"channel":"internal","body_text",...}` → internal note (VPS writer shape)
Field names per RESEARCH-gorgias-api.md (status/channel/via/from_agent/public/body_text/created_datetime…).

## 4. Shopify emulator (:9601) — see RESEARCH-shopify-api.md for exact field lists
- `POST /admin/oauth/access_token` `{"client_id","client_secret","grant_type":"client_credentials"}`
  → `{"access_token": uuid-ish, "scope":"read_orders,read_customers,read_products","expires_in":86399}`;
  bad secret → 401 `{"errors":"[API] Invalid API key or access token (unrecognized login or wrong password)"}`
- All `/admin/api/{ver}/*` require header `X-Shopify-Access-Token` w/ unexpired token (accept any `{ver}` matching `20\d\d-\d\d`).
- REST endpoints + params + envelopes exactly per research doc:
  `orders.json` (email, name, status, created_at_min, limit≤250, page_info + `Link` header),
  `orders/{id}.json`, `customers.json`, `customers/search.json?query=email:x`, `customers/{id}.json`,
  `products.json` (status, limit, page_info). Money=strings, snake_case, `admin_graphql_api_id` on all.
- Every REST response includes `X-Shopify-Shop-Api-Call-Limit: n/40` (real leaky bucket: 40 cap, 2/s leak; exceed → 429 + `Retry-After: 2.0`).
- GraphQL `POST /admin/api/{ver}/graphql.json`: support the products query shape used by
  `kb/scripts/sync_products.py` (products(first,after){edges{node{id title handle description bodyHtml onlineStoreUrl status tags variants(first){edges{node{id title price sku}}}}} pageInfo{hasNextPage endCursor}})
  plus a basic orders query; respond `{"data":..., "extensions":{"cost":{...}}}`.
- `X-Emulator-Scenario` request header: `rate-limit`→429, `server-error`→500, `slow`→sleep 5s.
- Test controls: `POST /emulator/reset` (reseed), `POST /emulator/orders` (add), `PATCH /emulator/orders/{id}` (change status/fulfillment), `GET /emulator/state`.
- Seed: JSON files in `emulators/shopify/seed/` — ~30 baby-clothing products (Buttons Bebe style), 25 customers, 40 orders across statuses; 5 customers referenced by the demo script below.

## 5. Redo emulator (:9602)
Bearer `REDO_API_KEY`. `GET /v2.2/stores/{store}/returns?limit=`, `.../returns/{id}`,
`.../returns?order_name=%23BB1015`. Return: `{"id","order_name","status":"requested|approved|in_transit|refunded|rejected","items":[{title,qty,reason}],"created_at","refund_amount"}`.
Seeded 8 returns tied to Shopify emulator orders. `POST /emulator/reset`.

## 6. Mailbox emulator (:9603)
- `POST /simulate/incoming` `{"from_email","from_name","subject","body_text"}` → forwards to Fable `POST /fable/api/intake/email` → `{"forwarded":true,"ticket_id":n}`
- `POST /send` (Fable calls this to "send" customer email) `{"to","subject","body_text"}` → stores in outbox
- `GET /outbox` / `DELETE /outbox` — tests assert exactly what "left" the system. NOTHING ever leaves localhost.

## 7. Demo scenario (scripts/demo.sh must pass)
1. `run-all.sh` boots 4 services; all `/health` green.
2. Email: "Where is my order #BB1015?" from seeded customer → ticket + low-risk draft containing that order's real tracking number from the emulator.
3. Chat: "Do you ship to Canada?" → ticket + draft.
4. WhatsApp: "My order arrived damaged, I want a refund!!" → ticket flagged SENSITIVE + draft with warning.
5. Console verbs: send #2's draft (appears in mailbox outbox), note #3, rewrite #4.
6. Gorgias-compat: `GET /api/tickets` lists all three.
```
