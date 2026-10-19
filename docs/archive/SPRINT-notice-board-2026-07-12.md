# Sprint Plan: Notice Board

**Date:** Sunday, 2026-07-12 (one-day sprint) · **Go-live target:** today
**Builder:** Claude (with Tony reviewing/approving) · **Client:** Chaim
**Branch:** `main` → deploy to VPS `srv1766050`

---

## Sprint Goal

> Give the owner a **Notice Board** in the knowledge base: short notices the owner posts that **override all other answers** the AI gives, each with an optional deadline that makes it **auto-expire** — with a simple panel in the console to add and remove them.

---

## How it will work (plain English)

Today the AI answers customers by searching the knowledge base (policies, FAQs, product data) and replying only from what it finds. The Notice Board sits **on top of** that search: whenever the AI looks something up, any live notice is handed to it first, stamped as "the owner's override — this is the current truth." So if the board says *"same-day delivery, free shipping,"* the AI says that — even though the product data still says *"7 days, $30."* The moment a notice's deadline passes (or the owner removes it), it disappears and the normal answers come back on their own.

Three things make this reliable:

1. **It always reaches the AI.** Notices are injected at the very top of every knowledge-base search result, so they can't be "out-ranked" or missed.
2. **Expiry is instant.** An expired notice is filtered out the instant it's read — it never reaches a customer even a second late — and a small cleanup job also tidies the board every 15 minutes.
3. **No rebuild needed.** Posting or removing a notice takes effect immediately; there's no waiting for the search index to rebuild.

---

## What we're building (the pieces)

| # | Piece | Where it lives | New/changed |
|---|-------|----------------|-------------|
| A | Notice storage (structured file: text, created, expiry) | `kb/notices/notices.json` | new |
| B | Notice helper (load, add, remove, drop-expired) | `kb/scripts/notices_lib.py` | new |
| C | Override injection into search | `kb/scripts/search_kb.py` (used by `kb_mcp_server.py` → tool `search_kb`, :8077) | changed |
| D | AI instruction ("Notice Board is the truth") | `kb/hermes-SOUL-buttonsbebe-addition.md` → server `~/.hermes/SOUL.md` | changed |
| E | Owner API (list / add / remove notices) | `kb-admin/server.js` (:8087, behind console auth at `/console/kbapi/*`) | changed |
| F | Owner panel ("Notice Board" in the console) | server console `/var/www/console/index.html` | changed (see risk R1) |
| G | Auto-expire cleanup job (every 15 min) | `kb/buttonsbebe-kb-notices-gc.service` + `.timer` | new |

---

## Capacity

This is a same-day, single-builder sprint. Estimates are in hours of focused build+test time; planned to ~75% of the day to leave room for review and surprises.

| Person | Available today | Allocation | Notes |
|--------|-----------------|------------|-------|
| Claude (build) | ~8 hrs | ~6 hrs planned | Writes code, deploys, tests |
| Tony (review) | as needed | ~1 hr | Approves deploy + tries the panel |
| **Total planned** | | **~6 hrs of ~8** | 75% — buffer kept for the console UI (the long pole) |

---

## Sprint Backlog

| Priority | Item | Est. | Depends on |
|----------|------|------|------------|
| **P0** | **B** — `notices_lib.py`: read/write `notices.json`, add, remove, and "active only" filter (expiry = now or future) | 1.0 hr | — |
| **P0** | **C** — inject active notices at top of `search_kb` results with a loud `NOTICE BOARD` override marker + fail-safe (search still works if file missing) | 1.0 hr | B |
| **P0** | **D** — add the override rule to SOUL so the AI treats a Notice Board result as the truth over anything conflicting | 0.5 hr | C |
| **P0** | **E** — owner API in `kb-admin`: `GET /notices`, `POST /notices` (text + optional expiry), `DELETE /notices/:id` | 1.0 hr | B |
| **P1** | **F** — "Notice Board" panel in the console: post box + optional deadline picker, live list with countdown + Remove | 1.5 hrs | E |
| **P1** | **G** — cleanup timer to physically drop expired notices every 15 min | 0.5 hr | B |
| **P1** | Deploy to server + restart `buttonsbebe-kb-mcp` & `buttonsbebe-kb-admin`, update SOUL, back up first | 0.5 hr | all |
| **P2** | End-to-end test: post "same-day / free shipping" notice → ask the AI a delivery question → confirm override → expire → confirm revert | 0.5 hr | deploy |
| **P2** *(stretch)* | Commit the buildable pieces (B, C, D, E, G) to `main` | 0.25 hr | test green |

