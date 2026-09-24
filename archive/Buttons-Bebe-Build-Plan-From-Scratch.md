# Buttons Bebe AI Support Agent — Build Plan (assuming nothing is built yet)

**Short answer: no, the old list was not right.** It described a smaller, simpler product than the one this project actually is. This version replaces it and matches the real system.

---

## What was wrong with the previous list

| # | The old list said | What this project actually does |
|---|---|---|
| 1 | AI writes an internal note in Gorgias, human reads it there | AI writes a draft into **our own private Support Console** (a password-protected web page). A human clicks **Send / Internal note / Ask for a rewrite / Discard**. The AI never posts anything by itself. |
| 2 | Sensitive tickets get escalated **instead of** drafted | **Every** ticket gets a draft. Sensitive ones get a clearly marked draft, a high review priority, and an alert to the owner. Nothing is hidden from the reviewer. |
| 3 | Escalate over Telegram (or WhatsApp with template approval) | We escalate over **WhatsApp**, using our own small service that pairs the owner's phone by QR code. No Telegram. No business-template approval, so no 5-day wait. |
| 4 | No mention of Redo | **Redo** is the store's returns/refunds service and is a core read-only data source. It needs its own connection and tool. |
| 5 | "Add Shopify order context" as the main Shopify job | Shopify's main Phase 1 job is **syncing the ~4,000-product catalogue into the knowledge base every 3 days**. Order/customer context comes from Gorgias, returns from Redo. Live order + tracking status inside drafts is **Phase 2**. |
| 6 | "Configure LLM and embedding API keys" | Only the writing model is a paid remote service. The **search/embedding model runs locally on our own server for free** — no key, and no customer text leaves the box. |
| 7 | One task: "prepare the knowledge base, 4–8 hours" | The knowledge base is the **biggest** part of the build: ~22 how-to-answer guides, ~5 FAQ docs, ~17 policy docs, example tickets, a custom hybrid search engine, a safe index-rebuild system, and the 4,000-product sync. |
| 8 | Nothing about a Notice Board | The owner can post a short notice ("free shipping this week") that **instantly overrides everything else the AI knows**, with an expiry date. This is a real Phase 1 feature. |
| 9 | Nothing about editing the knowledge base | There's an **admin API + editor** so the owner can fix a policy and re-index without a developer. |
| 10 | Nothing about a learning loop | Every human decision is captured as a lesson, personal details are scrubbed overnight, and good replies are promoted back into the knowledge base so drafts improve weekly. |
| 11 | Nothing about the queue and processor | The real chain is: webhook → **job queue** → **processor loop** → AI run → draft saved → console. Retries, duplicate protection and stuck-job recovery all live here. |
| 12 | Nothing about Hebrew | The store gets Hebrew tickets. That's the whole reason the search model is multilingual. |
| 13 | "Gorgias sandbox" as the main test bed | Real testing is **offline fakes + replaying real historical tickets** and comparing our draft to the reply a human actually sent. A sandbox is optional, not the backbone. |
| 14 | Phase 3 = "build our own help desk to replace Gorgias" | That's a **separate side track** (internally called *Fable*), not the roadmap's Phase 3. The real Phase 3 is **autonomy + more channels + taking real actions**. |
| 15 | Phase 1 ≈ 20–65 hours | From a truly blank server, Phase 1 is **≈ 210–340 focused hours (about 6–9 weeks for one builder)**. |

---

## The one rule everything is built around

> The AI **drafts**. A human **sends**. Every connection except Gorgias is read-only, and even Gorgias is only written to when a person clicks a button.

---

## Phase 0 — Access and answers (blockers)

Nothing else can start properly without these.

| # | Task | Main work | Estimated time |
|---|---|---|---|
| 0.1 | Kickoff and scope confirmation | Languages (English + Hebrew), tone of voice, who reviews drafts, working hours, escalation contact, what "good" looks like. | 2–4 hours |
| 0.2 | **Get the 5 policy answers from the client** | Return window in days · who pays return postage · are sale/clearance items final · the real international shipping rate · the pickup address vs the 24/7 return-bin address. Wrong answers here = wrong replies to customers. | 2–4 hours (client time) |
| 0.3 | Gorgias access | Dedicated support-agent login, API key, permission to create webhooks. | 1–2 hours |
| 0.4 | Shopify access | Custom app using client credentials (ID + secret) with product read access; order/customer read for later. | 1–2 hours |
| 0.5 | Redo access | API key + store ID for the returns service. | 1–2 hours |
| 0.6 | Writing-model account | Cloud account and key for the model that writes drafts. (Search model is local and free.) | 1 hour |
| 0.7 | Historical ticket export | Pull past tickets out of Gorgias — used both to seed the knowledge base and to test against. | 2–4 hours |
| 0.8 | Collect source material | Shipping/return/refund policies, FAQs, saved reply macros, best resolved-ticket examples. | 2–4 hours (client time) |

