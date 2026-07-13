# 07 · The Fable Rebuild (branch: `Fable_buttonsbebe`)

**What this doc covers:** Fable — a self-contained, brain-pluggable, fully-testable rebuild of the Buttons Bebe help desk that lives **only on the git branch `Fable_buttonsbebe`** (never merged to `main`, never deployed). It re-implements the "AI drafts, a human sends" product as its own FastAPI application with local API **emulators** so the whole thing can be developed, demoed, and tested offline without ever touching the real Gorgias, Shopify, or Redo. This branch is now explicitly **quarantined** pending the release gates in **handover doc 10**. This doc explains what Fable is and why, its architecture and request pipeline, the brain abstraction, the emulators, the test suite, how to run it, a file-by-file map, and how it relates to the roadmap.

**Sources read (all branch-qualified to `Fable_buttonsbebe`):** `fable/README.md`; `fable/docs/{API-CONTRACT,SPRINT-PLAN,TESTING-STRATEGY,RESEARCH-gorgias-api,RESEARCH-shopify-api}.md`; `fable/server/main.py`; every module in `fable/server/app/` (`config, db, models, pipeline, risk, intake, context, actions, audit, stats, tickets, draft_cleaner, gorgias_compat, kb_search, migration, channels_email, __init__`) and `fable/server/app/brains/` (`base, mock, anthropic, anthropic_stub, hermes_stub, __init__`); all four emulators (`fable/emulators/{shopify,redo,mailbox,gorgias}/app.py`) + `run-emulators.sh`/`stop-emulators.sh` + `shopify/seed/generate_seed.py`; every script in `fable/scripts/`; the console (`fable/console/{index.html,app.js,style.css}`) and widget (`fable/{console/,}widget/*`); `fable/.env.fable` (variable **names only**); and the whole `fable/tests/` tree (README, conftest, pytest.ini, unit/, integration/, e2e/). Line counts are from `git show`.

> **One-line orientation:** `main` = the LIVE system on the VPS (Hermes/`glm-5.2`, real Gorgias, three MCP tools — see docs `02`–`05`). **Fable = a parallel R&D branch** that replaces Gorgias entirely with its own inbox + AI pipeline, and stands up fake Shopify/Redo/mail servers so it needs no network. **Fable is not running anywhere.** The new team must decide whether to continue it, fold pieces of it into `main`, or shelve it.

---

## 0. TL;DR for the context-free agent

- **Where the code is:** the branch `Fable_buttonsbebe`, under the top-level folder `fable/`. Read it with `git show Fable_buttonsbebe:fable/<path>`. **Do not `git checkout`** — other agents are on other branches.
- **What it is:** a **FastAPI help-desk server** (`fable/server`, port 9600) with a SQLite DB, a background AI **pipeline** that drafts replies, a **Gorgias-compatible API** so existing tools keep working, a static **console** UI, an embeddable **chat widget**, and **local emulators** for Shopify/Redo/mail (and Gorgias, for migration).
- **The big idea #1 — brain plug:** the LLM is behind a `Brain` interface (`draft()`/`rewrite()`). `FABLE_BRAIN=mock|anthropic|hermes` swaps the model **without touching the pipeline**. `mock` is the deterministic default that tests rely on.
- **The big idea #2 — offline by construction:** every outbound HTTP call targets `http://127.0.0.1:96xx` with `trust_env=False`; outbound email is trapped in the mailbox emulator's outbox. **Nothing can leave localhost.** A test asserts this on every run.
- **Safety model = same as LIVE:** the AI only ever **drafts**; a human clicks **Send** (with a confirm step); sensitive tickets (refund/damaged/angry…) are flagged; every mutation is audited.
- **Run it:** `cd fable && ./scripts/demo.sh` (boots everything + plays a scripted story). **Test it:** `./fable/scripts/test.sh` (unit + integration; `FABLE_E2E=1` also boots the real 4-service stack).
- **Status:** **not deployed.** It has advanced past its own "Sprint 1 MVP" plan — several Sprint-2 items (real Anthropic brain, Gorgias→Fable migration importer, keyword KB search, IMAP/SMTP transport skeleton) are already built. See §8 and §10.

---

## 1. What Fable is, and WHY (contrast with the LIVE Hermes system)

### 1.1 The problem Fable is solving

The live system (docs `02`–`05`) is an **overlay on Gorgias**: customer messages arrive in Gorgias, a webhook wakes a processor, Hermes (Nous Research CLI, model `glm-5.2` via Ollama Cloud) reads the ticket through three read-only MCP tools, drafts a reply, and writes it back into Gorgias as an internal note. Gorgias remains the "post office."

That design has two properties the team wanted to escape:

1. **It is hard to test.** Per `Fable_buttonsbebe:fable/docs/TESTING-STRATEGY.md §6`, the VPS system's tests are "live-run judgment docs — human-graded, not CI-able." You cannot prove a change is safe without a human watching real tickets.
2. **It is coupled to paid SaaS.** Every dev iteration risks touching the real Gorgias/Shopify account, and the LLM is a specific remote model.

### 1.2 What Fable does differently

`Fable_buttonsbebe:fable/docs/SPRINT-PLAN.md §1` states the thesis plainly: **"Fable becomes the post office."** Messages (email / chat / WhatsApp) arrive **directly in Fable**, the AI drafts **inside Fable**, and a human clicks Send **inside Fable**. Gorgias is no longer required — but Fable keeps a **Gorgias-compatible API layer** so the existing tools and a one-time migration importer keep working.

To make this testable and offline, Fable ships **emulators** — small local FastAPI servers that speak the *exact* wire protocol of the real services (same URLs, JSON shapes, auth handshakes, even rate-limit errors). Point Fable at the emulators in dev; point it at the real services later by changing config, same code path.

### 1.3 Side-by-side

| Dimension | LIVE (`main`, on the VPS) | Fable (`Fable_buttonsbebe`, local only) |
|---|---|---|
| Help desk of record | **Gorgias** (external SaaS) | **Fable itself** (own SQLite inbox) |
| The "brain" | Hermes CLI, `glm-5.2` via Ollama Cloud, one-shot per ticket | Pluggable `Brain`: `mock` (default), `anthropic` (real, implemented), `hermes` (stub). Swap via `FABLE_BRAIN` |
| How context is fetched | 3 MCP HTTP tools (kb :8077, redo :8078, gorgias :8079) | Direct `httpx` to **emulators** (Shopify :9601, Redo :9602) + local keyword KB search |
| Channels | Email via Gorgias; WhatsApp via Baileys bridge | Email / chat / WhatsApp intake endpoints; email via mailbox emulator; chat via widget long-poll |
| The only "write" | Internal note into Gorgias (`gorgias_writer.py`) | Human-initiated **Send** (per-channel transport) + internal note; both local |
| Risk classification | Done by the LLM; deterministic `classifier.py` is a **stub** on `main` | **Deterministic `risk.py`** (implemented) + parity test against the VPS classifier port |
| Testability | Human-graded live runs | **~180 automated checks**, one command, **zero network** |
| Deployment | Running on VPS `srv1766050` | **Not deployed anywhere** |

