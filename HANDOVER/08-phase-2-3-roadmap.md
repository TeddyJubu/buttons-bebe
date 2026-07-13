# 08 · Phase 2 & 3 Roadmap

**What this doc covers:** the client-facing Phase 2 & 3 roadmap presented to Chaim on 2026-07-12 — the three phases at a glance, what's live today (Phase 1), every Phase 2 feature group (A–E) with its estimate **and a "Maps to" column tying each item to the real file/stub**, the Phase 2 timeline, the 5 policy questions the owner must answer, the Phase 3 capabilities with their architecture notes, and the whole-program timeline plus the estimate assumptions.

**Sources read:** `HANDOVER/_assets/phase-2-3-plan-fulltext.txt` (full extracted text of `Buttons-Bebe-Phase-2-3-Plan.pptx`, the authoritative deck — 19 slides); `SPRINT-feedback-collector.md`; `SPRINT-notice-board-2026-07-12.md`; `DEV-ISSUES.md`; `CLAUDE.md` (§4, §5, §8); `INCONSISTENCIES.md` (H2); plus repo file-existence checks (`tools/redo_mcp.py`, `feedback/`, `kb/learned/`, `kb/scripts/`, `dashboard/`, `console-src/` — and confirmation that `processor/` and `webhook/` are **not in this repo**).

> ⚠️ **This doc is the roadmap (the plan), not the live-system reference.** For what runs *today* read `CLAUDE.md` and `HANDOVER/02`–`05`. The deck is the authoritative source for scope and estimates; everything here is faithful to it. Where the deck's technical appendix (slide 18) names a file, this doc cross-checks whether that file is in the repo or **VPS-only** — see the "Maps to" columns and §7.
>
> **Nothing in this roadmap changes the safety model.** Through all of Phase 2 the AI still only *drafts* and a human still sends every customer reply (`CLAUDE.md §2`). Autonomy (auto-send) is a deliberately gated, one-topic-at-a-time, kill-switchable step that begins as an *optional stretch* at the end of Phase 2 and expands in Phase 3.

### How to read the citations

Compact tags point back to the exact source so the new team can verify every claim:

| Tag | Means |
|---|---|
| `S6`, `S18` … | Slide 6, slide 18 … of the deck (`phase-2-3-plan-fulltext.txt`). |
| `§8` | Section 8 of `CLAUDE.md`. |
| `DI#3` | Item 3 in `DEV-ISSUES.md`. |
| `H2` | Item H2 in `INCONSISTENCIES.md`. |
| `QA#03` | The QA-run finding referenced by that number inside `DEV-ISSUES.md`. |
| `SPR-FB` / `SPR-NB` | `SPRINT-feedback-collector.md` / `SPRINT-notice-board-2026-07-12.md`. |
| ⚠️ **VPS-only** | The named file/dir is **not in this repo** — it lives only on the VPS (`/root/Buttonsbebe Agent/…`). Obtain it during handover. |
| ✅ **in repo** | Verified present in this repo clone. |

---

## 1. The three phases at a glance

The through-line of the deck: *the AI earns more trust at each step* (`S3`).

| # | Phase | When (deck estimate) | What it delivers | The human's role |
|---|---|---|---|---|
| **1** | **Copilot** — *live today* | **NOW** (`S3`) | The AI drafts every reply from read-only system access; a human reviews and sends. | **Human sends every reply** (`S3`). |
| **2** | **Trustworthy & Visible** | **NEXT · ~4–6 weeks** (`S3`, `S4`) | Make it rock-solid and higher quality, *prove* it learns, and give the owner a dashboard to see it working. Still: AI drafts, human approves. | **Human still approves — now with proof & metrics** (`S3`). |
| **3** | **Autonomous & Multi-channel** | **LATER · ~3–4 months, in stages** (`S3`, `S11`) | The AI handles routine tickets end-to-end, across more channels, and can take real actions (refunds/returns/discounts) — one topic at a time, with a kill switch. | **Human supervises the exceptions** (`S3`). |

