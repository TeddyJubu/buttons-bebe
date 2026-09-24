# Buttons Bebe Agents — v3 Comparison (with connected Shopify context)

**Date:** 2026-06-29 · **Model:** deepseek-v4-flash:cloud (both) · **Queries:** 30 new (20 order-specific, 10 policy)

## Method
- 30 brand-new queries (distinct from v1/v2), identical inputs to both systems (`/root/qa_v3/fixtures_v3.py`).
- **Shopify context:** real Shopify still 403s (API scopes not granted), so both systems were run through their **real** integration code (old: `shopify_lookup`→shared module; Teddy: `skills.lookup_order`→shared module) with the shared module's network calls monkeypatched to a fixed mock order dataset. So this tests the *plumbing + how each system uses order facts*, not live Shopify data.
- Nothing sent to real Gorgias/Telegram. Harnesses: `gorgias-webhook/tests/run_old_system_queries_v3.py`, `teddy/tests/run_queries_v3.py`.

## Headline numbers

| Metric | Old (gorgias-webhook) | Teddy |
|---|---|---|
| Order context used (of 20 order queries) | 20/20 | 20/20 |
| KB confidence = high/HIGH | 30/30 | 29/30 (1 MEDIUM) |
| IMMEDIATE | 4 (Q05,Q08,Q17,Q25) | 2 (Q05,Q11) |
| HIGH | 9 | 6 |
| NORMAL/LOW | 17 / 0 | — / 22 |
| Hard escalations (no customer draft) | 0 | 6 (Q05,Q08,Q11,Q15,Q20,Q24*) |

\* Teddy escalations partly caused by KB-retrieval misses (see findings).

## Per-query results

| ID | Scenario | OLD urg | OLD KB | Ord | TEDDY prio | TEDDY KB | TEDDY action |
|---|---|---|---|---|---|---|---|
| Q01 | Where is my order (unfulfilled) | HIGH | high | Y | LOW | HIGH | draft |
| Q02 | Has my order shipped (fulfilled+tracking) | HIGH | high | Y | LOW | HIGH | draft |
| Q03 | Tracking number request | HIGH | high | Y | LOW | HIGH | draft |
| Q04 | Is it delivered yet | NORMAL | high | Y | LOW | HIGH | draft |
| Q05 | Cancel before ship (unfulfilled) | IMMEDIATE | high | Y | IMMEDIATE | HIGH | alert-only |
| Q06 | Change size before ship | NORMAL | high | Y | LOW | HIGH | draft |
| Q07 | Return eligibility (shipped order) | HIGH | high | Y | LOW | HIGH | draft |
| Q08 | Why was I refunded | IMMEDIATE | high | Y | HIGH | HIGH | draft |
| Q09 | Did both my orders ship (multi-order) | HIGH | high | Y | LOW | HIGH | no-draft/ESC |
| Q10 | Delivered but not received | HIGH | high | Y | HIGH | HIGH | draft |
| Q11 | Change shipping address | NORMAL | high | Y | IMMEDIATE | HIGH | alert-only |
| Q12 | Ordered wrong item, swap | HIGH | high | Y | LOW | HIGH | draft |
| Q13 | Add item to existing order | NORMAL | high | Y | HIGH | HIGH | no-draft/ESC |
| Q14 | Order seems stuck (older unfulfilled) | NORMAL | high | Y | LOW | HIGH | draft |
| Q15 | Partial shipment — missing item | NORMAL | high | Y | HIGH | HIGH | draft |
| Q16 | What did I order again | NORMAL | high | Y | LOW | HIGH | draft |
| Q17 | Refund status (returned item) | IMMEDIATE | high | Y | HIGH | HIGH | draft |
| Q18 | Expedite — need by Friday | NORMAL | high | Y | LOW | MEDIUM | draft |
| Q19 | Did my exchange ship | HIGH | high | Y | HIGH | HIGH | draft |
| Q20 | Cancel one item from order | NORMAL | high | Y | LOW | HIGH | draft |
| Q21 | Do items run small (sizing) | NORMAL | high | - | LOW | HIGH | draft |
| Q22 | Gift wrapping cost/eligibility | NORMAL | high | - | LOW | HIGH | draft |
| Q23 | International shipping to Israel | NORMAL | high | - | LOW | HIGH | draft |
| Q24 | Restocking fee | HIGH | high | - | LOW | HIGH | draft |
| Q25 | Final sale threshold | IMMEDIATE | high | - | LOW | HIGH | draft |
| Q26 | Care / machine washable | NORMAL | high | - | LOW | HIGH | draft |
| Q27 | Promo code stacking | NORMAL | high | - | LOW | HIGH | draft |
| Q28 | Warehouse pickup hours | NORMAL | high | - | LOW | HIGH | draft |
| Q29 | What is package protection | NORMAL | high | - | LOW | HIGH | draft |
| Q30 | First-time customer discount | NORMAL | high | - | LOW | HIGH | draft |

## Key findings

### Both systems ✅
- **Shopify order context works end-to-end in both.** All 20 order queries used live order facts: tracking links (Q02/Q03/Q10), item lists (Q16), payment/fulfillment status (Q01 'being prepared', Q14), partial-shipment (Q15), refunded status (Q08). Neither fabricated tracking/dates.
- Both answered policy questions from the KB (intl $35 Q23, no first-time discount Q30, final-sale 20% Q25).