### 1.4 Why it maps to the roadmap

`SPRINT-PLAN.md` calls this **"Fable Sprint 1 — Local MVP"** (dates 2026-07-10 → 2026-07-17, branch `Fable_buttonsbebe`, `main` untouched). The plan's own §10 "Sprint 2 preview" lists the road to actually replacing Gorgias (real email/WhatsApp/brain, migration importer, parallel run, cutover only after the client signs off). As built, Fable already reaches into that Sprint-2 list (see §8/§10). So Fable is best understood as a **proof-of-concept for a Gorgias replacement**, not a shipped product.

> **Safety carries over unchanged** (`SPRINT-PLAN.md §2`, `TESTING-STRATEGY.md §0`): AI only drafts; a human sends; sensitive tickets are flagged; everything is logged. These are the same four rules as `main`'s `CLAUDE.md §2`, and Fable's test suite asserts all four on every run (`fable/tests/integration/test_safety_invariants.py`).

---

## 2. Architecture

### 2.1 Component & port map

All services bind `127.0.0.1`. Config lives in `Fable_buttonsbebe:fable/.env.fable` (every default works with zero setup).

| Port | Service | Source | Started by `run-emulators.sh`? |
|---|---|---|---|
| **9600** | Fable help-desk server + console static files + native API + Gorgias-compat API | `fable/server/` | (server) `scripts/run-server.sh` |
| 9601 | **Shopify emulator** — OAuth, REST orders/customers/products, GraphQL, rate limits | `fable/emulators/shopify/app.py` | ✅ yes |
| 9602 | **Redo emulator** — returns/refunds | `fable/emulators/redo/app.py` | ✅ yes |
| 9603 | **Mailbox emulator** — inbound simulate + outbound capture | `fable/emulators/mailbox/app.py` | ✅ yes |
| 9604 | **Gorgias emulator** — read-only Gorgias API stand-in (for the migration importer) | `fable/emulators/gorgias/app.py` | ❌ **no** — tests-only / manual |

> **⚠️ Gotcha:** the Gorgias emulator (9604) is real and tested, but it is **not** in the README's port table and **not** launched by `run-emulators.sh`. It exists so `app/migration.py` can be exercised in-process by tests (`test_gorgias_emulator.py`, `test_migration.py`). See §10.

### 2.2 Server layout (`fable/server/`)

```
fable/server/
├── main.py                 FastAPI app factory: mounts native API (/fable/api/*),
│                           Gorgias-compat (/api/*), console static files (/),
│                           starts the pipeline worker thread on startup.
├── data/                   SQLite db lives here at runtime (fable.db, git-ignored)
└── app/
    ├── config.py           reads .env.fable (KEY=VALUE) + env overrides + defaults
    ├── db.py               SQLite schema (WAL), init_db(), additive migrations
    ├── models.py           Pydantic request bodies (intake/actions/gorgias-write)
    ├── intake.py           email/chat/whatsapp → find-or-create customer+ticket → enqueue job
    ├── pipeline.py         background worker: context→risk→gate→KB→brain→store draft
    ├── context.py          Shopify (orders) + Redo (returns) fetchers; degrade to None
    ├── risk.py             DETERMINISTIC risk classifier (code, not LLM)
    ├── draft_cleaner.py    should_draft() gate + clean_draft() (SHARED with the VPS)
    ├── kb_search.py        keyword search over repo kb/{policies,faq,intents}
    ├── brains/             the Brain abstraction (see §3)
    ├── actions.py          send / note / rewrite (the console verbs)
    ├── tickets.py          ticket read/list/patch + JSON serializers
    ├── stats.py            dashboard metrics
    ├── audit.py            audit-log helpers (every mutation records a row)
    ├── gorgias_compat.py   /api/* Gorgias-shaped reads + internal-note write
    ├── migration.py        Gorgias → Fable importer (idempotent, dry-run)
    └── channels_email.py   EmailTransport interface: emulator today, IMAP/SMTP skeleton
```

### 2.3 The request pipeline (intake → context → risk → gate → KB → brain → draft → audit)

This is the heart of Fable and mirrors the LIVE flow, but self-contained. Two phases: **synchronous intake** (fast, returns 202) and **asynchronous drafting** (a worker thread).

```
Customer message (email / chat / whatsapp)
      │  POST /fable/api/intake/{channel}
      ▼
INTAKE  (app/intake.py)                                    [synchronous]
  • find-or-create customer (by email / phone; enrich missing fields)
  • find-or-create OPEN ticket for (customer, channel) within a 7-day window
  • store the customer message (public, from_agent=0)
  • enqueue a row in the `jobs` table  ──►  returns 202 {ticket_id, message_id}
      │
      ▼
PIPELINE WORKER  (app/pipeline.py — daemon thread, polls `jobs` every 1s) [async]
  process_job():
   1. CONTEXT  (app/context.py) — mint Shopify token (client-credentials, cached) →
      GET orders by email → GET Redo returns for those order names.
      ⟶ ANY transport failure returns None: order_context set NULL, ticket still drafts.
   2. RISK  (app/risk.py) — deterministic classify(last_customer_text) → (low|sensitive, reason)
      ⟶ writes tickets.sensitive / sensitive_reason.
   3. GATE  (draft_cleaner.should_draft) — empty / "thanks" / emoji-only → NO draft, stop.
   4. KB  (app/kb_search.py) — top-3 policy/faq/intent snippets for grounding (never fails a ticket).
   5. BRAIN  (brains/get_brain().draft(ctx)) — build DraftContext, get DraftResult.
      ⟶ empty body (declined/cleaned to nothing / NotImplementedError) → NO draft, stop.
   6. STORE  — supersede any older proposed draft, INSERT the new one (status='proposed').
  Every step appends an audit_log row (who='pipeline'). One bad job never stalls the loop.
      │
      ▼
HUMAN reviews the draft in the CONSOLE and acts:
   POST /fable/api/tickets/{id}/send    → customer-facing reply via the channel transport (confirm step in UI)
   POST /fable/api/tickets/{id}/note    → internal note (never leaves Fable)
   POST /fable/api/tickets/{id}/rewrite → brain.rewrite(draft, instruction) → new proposed draft
  Every action is audited. Send on a closed ticket → 409. Transport failure on Send → 502, draft stays 'proposed'.
```

