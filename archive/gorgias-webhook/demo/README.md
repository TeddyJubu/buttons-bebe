# Demo Dashboard System

A fully isolated demo environment at **http://127.0.0.1:8081** that exercises the real gorgias-webhook pipeline (classification, draft generation, priority logic, Workflow B feedback) without touching production Gorgias or Telegram.

## Quick start

```bash
# Optional — KB service improves draft quality (falls back to BM25 if unavailable)
cd /root/gorgias-webhook && python3 kb_service.py

# Terminal 2 — start the demo dashboard
cd /root/gorgias-webhook && python3 demo/demo_server.py

# Open in browser
# http://127.0.0.1:8081/demo
```

## Architecture

```
Dashboard UI (:8081)
    ├── Gorgias panel → demo_runner → pipeline.fetch_ticket_context
    │                                      ↓
    │                              run_workflow_a / run_workflow_b
    │                                      ↓
    └── Telegram panel ← demo_store ← patched gorgias_api + telegram_*
```

- **demo_store.py** — In-memory tickets, messages, customers (Shopify integrations from `qa_v3.MOCK_ORDERS`), Telegram inboxes, owner-reply queue
- **demo_patches.py** — Intercepts `gorgias_api.request`, `telegram_notify._send_to_chat`, `telegram_priority._send_to_chat`, `telegram_priority._get_updates`; patches Shopify via `qa_v3.fixtures_v3.patch_shopify()`
- **demo_gorgias_handler.py** — Mock Gorgias REST matching paths in `gorgias_api.py`
- **demo_runner.py** — Builds webhook payloads and invokes real workflow functions from `server.py`
- **demo_server.py** — stdlib HTTP server on `127.0.0.1:8081` only

Production `server.py` on `:8080` is **not modified** and does not need to be running.

## Environment variables

Set automatically by `demo_patches.apply()` at startup:

| Variable | Demo value | Purpose |
|----------|------------|---------|
| `GORGIAS_BASE_URL` | `http://127.0.0.1:8081` | Points at mock Gorgias REST |
| `FEEDBACK_DB_PATH` | `demo/feedback.db` | Isolated metrics DB |
| `HERMES_ALLOW_WRITE` | `1` | Allow writes to demo store |
| `WORKFLOW_A_CONFIRM` | `1` | Enable Workflow A posting |

LLM config inherits from `/root/.env`. For offline/CI testing:

```bash
LLM_PROVIDER=mock python3 -m pytest demo/test_demo_flow.py -v
```

## Preset scenarios

Load from the dashboard dropdown or via API:

| Scenario | Email | What it tests |
|----------|-------|---------------|
| `tracking` | `shipped@example.com` | Order context + tracking draft |
| `cancel` | `cancelme@example.com` | Unfulfilled order cancellation |
| `no_order` | `customer@example.com` | KB-only, no Shopify link |
| `urgent` | `shipped@example.com` | URGENT priority + immediate Telegram |
| `kb_gap` | `customer@example.com` | KB gap → Telegram Q&A loop |

```bash
curl -X POST http://127.0.0.1:8081/api/demo/scenarios/tracking
```

## API endpoints

### Demo control (`/api/demo/*`)

| Method | Path | Action |
|--------|------|--------|
| GET | `/api/demo/state` | Stats + last run status |
| GET | `/api/demo/tickets` | List tickets |
| GET | `/api/demo/tickets/:id` | Full ticket + messages + last run |
| POST | `/api/demo/tickets` | Create ticket → Workflow A |
| POST | `/api/demo/tickets/:id/customer-message` | Customer msg → Workflow A |
| POST | `/api/demo/tickets/:id/agent-reply` | Agent reply → Workflow B |
| GET | `/api/demo/telegram?bot=notify\|priority` | Telegram inbox |
| POST | `/api/demo/telegram/reply` | Queue owner reply (KB gap) |
| POST | `/api/demo/reset` | Clear all demo state |
| POST | `/api/demo/scenarios/:name` | Load preset scenario |

### Mock Gorgias REST

Same paths as `gorgias_api.py`: `/api/tickets/*`, `/api/messages`, `/api/customers/*`

## Tests

```bash
cd /root/gorgias-webhook
python3 -m pytest demo/test_demo_flow.py -v
# or
python3 demo/test_demo_flow.py
```

## Safety

- Binds to `127.0.0.1` only — not exposed to the internet
- All Gorgias/Telegram HTTP calls intercepted — zero production API traffic
- Isolated `demo/feedback.db` — production `feedback.db` untouched
- `demo_patches.restore()` runs on server shutdown

## KB gap Q&A flow

1. Workflow A detects `kb_gap` → priority bot sends question to Telegram panel
2. Type owner answer in the Telegram reply box at the bottom
3. Patched `_get_updates` returns it → background thread regenerates draft and posts updated internal note
