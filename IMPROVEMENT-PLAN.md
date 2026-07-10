# Improvement Plan — Buttons Bebe Support System

> Written 2026-07-10. Covers BOTH halves of the project:
> **Track A** — the live VPS agent (Gorgias + Hermes + KB, handling real tickets today).
> **Track B** — Fable (the local Gorgias replacement built on branch `Fable_buttonsbebe`).
> Companion docs: `DESIGN-CRITIQUE.md` · `SPRINT-2-PLAN.md` · `TESTING-READINESS.md`.

---

## 1. The one big idea: stop building two of everything

Right now the project has two brains, two risk checkers, two consoles, and two learning
stories. That's normal mid-transition, but every week it stays that way costs double work.

**The convergence rule:** anything built once should be shared by both tracks.

| Piece | Best version today | What to do |
|---|---|---|
| Risk check (is this ticket sensitive?) | Fable's `server/app/risk.py` — real code, unit-tested | **Port it into the VPS processor** to replace the `classifier.py` stub |
| Draft cleaning (strip AI self-talk) | Neither — VPS leaks it, Fable's MockBrain doesn't have the problem yet | Build ONE cleaner module, use in both |
| Knowledge base | VPS LanceDB KB (4,246 products, policies, learned lessons) | **Wire it into Fable** (P1 item 18 already planned) |
| Learning loop (save human's real reply) | VPS `learning.py` + nightly promote | Port into Fable when Fable goes live |
| Console UI | Fable's console (newer, better structured) | Fix per `DESIGN-CRITIQUE.md`; retire the old dashboard when Fable takes over |
| Tests | Fable's suite (182 checks, one command) | Extend to cover the ported pieces |

---

## 2. Track A — the live system: what to fix (ranked)

These come from `DEV-ISSUES.md`, `INCONSISTENCIES.md`, and the QA runs. Ranked by
risk-to-customers first, annoyance second.

### Safety (do first)
1. **The safety gate is only the LLM.** `classifier.py` returns NORMAL for everything, so if
   the model has a bad day, nothing catches it. Fix: port Fable's `risk.py` (keyword +
   pattern rules, already tested) so a dumb-but-reliable code check runs BEFORE and AFTER the
   model. Belt and suspenders.
2. **`--yolo` lets Hermes auto-approve any tool.** Safe today (only write = internal note),
   but one future tool away from trouble. Fix: pass an explicit tool allow-list (`-t`).
3. **Draft leakage.** glm-5.2 sometimes appends "The response above was complete…" or repeats
   itself. A human might miss it and send it. Fix: a `draft_cleaner.py` that cuts known
   markers, de-dupes repeated blocks, and rejects drafts for empty/spam messages (QA #19).
4. **Grounding slip.** It quoted a "$35 USD" international rate that isn't in the KB. Fix:
   add the real rate to the KB (needs Chaim) + hard prompt rule "never state a price not in
   the KB."

### Reliability
5. **No one notices if the processor dies.** Add a heartbeat: processor touches a file/URL
   every loop; a tiny cron alerts the owner's WhatsApp if it goes quiet 10+ minutes.
6. **`.env` duplication** already caused a long debugging detour. Consolidate to one file.
7. **Shopify auth dead code** (`SHOPIFY_ADMIN_API_TOKEN` empty and unused) and the **Redo
   prompt mismatch** (prompt names a `get_order` tool that doesn't exist). Both are
   ten-minute fixes that remove landmines.

### Content (needs Chaim — start now, it's the slowest lane)
8. **Confirm the real policies** (return window, who pays return shipping, international
   rates, sale rules). Placeholders are the #1 source of wrong answers.
9. **Resolve the order-change contradiction** the 48-scenario run found: pickup↔shipping
   switches get drafted, but address changes escalate. Pick one rule, write it in
   `agent-core-rules.md` AND the safety model, so the model stops guessing.
10. **Fix the two-locations mixup** (pickup at 2133 Lakewood vs. 24/7 return bin at 6 Kenyon
    Drive) in the KB wording.

---

## 3. Track B — Fable: what it needs to become real

Fable works end-to-end locally with a mock brain and emulators. The gap to "actually
replaces Gorgias" is four plugs (all designed as plugs already — no rewrites):

1. **Real brain** — finish `brains/anthropic_stub.py` (Claude API) and/or `hermes_stub.py`.
   Same interface as MockBrain. Gate: golden-set evals must pass (see `TESTING-READINESS.md`).
2. **Real email** — IMAP/SMTP adapter replacing the mailbox emulator (same code path).
3. **Real WhatsApp** — point the emulated channel at the existing `whatsapp-connect` bridge.
4. **Migration importer** — Gorgias export → Fable, tested against a Gorgias **emulator**
   first (never the real account).

Then: parallel-run pilot (Fable + Gorgias both live for 2 weeks on a fresh VPS), compare,
and cut over only after Chaim signs off.

---

## 4. Feature spec — what makes it BETTER and EASIER

Format: what it is → why → "done" means. Full build order in `SPRINT-2-PLAN.md`.

### F1 · Shared risk engine (safety)
One `risk.py` used by both tracks. Deterministic rules run before and after the brain.
**Done:** VPS processor uses it; a ticket saying "refund" gets flagged even if the model
misses it; unit tests green on both sides.

### F2 · Draft cleaner + no-content guard (quality)
Module that strips model self-commentary, de-duplicates, and returns NO_DRAFT for
empty/spam/survey messages.
**Done:** QA #01/#04/#10 leak cases and #19 empty case all pass; used by both tracks.

### F3 · Real brain adapter for Fable (capability)
`FABLE_BRAIN=anthropic` produces grounded drafts using the KB + order context.
**Done:** the 48 golden scenarios run against it with ≥ the sim baseline score; safety
invariants still green.

### F4 · KB search inside Fable (quality)
Fable drafts cite the same policies/FAQ/products the VPS agent uses. Keyword search first,
LanceDB later.
**Done:** "Do you ship to Canada?" draft quotes the actual policy file.

### F5 · Heartbeat + morning digest (trust)
Processor heartbeat with WhatsApp alert on silence; optional 8am digest: "Yesterday: 34
tickets, 29 drafted, 4 flagged sensitive, 1 failed."
**Done:** kill the processor → owner gets a WhatsApp message within 10 minutes.

### F6 · Learning loop in Fable (gets smarter)
Port the VPS lesson capture (save the human's final reply, mask PII, promote nightly into
the KB as exemplars).
**Done:** editing + sending a draft in Fable creates a lesson file; nightly job promotes it.

### F7 · Console polish pack (ease of use)
The prioritized fixes from `DESIGN-CRITIQUE.md` — including the draft-edits-lost bug, undo
window after send, keyboard shortcuts, and new-message indicators.
**Done:** critique P0+P1 list closed; Playwright smoke test green.

### Non-goals (explicitly NOT doing)
- Auto-sending anything to customers, ever. The human-click rule is permanent.
- Multi-agent teams, assignment, CSAT surveys — Sprint 3+.
- Touching the live VPS from Fable work, or the real Gorgias account from any test.

### Success metrics
- **Zero safety violations** (nothing sent without a human click; all sensitive flagged).
- **Draft acceptance ≥ 60%** (sent as-is or lightly edited — visible in the learning ledger).
- **48/48 golden scenarios** correct on the deployed brain, not just the simulation.
- **First-response time trending down** on the Stats page.