Key robustness properties (all directly in code):

- **Context degrades, never crashes.** `context.fetch_context()` returns `None` on any connection/HTTP error; the pipeline records "no context (emulators unreachable)", nulls `order_context`, and still drafts. Proven by `test_pipeline.py` + the E2E "kill Shopify mid-run" case.
- **Returns are scoped to the customer's own orders.** `context.py` only looks up Redo returns for the order names it found for that email — it never lists other customers' returns.
- **Draft supersession.** A new proposed draft marks the previous proposed draft `superseded`, so a ticket has at most one active draft.
- **The pipeline never auto-sends.** It only ever inserts a `drafts` row. Sending is a separate human-triggered endpoint. (Safety invariant #1.)

### 2.4 Data model (`app/db.py`, SQLite + WAL)

Ten tables, Gorgias-shaped so migration is lossless. Timestamps are ISO-8601 UTC strings.

| Table | Purpose | Notable columns |
|---|---|---|
| `customers` | people | `email, name, firstname, lastname, phone, external_id` |
| `tickets` | conversations | `status(open|closed|snoozed), channel, sensitive, sensitive_reason, order_context(JSON), external_id, is_unread` |
| `messages` | every message | `from_agent, public(0=internal note), channel, body_text, via(customer|console|ai|api), external_id` |
| `drafts` | AI proposals | `body_text, risk, risk_reason, brain, kb_refs(JSON), status(proposed|sent|noted|superseded)` |
| `tags`, `ticket_tags` | labels (many-to-many) | |
| `jobs` | the pipeline queue | `ticket_id, message_id, kind, status(queued|running|done|error), attempts, error` |
| `audit_log` | every mutation | `ticket_id, who, action, detail, created_at` |
| `chat_sessions` | widget session ↔ ticket | `session_id, customer_id, ticket_id` |
| `whatsapp_outbox` | captured WA "sends" | `ticket_id, phone, body_text` |

- **`external_id`** on customers/tickets/messages exists purely for the migration importer's idempotency (added via `_ADDITIVE_COLUMNS` so old DBs upgrade in place).
- `init_db()` runs the `SCHEMA` script (idempotent `CREATE TABLE IF NOT EXISTS`) then backfills any missing additive columns guarded by `PRAGMA table_info`.

### 2.5 The Gorgias-compatibility layer (`app/gorgias_compat.py`) — and why it exists

Mounted at **`/api/*`** on the same server (port 9600), this maps Fable's objects onto **Gorgias field names** (`created_datetime`, `from_agent`, `public`, `body_text`, the `{data, object:"list", meta:{next_cursor,…}}` envelope) so that **the existing VPS tool `tools/gorgias_mcp.py` works against Fable by changing only its base URL**. Basic auth is **accepted-but-ignored**.

Endpoints (the five reads + one write the VPS uses):

- `GET /api/tickets?limit=&cursor=` — cursor-paginated list (messages omitted)
- `GET /api/tickets/{id}` — one ticket with messages inline
- `GET /api/tickets/{id}/messages?limit=`
- `GET /api/customers?email=` and `GET /api/customers/{id}`
- `POST /api/tickets/{id}/messages` — the **VPS writer path**: `channel:"internal"` → stored as an internal note (`public=0`, channel `internal-note`).

Why it exists: it is the seam that lets the current fleet of tools (and a Gorgias migration) treat Fable as a drop-in Gorgias, so a future cutover doesn't require rewriting the tools. It is the mirror image of the emulators — instead of Fable *pretending to call* Gorgias, it *pretends to be* Gorgias for downstream readers.

### 2.6 The native API (`/fable/api/*`, defined in `main.py`)

Tickets (`GET /tickets` with `status|channel|sensitive|q|limit|cursor` filters + counts; `GET /tickets/{id}`; `PATCH /tickets/{id}`), actions (`/send`, `/note`, `/rewrite`), intake (`/intake/{email,chat,whatsapp}`), chat long-poll (`GET /chat/{session_id}/messages?after=`), customers, `GET /stats`, `GET /audit`, `GET /macros` (stubbed `{"macros":[]}`), and `GET /health` (also at `/health`). The full contract is `Fable_buttonsbebe:fable/docs/API-CONTRACT.md`.

---

## 3. The brain abstraction (the key design idea)

**Swap the LLM without touching the pipeline.** Everything AI-shaped goes through one small interface.

### 3.1 The interface — `app/brains/base.py`

```python
@dataclass
class DraftContext:
    ticket_id, subject, channel, customer(dict), messages(list),
    last_customer_text, orders(list), returns(list), kb_snippets(list),
    risk="low", risk_reason=None

@dataclass
class DraftResult:
    body_text: str; kb_refs: list = []; notes: str = ""

@runtime_checkable
class Brain(Protocol):
    name: str
    def draft(self, ctx: DraftContext) -> DraftResult: ...
    def rewrite(self, ctx: DraftContext, current_draft: str, instruction: str) -> DraftResult: ...
```

The pipeline builds a `DraftContext` from the ticket bundle (customer, messages, Shopify orders, Redo returns, KB snippets, risk) and calls `brain.draft(ctx)`. It never knows which model answered.

### 3.2 Selection — `app/brains/__init__.py`

`get_brain(name=None)` resolves `name or config.BRAIN or "mock"`:

- `"mock"` → `MockBrain()`
- `"anthropic"` → tries `AnthropicBrain()`; on `BrainConfigError` (e.g. no API key) it **logs a warning and falls back to `MockBrain`** so the app never crashes.
- `"hermes"` → `HermesBrain()` (stub — raises `NotImplementedError`)
- unknown → `MockBrain` (safe default)

`FABLE_BRAIN` in `.env.fable` (default `mock`) selects it. That single env var is the whole switch.

### 3.3 The three implementations

**`MockBrain` (`brains/mock.py`) — the deterministic default.** Template-based; same `DraftContext` in ⇒ same `DraftResult` out (tests depend on this). Behaviour:
- **Sensitive** → a warm, no-commitment acknowledgement that makes **no money promises** (never contains "refund"), flagged for the care team.
- **Order-status** (order keyword + real orders in context) → a reply that quotes the actual fulfillment status and **real tracking number** from the emulator context.
- **Ship-to-country** → a generic shipping answer.
- **Fallback** → polite ack, asks for an order number.
- Always signs off `— Buttons Bebe Care Team`. `rewrite()` handles "shorter"/"friendlier"/passthrough transforms deterministically.

**`AnthropicBrain` (`brains/anthropic.py`) — a REAL, working adapter (feature "F3").** ~314 lines. Drop-in with the same interface. It:
- Builds a **grounded system prompt** from the safety rules (`CLAUDE.md §2` restated) + the order/return context + KB snippets, and calls the **Anthropic Messages API** over `httpx` (default model `claude-sonnet-4-5`, `anthropic-version: 2023-06-01`).
- Enforces grounding in the prompt: only state facts present in the provided context/KB; never invent prices/sizes/dates/tracking; on **sensitive** tickets make no money promises.
- Runs the customer message through `should_draft` (no draft for bare "thanks"/empty) and every model output through the shared `clean_draft` (strips self-commentary / de-dupes).
- **Never raises for the pipeline:** any API error (429/500/timeout/bad body) degrades to an empty string ⇒ "no draft."
- **Offline-testable:** the constructor accepts an injected `httpx` client/transport, so `tests/unit/test_anthropic_brain.py` uses an `httpx.MockTransport` — **no real network call is ever made.** Requires `FABLE_ANTHROPIC_API_KEY` at runtime or it raises `BrainConfigError` (→ factory falls back to mock).

**`HermesBrain` (`brains/hermes_stub.py`) — a genuine stub.** `draft`/`rewrite` raise `NotImplementedError` with a clear TODO (shell out to the Hermes CLI / bridge in a later sprint).

> **⚠️ Dead file:** `brains/anthropic_stub.py` is an older `NotImplementedError` stub for Anthropic that is **no longer used** — `brains/__init__.py` imports the real `.anthropic`. Keep this in mind: the API-CONTRACT still describes anthropic as a stub, but the code moved on (see §10). The new team should probably delete `anthropic_stub.py`.

### 3.4 The shared draft cleaner (`app/draft_cleaner.py`) — note the cross-branch link

Two stable, stdlib-only functions both tracks import:
- `should_draft(customer_message)` → gate: returns `ok=False` for empty / whitespace / bare-ack ("thanks", emoji, punctuation-only) messages so the pipeline drafts nothing (fixes live QA #19: a fabricated reply to an empty message).
- `clean_draft(ai_draft)` → two conservative passes: (1) cut trailing model self-commentary ("The response above was complete…", "Note to the reviewer:"), (2) collapse a draft that is the same body repeated 2×/3× back to one copy. Designed so a normal reply passes through unchanged.

Its docstring states it is **owned by Fable but also shipped to the LIVE processor** via `deploy/vps-patches/` (a copy of this same file), fixing real QA leak patterns (#01/#04/#10/#19). This is a concrete artifact that already crossed from the Fable branch back toward `main`/VPS — worth tracing during handover.

---

## 4. Local emulators (`fable/emulators/`)

The emulators are "the mocks" — no mocking library is used; the test suite drives these real (in-process) apps. Each is stdlib + FastAPI only, binds `127.0.0.1`, and exposes `/health` plus `/emulator/*` test controls.

### 4.1 Shopify emulator — `emulators/shopify/app.py` (port 9601)

The most elaborate one; it is "indistinguishable from real Shopify for our code paths" (`SPRINT-PLAN.md §5`):
- **OAuth client-credentials grant** `POST /admin/oauth/access_token` → 24h token (exact Shopify 401 body on bad secret).
- **`X-Shopify-Access-Token` required** on every Admin call; accepts any version matching `20\d\d-\d\d`.
- **REST**: `orders.json` (filters `email`, `name`, `status`, `financial_status`, `fulfillment_status`, `created_at_min/max`, `ids`, `limit`, `page_info` + `Link` header), `orders/{id}.json`, `customers.json`, `customers/search.json?query=email:…`, `customers/{id}.json`, `products.json`. snake_case, money-as-strings, `admin_graphql_api_id` on everything.
- **GraphQL** `POST /admin/api/{ver}/graphql.json`: the products query used by `kb/scripts/sync_products.py` **including the Bulk Operations flow** (`bulkOperationRunQuery` → `currentBulkOperation` → a JSONL export at `/emulator/bulk/products.jsonl`), plus a basic orders query, with `extensions.cost.throttleStatus`.
- **Leaky-bucket rate limit** (cap 40, leak 2/s) → `X-Shopify-Shop-Api-Call-Limit: n/40`, and 429 + `Retry-After: 2.0` on overflow.
- **Fault injection** via request header `X-Emulator-Scenario: rate-limit|server-error|slow` → 429 / 500 / 5s sleep.
- **Test controls**: `POST /emulator/reset` (reseed), `POST /emulator/orders`, `PATCH /emulator/orders/{id}` (change status / add tracking), `GET /emulator/state`.
- **Seed** (`seed/*.json`, generated deterministically by `seed/generate_seed.py`, RNG seed 42): **30** baby-clothing products, **25** customers, **40** orders `#BB1001–#BB1040` across mixed statuses. Order `#BB1015` for Emma Wilson carries tracking `1Z999AA10123456784` — the value the demo/E2E asserts flowed through the pipeline into a draft.

### 4.2 Redo emulator — `emulators/redo/app.py` (port 9602)

Bearer-auth (`REDO_API_KEY`) returns API. `GET /v2.2/stores/{store}/returns` (filter by `order_name`, `status`, `email`, `limit`), `.../returns/{id}`. **8 seeded returns** tied to real emulator order names (e.g. `#BB1022` = approved, `#BB1015` = rejected/out-of-window), spanning `requested|approved|in_transit|refunded|rejected`. `POST /emulator/reset`, `GET /emulator/state`.

### 4.3 Mailbox emulator — `emulators/mailbox/app.py` (port 9603)

The "nothing ever leaves localhost" guarantee:
- `POST /simulate/incoming` — pretend a customer emailed; **forwards** the payload to Fable's `POST /fable/api/intake/email` (this is how the demo injects Emma's email).
- `POST /send` — Fable calls this to "send" a customer email; it is **captured in an in-memory outbox**, never transmitted.
- `GET /outbox` / `DELETE /outbox` — tests assert exactly what "left" the system (it never does).

### 4.4 Gorgias emulator — `emulators/gorgias/app.py` (port 9604)

A **read-only** stand-in for the real Gorgias REST API, shaped per `RESEARCH-gorgias-api.md`: HTTP Basic auth, cursor pagination with the `{data, object, meta}` envelope, `GET /api/tickets`, `/api/tickets/{id}` (messages inline), `/api/tickets/{id}/messages`, `/api/customers?email=`, `/api/customers/{id}`, plus `/emulator/reset`. **Seed:** ~10 customers and **15** Buttons Bebe tickets (`#6001–6015`) across email/chat/whatsapp/sms, some closed, several sensitive (refund/damaged/chargeback/missing), with agent replies and internal notes. Its sole purpose is to give the **migration importer** (`app/migration.py`) something Gorgias-shaped to read in tests — **it is not part of the runtime stack** and `run-emulators.sh` does not start it.

### 4.5 How they're launched, and how the server points at them

- **`emulators/run-emulators.sh`** starts **shopify/redo/mailbox** (not gorgias) via `nohup python3 app.py`, writes PIDs to `/tmp/fable-emu-*.pid` and logs to `/tmp/fable-emu-*.log`, then health-checks each on a 30×0.5s loop. `stop-emulators.sh` kills by PID file plus a belt-and-suspenders `pkill -f`.
- **The server points at them by config**: `SHOPIFY_BASE=http://127.0.0.1:9601`, `REDO_BASE=…:9602`, `MAILBOX_BASE=…:9603` in `.env.fable`. `context.py`/`actions.py` read these. Every outbound call passes **`trust_env=False`** so no ambient proxy can route it off-box. To go to real services later you change the base URLs + real credentials — the code path is identical (that's the whole point of the emulators).

---

## 5. Testing (`fable/tests/`)

Implements `Fable_buttonsbebe:fable/docs/TESTING-STRATEGY.md`. The goal: **one command proves the whole help desk works AND that it can never send anything to a real customer/store/the internet.**

### 5.1 The pyramid & layout

```
tests/
  conftest.py              fixtures: server on sys.path, in-process emulators, tmp DB, httpx router
  pytest.ini               markers (e2e) + warning filters
  unit/        (8 files)   risk, risk-parity, MockBrain, AnthropicBrain, draft_cleaner, compat mappers, cursors, config
  integration/ (19 files)  intake, pipeline, actions, tickets API, chat, stats, golden set, safety invariants (×2),
                           shopify/redo/gorgias-compat contracts, gorgias emulator, migration, kb_search,
                           email adapter, route coverage, frontend, console static
  e2e/         (1 file)    test_live_stack.py — @pytest.mark.e2e, skipped unless FABLE_E2E=1
```

### 5.2 The in-process wiring (the clever part — read `conftest.py`)

Unit + integration run **without opening a single real socket**:
1. `fable/server` is put on `sys.path`, so tests `import main` / `from app import …` exactly like the server. `FABLE_DB` is pointed at a fresh tempfile under `/tmp` per test (WAL needs local disk).
2. Each emulator `app.py` is imported **in-process** under a unique module name (via `importlib`, to avoid clashing with the server's own `app` package) and wrapped in a Starlette `TestClient`.
3. **The httpx router** monkeypatches module-level `httpx.get`/`httpx.post` with a dispatcher that routes **by port** to the matching emulator `TestClient`. `env.kill(9601)` adds a port to a `down` set so the router raises `httpx.ConnectError` — exercising the "emulator down → still drafts" path; `env.revive(port)` restores it.
4. **Determinism:** the pipeline is normally driven by calling `pipeline._run_once(conn)` directly (`env.run_pipeline()`) instead of sleeping on the worker thread. Two tests (`test_pipeline.py`, `test_intake.py`) exercise the real thread with a bounded poll. `MockBrain` is deterministic; emulator state is reset per test.

### 5.3 What's covered — the safety-critical suites

- **Safety invariants** — `test_safety_invariants.py` proves the four rules: (1) drafting never populates the outbox; (2) the outbox fills **only after a human Send**; (3) refund/damaged/never-arrived/ALL-CAPS/`!!!` messages are flagged `sensitive` with a reason and the sensitive draft contains no "refund"/promise; (4) every mutation (intake, each pipeline step, patch, rewrite, note) appends an audit row. `test_safety_invariants_anthropic.py` re-runs the core invariants with the **real** `AnthropicBrain` wired in (via MockTransport), proving the model path is equally safe.
- **Golden set** — `test_golden_set.py` drives all **48** scenarios from the repo's `testing/scenarios.json` through the offline pipeline and honestly asserts only what's checkable without a live model: deterministically-sensitive scenarios are flagged and make no money promise; empty/bare-ack scenarios yield NO draft; every drafted reply is **clean** (re-running `clean_draft` is a no-op); and the stored `sensitive` flag equals the `risk.py` verdict for every scenario. It is careful to document (not fabricate) what only a real brain can satisfy.
- **Risk parity** — `test_risk_parity.py` feeds identical inputs through Fable's `risk.py` and the ported VPS classifier and proves they agree (feature "F1": the port didn't drift). This matters because risk classification is a **stub on `main`** but implemented here.
- **Contract tests** — `test_shopify_contract.py`, `test_redo_contract.py`, `test_gorgias_compat.py`, `test_gorgias_emulator.py` assert the emulators/compat layer emit the exact shapes from the research docs (token grants, envelopes, snake_case, money-as-strings, `Link` pagination, `X-Shopify-Shop-Api-Call-Limit`, leaky-bucket 429, bearer auth, Basic auth).
- **Route coverage** — `test_route_coverage.py` hits every route documented in `API-CONTRACT.md` and asserts a non-5xx status.

### 5.4 E2E (`FABLE_E2E=1`)

`e2e/test_live_stack.py` boots the **real four services** (real uvicorn, real HTTP on 127.0.0.1) via the `scripts/run-*.sh` launchers, runs the demo scenario (Emma's `#BB1015` draft must contain tracking `1Z999AA10123456784`), verifies the mailbox outbox after a human Send, and checks kill-emulator resilience. Torn down at the end.

### 5.5 Running the suite

```bash
./fable/scripts/test.sh              # unit + integration, with coverage (auto-installs pytest/httpx if missing)
FABLE_E2E=1 ./fable/scripts/test.sh  # also boots the real 4-service stack and runs e2e
```
`test.sh` cds to the repo root, puts coverage data on `/tmp`, runs `pytest fable/tests/unit fable/tests/integration --cov=fable/server/app`, and exits non-zero on any failure. Coverage targets (`TESTING-STRATEGY §3`): `server/app` ≥ 80% (risk/brains/actions ≥ 90%), emulators ≥ 70%, every documented route hit at least once.

> **Doc drift on the count:** the README says "182 automated checks" in one place and "186" in another, and the folder banner says "186". Treat the exact number as approximate (~180); the suite is what runs, not the prose.

---

## 6. How to run Fable locally

Everything is under `fable/`. Zero setup — all `.env.fable` defaults work. Requires Python 3.10+ and (auto-installed by the scripts) `fastapi`, `uvicorn`, `httpx`, `pydantic`, and for tests `pytest`/`pytest-cov`/`coverage`.

**One-command demo (boots everything + plays the story, leaves it running):**
```bash
cd fable
./scripts/demo.sh
# then open http://127.0.0.1:9600  (console)
#          http://127.0.0.1:9600/widget/demo-store.html  (chat widget demo)
```
`demo.sh` uses a fresh throwaway DB at `/tmp/fable-demo.db`, boots the stack via `run-all.sh`, then: emails "Where is my order #BB1015?" (asserts the tracking number appears in the draft), sends a chat "Do you ship to Canada?", sends a WhatsApp "damaged… refund!!" (asserts it's flagged sensitive), then exercises the console verbs (send Emma's reply → shows up in the mailbox outbox; note the chat; rewrite the WhatsApp draft), and finally lists all tickets via the Gorgias-compat API.

**Manual start / stop:**
```bash
./scripts/run-all.sh            # emulators (shopify/redo/mailbox) + server, then health-checks all four
# or individually:
./emulators/run-emulators.sh    # start shopify:9601 redo:9602 mailbox:9603
./scripts/run-server.sh         # start Fable server on :9600 (uvicorn via nohup)
# stop:
./scripts/stop-server.sh && ./emulators/stop-emulators.sh
```

**Seed a lived-in inbox** (real intake + action endpoints, ~18 tickets mixed sensitive/routine/sent/noted/closed/snoozed):
```bash
./scripts/seed-demo.sh            # seed on top of whatever's there
./scripts/seed-demo.sh --fresh    # wipe the DB first
```

**Test:** see §5.5.

**Migrate from a (real or emulated) Gorgias account into Fable** (read-only on Gorgias; writes only local rows; idempotent):
```bash
./scripts/migrate-from-gorgias.sh \
    --base-url https://YOURSTORE.gorgias.com \
    --email you@yourstore.com \
    --api-key YOUR_GORGIAS_API_KEY \
    [--dry-run]
```

**Switch the brain / point at real services** (`.env.fable` or real env vars):
- `FABLE_BRAIN=anthropic` + `FABLE_ANTHROPIC_API_KEY=…` → real Claude drafting (falls back to mock if the key is missing).
- Change `SHOPIFY_BASE`/`REDO_BASE` + real credentials → hit real Shopify/Redo (same code path).
- `FABLE_EMAIL_TRANSPORT=imap` + IMAP/SMTP vars → use the real-email skeleton in `channels_email.py`.

### 6.1 `.env.fable` — variable NAMES only (no values printed)

`FABLE_DB`, `FABLE_BRAIN`, `SHOPIFY_BASE`, `SHOPIFY_SHOP`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`, `SHOPIFY_API_VERSION`, `REDO_BASE`, `REDO_API_KEY`, `REDO_STORE_ID`, `MAILBOX_BASE`, `SUPPORT_EMAIL`, `FABLE_HOST`, `FABLE_PORT`.

> These are all **local test placeholders** (e.g. `test-client-id`, `test-redo-key`) — the committed `.env.fable` contains **no real secrets**. Real credentials would be injected via real env vars (which override the file) at cutover. `config.py` resolution order is: real env var > `.env.fable` > built-in contract default. Additional vars read elsewhere but not in the file (they have code defaults): `FABLE_ANTHROPIC_API_KEY|MODEL|BASE|MAX_TOKENS`, `FABLE_KB_DIR`, `FABLE_EMAIL_TRANSPORT`, `IMAP_HOST/PORT`, `SMTP_HOST/PORT`, `EMAIL_USER/PASSWORD`.

---

## 7. File-by-file map of `fable/`

Line counts (`git show … | wc -l`) are approximate size hints. Seed JSON and logs are runtime data, not logic.

### Docs & top-level
| Path (`Fable_buttonsbebe:fable/…`) | Lines | What it does |
|---|---|---|
| `README.md` | 85 | Plain-English overview, safety rules, folder map, "swap fake for real" notes |
| `.env.fable` | 15 | Local config (test placeholders; no real secrets) |
| `docs/API-CONTRACT.md` | 143 | The blueprint Wave-1 agents built to: ports, native API, pipeline, brain interface, Gorgias-compat, emulator contracts, demo scenario |
| `docs/SPRINT-PLAN.md` | 167 | Sprint 1 plan, architecture, backlog (P0/P1/P2), risks, Sprint-2 preview |
| `docs/TESTING-STRATEGY.md` | 94 | The four safety invariants, test pyramid, coverage targets, what's skipped |
| `docs/RESEARCH-gorgias-api.md` | 90 | Haiku research: Gorgias data model/endpoints/webhooks; the 5 reads to emulate |
| `docs/RESEARCH-shopify-api.md` | 74 | Haiku research: Shopify OAuth/REST/GraphQL/rate-limits/webhooks to emulate |

### Server core — `server/app/`
| Path | Lines | What it does |
|---|---|---|
| `../main.py` | 250 | FastAPI app factory; mounts native + Gorgias-compat APIs + console; starts pipeline thread |
| `config.py` | 90 | Parse `.env.fable`, env override, defaults; `db_path()`; convenience accessors |
| `db.py` | 173 | SQLite schema (10 tables, WAL), `init_db()`, additive `external_id` migrations |
| `models.py` | 58 | Pydantic request bodies (intake, actions, Gorgias write) |
| `intake.py` | 231 | Email/chat/whatsapp → find-or-create customer+ticket (7-day reuse) → enqueue job |
| `pipeline.py` | 219 | Worker thread: context→risk→gate→KB→brain→store draft; per-step audit; fault-tolerant |
| `context.py` | 208 | Shopify token mint+cache, orders-by-email, Redo returns; trims; degrades to None |
| `risk.py` | 60 | **Deterministic** risk classifier (trigger words + `!!!` + ALL-CAPS ≥6) |
| `draft_cleaner.py` | 246 | `should_draft()` gate + `clean_draft()`; **shared with the VPS** via vps-patches |
| `kb_search.py` | 221 | Keyword search over `kb/{policies,faq,intents}` markdown; in-memory cache |
| `actions.py` | 180 | `send`/`note`/`rewrite`; per-channel transport; 409 on closed, 502 on transport fail; audits |
| `tickets.py` | 257 | Ticket read/list/patch, filters+counts, JSON serializers (summary/full/message/draft) |
| `stats.py` | 65 | Dashboard metrics (tickets today, open, avg first response, draft acceptance %, by channel) |
| `audit.py` | 43 | `record()` / `list_recent()` / `for_ticket()` audit helpers |
| `gorgias_compat.py` | 191 | `/api/*` Gorgias-shaped reads + internal-note write (VPS-tool compatibility) |
| `migration.py` | 295 | Gorgias→Fable importer: cursor-walk, idempotent via `external_id`, `--dry-run`, CLI |
| `channels_email.py` | 296 | `EmailTransport` interface: `MailboxEmulatorTransport` (today) + `ImapSmtpTransport` (Sprint-3 skeleton, lazy) |
| `__init__.py` | 1 | package marker |

### The brain — `server/app/brains/`
| Path | Lines | What it does |
|---|---|---|
| `base.py` | 43 | `DraftContext`, `DraftResult`, `Brain` Protocol (`draft`/`rewrite`) |
| `__init__.py` | 33 | `get_brain()` factory: `mock|anthropic|hermes`, anthropic→mock fallback |
| `mock.py` | 185 | `MockBrain` — deterministic templates (sensitive / order-status / shipping / fallback / rewrite) |
| `anthropic.py` | 314 | `AnthropicBrain` — **real** Claude Messages API adapter, grounded, offline-testable, safe-degrading |
| `anthropic_stub.py` | 25 | **Dead** older stub (not imported by the factory — candidate for deletion) |
| `hermes_stub.py` | 25 | `HermesBrain` — genuine stub (`NotImplementedError`) |

### Emulators — `server/../emulators/`
| Path | Lines | What it does |
|---|---|---|
| `shopify/app.py` | 764 | Shopify Admin API clone: OAuth, REST, GraphQL+Bulk, leaky-bucket, fault injection, `/emulator/*` |
| `shopify/seed/generate_seed.py` | 526 | Deterministic seed generator (30 products / 25 customers / 40 orders) |
| `shopify/seed/{products,customers,orders}.json` | 5101 / 1501 / 9295 | Generated seed data |
| `redo/app.py` | 126 | Redo returns API clone (bearer auth, 8 seeded returns) |
| `mailbox/app.py` | 121 | Inbound `simulate/incoming`→intake; outbound `/send`→captured outbox |
| `gorgias/app.py` | 635 | Read-only Gorgias API clone (15 tickets/10 customers) — for the migration importer |
| `run-emulators.sh` / `stop-emulators.sh` | 35 / 18 | Start/stop shopify+redo+mailbox via nohup + PID files |

### Scripts — `scripts/`
| Path | Lines | What it does |
|---|---|---|
| `demo.sh` | 118 | Boot stack + play the API-CONTRACT §7 scenario, leave running |
| `run-all.sh` | 32 | Boot emulators (if present) + server, health-check all four |
| `run-server.sh` / `stop-server.sh` | 30 / 20 | Start/stop the Fable server (uvicorn via nohup + PID) |
| `seed-demo.sh` / `seed_demo.py` | 116 / 409 | Seed a lived-in inbox via the real intake+action endpoints |
| `test.sh` | 56 | One-command test runner (unit+integration, optional E2E) |
| `migrate-from-gorgias.sh` | 47 | Friendly front door to `python -m app.migration` |

### Console & widget
| Path | Lines | What it does |
|---|---|---|
| `console/index.html` | 14 | Console shell (loads `app.js`/`style.css`) |
| `console/app.js` | 903 | Vanilla-JS SPA: Inbox / Ticket / Customers / Stats / Settings; talks to `/fable/api/*`; escapes all user text |
| `console/style.css` | 350 | Purple design-system styling (matches the old dashboard) |
| `console/widget/{widget.js,demo-store.html}` | 223 / 114 | Chat widget served at `/widget/*` (identical copy of the top-level `widget/`) |
| `widget/{widget.js,demo-store.html}` | 223 / 114 | Source copy of the embeddable chat bubble + demo store page |

### Tests — `tests/`
| Path | Lines | What it does |
|---|---|---|
| `README.md` / `pytest.ini` / `conftest.py` | 69 / 11 / 271 | Test docs; markers; the in-process wiring (sys.path, in-proc emulators, tmp DB, httpx router) |
| `unit/test_risk.py` / `test_risk_parity.py` | 90 / 184 | Risk classifier behaviour; parity vs the VPS classifier port |
| `unit/test_mock_brain.py` / `test_anthropic_brain.py` | 163 / 214 | MockBrain determinism+safety; AnthropicBrain via `httpx.MockTransport` (offline) |
| `unit/test_draft_cleaner.py` | 304 | Cleaner seeded with **real** live-QA model outputs |
| `unit/test_compat_mappers.py` / `test_cursors.py` / `test_config.py` | 113 / 79 / 72 | Gorgias field mappers; pagination cursors; config parsing |
| `integration/test_intake.py` / `test_pipeline.py` / `test_actions.py` | 131 / 104 / 130 | Intake→ticket; pipeline→draft; send/note/rewrite |
| `integration/test_tickets_api.py` / `test_chat.py` / `test_stats.py` | 116 / 40 / 39 | Native tickets API; chat long-poll; stats |
| `integration/test_golden_set.py` | 177 | 48-scenario golden harness (flags, no-draft, clean, no promises) |
| `integration/test_safety_invariants.py` / `_anthropic.py` | 99 / 162 | The four invariants (mock brain, then real brain) |
| `integration/test_shopify_contract.py` / `test_redo_contract.py` | 260 / 71 | Emulator contract compliance |
| `integration/test_gorgias_compat.py` / `test_gorgias_emulator.py` / `test_migration.py` | 109 / 205 / 216 | Compat reads/writes; Gorgias emulator; migration importer |
| `integration/test_kb_search.py` / `test_email_adapter.py` | 100 / 126 | KB keyword search; email transport interface |
| `integration/test_route_coverage.py` / `test_frontend.py` / `test_console_static.py` | 76 / 41 / 79 | Every documented route hit; light console checks; console source guards |
| `e2e/test_live_stack.py` | 222 | Full live-stack scenario (gated by `FABLE_E2E=1`) |

### Runtime artifacts committed by mistake (should be git-ignored)
| Path | Lines | What it is |
|---|---|---|
| `logs/server.log` | 125 | A captured uvicorn log — runtime output, not source |
| `logs/server.pid` | 1 | A stale PID file — runtime state, not source |

---

## 8. Status & relationship to the roadmap

### 8.1 LIVE / DONE / STUB inside Fable

**Working & tested (offline, deterministic):**
- Intake (email/chat/whatsapp) → queue → pipeline → draft → console verbs (send/note/rewrite).
- Deterministic risk classifier (`risk.py`) + `should_draft` gate + `clean_draft` cleaner.
- Local keyword KB grounding over the repo's `kb/` content.
- All four emulators + the four safety invariants + the 48-scenario golden set.
- `MockBrain` (default) and the **real `AnthropicBrain`** (offline-testable; live only with an API key).
- Gorgias-compat read/write layer; Gorgias→Fable migration importer (dry-run + idempotent).
- Email transport interface with a real IMAP/SMTP skeleton (constructing it opens no socket).

**Stub / not yet implemented:**
- `HermesBrain` (`hermes_stub.py`) — raises `NotImplementedError`.
- `anthropic_stub.py` — dead leftover (the real adapter superseded it).
- Real WhatsApp transport (would reuse the VPS `whatsapp-connect` Baileys bridge).
- Macros/rules/CSAT/multi-agent (P1/P2 parity features listed but not built).

### 8.2 How Fable maps onto the phased plan

- **Phase 2 (hardening, classifier/risk, draft cleaning, grounding, dashboards):** Fable **is** where much of Phase 2 was prototyped. Its `risk.py` is the deterministic classifier that is a **stub on `main`** (`classifier.py` returns NORMAL for everything) — and `test_risk_parity.py` proves Fable's version matches the ported VPS classifier. Its `draft_cleaner.py` (grounding-safe draft cleaning) is **already shipped back to the VPS** via `deploy/vps-patches/`. Its `kb_search.py` is the keyword grounding step; its `stats.py` + console Stats view are the dashboards. So the new team can treat Fable as the reference implementation for these Phase-2 pieces even if Fable itself is shelved.
- **Phase 3 (actions, multi-channel):** Fable's `actions.py` (send/note/rewrite with per-channel transports and audit) and its three intake channels are the multi-channel model. `channels_email.py` is the explicit seam for the Phase-3 email cutover (swap `MailboxEmulatorTransport` for `ImapSmtpTransport` via `FABLE_EMAIL_TRANSPORT`). The Gorgias→Fable migration importer is the Phase-3 data-migration tool, already built and tested against an emulator.

### 8.3 The decision the new team must make

Fable is a **parallel track**, not part of the running system. It has quietly advanced past its own "Sprint 1 MVP" charter into several Sprint-2 items (real brain, migration, KB search, transport skeleton). The team must decide, explicitly, one of:
1. **Continue Fable** toward a real Gorgias replacement (finish Hermes brain, real email/WhatsApp transports, parallel-run pilot on a fresh VPS, then cutover) — the SPRINT-PLAN §10 path; or
2. **Harvest and fold** the strong, tested pieces into `main`/the VPS (the deterministic classifier, the draft cleaner already crossing over, the KB grounding, the safety-invariant test methodology) and shelve the rest; or
3. **Shelve** Fable and keep the Gorgias overlay.

Nothing about Fable forces a choice — `main` is untouched and Fable runs nowhere — but leaving it undecided means two divergent architectures in one repo.

---

## 9. Findings, discrepancies & gotchas (for the next agent)

1. **Fable is NOT deployed.** `main` (Hermes overlay on Gorgias) is the only live system (docs `02`–`05`). Everything in `fable/` runs on localhost only, and only if you start it.
2. **Read it from the branch, don't check it out.** `git show Fable_buttonsbebe:fable/<path>`. The working tree is on `main`, where `fable/` does not exist.
3. **The Anthropic brain is real, but the API-CONTRACT and one stub file say otherwise.** `API-CONTRACT.md §2` still describes anthropic/hermes as "stub files raising NotImplementedError," and `brains/anthropic_stub.py` is exactly that — but `brains/__init__.py` imports the **implemented** `brains/anthropic.py`. Trust the code: `anthropic` works (offline-testable), `hermes` is the only real stub. Delete `anthropic_stub.py` to remove the confusion.
4. **A fourth emulator (Gorgias, :9604) exists but is off the beaten path.** It's real and tested, but it's absent from the README port table and `run-emulators.sh` does not start it — it's there solely to test the migration importer in-process. Don't assume the runtime stack includes it.
5. **The two widget copies are identical.** `fable/widget/` and `fable/console/widget/` are byte-for-byte the same; the server serves the `console/widget/` copy at `/widget/*` (because it mounts the `console/` dir at `/`). Keep them in sync or dedupe.
6. **Committed runtime artifacts.** `fable/logs/server.log` and `fable/logs/server.pid` are checked into the branch — runtime output that should be git-ignored, not source.
7. **The "number of tests" wobbles in the prose** (README says 182 and 186; folder banner says 186). The suite that runs is authoritative (~180 checks across unit+integration; E2E gated).
8. **`draft_cleaner.py` is a shared file across branches.** Its docstring says a copy ships to the live processor via `deploy/vps-patches/`. If you edit it in Fable, check whether the VPS copy needs the same change (and vice-versa).
9. **`.env.fable` has no real secrets** — all values are local test placeholders. Real credentials are meant to come from real env vars (which override the file). Never commit real keys here.
10. **Offline-ness is enforced in code, not just by convention:** every outbound `httpx` call uses `trust_env=False` (so no ambient proxy can exfiltrate), and `test_safety_invariants.py::test_no_server_config_url_leaves_localhost` asserts all base URLs start with `http://127.0.0.1`. If you point Fable at real services, you are deliberately turning this off.

---

*End of doc 07. Cross-references: `02-live-architecture.md` (the LIVE system Fable contrasts with), `03-live-components-reference.md`, `04-knowledge-base-and-learning.md` (the `kb/` content Fable's `kb_search.py` reads), `05-services-deploy-and-secrets.md`.*