**Phase 0 total: 12–23 hours** (much of it waiting on the client).

---

## Phase 1 — Copilot (AI drafts, human sends)

### Group A — Server foundation

| # | Task | Main work | Estimated time |
|---|---|---|---|
| 1 | Provision the server | Ubuntu VPS, users, firewall, Python + Node + SQLite + web server, folder layout. | 2–4 hours |
| 2 | Public entrance and lockdown | HTTPS certificate; only the webhook and health checks reachable from the internet; console behind a password; every other address returns "not found"; request size cap; access logs. | 3–5 hours |
| 3 | Secrets and configuration | One clean config layout, generated signing secrets for the webhook, console and alerts, correct file permissions, no secrets in code. | 2–3 hours |

### Group B — Knowledge base (the biggest piece)

| # | Task | Main work | Estimated time |
|---|---|---|---|
| 4 | Design the content model | Folders for how-to-answer guides, FAQs, policies, example tickets, products, notices and raw lessons. Rules for how a document is split into searchable pieces, and how "sensitive" topics get tagged. | 3–5 hours |
| 5 | **Write the actual content** | ~22 how-to-answer guides, ~5 FAQ documents, ~17 policy documents, ~5 curated example tickets. Slowest non-code task and the one that decides reply quality. | 12–20 hours |
| 6 | Build the search engine | Local search database combining exact keyword matching with meaning-based matching, blended into one ranked result. Multilingual so Hebrew works. Runs entirely on our server, no per-search cost. | 8–12 hours |
| 7 | Safe index rebuilding | Build the new index to the side, check it, then swap it in. Restore the old one if anything fails. Searches must never see a half-built index. | 6–10 hours |
| 8 | Shopify product sync | Mint a short-lived token, bulk-export the catalogue, write ~4,000 product pages, delete removed products, rebuild the index. Runs automatically every 3 days. | 6–10 hours |
| 9 | Search service | Always-on local service exposing one read-only "search the knowledge base" capability to the AI, warmed up so the first ticket isn't slow. | 3–5 hours |
| 10 | Notice Board | Owner posts a short override notice that beats everything else instantly, with an optional expiry. No rebuild needed. Expired notices cleaned up automatically. A broken notice must never break search. | 5–8 hours |
| 11 | Knowledge-base editor | Admin service + screen so the owner can open a policy, fix it, save it and re-index — without a developer. Products and raw lessons are deliberately not editable here. | 6–10 hours |

### Group C — Read-only data connections

| # | Task | Main work | Estimated time |
|---|---|---|---|
| 12 | Gorgias reader | Read ticket, messages, customer and order context. Read-only, correct pagination, custom user agent so the firewall doesn't block us. | 4–6 hours |
| 13 | Redo reader | Look up returns and refunds by order, plus recent returns. Read-only. | 3–5 hours |
| 14 | Lock the toolset | Write down exactly which capabilities the AI may call, make the names match the instructions exactly, and make sure the AI never receives raw credentials or a way around the tools. | 2–3 hours |

### Group D — The pipeline

| # | Task | Main work | Estimated time |
|---|---|---|---|
| 15 | Webhook receiver | Accept the signal from Gorgias, verify it's genuinely signed, ignore duplicates, ignore our own internal notes and agent messages, drop a job on the queue. | 5–8 hours |
| 16 | Job queue | Small local database of tickets to process — statuses, retry counts, and recovery of jobs stuck longer than ~10 minutes. | 4–6 hours |
| 17 | Processor loop | Background service that picks up one job at a time, runs the AI once, records the outcome, retries on failure, logs everything. | 5–8 hours |
| 18 | AI runtime and instructions | Install the agent runtime, point it at the writing model, register the three read-only tools, and write its behaviour file plus the step-by-step ticket workflow. | 6–10 hours |
| 19 | Draft output and storage | Fixed output shape (the draft plus a small structured result), saved against the ticket. The AI reports that it posted nothing and changed nothing — and that must stay true. | 4–6 hours |

### Group E — Safety

