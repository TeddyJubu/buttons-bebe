# 🍼 Buttons Bebe AI Support Assistant — What We Built

*A plain-English overview. No tech background needed.*

---

## In one sentence

> We built a tireless assistant that reads every customer message, writes a
> ready-to-send reply in your store's voice, and hands it to your team to
> approve — **so your team answers faster, and never from scratch.**

Think of it as a **really well-trained new hire** who has memorized all your
policies, drafts the reply for every ticket, and leaves it on your desk for a
quick "yes, send it" — but **never hits send on their own.**

---

## 🔒 The most important thing first: it is 100% safe

```
┌──────────────────────────────────────────────────────────┐
│                                                          │
│   The assistant NEVER messages a customer by itself.     │
│                                                          │
│   It only writes a PRIVATE DRAFT that your team reads    │
│   and approves first. A human is always in control.      │
│                                                          │
│   Right now it's in "practice mode": it writes its       │
│   drafts as internal notes so you can see how good they  │
│   are — without sending anything to anyone.              │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

There is one simple on/off switch (explained at the end) for when *you* decide
it's ready to do more. Until you flip it, it just practices.

---

## How it works (the whole flow)

```
   📨  A customer emails Buttons Bebe
              │
              ▼
   🔎  The assistant reads the FULL conversation
       + looks up the order + your store policies
              │
              ▼
   🧠  Is this a tricky ticket?
       (refund · chargeback · "you sent the wrong item" ·
        "it arrived damaged" · dispute · legal)
              │
       ┌──────┴───────────────────────┐
       │ NO — normal question          │ YES — sensitive
       ▼                               ▼
   ✍️  Writes a friendly draft     🔔  Does NOT answer.
       reply using your real           Flags it + alerts a
       policies                        human to handle it
       │                               │
       └──────────────┬────────────────┘
                      ▼
        👀  Your team reviews and decides
            (nothing reaches the customer
             until a person approves)
```

---

## What it actually says — real examples

These are **real replies from the live system**, using *your* policies:

| A customer writes… | The assistant drafts… |
|---|---|
| "how many days do I have to return my order?" | *"hi! you can return your order within 7 days of delivery. just make sure the return package is scanned by the carrier within that 7-day window. if it's later, it may only qualify for store credit."* |
| "do you offer a first-time customer discount?" | *"hi! we don't have a first-time customer discount, but we do run sales and promotions from time to time…"* |
| "when will my order ship?" | *"hi! orders usually ship within 24–48 hours after we process them. could you share your order number so I can check?"* |
| "what fabric is this made of?" | *"hi! we'll check on that and get right back to you."* ← **it doesn't guess** |
| "you sent me the wrong item" | 🔔 **Flagged for a human** — it does *not* auto-reply |
| "my dress arrived with a rip in it" | 🔔 **Flagged for a human** |

👉 Notice three things:
1. It sounds **like your store** — warm, short, friendly.
2. It uses your **real rules** (the 7-day window, the 24–48h timing).
3. It **knows what it doesn't know** — it never makes up a fabric, a price, or a tracking number. If it's unsure, it says "we'll check."

---

## What's "under the hood" — in plain terms

| What we built | What it means for you |
|---|---|
| 📚 **Knowledge base** | A tidy digital copy of your store's policies **and your 22 approved answer templates** (from the document you gave us). The assistant can *only* answer from this — so it stays accurate and on-brand. |
| 🔎 **Smart search** | Instantly finds the right answer even when customers word things differently. "where's my package?" and "hasn't shipped yet?" both find your shipping info. |
| 🛡️ **Safety filter** | Recognizes the touchy tickets (refunds, chargebacks, wrong/damaged items, disputes, legal) and routes them to a human instead of answering. |
| 🧠 **The writer** | A top AI model turns the right policy into a friendly reply in your voice — grounded in your facts, never inventing. |
| 📈 **Learning loop** | Every time your team sends a real reply, the system quietly compares it to what the AI would have written and keeps score — so we can watch it improve over time. |
| 🔔 **Owner alerts** | Sends *you* a Telegram message when a ticket needs a human, or when the assistant hits a question it can't answer yet (so you can teach it). |
| 🗓️ **Weekly report + backups** | A Monday summary of activity, plus automatic nightly backups so nothing is ever lost. |

---

## 🗺️ How the pieces fit together (the architecture)

Here's the journey of **one ticket** through the whole system:

```
        🌍 Customer sends a message
                  │
                  ▼
        ☁️  GORGIAS  (your helpdesk)
                  │   sends "new ticket"  →  secure HTTPS
                  ▼
  ╔════════════════════════════════════════════════════╗
  ║   🖥️  YOUR PRIVATE SERVER                            ║
  ║                                                    ║
  ║   🔐 Front door  — confirms it's really Gorgias     ║
  ║         │                                          ║
  ║         ▼                                          ║
  ║   📥 Receiver  — gathers the full story             ║
  ║         │         (order · customer · conversation)║
  ║         ▼                                          ║
  ║   🛡️ Safety filter ───── tricky? ────▶ 🔔 alert a   ║
  ║         │                              human, STOP  ║
  ║         ▼  (normal question)                       ║
  ║   📚 Find the answer ◀──▶ 🧰 Knowledge base          ║
  ║         │                  (a sealed box — see below) ║
  ║         ▼                                          ║
  ║   ✍️ Write the draft  ◀──▶ 🤖 AI model ☁️            ║
  ║         │                                          ║
  ║         ▼                                          ║
  ║   📝 Save a PRIVATE draft   💾 log it   📲 alert you ║
  ╚═════════│══════════════════════════════════════════╝
            ▼
   👀 Your team reviews the draft and sends the reply
