# Fable Testing Strategy

> Plain-English goal: prove, with one command, that the whole help desk works — and that it can
> NEVER send anything to a real customer, a real store, or the real internet by accident.

## 0. The four safety invariants (tested at every layer)

1. **No draft is ever auto-sent.** Sending requires the explicit send action; the pipeline only creates drafts.
2. **Nothing leaves localhost.** All outbound HTTP targets 127.0.0.1; outbound email exists only in the mailbox emulator's outbox.
3. **Sensitive tickets are always flagged** (refund/chargeback/damaged/angry → `sensitive: true` + reason) and their drafts make no promises.
4. **Everything is audited.** Every mutation appends an audit row.

## 1. The pyramid

```
        /   E2E (live stack)   \    ~10 scenario tests — 4 real services, real HTTP
       /  Integration (in-proc)  \  ~60 tests — FastAPI TestClient, real SQLite, emulators in-process
      /        Unit tests         \ ~80 tests — risk.py, MockBrain, config, cursors, compat mappers
```

**In-process first.** Sandbox/CI learnings: run apps via Starlette `TestClient` (no sockets, no
teardown flakes) and SQLite on local disk (`/tmp`) — the live-stack layer is the only one that
boots real uvicorn processes.

## 2. What to test, by component

### 2.1 Unit (fast, no I/O)
| Area | Cases |
|---|---|
| `risk.py` | each trigger word; `!!!`; ALL-CAPS ≥6 words; clean message → low; mixed case; word-boundary safety ("refundable"?) — document chosen behavior |
| `MockBrain` | deterministic: same ctx → same draft; order-status ctx → tracking number appears; no orders → asks for order number; sensitive → no "refund"/"will"-promise wording; always signs off; rewrite transforms (shorter/friendlier/other) |
| Gorgias-compat mappers | Fable→Gorgias field mapping (`created_datetime`, `via`, `public`, internal-note channel), envelope shape |
| Pagination cursors | opaque page_info encode/decode, limit clamps |
| Config | env parsing, defaults, missing file |

### 2.2 Integration (TestClient, real DB on /tmp, emulator apps in-process where needed)
| Area | Cases |
|---|---|
| Intake | email/chat/whatsapp → 202 + ticket; 7-day open-ticket reuse (same customer+channel appends, different channel makes new); customer find-or-create; malformed body → 422 |
| Pipeline | job → draft within poll interval; emulator down → draft still produced, `order_context` null; supersede older proposed drafts |
| Actions | send (email→outbox capture; chat→long-poll; whatsapp→outbox table); send on closed → 409; transport failure → 502 + draft stays proposed; note; rewrite; audit rows for each |
| Tickets API | filters (status/channel/sensitive/q), counts, PATCH status/tags/snooze |
| Chat long-poll | `after` cursor semantics |
| Stats | counts move after activity |
| **Shopify emulator contract** | token grant (happy/bad-secret/missing/expired); orders by email/name/status; exact envelopes + snake_case + money-as-strings + `admin_graphql_api_id`; `Link` pagination follows; `X-Shopify-Shop-Api-Call-Limit` present; leaky bucket 429 + `Retry-After`; GraphQL products query from `sync_products.py` passes; scenario headers (500/slow/rate-limit) |
| Redo emulator contract | bearer auth, by order_name, 401s |
| Gorgias-compat | 5 reads used by `tools/gorgias_mcp.py` return Gorgias shapes; POST internal note |
| **Safety invariants** | outbox empty until a human send action; every mutation audited; sensitive flags set |

### 2.3 E2E (live stack: run-all.sh, real uvicorn, real HTTP)
The **demo scenario** from API-CONTRACT §7, asserted end to end:
1. Boot 4 services → all healths green.
2. Mailbox `simulate/incoming` "Where is my order #BB1015?" (Emma Wilson) → ticket; draft contains tracking `1Z999AA10123456784` (proves real Shopify-emulator context flowed in).
3. Chat "Do you ship to Canada?" → draft.
4. WhatsApp "damaged… refund!!" → sensitive ticket + careful draft.
5. Console verbs: send → mailbox outbox contains exactly one email to Emma; note; rewrite.
6. Gorgias-compat lists all three tickets.
7. Resilience: kill Shopify emulator mid-run → new intake still drafts (context degraded); restart → context returns.
8. Rate-limit storm (`X-Emulator-Scenario`) → pipeline retries/degrades, never crashes a ticket.

### 2.4 Frontend (light, deliberate)
Console is static HTML+JS. Test: server serves `/` (200, contains app root); JS syntax check
(`node --check`); the API endpoints the console calls are all covered above. Visual/interaction
testing is manual for MVP (Tony reviews), Playwright is a Sprint-2 item.

## 3. Coverage targets
- `server/app/` line coverage ≥ 80% (risk.py + brains + actions ≥ 90% — they guard safety).
- Emulator code ≥ 70% (contract endpoints 100% exercised).
- Every route in API-CONTRACT.md hit by at least one test (route-coverage checklist test).

## 4. Tooling & layout
```
fable/tests/
  conftest.py          # fixtures: tmp DB, in-process apps (fable/shopify/redo/mailbox), seeded state
  unit/    test_risk.py test_mock_brain.py test_compat_mappers.py test_cursors.py test_config.py
  integration/  test_intake.py test_pipeline.py test_actions.py test_tickets_api.py
                test_shopify_contract.py test_redo_contract.py test_gorgias_compat.py
                test_chat.py test_stats.py test_safety_invariants.py
  e2e/     test_live_stack.py   # marked @pytest.mark.e2e, skipped unless FABLE_E2E=1
fable/scripts/test.sh  # pip deps → pytest unit+integration (with coverage) → optionally E2E
```
- pytest + httpx/TestClient + coverage. No mocking libraries needed — the emulators ARE the mocks.
- Determinism: MockBrain deterministic, seed data fixed, `POST /emulator/reset` between tests.
- One command: `./scripts/test.sh` (exit 0 = ship).

## 5. Explicitly skipped (and why)
- Real Shopify/Gorgias/WhatsApp calls — forbidden by project rules; emulators stand in.
- Real-LLM draft quality — Sprint 2 golden-set evals when a real brain is plugged in.
- Load testing — 2,000 tickets/month ≈ 3/hour; a `test_bulk_50_intakes` sanity test suffices.
- Browser automation — manual UI review this sprint; Playwright next.

## 6. Gaps this closes vs. today
The VPS system's tests (`testing/`) are live-run judgment docs — human-graded, not CI-able.
Fable gets a deterministic, one-command, no-network suite that can run on every change.
