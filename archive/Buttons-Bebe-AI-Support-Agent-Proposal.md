# AI Customer Support Agent — Project Proposal

**Prepared for:** Chaim — Buttons Bebe
**Prepared by:** Tony
**Date:** 31 July 2026
**Rate:** $25 / hour
**Total programme:** 787–1,278 hours · **$19,675 – $31,950** · ~4.5–6 months

---

## 1. The situation

Buttons Bebe receives around **2,000 support tickets every month** through Gorgias. Most of them are the same handful of questions — where's my order, can I return this, does it come in another size, what are your shipping costs. Every one of those still takes a person's time to look up an order, check a policy, and write a reply.

The goal of this project is not to replace your support team. It's to stop them retyping the same answers, so their time goes to the tickets that actually need a human.

---

## 2. What we're building

An AI support agent that sits quietly behind Gorgias. For every ticket that comes in, it:

1. Reads the customer's message properly, in context
2. Looks up the real order, return and product details
3. Searches your policies, FAQs and past resolved tickets
4. Decides how risky the ticket is
5. Writes a ready-to-send draft reply

The draft appears in a private review screen. Your team reads it, edits it if needed, and clicks Send.

### The promise everything is built around

> **The AI writes. A person sends.**
>
> The AI has no ability to email a customer, issue a refund, cancel an order or change anything in Shopify. It can only read and suggest. Sensitive tickets — refunds, chargebacks, disputes, damaged or missing items, angry customers — are clearly flagged and alerted, never quietly handled.

This is a deliberate design choice, not a limitation we plan to remove. Autonomy only ever gets switched on later, one topic at a time, with your approval and a kill switch.

---

## 3. How it works, in plain terms

```
Customer emails you
      ↓
Gorgias receives it and tells our system
      ↓
The ticket goes on a queue (so nothing is ever lost or done twice)
      ↓
The AI reads the ticket, looks up the order, searches your policies
      ↓
It checks how sensitive the ticket is
      ↓
A draft reply appears in your private review screen
      ↓
Your team reads it → Send · Edit and send · Ask for a rewrite · Discard
      ↓
Whatever your team actually sent is captured as a lesson,
scrubbed of personal details overnight, and used to improve the next draft
```

Sensitive tickets additionally send a **WhatsApp alert to you** so nothing urgent sits waiting.

---

## 4. The three phases

| Phase | Name | What changes | Your team's role |
|---|---|---|---|
| **1** | **Copilot** | The AI drafts every reply. Everything is read-only. | Sends every reply |
| **2** | **Trustworthy and visible** | Same safety, but hardened, measurable, and provably learning week to week. | Still approves everything — now with proof and numbers |
| **3** | **Autonomous and multi-channel** | Routine tickets answer themselves, more channels, real actions. | Supervises the exceptions |

The AI earns more trust at each step. We do not skip ahead.

---

## 5. Phase 1 — Copilot

**208–342 hours · $5,200 – $8,550 · roughly 6–9 weeks**

This is the whole working product: from a blank server to a live agent drafting real replies.

### 5.1 What gets built

| Group | Work | Hours | Cost |
|---|---|---|---|
| **0** | **Access and answers** — accounts, API keys, historical ticket export, and the policy questions in §9 | 12–23 | $300 – $575 |
| **A** | **Server foundation** — private server, HTTPS, password-protected review screen, everything else locked away from the internet, secure handling of keys | 7–12 | $175 – $300 |
| **B** | **Knowledge base** — the AI's brain. ~22 answer guides, ~5 FAQ documents, ~18 policy documents, curated example tickets, a search engine that understands both exact terms and meaning (and handles Hebrew), a safe rebuild system, an automatic 3-day sync of your ~4,000 products, an owner Notice Board, and an editor so you can fix a policy yourself | 49–80 | $1,225 – $2,000 |
| **C** | **Data connections** — read-only links to Gorgias (tickets, customers, orders) and Redo (returns and refunds) | 9–14 | $225 – $350 |
| **D** | **The pipeline** — receiving tickets securely, a job queue with retries and duplicate protection, the processing loop, and the AI runtime with its instructions | 24–38 | $600 – $950 |
| **E** | **Safety** — an independent rule-based safety net that can only ever *raise* urgency, sensitive-ticket handling, strict "never invent a price or policy" grounding, and clean-up of messy AI output | 16–26 | $400 – $650 |
| **F** | **Review screen** — the private console your team works in: ticket feed, draft, warnings, sources, and the Send / Note / Rewrite / Discard buttons | 16–26 | $400 – $650 |
| **G** | **WhatsApp escalation** — pair your phone once by QR code, then get alerts on urgent tickets | 10–16 | $250 – $400 |
| **H** | **Learning loop** — capture what your team actually sent, scrub personal details, and feed the good replies back in overnight | 16–25 | $400 – $625 |
| **I** | **Testing** — an offline copy of Gorgias/Shopify/Redo so tests never touch real customers, safety tests that must never break, a replay of your real past tickets compared against what humans actually sent, and deliberate attempts to break it | 28–48 | $700 – $1,200 |
| **J** | **Operations and launch** — monitoring, backups, a kill switch, a plain-language runbook, production cutover, and training your reviewer | 21–34 | $525 – $850 |
| | **Phase 1 total** | **208–342** | **$5,200 – $8,550** |

