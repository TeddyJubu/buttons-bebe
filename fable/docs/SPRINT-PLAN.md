# Sprint Plan: Fable — Gorgias Replacement for Buttons Bebe

**Sprint:** Fable Sprint 1 ("Local MVP") | **Dates:** 2026-07-10 → 2026-07-17 | **Team:** 1 human (Tony) + Claude agent waves (Haiku research, Opus build)
**Branch:** `Fable_buttonsbebe` (main untouched; live VPS + real Gorgias never touched)

**Sprint Goal:** A complete help desk running on Tony's machine that can do everything Buttons Bebe uses Gorgias for — receive customer messages (email / chat / WhatsApp), show them in a friendly inbox, AI-draft replies for human approval, and pull real-looking order data from a local Shopify emulator — all provable by an automated test suite.

---

## 1. The big picture (plain English)

Today, Gorgias is the "post office" — customer messages arrive there, and our AI reads them
through Gorgias's window. Fable flips this: **Fable becomes the post office.** Messages arrive
directly in Fable, the AI drafts inside Fable, and the human clicks Send inside Fable.
Gorgias is no longer needed (we keep a Gorgias-compatible API layer so existing tools and a
one-time migration importer keep working).

Because we can't test against the real Shopify store, we build a **Shopify emulator** — a small
local server that speaks the exact same API language as real Shopify (same URLs, same JSON,
same auth handshake, even the same rate-limit errors). Point Fable at the emulator during
development; point it at real Shopify on launch day by changing one line in a config file.

## 2. Architecture (what we're building where)

```
fable/
├── server/            The help desk itself (FastAPI, port 9600, SQLite database)
│   ├── Tickets, messages, customers, tags, macros, rules, views, search, stats
│   ├── Channels: email-in/out, chat widget, WhatsApp (emulated locally)
│   ├── Brain: pluggable AI drafting — MockBrain default, Claude/Hermes plug in later
│   └── Gorgias-compat API (/api/tickets…) + migration importer
├── console/           The screens humans use (same design system as current dashboard)
├── widget/            Chat bubble for the store website + a demo store page
├── emulators/
│   ├── shopify/       Exact Shopify Admin API clone (port 9601): OAuth client-credentials,
│   │                  REST orders/customers/products, GraphQL, rate limits, webhooks
│   ├── redo/          Returns API clone (port 9602)
│   └── mailbox/       Email emulator (port 9603): fake customer inboxes; captures outbound
│                      mail so nothing real is ever sent
├── tests/             Automated test suite (unit + API + end-to-end)
├── scripts/           run-all.sh, seed-data, demo scenario player
└── docs/              This plan, research, testing strategy, design decisions
```

**Safety model carries over unchanged:** AI only ever drafts; a human clicks Send; sensitive
tickets (refunds, chargebacks, damaged/missing, angry) are flagged with a warning; every
action is logged.

## 3. Capacity

| Worker | Role | Availability | Notes |
|--------|------|--------------|-------|
| Tony | Product owner / reviewer | Review + decisions | Non-technical: all docs & UI in plain language |
| Haiku agents | Docs research | Done (2 reports in `fable/docs/`) | Gorgias + Shopify API specs |
| Opus agent A | Emulators (Shopify, Redo, mailbox) | Wave 1 | Independent of B |
| Opus agent B | Help desk server core | Wave 1 | Independent of A |
| Opus agent C | Console UI + chat widget | Wave 2 | Needs B's API contract |
| Opus agent D | Test suite | Wave 3 | Needs A+B+C |
| Claude (orchestrator) | Integration, verification, fixes | Continuous | Runs everything end-to-end |

Planned to ~75% — buffer for integration surprises between waves.

## 4. Sprint backlog

### P0 — must ship this sprint (the MVP)