Full program after today: **~4.5–6 months** of focused build, "with something new shipping almost every week" (`S16`). See §6.

---

## 2. Phase 1 recap — what is LIVE today

> For every incoming ticket the agent reads the message, pulls the order/return/product details, searches the knowledge base, and writes a first-draft reply — placed in Gorgias **as a private (internal) note** for the team to review and send (`S2`). Built for the store's **~2,000 tickets/month** (`S2`).
>
> **The safety promise (`S2`):** *the AI never sends on its own. Every customer reply is written by the AI but sent by a human. Sensitive tickets are flagged, never auto-handled.*

The six live capabilities the deck credits to Phase 1 (`S2`):

| # | Capability | What it does (deck wording) | Live-system anchor |
|---|---|---|---|
| 1 | **Reads every ticket** | Understands the customer's message in context. | Hermes brain, one-shot per ticket (`CLAUDE.md §4`). |
| 2 | **Pulls order context** | Order, return & product details, automatically. | `buttonsbebe_gorgias` (:8079) + `buttonsbebe_redo` (:8078) MCP tools (`CLAUDE.md §4`). |
| 3 | **Searches the knowledge base** | Policies, FAQs and **4,246 live products**. | `buttonsbebe_kb` (:8077), LanceDB hybrid search; products auto-synced every 3 days (`CLAUDE.md §4`, §8). |
| 4 | **Drafts the reply** | Written as a private note for staff to approve. | `processor/gorgias_writer.py` posts the internal note — the only write in the system (`CLAUDE.md §4`). ⚠️ **VPS-only**. |
| 5 | **WhatsApp escalation** | Urgent tickets ping the owner instantly. | `whatsapp-connect` (:8085, Node + Baileys) ✅ **in repo** + processor notifier (VPS-only) (`CLAUDE.md §5`, §8). |
| 6 | **Notice Board** | Owner posts override the AI's answers on the fly. | Injected at top of `search_kb`; built 2026-07-12 (`SPR-NB`; `kb/scripts/notices_lib.py`, `kb/notices/` ✅ **in repo**). |

> **Note for the new team:** capability 6 (Notice Board) shipped *after* `CLAUDE.md` was last written, so it is not in that file's port table — see `SPR-NB` and `HANDOVER/04`/`05`. It is nonetheless part of "Phase 1, live today" per the deck.

---

## 3. Phase 2 — Trustworthy & Visible (feature groups A–E)

**Goal (`S4`):** *rock-solid, measurable, and higher quality — while the AI still drafts and a human still approves every reply.* **Estimate: ~4–6 weeks**, one focused builder, delivered in small pieces so value ships every week.

**Why it matters (`S5`):** (1) fewer risky replies slip through — a rule-based safety net backs up the AI's judgment; (2) you can *see* it working — a dashboard of drafts-used-as-is, tickets handled, hours saved, top topics; (3) drafts get smarter weekly and we *prove* a lesson changed the next draft; (4) faster, more accurate answers — live order/tracking data plus a guardrail against invented prices/policies.

The deck's technical appendix (`S18`) maps each item to the real system "from CLAUDE.md, DEV-ISSUES.md and the sprint notes." Those mappings are reproduced verbatim-in-spirit in the **Maps to** columns below, with repo/VPS status added from direct file checks.

### Group A — Make what we have rock-solid  ·  ~8–10 days (`S6`, `S9`)

*"The behind-the-scenes cleanup that turns a working prototype into a system you can trust in production."*

