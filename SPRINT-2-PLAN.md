# Sprint 2 Plan — "Converge & Get Real" (everything in parallel)

**Dates:** 2026-07-13 → 2026-07-24 | **Team:** Tony (owner/reviewer) + Claude agent waves + Chaim (policy answers)
**Inputs:** `IMPROVEMENT-PLAN.md` (what & why) · `DESIGN-CRITIQUE.md` (console fixes) · `TESTING-READINESS.md` (gates)

**Sprint goal:** the live VPS agent gets a code-level safety net and clean drafts; Fable gets
a real brain, real KB, and a polished console — all proven by the test gates before anything
ships. Five streams run **in parallel** because they touch different code.

---

## 1. The five parallel streams

Each stream is independent — different folders, no shared files — so agents can run
side-by-side without stepping on each other. The only sync points are the two gates (§3).

### Stream V — VPS hardening (live system) · Agent V
Touches: VPS `processor/`, `.env` files, KB content. Nothing in `fable/`.

| # | Item | Size |
|---|---|---|
| V1 | Port Fable's `risk.py` into `processor/classifier.py` (replaces the stub) + unit tests | M |
| V2 | Build shared `draft_cleaner.py` (strip self-talk, de-dupe, NO_DRAFT for empty/spam) + wire into `hermes_runner.py` | M |
| V3 | Restrict Hermes toolset (replace bare `--yolo` with explicit tool allow-list) | S |
| V4 | Consolidate the two `.env` files to one source of truth; remove dead `SHOPIFY_ADMIN_API_TOKEN`; add client-cred token helper | M |
| V5 | Fix the Redo prompt mismatch (`get_order` doesn't exist — correct the prompt or add the tool) | S |
| V6 | Heartbeat + WhatsApp alert if the processor goes quiet 10 min (feature F5) | M |
| V7 | Rotate any secrets ever pasted into chats/notes; `chmod 600` check | S |

### Stream B — Fable brain + KB (make drafts real) · Agent B
Touches: `fable/server/app/brains/`, new `fable/server/app/kb/`.

| # | Item | Size |
|---|---|---|
| B1 | Finish `brains/anthropic_stub.py` → real Claude adapter (same interface as MockBrain) | L |
| B2 | KB search inside Fable — reuse `kb/` content, keyword search first (feature F4) | M |
| B3 | Use the SAME `draft_cleaner.py` from V2 (import, don't copy) | S |
| B4 | Golden-set harness: run the 48 scenarios from `testing/scenarios.json` against the real brain | M |

### Stream C — Console polish (the critique list) · Agent C
Touches: `fable/console/` only.

| # | Item | Size |
|---|---|---|
| C1 | **P0 bugs:** draft-edits-lost (B1), keyboard on customer cards (B2), stale-ticket banner (B3) | M |
| C2 | Draft-first layout: collapse older messages + sticky draft card; mobile order-summary chip | M |
| C3 | Undo-send grace window (5s delayed dispatch) | M |
| C4 | Tag inline input + removal; edited-draft flag; SVG channel icons; type-scale snap | M |
| C5 | New-message pulse on inbox badge + row highlight | S |

### Stream R — Real channels + migration (the road off Gorgias) · Agent R
Touches: `fable/emulators/`, new adapter modules. Longest lead time — start day 1.

| # | Item | Size |
|---|---|---|
| R1 | Gorgias **emulator** (read endpoints + export pagination) — required before R2 | M |
| R2 | Migration importer: Gorgias export → Fable, `external_id` preserved; dry-run against R1 | L |
| R3 | Email adapter (IMAP/SMTP) behind the same interface as the mailbox emulator | L |
| R4 | WhatsApp channel → existing `whatsapp-connect` bridge (config swap design + local test) | M |

### Stream T — Testing & QA (the referee) · Agent T
Touches: `fable/tests/`, `testing/`. Details in `TESTING-READINESS.md`.

| # | Item | Size |
|---|---|---|
| T1 | Run the 48 golden scenarios on the LIVE VPS model (glm-5.2); score with the A–E rubric; diff vs. the sim baseline | M |
| T2 | Unit tests for `draft_cleaner.py` using the real QA leak cases (#01/#04/#10/#19) | S |
| T3 | Playwright smoke: inbox → open ticket → edit → note → send-with-confirm; regression tests for C1 bugs | M |
| T4 | Extend safety invariants to cover the real-brain path (no send without click, sensitive always flagged) | S |
| T5 | Wire everything into one gate command per track (see §3) | S |

### Tony's lane (decisions — everything else parallelizes around you)
1. **Order-change rule** (the contradiction the 48-run found): may the agent draft pickup↔shipping switches and pre-ship address fixes, or does every order change escalate? One sentence from you unblocks V1 + B4.
2. **Chaim's policy confirmations** (return window, return shipping, international rate, sale rules) — send him the list day 1; content lands whenever it lands.
3. **Brain choice & budget** for B1 (Claude API model tier).
4. Mid-sprint UI review of Stream C (2026-07-17).

---

## 2. Dependency map (what actually blocks what)

```
V2 draft_cleaner ──► B3 (import it)          R1 gorgias emulator ──► R2 importer
V1 risk port     ──► T1 re-run scores it     B1 real brain ──► B4 golden-set ──► GATE 2
C1 P0 bugs       ──► T3 playwright regress   Everything else: independent, start day 1
Tony decision #1 ──► final V1/B4 sign-off (build proceeds; rule slots in)
```
Five agents can start simultaneously; only B3, R2, B4, T1, T3 wait on one earlier item each.

## 3. The two gates (nothing ships around them)

- **GATE 1 — VPS changes go live** only when: fable test suite green + T2 cleaner tests
  green + T1 live 48-run shows no regression vs. baseline (esp. all 12 sensitive escalate).
- **GATE 2 — Fable real-brain merges** only when: B4 golden-set ≥ sim baseline, T4 safety
  invariants green, T3 Playwright green.

## 4. Definition of Done (every item)
Code on the right branch (`main` for VPS docs, `Fable_buttonsbebe` for Fable) · tests pass
via one command · no real network calls from tests · plain-language doc/README updated ·
orchestrator ran it end-to-end once.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Real brain scores below the sim baseline | Expected first try — iterate prompt/KB, gate holds until ≥ baseline |
| VPS edits break live traffic | Streams V changes behind GATE 1; deploy in a quiet hour; heartbeat (V6) ships first so we'd know |
| Chaim's answers arrive late | Content lane is non-blocking; placeholders stay conservative |
| Agents collide in shared files | Only shared file is `draft_cleaner.py` — owned by V, imported by B |

## 6. Key dates
| Date | Event |
|---|---|
| 07-13 | All five streams start (parallel) |
| 07-17 | Mid-sprint: Tony reviews console (C1–C3 done) + T1 live-run results |
| 07-22 | Gates run; fix window |
| 07-24 | Sprint end: GATE 1 shipped, GATE 2 merged, Sprint 3 (parallel-run pilot) planned |