| # | Item | What "done" means | Est | Owner |
|---|------|-------------------|-----|-------|
| 1 | Branch + skeleton | `Fable_buttonsbebe` branch, `fable/` structure, run scripts | S | done |
| 2 | Shopify emulator | Client-credentials token grant; REST `orders`/`customers`/`products` (+search, pagination via Link header); GraphQL products+orders; 401/404/429+`Retry-After` and `X-Shopify-Shop-Api-Call-Limit`; seeded with ~30 Buttons-Bebe-like products, ~25 customers, ~40 orders (mixed statuses: fulfilled w/ tracking, unfulfilled, refunded, partially shipped) | L | Opus A |
| 3 | Redo emulator | Returns list / by-order / by-id, Bearer auth, seeded returns tied to emulator orders | S | Opus A |
| 4 | Mailbox emulator | Simulate inbound customer email (`POST /simulate/incoming`); capture ALL outbound mail for inspection (`GET /outbox`) — guarantees nothing real is ever sent | S | Opus A |
| 5 | Ticket engine | SQLite schema Gorgias-shaped (tickets, messages incl. internal notes, customers, tags); full CRUD; open/closed/snoozed; search | L | Opus B |
| 6 | Channel intake | Email (from mailbox emulator), chat widget messages, WhatsApp (emulated endpoint) all become tickets in ONE inbox, tagged by channel | M | Opus B |
| 7 | AI pipeline | On new customer message: fetch order context from Shopify emulator + returns from Redo emulator → classify risk (deterministic rules + brain) → MockBrain drafts reply → draft attached to ticket for approval. Brain is a plug: `FABLE_BRAIN=mock|anthropic|hermes` | L | Opus B |
| 8 | Human approval flow | Console shows draft → human can Edit / Send / Save-as-note / Request-rewrite; Send requires confirm click; sensitive tickets show warning banner; all actions logged to an audit trail | M | Opus B+C |
| 9 | Console UI | Inbox (filters: channel, status, sensitive), ticket view (conversation + order sidebar + draft box), customers, settings — using DESIGN-SYSTEM.md tokens exactly; plain-language labels for non-tech users | L | Opus C |
| 10 | Chat widget | Embeddable JS bubble + demo store page; messages flow to Fable inbox; replies flow back | M | Opus C |
| 11 | Gorgias-compat reads | `GET /api/tickets`, `/tickets/{id}`, `/tickets/{id}/messages`, `/customers/{id}`, `/customers?email=` on Fable — so `tools/gorgias_mcp.py` works against Fable by changing only its base URL | S | Opus B |
| 12 | Test suite | Unit + API-contract + end-to-end scenario tests, one command (`./scripts/test.sh`), all green | L | Opus D |
| 13 | Run-locally proof | `./scripts/run-all.sh` starts everything; scripted demo pushes email+chat+WhatsApp messages through to approved replies | M | Orchestrator |

### P1 — should ship (this sprint if room, else Sprint 2)

| # | Item | Est |
|---|------|-----|
| 14 | Macros (canned replies with `{{customer.firstname}}` variables) | M |
| 15 | Rules (auto-tag/auto-close on conditions, e.g. auto-tag "where is my order") | M |
| 16 | Stats page (tickets/day, first-response time, resolution time, AI-draft acceptance rate) | M |
| 17 | Learning loop port (approved/edited replies saved as lessons, like the VPS `learned/` flow) | M |
| 18 | KB search wired in (reuse `kb/` content; simple keyword search locally, LanceDB later) | M |

### P2 — stretch / Sprint 2+

| # | Item |
|---|------|
| 19 | Gorgias migration importer (paginated export → Fable, `external_id` preserved) — tested against a **Gorgias emulator**, never the real account |
| 20 | Real email connection plan (IMAP/SMTP or Gmail API) — config swap, same code path as mailbox emulator |
| 21 | Real WhatsApp via existing `whatsapp-connect` (Baileys) service |
| 22 | Real brain: Claude API adapter + Hermes adapter (same interface as MockBrain) |
| 23 | CSAT surveys, satisfaction tracking |
| 24 | Multi-agent accounts, assignment, teams |
| 25 | Deployment plan to a fresh VPS (NOT the live one) + parallel-run pilot next to Gorgias |