| # | Item (deck) | Est. | Maps to (file / stub · issue) |
|---|---|---|---|
| A1 | **Deterministic safety net** — code-level gate flags refunds, disputes, damaged/angry cases, backing up the AI's own judgment. | **2–3 days** | Implement `processor/classifier.py` → `IMMEDIATE/HIGH/NORMAL`; **currently a STUB returning `NORMAL`** (`S18`; `DI#3`; `§8`). ⚠️ **VPS-only**. |
| A2 | **Cleaner drafts** — strip the AI's leftover self-talk and duplicated answers. | **~1 day** | Strip trailing self-commentary / de-dupe in `processor/hermes_runner.py` (glm-5.2 verbosity; **QA#01/#04/#10**) (`S18`; `DI#5`). ⚠️ **VPS-only**. |
| A3 | **Handle empty / junk messages** — recognise blank or survey messages and not invent a reply. | **~½ day** | Guard blank/survey messages in the ticket workflow (`hermes_runner.py` + the `buttonsbebe` skill; **QA#19**) (`S18`; `DI#6`). ⚠️ **VPS-only**. |
| A4 | **Never invent facts or prices** — only quote prices/policies actually in the KB. | **~1 day** | Reinforce "no price not in KB" in SOUL/prompt **and** add the real international rate to the KB (**QA#03**) (`S18`; `DI#7`). SOUL = `~/.hermes/SOUL.md` ⚠️ **VPS-only**; KB content = `kb/policies/international-orders.md` ✅ **in repo**. |
| A5 | **Finish the Shopify connection** — wire live order & tracking data using the correct secure login method. | **~2 days** | Add client-credentials token minting to `config.py`; remove the dead `SHOPIFY_ADMIN_API_TOKEN` (`S18`; **H2**; `DI#1`). Target files `webhook/src/bb_webhook/config.py` + `processor/config.py` ⚠️ **VPS-only**; **working pattern already exists** in `kb/scripts/sync_products.py` ✅ **in repo** (POST `/admin/oauth/access_token`, `grant_type=client_credentials`). |
| A6 | **Fix returns tool + lock the toolset** — correct a returns lookup and restrict what the AI may touch. | **~1 day** | (a) Fix the `redo_mcp` `get_order` mismatch (`S18`; `DI#2`); (b) restrict the `--yolo` toolset on the Hermes call (`DI#8`). `tools/redo_mcp.py` ✅ **in repo** — **verified**: it exposes `list_recent_returns`, `get_returns_for_order`, `get_return` and has **no `get_order`**, yet `processor/hermes_runner.py`'s prompt calls `get_order` → add the tool or fix the prompt. `hermes_runner.py` ⚠️ **VPS-only**. |
| A7 | **Security & config cleanup** — one settings file, tightened secrets, hardened owner login page. | **~1 day** | Consolidate the two `.env` files (`DI#9`); `chmod 600` + rotate keys (`DI#13`); harden the WhatsApp connect page (`DI#12`) (`S18`). Root `.env` ✅ **in repo (git-ignored)**; `webhook/.env` ⚠️ **VPS-only**; `whatsapp-connect/server.js` password gate ✅ **in repo**. |

### Group B — Learning loop, proven ON  ·  ~1 week (`S7`, `S9`)

*"Most of this is already built. Phase 2 turns it fully on and proves it actually helps."* The four steps (`S7`): **(1)** capture the team's real reply on each ticket → **(2)** review & approve the good ones (with privacy scrub) → **(3)** promote them so the AI can reuse them → **(4) PROVE a promoted lesson changes the next draft.**

| Item | Est. | Maps to (file / stub · issue) |
|---|---|---|
| Turn the capture→review→promote→prove loop fully ON. | **~1 week** | `S18`: *run the spike + `feedback/validate.py` on 10+ tickets, then flip STUB→LIVE + add the timer.* The `feedback/` package is ✅ **in repo** and its offline test suite is green (17/17): `feedback/collector.py`, `pairing.py`, `pii.py`, `similarity.py`, `language.py`, `store.py`, `validate.py`; promote CLI `kb/scripts/review_learned.py`. The stub to flip is `processor/feedback_collector.py` (`DI#4`) ⚠️ **VPS-only**. `feedback/validate.py` is the **go-live gate** (before/after retrieval check — "the M5 gate"; `SPR-FB` tasks 7–8). Remaining work per `SPR-FB` is **VPS-only**: the Gorgias field spike, deploy, systemd timer, and validation on 10+ real tickets. |

> ⚠️ **Two learning-loop designs exist in the sources — reconcile before building.** The deck's Group B and `DI#4` describe the **poll-based** `feedback/` package (flip `feedback_collector.py` STUB→LIVE, gated by `feedback/validate.py`; `SPR-FB`). Separately, **`CLAUDE.md §8` (added 2026-07-09) describes a *newer, console-action* learning loop already marked LIVE** — `webhook/src/bb_webhook/learning.py` writing `KB/learned/lesson-*.md` + `_ledger.json`, promoted nightly by `KB/scripts/auto_promote_learned.py` via `buttonsbebe-kb-learn.timer` (03:30) — and states it *supersedes* the poll-based collector. **Repo check:** the console-action files (`learning.py`, `auto_promote_learned.py`, the learn timer, `KB/learned/_ledger.json`, any `lesson-*.md`) are **not in this repo → VPS-only**; local `kb/learned/` holds only `.gitkeep` + one `owner-qa-*.md`. The deck (the client-facing plan) still frames Group B around the `feedback/` package + `validate.py` proof. The new team should confirm on the VPS which loop is actually running and treat "Group B" as *finish + prove whichever capture is live, then pass `validate.py` before calling it LIVE.* |

### Group C — Owner performance dashboard  ·  ~1–1.5 weeks (`S7`, `S9`)

*"A brand-new screen so you can see, at a glance, how the agent is doing."* Deck metrics (`S7`):

| Metric (deck) | Meaning |
|---|---|
| **Draft acceptance** | % of drafts sent as-is vs edited. |
| **Tickets handled** | Volume per day / week. |
| **Time saved** | Estimated hours the AI saved the team. |
| **Top topics** | What customers ask about most. |
| **Escalations** | Sensitive tickets flagged for a human. |

| Item | Est. | Maps to (file / stub · issue) |
|---|---|---|
| Build the owner metrics dashboard. | **~1–1.5 weeks** | `S18`: *new views over `webhook.db` + `KB/learned/_ledger.json` — **no analytics code exists yet.*** Data sources are ⚠️ **VPS-only**: the job queue `webhook/data/webhook.db` and the learning ledger `KB/learned/_ledger.json` (`§8`). **Note:** a `dashboard/index.html` and `console-src/index.html` exist ✅ **in repo**, but both are the current *Support Console* (per-ticket action feed served at `/dashboard`, `CLAUDE.md §5`), **not** this metrics screen — the performance dashboard is net-new. |

### Group D — Live order & shipping status  ·  ~1–2 days (`S8`, `S9`)

*"Every draft can include the real, up-to-the-minute order status and tracking link — so 'where is my order?' answers itself, accurately."*

| Item | Est. | Maps to (file / stub · issue) |
|---|---|---|
| Inject live order status + tracking link into drafts. | **~1–2 days** | **Builds on the finished Shopify connection from Group A (A5)** (`S8`). Depends on the client-credentials token helper in `config.py` and the Shopify read path (⚠️ **VPS-only**; `H2`, `DI#1`). No new external write — this is read-only order/tracking data surfaced in the draft. |

### Group E — Auto-send pilot, ONE safe topic  ·  ~1 week (stretch) (`S8`, `S9`)

*"Optional stretch: let the AI send its own reply for a single, very low-risk topic (order status) — behind confidence checks, the safety net, full logging, and a one-click kill switch. Sensitive tickets are never in scope — the bridge to Phase 3."*

| Item | Est. | Maps to (file / stub · issue) |
|---|---|---|
| Auto-send pilot for order-status only. | **~1 week** *(stretch)* | First send beyond a staff-only note. Gated by: **confidence check + the Group A safety net (`classifier.py`, A1) + full logging + one-click kill switch** (`S8`; architecture pattern on `S19`). The current only write is the Gorgias internal note via `processor/gorgias_writer.py` (⚠️ **VPS-only**; `CLAUDE.md §2`, §4) — the pilot adds a *new, gated public-reply send* for one intent. Sensitive tickets (per `classifier.py`) are excluded by construction. This is explicitly **the bridge into Phase 3** (`S8`). |

> **Scope note (`S9`):** core groups **A–D ≈ ~4 weeks**; the **pilot E adds ~1 week**, taking the phase to **~4–6 weeks**. E is the *first-to-cut* item if the phase runs tight and can be deferred into Phase 3's auto-send graduation.

---

## 4. Phase 2 timeline & the owner's inputs

### 4.1 Timeline (`S9`)

*"Focused build + test time for one person. Ranges leave room for testing and surprises."*

| Workstream | Estimate |
|---|---|
| Rock-solid foundation (Group A) | **~8–10 days** |
| Learning loop, proven ON (Group B) | **~1 week** |
| Owner dashboard (Group C) | **~1–1.5 weeks** |
| Live order & shipping context (Group D) | **~1–2 days** |
| Auto-send pilot — stretch (Group E) | **~1 week** |
| **Phase 2 total** | **~4–6 weeks** — core A–D ~4 weeks · pilot E adds ~1 week |

### 4.2 The 5 policy questions Chaim must answer (`S10`)

*"The AI is only as accurate as the policies it's given. A few confirmed answers replace today's cautious placeholders."* These directly **unblock KB accuracy** (they resolve the placeholder/DRAFT wording in `DI#10` and the location mix-up in `DI#11`).

| # | Question (deck) | What we need | Unblocks |
|---|---|---|---|
| 1 | **Return window** | How many days do customers have to return? | KB `policies/`/`intents/` placeholders (`DI#10`). |
| 2 | **Return shipping** | Who pays return postage — you or the customer? | Return-policy accuracy (`DI#10`). |
| 3 | **Sale-season rules** | Are sale / clearance items final, or returnable? | Return-policy edge cases (`DI#10`). |
| 4 | **International rates** | What's the real shipping cost outside the country? | Removes the invented-price slip (**QA#03**; `DI#7`) and lets Group A4 quote a real rate. |
| 5 | **Pickup vs return bin** | Confirm the pickup spot **and** the 24/7 return-bin address. | Fixes the conflation of the 2133 Lakewood pickup spot vs the 24/7 return bin at 6 Kenyon Drive (**QA#08**; `DI#11`). |

### 4.3 From today's meeting — what to decide (`S17`)

1. **Confirm Phase 2 priorities** — agree the order of work, and whether the auto-send pilot (Group E) is in or out for now.
2. **Answer the 5 policy questions** (§4.2).
3. **Steer Phase 3** — where to lean first: more autonomy, more channels, or taking actions.
4. **Line up access** — logins for the new channels & actions when Phase 3 reaches them.

> **Next step (deck):** *lock Phase 2 scope today and start on the rock-solid foundation (Group A) this week.*

---

## 5. Phase 3 — Autonomous & Multi-channel

**Goal (`S11`):** *the AI handles routine tickets end-to-end, reaches customers on more channels, and can take real actions — while you supervise the exceptions.* **Estimate: ~3–4 months, in stages**, each feature live before the next begins.

**What changes (`S12`):** (1) routine tickets answer themselves; (2) customers reached anywhere — website chat, Instagram & Facebook DMs, text/WhatsApp; (3) the AI can *do* things — with approval it issues refunds, starts returns, applies discounts. **Still safe:** sensitive tickets (unapproved refunds, disputes, upset customers) always go to a human; autonomy switches on one topic at a time, with a kill switch (`S12`).

### 5.1 The big three (`S13`, `S15`)

| # | Capability | Est. | What it delivers |
|---|---|---|---|
| 1 | **Auto-send graduation** | **2–3 weeks** | Expand from the Phase-2 pilot to a growing set of safe, high-confidence topics — each with its own on/off switch, confidence threshold and monitoring. |
| 2 | **Multi-channel** | **2–4 weeks** | Handle conversations from website live chat, Instagram & Facebook DMs, and SMS / WhatsApp — not just email tickets. |
| 3 | **Action-taking** | **3–4 weeks** | With approval (then trusted over time): issue refunds, start & approve returns, apply discount codes, edit or cancel orders. |

### 5.2 Four more capabilities — rounding it out (`S14`, `S15`)

| # | Capability | Est. | What it delivers |
|---|---|---|---|
| 1 | **Proactive & bulk handling** | **1–2 weeks** | Spot a delay hitting many orders, auto-post a Notice, and reply in bulk — before customers even ask. |
| 2 | **Multi-language at scale** | **1–2 weeks** | Detect, reply and learn per language — reliable Hebrew and others, not just English. |
| 3 | **Continuous-improvement engine** | **2–3 weeks** | Test reply variations, auto-surface gaps in the KB, and a weekly quality report. |
| 4 | **Scale & reliability** | **1–2 weeks** | Automatic retries, alerting if something breaks, and evaluating a faster / cheaper model. |

**Phase 3 total: ~3–4 months**, delivered feature-by-feature (`S15`).

### 5.3 Phase 3 architecture notes (for the build) (`S19`)

These are the deck's own technical notes for how each capability is intended to be built:

| Capability | Architecture note (deck) | Cross-check for the build |
|---|---|---|
| **Auto-send** | `= confidence score + classifier gate + per-intent switch + kill switch + full logging`. | The classifier gate is `processor/classifier.py` (Phase-2 Group A1; today a STUB, `DI#3`). Per-intent switches + kill switch + logging are net-new. Builds directly on the Group E pilot. |
| **New channels** | *Ride Gorgias' native chat / social / SMS integrations where possible.* | Keeps Gorgias as the hub; the agent already reads/writes Gorgias via `buttonsbebe_gorgias` (:8079) and `gorgias_writer.py`. New channels arrive as Gorgias tickets rather than new bespoke connectors. Requires the owner's channel logins (`S17` item 4). |
| **Action-taking** | *Refunds, returns, discounts, order edits `= new gated writes to Shopify & Redo` — today the only write is a Gorgias internal note.* | Introduces the **first external writes** to Shopify & Redo. Today those MCP tools are **read-only** (`CLAUDE.md §2`, §5); `redo_mcp.py`/`gorgias_mcp.py` expose reads only. Each action needs its own gate + approval, mirroring the auto-send pattern. Shopify writes also depend on the Phase-2 client-credentials work (A5). |
| **Multi-language** | *Builds on the existing multilingual KB embeddings; add per-language routing.* | Embeddings are already multilingual (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` via fastembed; `DEV-ISSUES` env notes). `feedback/language.py` already routes non-English (e.g. Hebrew) to manual (`SPR-FB`). Add per-language detect/reply/learn routing. |
| **Model evaluation** | *Test a less verbose / cheaper model than `glm-5.2` to cut cost & cleanup.* | Current model is `glm-5.2` via Ollama Cloud (`CLAUDE.md §3`); it is verbose (the root cause behind Group A2 draft cleanup, `DI#5`). A less chatty model reduces both cost and cleanup work. |

---

## 6. Overall program timeline & estimate assumptions

### 6.1 Roadmap at a glance (`S16`)

| Window | Phase | Headline |
|---|---|---|
| **NOW** | Phase 1 — Copilot | Live: AI drafts, human sends. |
| **Weeks 1–6** | Phase 2 — Trustworthy & Visible | Hardening · proven learning · owner dashboard · live order data · auto-send pilot. |
| **Months 2–5** | Phase 3 — Autonomous & Multi-channel | Auto-send graduation · new channels · refunds/returns actions · proactive · languages · scale. |

**Full program: ~4.5–6 months** of focused build after today — "with something new shipping almost every week" (`S16`).

### 6.2 What the estimates assume (`S19`)

- **One focused builder** (Tony + Claude), working **sequentially**.
- Estimates are **build + test time**; the ranges absorb testing & surprises.
- **Chaim's 5 policy answers and any channel/action logins arrive promptly.**
- **Scope stays fixed per item**; features can be re-ordered to taste.
- **Days = focused work-days, not calendar days; ~1 week ≈ 5 build-days.**

> These assumptions are consistent with the sprint docs' own capacity model — e.g. `SPR-NB` plans a one-day sprint to ~75–78% of the day to leave buffer for review; `SPR-FB` books ~2.2 of 3 days (73%). Treat the deck's day/week figures as *focused-work* budgets, then apply that same buffer to land calendar dates.

---

## 7. File cross-check summary (deck appendix `S18` vs this repo)

Every file the Phase-2 appendix names, and whether it is in this repo or VPS-only. This is the fastest way for a context-free team to see what they can open today vs. what to pull from the VPS.

| Deck item (`S18`) | Named target | Repo status |
|---|---|---|
| Safety net | `processor/classifier.py` (STUB→NORMAL) | ⚠️ **VPS-only** (no `processor/` dir in repo) — STUB per `DI#3`, `§8`. |
| Cleaner drafts | `processor/hermes_runner.py` | ⚠️ **VPS-only**. |
| Empty messages | ticket workflow: `hermes_runner.py` + `buttonsbebe` skill | ⚠️ **VPS-only** (skill at `~/.hermes/skills/buttonsbebe`). |
| Grounding | SOUL/prompt + `policies/international-orders.md` | SOUL `~/.hermes/SOUL.md` ⚠️ **VPS-only**; `kb/policies/` ✅ **in repo**. |
| Shopify | `config.py` (client-creds) | ⚠️ **VPS-only** (`webhook/…/config.py`, `processor/config.py`); pattern in `kb/scripts/sync_products.py` ✅ **in repo**. |
| Returns + toolset | `tools/redo_mcp.py` + Hermes `--yolo` | `redo_mcp.py` ✅ **in repo** (verified: **no `get_order`**); `hermes_runner.py` `--yolo` ⚠️ **VPS-only**. |
| Security / config | root `.env` + `webhook/.env`; `whatsapp-connect/server.js` | root `.env` ✅ (git-ignored); `webhook/.env` ⚠️ **VPS-only**; `whatsapp-connect/server.js` ✅ **in repo**. |
| Learning loop | `feedback/validate.py`, `feedback_collector.py` STUB, timer | `feedback/*` ✅ **in repo** (incl. `validate.py`); `processor/feedback_collector.py` + `buttonsbebe-kb-learn.timer` ⚠️ **VPS-only** (also see the two-design note in §3-B). |
| Dashboard | `webhook.db` + `KB/learned/_ledger.json` | both ⚠️ **VPS-only** ("no analytics code exists yet"); existing `dashboard/index.html`/`console-src/index.html` ✅ **in repo** but are the *current console*, not the new metrics screen. |

> **Bottom line for the new team:** most of Phase 2 Group A and all of the write-path work live in the **`processor/` and `webhook/` directories, which are not in this repo** — they must be obtained from the VPS (`/root/Buttonsbebe Agent/`) at handover (this matches the finding in `HANDOVER/05 §1`). The pieces you *can* open and work on locally today are the `feedback/` learning package, the `kb/` search + notices + scripts, `tools/redo_mcp.py`/`gorgias_mcp.py`, `whatsapp-connect/`, and the console/dashboard HTML.