| # | Task | Main work | Estimated time |
|---|---|---|---|
| 20 | Rule-based safety net | Independent keyword/pattern check that runs alongside the AI's own judgement. It can only ever **raise** urgency, never lower it. Levels: immediate / high / normal. | 6–10 hours |
| 21 | Sensitive-ticket handling | Refunds, chargebacks, disputes, damaged/wrong/missing items, cancellations, angry customers, payment problems: clearly marked draft, elevated review priority, owner alert. | 3–5 hours |
| 22 | Grounding rules | Answer only from what search returned; never invent a price, date or policy; escalate when nothing relevant is found; obey the Notice Board — but never let a notice override a safety rule. | 3–5 hours |
| 23 | Draft cleanup and edge cases | Strip the model's own commentary, remove duplicated blocks, handle blank/auto-reply/survey messages, and handle tickets whose only content is an attached photo. | 4–6 hours |

### Group F — The human review surface

| # | Task | Main work | Estimated time |
|---|---|---|---|
| 24 | Support Console | The private web page the team lives in: ticket feed, the draft, the sensitive warning, the sources used, a knowledge-base panel, the notice panel, connection status. | 10–16 hours |
| 25 | Console actions | Send reply (with a confirmation click), post as internal note, ask the AI for a rewrite, discard. Password-protected, human-triggered only — these are the **only** writes in the whole system. | 6–10 hours |

### Group G — Escalation

| # | Task | Main work | Estimated time |
|---|---|---|---|
| 26 | WhatsApp alert service | QR pairing page so the owner links their phone once, saved session, authenticated send endpoint, optional two-way replies. | 8–12 hours |
| 27 | Wire alerts to the pipeline | Processor sends urgent-ticket alerts using its own separate secret; sensible alert wording and no alert storms. | 2–4 hours |

### Group H — Learning loop

| # | Task | Main work | Estimated time |
|---|---|---|---|
| 28 | Capture every decision | Each console action saves a lesson (the situation, our draft, what the human actually sent, whether it was edited) plus a running tally. | 4–6 hours |
| 29 | Privacy scrub | Mask emails, phone numbers, order and tracking numbers, addresses and names before anything is reused. Assume it's imperfect and keep everything reviewable and deletable. | 4–6 hours |
| 30 | Nightly promotion | Overnight job turns approved replies into indexed examples the AI can mirror, then rebuilds the index. | 5–8 hours |
| 31 | Prove it works | Automated check that a newly promoted lesson is actually found when its own question is asked. Nothing counts as "learning" until this passes. | 3–5 hours |

### Group I — Testing

| # | Task | Main work | Estimated time |
|---|---|---|---|
| 32 | Offline test rig | Fake Gorgias, Shopify and Redo so tests run fully offline and can never touch the real store or a real customer. | 8–14 hours |
| 33 | Safety tests that must never break | Sensitive ticket is never auto-sent · the AI never writes anywhere · it never replies to its own notes · duplicate webhooks are ignored · missing order data degrades gracefully · a tool outage doesn't produce a confident wrong answer. | 6–10 hours |
| 34 | Replay real history | Run the agent over exported past tickets and compare each draft to the reply the human actually sent. Score them and fix the gaps. | 8–14 hours |
| 35 | Adversarial testing | Hebrew and mixed-language messages, indirect refund requests ("this isn't what I ordered"), angry customers, photo-only evidence, wrong or stale data, provider outages, webhook floods. | 6–10 hours |

### Group J — Operations and launch

| # | Task | Main work | Estimated time |
|---|---|---|---|
| 36 | Health and monitoring | Health/readiness checks, structured logs, and alerts for queue backlog, failed services, missed scheduled jobs and a stale index. | 4–6 hours |
| 37 | Backups | Back up the queue, knowledge base, lessons, notices and the WhatsApp session — and actually practise restoring them. | 3–5 hours |
| 38 | Kill switch and runbook | One-click pause, clear restart order, and a plain-language guide to the ten most likely failures. | 3–5 hours |
| 39 | Deploy to production | Install services and schedules, cut the real webhook over, verify end to end. | 5–8 hours |
| 40 | Train the team and pilot | Train the reviewer, hand over the runbook, require human approval on every reply, watch the first live tickets closely. | 6–10 hours + 3–7 days observation |

### Phase 1 estimate

| | Hours |
|---|---|
| Phase 0 — access and answers | 12–23 |
| A — server foundation | 7–12 |
| B — knowledge base | 49–80 |
| C — read-only connections | 9–14 |
| D — pipeline | 24–38 |
| E — safety | 16–26 |
| F — review console | 16–26 |
| G — escalation | 10–16 |
| H — learning loop | 16–25 |
| I — testing | 28–48 |
| J — operations and launch | 21–34 |
| **Total** | **≈ 208–342 hours** |