## 5. The Shopify emulator — contract (exact-API promise)

The emulator is only useful if it's indistinguishable from real Shopify for our code paths.
Contract (full field lists in `RESEARCH-shopify-api.md`):

1. `POST /admin/oauth/access_token` with `grant_type=client_credentials` → 24h token; wrong
   secret → Shopify's exact 401 body.
2. `X-Shopify-Access-Token` required on every Admin call; missing/expired → exact 401.
3. REST: `GET /admin/api/{ver}/orders.json` (filters: `email`, `name`, `status`,
   `created_at_min`, `limit`, `page_info` + `Link` header), `orders/{id}.json`,
   `customers.json` + `customers/search.json`, `products.json` — snake_case, money as strings,
   `{"order": {...}}` envelopes.
4. GraphQL: `POST /admin/api/{ver}/graphql.json` — products + orders queries with
   `pageInfo{hasNextPage,endCursor}` and cost `extensions` (the KB product sync must run
   against it unmodified).
5. Failure modes on demand: a test header (`X-Emulator-Scenario: rate-limit|timeout|500`)
   triggers 429+`Retry-After`, hangs, or 500s — so we can test resilience.
6. Admin panel endpoints (`/emulator/*`) to add orders/edit statuses mid-test.

## 6. UX principles (non-negotiable)

- Plain words: "Waiting for you" not "Pending triage"; "Send to customer" not "Dispatch".
- One inbox, three coloured channel chips (Email ✉, Chat 💬, WhatsApp 🟢).
- The AI draft is a card with three big buttons: **Send** (confirm step), **Edit**, **Ask AI to rewrite**.
- Sensitive tickets: amber banner "Take a careful look — this one mentions a refund."
- Everything reachable in ≤2 clicks from the inbox. Keyboard optional, never required.
- Same design tokens as `dashboard/DESIGN-SYSTEM.md` (purple `--acc` family, gray ramp,
  semantic green/amber/red chips).

## 7. Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Emulator drifts from real Shopify | Bugs appear only in production | Contract tests generated from the research doc field lists; GraphQL sync script must pass unmodified |
| Scope explosion (Gorgias has 100s of features) | Sprint fails | Parity checklist ranked; P0 = only what Buttons Bebe actually uses today |
| Mock brain hides real-AI problems | Drafts look great in tests, poor live | Brain interface identical for mock/real; Sprint 2 runs golden-set evals with a real model |
| Real email/WhatsApp integration harder than emulated | Cutover slips | Channel code paths identical; only the transport adapter swaps |
| Wave agents produce incompatible pieces | Integration hell | API contract doc (`docs/API-CONTRACT.md`) written BEFORE Wave 1; agents build to it |
| Long-running services vs. session limits | Can't demo | Services start via nohup scripts; scripted demo + tests prove flow without babysitting |

## 8. Definition of Done (per item)

- Code merged on `Fable_buttonsbebe`, nothing on `main`.
- Tests pass via `./scripts/test.sh`.
- No real network calls: test suite asserts zero requests leave localhost.
- Plain-language README section updated.
- Orchestrator ran it end-to-end at least once.

## 9. Key dates

| Date | Event |
|------|-------|
| 2026-07-10 | Sprint start; Waves 1–3 execute (this session) |
| 2026-07-10 | End-to-end demo: email+chat+WhatsApp → AI draft → approved reply |
| 2026-07-13 | Mid-sprint: Tony reviews UI + flows, feedback round |
| 2026-07-17 | Sprint end: P0 all green, P1 triaged, Sprint 2 (real integrations + migration) planned |

## 10. Sprint 2 preview (the road to actually replacing Gorgias)

1. Real email in/out (IMAP/SMTP), real WhatsApp (existing Baileys bridge), real brain (Claude or Hermes).
2. Gorgias migration importer + full export dry-run (against emulator first).
3. Parallel run: Fable and Gorgias both receive traffic on a fresh VPS; compare for 2 weeks.
4. Cutover checklist: DNS/mail routing switch, Gorgias account archived — only after Chaim signs off.
