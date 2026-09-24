# Sprint Plan v2: Turn on the learning loop (`feedback_collector`)

**Dates:** Wed Jul 8 → Fri Jul 10, 2026 (3 days) | **Team:** 1 (Tony, solo)
**Sprint Goal:** *Safely capture the human's real reply, let me review and promote
the good ones, and prove a promoted example actually changes a later draft — before
calling the loop "live."*

> This is v2. It was rewritten after an adversarial review of v1. The big changes:
> similarity is a hint (never a gate), macros/non-English are skipped, PII is a
> first-class step, only clean single-exchange tickets in v1, and we do **not** flip
> the STUB→LIVE flag until a before/after retrieval test passes.

---

## The one thing that shapes this whole sprint

Writing files into `kb/learned/` **does nothing on its own** — the search engine
deliberately skips that folder (`kb/scripts/kb_lib.py`). "Getting better" only
happens when a captured reply is **promoted** into the indexed `tickets/` folder and
re-indexed. That promotion is a human decision (mine), for quality **and** PII (the
`tickets/` folder must be anonymized). So the sprint is three moves:
**capture → review → promote.**

---

## Status: most of this is already built and tested ✅

The capture package, the review/promote CLI, tests, and the validation harness are
implemented in this repo and the offline test suite is green (17/17). What remains is
VPS-only work: the spike, deploy, the timer, and the real-ticket validation.

| # | Item | State |
|---|------|-------|
| 0 | **Spike:** confirm `public`/`from_agent` on real Gorgias messages | ⬜ VPS-only (do first) |
| 1 | Trigger: poll tickets since a **high-water-mark cursor** | ✅ built (`collector.run_poll`, `store.py`) |
| 2 | `feedback` capture → `kb/learned/ticket-<id>.md` (review_pending) | ✅ built (`collector.py`, `pairing.py`) |
| 3 | `review_learned.py`: approve/reject → promote + batched reindex | ✅ built (`kb/scripts/review_learned.py`) |
| 4 | PII highlighter + **hard human gate** on approve | ✅ built (`pii.py`; approve refuses without `--pii-cleared`) |
| 5 | Skip rules: sensitive, macro, multi-turn, empty, from-scratch, dedupe | ✅ built (`pairing.py`, `collector.py`) |
| 6 | Non-English handling (Hebrew → suppress similarity, go manual) | ✅ built (`language.py`, `similarity.py`) |
| 7 | Before/after retrieval check (the go-live proof) | ✅ built (`validate.py`) — ⬜ must be RUN on VPS |
| 8 | Deploy + systemd timer + validate on 10+ tickets | ⬜ VPS-only |

Offline proof already run: `python3 -m unittest feedback.tests.test_all` → 17 passed;
capture→refuse→promote smoke test masks `[order]`/`[email]` and correctly leaves the
name "Sam" for the human (which is the whole point of the gate).

---

## Every adversary finding and what we did about it

| Finding | Fix in the build |
|---|---|
| **C1** schedule hangs on the spike | Spike is task 0 and gated; the pairing heuristic (`public`/`from_agent`) is the documented default to confirm, not discover from scratch |
| **C2** "human reply" ≠ "edit of the draft" | Everything is `review_pending`; the **human gate** decides. Bot identity (`FEEDBACK_BOT_EMAIL`) pins which note is the AI draft |
| **C3** difflib is character-level & language-blind | `similarity.py` is a **hint only, never a gate**; `language.py` marks Hebrew/non-English `n/a` and routes to manual |
| **C4** learning from macros | `pairing.looks_like_macro` skips via Gorgias metadata + a configurable signature list |
| **M1** poll window silently misses/double-fires | `store.py` high-water-mark cursor + overlap + processed ledger |
| **M2** "big rewrite = gold" is backwards | Rewrite band = "read carefully," not auto-valued; no auto anything |
| **M3** PII under-resourced | PII is P0: `approve` **refuses** without `--pii-cleared` and prints findings; README states names aren't caught |
| **M4** multi-turn threads | v1 skips them (`multi_turn`), configurable |
| **M5** nobody measures improvement | `validate.py` before/after retrieval check; **must pass before STUB→LIVE** |
| **M6** retrieval poisoning | README: cap promotions per topic, review what `tickets/` retrieves; promoted files start `needs_final_edit` |
| **m1–m6** ½-good replies, reindex atomicity, PII-at-rest, thin DoD, env drift, owner-qa clobber | flags on partial edits; **batched** reindex (separate command); `--purge` option; DoD now 10+ tickets; config reads the shared `.env`; collector/CLI only touch `ticket-*.md`, never `owner-qa-*.md` |

---

## Capacity

| Person | Available | Realistic focus | Notes |
|--------|-----------|-----------------|-------|
| Tony | 3 days | ~2.2 days (73%) | Solo; most build is done — remaining is spike + deploy + validate |

---

## What's left to do (the actual remaining work)

**Day 1 — Spike + deploy (task 0, 8).** On the VPS, `get_ticket_messages` on 2–3
resolved tickets; confirm internal notes are `public:false` and outbound replies are
`public:true`. If the payload differs, adjust two functions in `pairing.py`
(`is_internal_note`, `is_public_agent_reply`) — everything else stays. Copy `feedback/`
next to `processor/`, set `FEEDBACK_BOT_EMAIL` + paths in the main `.env`, run
`python3 -m feedback.collector poll` by hand, eyeball `kb/learned/`.

**Day 2 — Review a real batch (task 3/4).** Run `review_learned.py list/show`, promote
a few, edit them to `confirmed`, `reindex`. Feel where the PII gate and the drafted
exemplars need wording tweaks.

**Day 3 — Validate + decide (task 7).** Run `feedback/validate.py` on a promoted
exemplar. **Only if it PASSES** on 10+ tickets across easy/hard paths, flip
CLAUDE.md §8 + DEV-ISSUES #4 from STUB → LIVE. Then add a systemd timer (~10 min) so
capture runs itself. If it fails, stay in SHADOW and fix retrieval first.

---

## Risks (remaining)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Spike shows the draft/reply signal is ambiguous | Rework pairing | Timeboxed to 2h; fallback = capture-only, drop the band |
| PII (names) slips through into `tickets/` | High | Human reads every promotion; promoted files start `needs_final_edit`; `--purge` for archives |
| Promoted exemplars never actually retrieved | Loop is a no-op | `validate.py` gate before go-live |
| Retrieval skews toward one week's tickets | Worse drafts | Cap promotions per topic; review `tickets/` retrieval periodically |

---

## Definition of Done

- [ ] Spike confirmed the message fields (or pairing adjusted).
- [x] Capture writes `kb/learned/ticket-<id>.md` with the pair + hint + PII summary.
- [x] `review_learned.py` refuses to promote without `--pii-cleared`; on approve writes
      a PII-masked draft exemplar and archives the packet.
- [x] Skips: sensitive, macro, multi-turn, empty, no-draft; no double-processing.
- [x] Offline test suite green (17/17).
- [ ] Verified on **10+ real tickets** (easy + hard paths), on the VPS.
- [ ] `feedback/validate.py` PASSES (promoted exemplar is retrieved for its question).
- [ ] Only then: CLAUDE.md §8 + DEV-ISSUES #4 flipped STUB → LIVE; systemd timer added.

---

## Explicitly NOT in this sprint

- Owner-Q&A branch, auto-promotion (unsafe — PII), a real Gorgias webhook (poll is
  fine for v1), multi-turn threads, a web review UI, and the other open stubs
  (`classifier.py`, Shopify client-creds). All out of scope.