**Planned load:** ~6.25 hrs vs ~8 hr day (~78%). P2 items are the first to slip if time runs short.

---

## The override, precisely (for the build)

A notice record:

```json
{ "id": "n_1752350000", "text": "Same-day delivery, free shipping on all orders.",
  "created_at": "2026-07-12T20:40:00Z", "expires_at": "2026-07-14T00:00:00Z", "created_by": "owner" }
```

`expires_at` may be `null` (stays until removed). On every `search_kb` call, active notices are prepended as results shaped like the normal ones but with `score: 999`, `sensitive: false`, `source: "NOTICE BOARD"`, and text wrapped:

> `[NOTICE BOARD — OWNER OVERRIDE. This is the current truth and overrides any conflicting policy, FAQ, or product info below.] <notice text>`

SOUL gains one rule: *if a `search_kb` result is a NOTICE BOARD entry, follow it as the current truth and let it supersede any conflicting information for as long as it appears.*

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **R1 — Console page source isn't in `main`** (it lives only on the server at `/var/www/console/index.html`) | Owner panel (F) can't be built purely on main | Build the panel directly on the server file (backed up first); optionally copy it back into the repo afterward. **Decision needed — see below.** |
| **R2 — `main` is a partial snapshot** (has `kb/` + `kb-admin/`, not the console/webhook) | "Build on main then deploy" only fully applies to B–E, G | Commit B–E, G to main; treat D (SOUL) and F (console) as server-side edits; document in deploy notes |
| **R3 — Folder name casing** (`kb/` local vs `KB/` on server) | Wrong path breaks deploy | Deploy script targets server `KB_DIR=/root/Buttonsbebe Agent/KB` explicitly |
| **R4 — Bad/missing notices file** | Could break all searches | `search_kb` wraps notice-loading in try/except → empty list on any error; search never fails because of the board |
| **R5 — Same-day full scope is tight** | UI may slip past today | UI (F) is the only P1 that can drop to "tomorrow"; the override brain (B–D) ships today regardless |

---

## Definition of Done

- [ ] Owner can post a notice (with or without a deadline) from the console
- [ ] A live notice overrides conflicting delivery/shipping/policy answers in the AI's draft
- [ ] An expired (or removed) notice stops affecting answers immediately
- [ ] Search still works if the notices file is empty or missing (fail-safe verified)
- [ ] Services restarted; SOUL updated; config backed up; rollback path noted
- [ ] Buildable pieces committed to `main`

---

## Today's timeline

| Time (approx) | Milestone |
|---------------|-----------|
| Now | Plan approved + R1 decision |
| +2 hrs | Override brain done (B, C, D) — AI obeys notices |
| +3.5 hrs | Owner API + cleanup timer (E, G) |
| +5 hrs | Console panel (F) live |
| +5.5 hrs | Deployed + end-to-end tested |
| +6 hrs | Committed to `main` · demo to Tony |

---

## One decision before I start (R1)

The owner panel needs the console page, which only exists on the **live server**, not in `main`. Pick one:

- **A (recommended):** Build the panel directly on the server's console page (backed up first), then copy it back into `main` so the repo has it too. Fastest path to live-today.
- **B:** First pull the current console page into `main`, build there, then deploy. Cleaner git history, ~30 min slower.

Everything else (the override brain, the owner API, auto-expire) is built on `main` and deployed either way.