That's roughly **26–43 focused build-days**, or **6–9 weeks for one builder** — plus 3–7 days of supervised pilot. Note "focused day" ≠ calendar day; plan calendar dates at about 75% of that.

---

## What Phase 1 must NOT include

- Automatic replies to customers
- Automatic refunds, cancellations or order edits
- Any write to Shopify or Redo
- Suppressing a draft on sensitive tickets (we mark it, we don't hide it)
- Replacing Gorgias
- A performance/analytics dashboard (that's Phase 2)

---

## Phase 1 acceptance criteria

Ready to launch only when all of these are true:

- Every customer-facing reply is sent by a human.
- Shopify and Redo are read-only; the only writes are human-clicked Gorgias actions.
- Every ticket produces a draft; sensitive ones are clearly marked, prioritised and alerted.
- Zero missed sensitive tickets across the historical replay and adversarial tests.
- The agent never reacts to its own notes, and duplicate webhook events are ignored.
- Missing or unavailable order data produces a safe answer, never an invented one.
- Prices, dates and policies in drafts can be traced back to a knowledge-base source.
- Hebrew tickets are handled correctly or escalated — never answered badly.
- A promoted lesson provably changes the next draft.
- Personal details are masked before anything is reused, and every promoted example can be reviewed and deleted.
- Backups restore, the kill switch works, and the runbook has been walked through with the reviewer.

---

## Phase 2 — "Trustworthy and visible" (~4–6 weeks)

Still AI drafts, human approves — but harder to break, and now measurable.

| Group | Task | What it delivers | Estimated time |
|---|---|---|---|
| A | Rock-solid foundation | Turn the safety net from advisory into a proven gate; cleaner drafts; blank/junk message handling; a hard "never invent a price" rule; finish the Shopify connection; fix any tool-name mismatches and lock the toolset; consolidate config and rotate keys. | 8–10 days |
| B | Learning loop proven ON | Run it on 10+ real tickets and prove, before/after, that a captured lesson changed the next draft. | ~1 week |
| C | Owner performance dashboard | New screen: how often drafts are sent as-is, tickets handled, hours saved, top topics, escalations. | 1–1.5 weeks |
| D | Live order and shipping status | Real-time order status and tracking link inside the draft, so "where is my order?" answers itself accurately. | 1–2 days |
| E | Auto-send pilot — one safe topic *(stretch)* | Let the AI send its own reply for order-status questions only, behind a confidence check, the safety net, full logging and a one-click kill switch. Sensitive tickets excluded by design. | ~1 week |

**Phase 2 total: ~4–6 weeks** (core A–D ≈ 4 weeks; E adds ~1 week and is the first thing to cut).

---

## Phase 3 — "Autonomous and multi-channel" (~3–4 months, in stages)

One capability at a time, each live and stable before the next starts.

| Task | What it delivers | Estimated time |
|---|---|---|
| Auto-send graduation | Expand from one safe topic to a growing set, each with its own switch, confidence threshold and monitoring. | 2–3 weeks |
| Multi-channel | Website live chat, Instagram and Facebook DMs, SMS/WhatsApp — riding Gorgias's own channel integrations where possible. | 2–4 weeks |
| Action-taking | With approval: issue refunds, start and approve returns, apply discount codes, edit or cancel orders. **These are the first outward writes — each needs its own gate.** | 3–4 weeks |
| Proactive and bulk handling | Spot a delay affecting many orders, auto-post a Notice, and reply in bulk before customers ask. | 1–2 weeks |
| Multi-language at scale | Detect, reply and learn per language — reliable Hebrew and others. | 1–2 weeks |
| Continuous improvement | Test reply variations, auto-surface knowledge gaps, weekly quality report. | 2–3 weeks |
| Scale and reliability | Automatic retries, alerting on breakage, evaluate a faster/cheaper model. | 1–2 weeks |

---

## A note on the "custom help desk" idea

Building our own help desk to replace Gorgias is a **separate side project**, not Phase 3. It exists as an offline rebuild track and would be another 60–120 hours minimum on top of everything above. Decide it on its own merits (cost saving vs. rebuilding a mature product), not as a roadmap step.

---

## Programme summary

| Window | Phase | Headline |
|---|---|---|
| Weeks 1–9 | Phase 1 — Copilot | AI drafts every reply from read-only data; a human sends every one |
| Weeks 10–15 | Phase 2 — Trustworthy and visible | Hardened, provably learning, measurable, live order data |
| Months 4–7 | Phase 3 — Autonomous and multi-channel | Auto-send graduation, new channels, real actions, proactive handling |