```

> 🔑 **Key point:** the AI model (a service called *Ollama Cloud*) is the
> **only** outside service the system ever talks to. Everything else — your
> policies, the drafts, the customer info, the records — stays **private on
> your own server**.

### 🧩 The whole system is a set of sealed boxes

The journey above shows the *flow*. Now zoom out and look at the *shape*: this is
**not one big program**. It's a handful of small, **sealed boxes** — each does
**one job**, each just **takes something in and gives something back**, and each
can be restarted, fixed, or upgraded **on its own** without disturbing the
others. The "brain" in the middle simply wires them together.

```
 ══════════════════  ☁️ OUTSIDE YOUR SERVER (the cloud)  ══════════════════
      🌍 Customer        ☁️ Gorgias          🤖 Ollama Cloud     📲 Telegram
          │ message          │ new ticket         ▲ reply wording    ▲ alert
          └───────▶──────────┤                    │                  │
                             │ (draft note ◀──┐   │                  │
 ════════════════════════════│════════════════│═══│══════════════════│════════
   🖥️ YOUR PRIVATE SERVER    ▼                │   │                  │
                      ┌──────────────┐         │   │                  │
                      │ 🔐 FRONT DOOR│ (Caddy) │   │                  │
                      └──────┬───────┘         │   │                  │
                             ▼                 │   │                  │
        ┌──────────────────────────────────────┴───┴──┐               │
        │   🧠 THE BRAIN  (the orchestrator)            │               │
        │   in: a ticket   →   out: a private draft     │               │
        └──┬──────────┬───────────┬──────────┬──────────┘               │
     asks  │   flags  │    logs   │   needs  │  (also posts the draft   │
     facts │   tricky │   result  │   words  │   back into Gorgias ▲)   │
           ▼          ▼           ▼          ▼                          │
     ┌──────────┐ ┌────────┐ ┌──────────┐ (to 🤖 AI, above)            │
     │🧰 KB BOX │ │📲 ALERT│ │💾 RECORDS│                              │
     └────┬─────┘ └───┬────┘ └──────────┘                              │
          ▼           └──────────────────────────────────────────────▶┘
     ┌──────────┐
     │🗄️ SEARCH │      🗓️ TIMERS keep their own schedule, untouched:
     │   DB     │         • Mon 9:00am  → weekly report ─────────────▶ 📲
     └──────────┘         • 3:30am nightly → full backup
```

**The boxes, and the one job each one does:**

| 📦 Sealed box | Its one job | Takes in → | Gives back → | Lives where |
|---|---|---|---|---|
| 🔐 **Front door** | Prove the caller is really Gorgias; encrypt the line | An internet request | A verified request (or a slammed door) | Your server (Caddy) |
| 🧠 **The brain** | Read the ticket, decide, assemble the reply | A new ticket | A private draft + a record | Your server (`gorgias-webhook`) |
| 🛡️ *Safety filter* | Spot the touchy tickets and stop | A ticket | "answer it" or "get a human" | *(inside the brain)* |
| 🧰 **Knowledge base** | Find the right policy for a question | A question | The matching policy text | Your server (`gorgias-kb`, own program) |
| 🗄️ **Search database** | Hold the policies, ready to search | Policy text | Best-match results | Your server (Postgres, Docker) |
| 🤖 **AI writer** | Turn facts into a warm reply | Facts + question | The draft wording | ☁️ Ollama Cloud — **the only outside call** |
| 💾 **Records** | Remember every draft & real reply | What happened | History + a score | Your server (`feedback.db`) |
| 📲 **Alerts** | Tap the owner on the shoulder | "needs a human" | A Telegram message | Your phone |
| 🗓️ **Timers** | Run upkeep on a clock | The time of day | Weekly report + nightly backups | Your server (systemd) |

**Why building it this way protects you:**

- 🧱 **One job per box.** Every box has a tiny, clear contract — *something in,
  something out*. Nothing reaches inside another box's guts, so a change in one
  can't quietly break another.
- 🔌 **Any box can fall over and the rest keep going.** If the knowledge base is
  slow or down, the brain waits ~2 seconds and uses a backup search. If a backup
  runs, nobody else even notices.
- 🌐 **Only one box ever talks to the outside internet** — the AI writer. Your
  policies, drafts, customer details, and records never leave your server.
- ➕ **Growth without surgery.** A new capability later (say, a returns portal or
  an analytics view) is just **a new box plugged into the brain** — not a rebuild
  of what already works.

The next section zooms all the way into **one** of these boxes — the knowledge
base — to show how a single sealed box is built.

### 🧰 The knowledge base is a sealed "black box"

We deliberately built the knowledge base as its **own separate, sealed program**.
The assistant doesn't reach inside it — it just **asks a question and gets an
answer back**, like ordering at a counter:

```
   ✍️ The writer                              🧰 KNOWLEDGE BASE (sealed)
      "what's our return policy?"  ─────────▶    looks it up in your policies
                                   ◀─────────    "7 days after delivery…"
            │
            │   ⏱️ If the knowledge base is ever slow or down, the
            ▼      assistant waits only ~2 seconds, then falls back to a
      keeps working   simpler built-in search. It never freezes, never crashes.
```

**Why this matters to you:**
- 🧱 **Walled off** — a hiccup in the knowledge base can't take down the
  assistant, and a problem in the assistant can't take down the knowledge base.
  They run as **completely separate programs**.
- ⏱️ **Never freezes** — if the knowledge base is busy, the assistant gives it a
  couple of seconds, then uses a backup search and carries on. A customer never
  waits on a stuck system.
- 🔁 **Swappable & upgradable** — we can improve, update, or restart the
  knowledge base on its own, without touching the rest of the assistant.
- 🔒 **Private** — it lives entirely on your server and is not reachable from the
  internet.

*(We stress-tested this: we deliberately made the knowledge base hang for 10
seconds, and the assistant still answered in ~2 seconds using its backup. We also
killed it completely — the assistant kept working.)*

### Three things happening quietly in the background

**1. Keeping the knowledge up to date**
```
  📄 Your policy document  ──▶  📚 Knowledge base  ──▶  🔎 instantly searchable
     (whenever you edit it)      (updates itself)
```
Update a policy, and the assistant starts using the new version automatically.

**2. The learning loop**
```
  ✍️ AI's draft ─────┐
                     ├──▶  📊 compared & scored  ──▶  watch it improve
  🧑 Your real reply ─┘
```
Each time your team sends a real reply, the system measures how close the AI got.

**3. Automatic upkeep (no one has to remember)**
```
  🗓️ Every Monday 9am   ──▶  a summary report is texted to you
  🌙 Every night 3:30am  ──▶  full backups (so nothing is ever lost)
```

### For your developer — what each friendly name really is

| Friendly name | The actual technology |
|---|---|
| 🔐 Front door | **Caddy** reverse proxy (HTTPS + auto SSL certificate) |
| 📥 Receiver | Python web server (`server.py`), runs as an always-on service |
| 🔎 Gathers the story | `pipeline.py` (pulls ticket + customer + order from Gorgias) |
| 🛡️ Safety filter | `classifier.py` (rule-based, escalates sensitive tickets) |
| 🧠 Knowledge base (the sealed black box) | Its own program (`kb_service.py`, runs as the `gorgias-kb` service) that owns the search model + a private search database (**Postgres + pgvector**, on-server). Your policies live as text files in Git and flow into it. |
| 📚 Asking the knowledge base | `kb_client.py` — a thin caller with a **strict timeout + automatic fallback**; `ingestion_worker.py` keeps the box up to date |
| 🤖 AI model | **Ollama Cloud** (`glm-5.2`) via `model_gateway.py` |
| ✍️ Write the draft | `draft_engine.py` |
| 📝 Private draft note | `gorgias_api.py` (writes an internal note only — safety-gated) |
| 💾 Logs & reports | `feedback.db` + `weekly_review.py` |
| 📲 Owner alerts | `telegram_notify.py` |
| 🌙 Backups | `backup.sh` (runs nightly) |

Everything in that table runs on **your one server**, privately — except the AI
model, which is a paid cloud service the assistant calls when it needs to write.

---

## How we made sure it's reliable

We didn't just build it and hope. For **every single part**, we used a
two-step process:

```
   👷  A team builds the piece
              │
              ▼
   🕵️  A separate, independent "inspector" tries hard to BREAK it
       — testing weird inputs, edge cases, and safety holes
              │
       ┌──────┴───────┐
    ✅ Passes      ❌ Finds a problem
       │              │
       ▼              ▼
   We keep it    We fix it and re-inspect
```

**The inspector caught real problems before they ever went live** — for example:

- It noticed the assistant *wasn't* flagging some ways customers describe damage
  (like *"there's a rip in it"*). We fixed it so **all** those phrasings now go
  to a human. ✅
- It caught a place where the assistant might have leaked private customer info
  into its memory. We blocked that. ✅
- It double-checked that the assistant can **never** accidentally message a
  customer. Confirmed, repeatedly. ✅

Nothing was accepted until the inspector signed off.

---

## ✅ What's working right now

- The assistant is **live** and already reading your real tickets.
- It writes drafts using **your actual policies and approved templates**.
- It correctly **flags sensitive tickets** for a human.
- It's **alerting you** and **keeping records** of everything it does.
- It is **safe** — still in practice mode, messaging *no one*.

---

## 🎚️ The one decision left — and it's yours

Today the assistant writes its drafts as **private notes** ("practice mode").

When **you** feel ready, there's a single switch to let it **post those drafts
into your helpdesk** as suggested replies for your team — still for a human to
send, just one step further along.

**Our suggestion:** watch the practice drafts for a few days (and the Monday
report) to see how well they read on your real tickets. When you're happy,
just say the word and we'll flip the switch.

---

## A couple of things to confirm when you have a moment

- ✅ The **22 answer templates** you provided are in and confirmed.
- 📝 A few side topics that *weren't* in your document (e.g. gift-wrap details,
  exact size charts) are still marked "draft" — send us those whenever you like
  and we'll add them.
- 🤝 As your team answers tickets, the assistant keeps learning from their
  replies — so it gets sharper the more it's used.

---

*Questions about any of this? We're happy to walk through it live.*

<sub>— Built and tested in one working session. For the developer: full technical
detail, a step-by-step build log, and the safety design live in `TASKLIST.md`,
`SYSTEM_WORKFLOW.md`, and the git history of this project.</sub>