### Where they differ
1. **Priority calibration — Teddy better for routine status.** Old marks every 'where is my order / shipped?' as **HIGH** (Q01-03,09,10,12,19); Teddy keeps these **LOW** and reserves HIGH for refund/missing/exchange. Old also fired **IMMEDIATE** on a pure *policy* question (Q25 'is my discounted item returnable' → final_sale_exception); Teddy answered it LOW from KB. Edge: Teddy escalates address-change (Q11) to IMMEDIATE with no draft — arguably too conservative.
2. **Helpfulness on action requests — Old better.** For cancel-one-item (Q20), expedite (Q18), add-item (Q13), address (Q11), Old drafts a useful 'we'll put a hold / check with the warehouse / reply with the address' holding reply; Teddy tends to withhold the draft and escalate.
3. **KB recall — Old (BM25) beat Teddy (semantic router) on Q24.** Restocking fee: Old answered exactly ($1.69/item, store-credit alternative); **Teddy said it 'isn't in the policy' and escalated — even though the fee IS in Teddy's `returns.md`.** A Teddy retrieval/routing miss (false KB-gap).
4. **⚠️ SAFETY REGRESSION in Old — refund/cancel not escalated.** The Old classifier correctly flags Q05 (cancel), Q08/Q17 (refund) as `escalate=True, auto_draft_allowed=False`, but `draft_engine.generate_draft` returns `is_escalation=False, should_post=True` and produces a **customer-facing draft** anyway (verified directly under mock). This contradicts the documented Stage-2 invariant (refunds/cancellations = escalation-only, no customer draft). Teddy correctly withholds the draft (Q05 → IMMEDIATE alert only) / flags Q08 with an ESCALATE marker.
5. **Tone.** Old = warm, lowercase, emoji ('hey! 🎉'). Teddy = polished, capitalized, signs 'The Buttons Bebe Team'. Stylistic preference.

## Verdict
- **Both correctly consume Shopify order context** — the integration goal is met on both sides.
- **Teddy** is safer (honors escalation, better priority calibration) but **over-escalates from KB-retrieval misses** (restocking fee, expedite, remove-item) — its semantic router needs tuning so it surfaces facts it actually has.
- **Old** is more helpful and has stronger KB recall, but has a **priority-inflation issue** and a **real safety bug**: the refund/cancel escalation gate in `draft_engine` is not firing. **Fix before enabling auto-post.**

---

## Post-fix update (2026-06-29)

Both reported issues fixed and re-verified by re-running the full v3 harness.

### Fix 1 — Old system: escalation gate restored (`draft_engine.py`)
**Root cause:** the sensitive-ticket gate had been deliberately deleted (comment claimed "internal notes only → no need to gate"), which also broke the module's own self-test and the documented invariant. The old system's own LLM, asked directly, agreed refunds must be escalated, not drafted.
**Fix:** re-added `_escalation_note()` + SAFETY GATE 1 in `generate_draft()` — `auto_draft_allowed=False` ⇒ `is_escalation=True, should_post=False`, labeled "⚠️ ESCALATE — DO NOT AUTO-REPLY" note, no customer draft.

| Query | Pre-fix | Post-fix |
|---|---|---|
| Q05 cancel | drafted customer reply, should_post=True | **escalation note, should_post=False** |
| Q08 / Q17 refund | drafted customer reply, should_post=True | **escalation note, should_post=False** |
| Q25 final-sale exception | drafted, should_post=True | **escalation note, should_post=False** |
| Q01/Q02/Q16/Q23/Q24 (benign) | drafted | drafted (unchanged) |

Totals: escalations 0 → **4**; draftable 30 → **26**. Verified: `draft_engine` self-test (c)/(c2) now pass; `classifier` 68/68 OK; `test_workflow_a` sensitive-escalation property passes. (Pre-existing, unrelated: the `(b)` KB-gap self-test under the mock provider still fails — flagged for separate follow-up, not introduced here.)

### Fix 2 — Teddy: KB context truncation (`agent.py`)
**Root cause:** the router routed `restocking fee` to `returns.md` correctly (HIGH), but `returns.md` grew to ~9K during the KB integration and the draft step truncated KB context to **3000 chars** — the restocking section sits at offset ~3,700, so it was cut before the LLM saw it ⇒ false "not in policy" KB-gap. (The same truncation hid gift-wrapped / missing-item / baby-gift facts.)
**Fix:** raised `_MAX_CONTEXT_CHARS` 3000 → **12000** in `agent.py` (and the v3 harness to match). One-constant change; most KB files are <3.5K so only large/multi-file contexts are affected.

| Query | Pre-fix | Post-fix |
|---|---|---|
| Q24 restocking fee | false KB-gap → escalated | **answered: "$1.69/item, or store credit to avoid the fee"** |
| Q13 add-item | escalated | drafted |

Remaining Teddy no-drafts (Q05 cancel, Q11 address, Q15 missing-item, Q18 expedite) are **by-design escalations**, not retrieval misses.

### Status
- Both fixes verified; benign/routine flows unchanged; no new regressions.
- **Recommended follow-ups (not done — flagged):** (a) old system's pre-existing `(b)` KB-gap detection under mock; (b) consider splitting the large `returns.md` into focused files (old-agent style) for retrieval precision + maintainability rather than relying on a larger context budget; (c) old system's priority inflation (routine "where's my order" → HIGH) — calibration, left untouched to avoid classifier risk.