### 5.2 Phase 1 explicitly does NOT include

- Sending anything to a customer automatically
- Automatic refunds, cancellations or order changes
- Any write access to Shopify or Redo
- Replacing Gorgias
- A performance dashboard (that's Phase 2)

### 5.3 Phase 1 is finished when

- Every customer-facing reply was sent by a person
- Shopify and Redo remain read-only; the only writes are your team's own clicks
- Every ticket produces a draft, and sensitive ones are clearly marked, prioritised and alerted
- Zero missed sensitive tickets across the historical replay and the break-it testing
- The agent never reacts to its own notes, and duplicate events are ignored
- Missing order data produces a safe answer, never an invented one
- Every price, date and policy in a draft traces back to a real source document
- Hebrew tickets are answered correctly or escalated — never answered badly
- A captured lesson provably changes the next draft
- Personal details are masked before anything is reused, and every stored example can be reviewed and deleted
- Backups restore, the kill switch works, and your reviewer has been walked through the runbook

---

## 6. Phase 2 — Trustworthy and visible

**159–236 hours · $3,975 – $5,900 · roughly 4–6 weeks**

Same safety promise. The difference is that it becomes hard to break, and you can *see* it working.

| Group | Work | Hours | Cost |
|---|---|---|---|
| **A** | **Rock-solid foundation** — turn the safety net from a second opinion into a proven gate; cleaner drafts; handle blank and junk messages; a hard rule against quoting any price not in your policies; finish the Shopify connection; lock down exactly what the AI is allowed to touch; tidy and rotate credentials | 56–80 | $1,400 – $2,000 |
| **B** | **Learning loop proven ON** — run it across 10+ real tickets and demonstrate, before and after, that a captured lesson actually changed the next draft | 30–40 | $750 – $1,000 |
| **C** | **Owner dashboard** — a new screen showing how often drafts are sent as-is, tickets handled, hours saved, your top customer topics, and escalations | 35–60 | $875 – $1,500 |
| **D** | **Live order and shipping status** — real-time order status and tracking link inside the draft, so "where is my order?" answers itself accurately | 8–16 | $200 – $400 |
| **E** | **Auto-send pilot, one safe topic** — let the AI send its own reply for order-status questions only, behind a confidence check, the safety net, full logging and a one-click kill switch. Sensitive tickets excluded by design | 30–40 | $750 – $1,000 |
| | **Phase 2 total** | **159–236** | **$3,975 – $5,900** |

Group E is the bridge into Phase 3 and is the first thing to cut if you'd rather not move that fast.

---

## 7. Phase 3 — Autonomous and multi-channel

**420–700 hours · $10,500 – $17,500 · roughly 3–4 months, in stages**

Each capability goes live and proves itself before the next one starts. Sensitive tickets always go to a human, at every stage.

| Work | What it delivers | Hours | Cost |
|---|---|---|---|
| **Auto-send graduation** | Expand from one safe topic to a growing set, each with its own on/off switch, confidence threshold and monitoring | 70–105 | $1,750 – $2,625 |
| **Multi-channel** | Website live chat, Instagram and Facebook DMs, SMS/WhatsApp — not just email tickets | 70–140 | $1,750 – $3,500 |
| **Action-taking** | With your approval: issue refunds, start and approve returns, apply discount codes, edit or cancel orders. These are the first outward changes the system can make, so each one gets its own gate | 105–140 | $2,625 – $3,500 |
| **Proactive and bulk handling** | Spot a delay hitting many orders, post a notice automatically, and reply in bulk — before customers ask | 35–70 | $875 – $1,750 |
| **Multi-language at scale** | Detect, reply and learn per language — reliable Hebrew and others | 35–70 | $875 – $1,750 |
| **Continuous improvement** | Test reply variations, automatically surface gaps in your knowledge base, weekly quality report | 70–105 | $1,750 – $2,625 |
| **Scale and reliability** | Automatic retries, alerting when something breaks, evaluate a faster and cheaper model | 35–70 | $875 – $1,750 |
| | **Phase 3 total** | **420–700** | **$10,500 – $17,500** |

---

## 8. Investment summary

| Phase | Hours | Cost at $25/hr | Duration |
|---|---|---|---|
| Phase 1 — Copilot | 208–342 | **$5,200 – $8,550** | ~6–9 weeks |
| Phase 2 — Trustworthy and visible | 159–236 | **$3,975 – $5,900** | ~4–6 weeks |
| Phase 3 — Autonomous and multi-channel | 420–700 | **$10,500 – $17,500** | ~3–4 months |
| **Total programme** | **787–1,278** | **$19,675 – $31,950** | **~4.5–6 months** |

Billed hourly against work actually done, so the low end of each range is a real possibility, not a sales number. Suggested billing rhythm: **invoiced every two weeks against logged hours**, with a written progress note each time so you always know what you paid for.

Each phase is a separate decision. You can stop after Phase 1 and still have a working, valuable product.

### Running costs (not included above)

These are third-party services billed to you directly, not through me:

| Item | What it's for |
|---|---|
| Server | Runs everything. A modest VPS is sufficient. |
| AI writing model | Charged per ticket processed. The single largest running cost — we evaluate a cheaper model in Phase 3. |
| Search / embeddings | **$0** — runs locally on your own server. No per-search cost and no customer text leaves the box. |
| Gorgias, Shopify, Redo | Your existing subscriptions. No new plan needed. |

I'll confirm current pricing for the server and model before you commit, since both change often.

---

## 9. What I need from you

The build cannot produce correct answers without these. They're the main thing that can delay the project.

### Access

- Gorgias: a dedicated support-agent login, an API key, and permission to create webhooks
- Shopify: a custom app with read access to products, orders and customers
- Redo: API key and store ID
- A historical export of past tickets (I'll do the export; I need the permission)

### Five policy answers

These are the questions your current documentation doesn't answer clearly, and every one of them changes what the AI tells a customer:

1. **Return window** — how many days does a customer have to return?
2. **Return postage** — who pays for return shipping?
3. **Sale items** — are sale and clearance items final, or returnable?
4. **International shipping** — what is the real rate outside the country?
5. **Pickup vs return bin** — confirm the pickup address *and* the separate 24/7 return-bin address, so the AI never mixes them up

### Source material

Your written shipping, return and refund policies, your FAQs, your saved reply macros, and a handful of resolved tickets you'd consider a perfect answer.

### One named reviewer

A person on your side who reviews drafts during the pilot and tells me when one is wrong. This is the single biggest factor in how fast quality improves.

---

## 10. Assumptions

- One focused builder working sequentially through the plan
- Hour ranges are build **and** test time; the ranges already absorb the usual surprises
- Access and the five policy answers arrive promptly — every week of delay is a week of delay
- Scope stays fixed per item; the order of items can be rearranged freely
- Estimates are in focused working hours, not calendar hours — plan calendar dates at roughly 75% efficiency
- Gorgias remains the help desk. Building a replacement for it is a separate project and is not part of this proposal

---

## 11. Risks and how they're handled

| Risk | How it's handled |
|---|---|
| The AI sends something wrong to a customer | It structurally cannot. It has no send capability. Only your team's click sends anything. |
| It quotes a price or policy that isn't real | Hard grounding rule: it may only use what the knowledge base returned, and it must escalate when it finds nothing relevant. Tested explicitly. |
| It mishandles a refund or an angry customer | Two independent checks flag sensitive tickets — the AI's own judgement plus a separate rule-based net that can only ever raise urgency. Both must fail silently for one to slip through, and the historical replay tests exactly this. |
| Customer personal data leaks into the AI's memory | Nothing is reused until personal details are masked, and every stored example stays reviewable and deletable. |
| Your policies change and the AI gives stale answers | The Notice Board lets you post an override that takes effect immediately, plus an editor to fix the underlying policy yourself. |
| The whole thing misbehaves | One-click kill switch, backups, and a plain-language runbook for the ten most likely failures. |

---

## 12. Next steps

1. Confirm Phase 1 scope and give the go-ahead
2. Send over the access listed in §9
3. Answer the five policy questions
4. Name your reviewer
5. Work starts on the knowledge base and server foundation in week one

---

*Prepared by Tony · 31 July 2026 · All figures are estimates in ranges and billed against actual hours worked.*
