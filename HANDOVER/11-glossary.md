# 11 · Glossary

*What this doc covers: plain‑English definitions of every product, tool, and term used across this handover. Skim it once; refer back as needed.*

---

## Products & external services

- **Buttons Bebe** — the client's e‑commerce store (baby/children's clothing). Runs on Shopify; does support in Gorgias. The end customer of this whole project. Owner contact: **Chaim**.
- **Gorgias** — the **help‑desk** software Buttons Bebe uses. All customer support "tickets" (email conversations) live here. Our agent reads tickets from Gorgias and writes its draft replies back into Gorgias as internal notes. Auth method: **Basic** (email + API key).
- **Shopify** — the **e‑commerce platform** the store runs on. Source of order, product, and customer data. The agent reads it (read‑only). Auth method: **client‑credentials** (mint a 24‑hour Admin API token from a client id + secret).
- **Redo** — a **returns/refunds** service. The agent reads return/refund status from it (read‑only). Auth method: **Bearer** token.
- **Ollama Cloud** — a hosted service that **runs the AI model** (`glm-5.2`) the agent thinks with. The Hermes brain calls it.
- **WhatsApp** — used only for **escalation alerts** to the store owner (not for customer replies in Phase 1). Handled by the `whatsapp-connect` service.
- **Twilio** — an original design that is not used. The current escalation path uses the `whatsapp-connect` Baileys service and `whatsapp_notifier.py`.

## The "brain" and how the AI runs

- **Hermes / Hermes Agent** — a terminal‑based AI agent framework (by Nous Research) that is the **"brain"** of the *live* system. For each ticket it's run once (`hermes … "process ticket …"`), reads context via tools, and produces a draft. Configured in `~/.hermes/` (**not in this repo** — see doc 06).
- **`glm-5.2`** — the specific **large language model** Hermes uses, served via Ollama Cloud. (A future task is to evaluate a cheaper/less‑verbose model.)
- **SOUL.md** — the **instruction file** that tells Hermes how to behave (its persona, rules, and the ticket workflow). Lives in `~/.hermes/` on the server. A partial addition is in the repo at `kb/hermes-SOUL-buttonsbebe-addition.md`.
- **Hermes skill (`buttonsbebe`)** — a packaged **workflow** that guides Hermes through the ticket‑handling steps. Lives in `~/.hermes/skills/buttonsbebe/` on the server (**not in this repo**).
- **`--yolo`** — the flag the processor uses when running Hermes; it **auto‑approves the AI's tool calls**. Safe today only because the sole write is a staff‑only internal note. Restricting this is a Phase 2 task.
- **Brain (Fable)** — in the Fable rebuild, a **"brain"** is a swappable component behind one interface (`base.py`), with implementations for `mock`, `anthropic`, and a `hermes` stub. Lets you change the AI without touching the pipeline.

## System components (the moving parts)

- **Webhook receiver** — a small web service (FastAPI app called `bb_webhook`, port **8000**) that **receives a signal from Gorgias** whenever a new ticket/message arrives, checks it's genuine, and adds a job to a queue. Also serves the **console/dashboard** web UI. **Source is on the server, not in this repo** (doc 06).
- **Webhook** — a "reverse API call": Gorgias calls *our* URL when something happens, instead of us polling it.
- **Job queue** — a simple **to‑do list of tickets to process**, stored in a small SQLite database (`webhook/data/webhook.db`). The processor pulls jobs from it.
- **Processor / Orchestrator** — the **loop** (a systemd service) that pulls each queued ticket and runs the Hermes brain once on it, then records the result and triggers escalation if needed. **Source is on the server, not in this repo** (doc 06).
- **Write‑back** — the step where the finished draft is **posted into Gorgias as an internal note** (`gorgias_writer.py`). This is the *only* thing the system writes to any external service.
- **Console / Dashboard** — the **web page the human support agent uses** to review the AI's drafts and click Send / Draft as note / Request edit. Front‑end source is in the repo (`console-src/`, `dashboard/`); the API it calls is served by the webhook app on the server.
- **Notice Board** — an **owner override** feature: the store owner can post a short notice (e.g. "shipping delayed this week") that temporarily overrides what the AI says. Managed via the `kb-admin` service and the `notices` part of the knowledge base.

## Knowledge base & search

- **Knowledge base (KB)** — the **reference material the AI searches** before answering: policies, FAQs, ~22 support "intents", curated example tickets, and the product catalog. Stored as Markdown files under `kb/`.
- **Intent** — a **category of support question** (e.g. "where is my order", "return request"). There are ~22, each a Markdown file guiding how to answer that type.
- **LanceDB** — the **search database** the KB is indexed into. Enables fast lookups over the Markdown content.
- **Hybrid search** — combining two search methods: **keyword** matching (BM25) and **semantic/embeddings** matching (meaning‑based), fused together (via "RRF", reciprocal rank fusion) for better results. Works across languages.
- **Embeddings** — a way of turning text into numbers so the computer can find **passages with similar meaning**, not just matching words. The KB uses a local multilingual embedding model.
- **Product sync** — an automated job that **refreshes the product catalog** (~4,246 products) from Shopify into the KB **every 3 days**.
- **Learning loop** — the mechanism that **captures the human's real, edited reply** on each ticket, scrubs personal info, and promotes good examples back into the KB so future drafts improve. Parts are live; parts were stubs — see doc 06.
- **Exemplar** — a **known‑good example reply** stored in the KB (`kb/tickets/`) for the AI to mirror.

## Infrastructure & operations

- **VPS** — the **Virtual Private Server** (a rented Linux machine) where everything runs. This one is `srv1766050` at IP `2.25.137.77`, running Ubuntu.
- **systemd** — Linux's **service manager**. Each part of the system (webhook, the three tools, WhatsApp, processor, timers) is a systemd "unit" that starts on boot and restarts on failure.
- **Timer** — a systemd unit that **runs a job on a schedule** (like a cron job) — e.g. product sync every 3 days, notices cleanup, nightly learning promotion.
- **Caddy** — the **web server / reverse proxy** that provides HTTPS and routes public traffic to the right internal service (e.g. `/connect-whatsapp/*` → port 8085, everything else → port 8000).
- **MCP (Model Context Protocol)** — a standard way to give an AI **tools** it can call. Here, three small local services expose read‑only tools to Hermes: the KB search (:8077), Redo returns (:8078), and Gorgias reads (:8079).
- **`.env` file** — a plain file holding **secret settings** (API keys, passwords). Never committed to git. There are currently two of them (a known wart to consolidate).
- **Stub** — a piece of code that **exists but doesn't really do its job yet** — a placeholder. Example: `classifier.py` currently returns "NORMAL" for everything; the AI does the real risk judgment for now.

## Project‑specific terms

- **Copilot (Phase 1)** — the current mode: **AI drafts, human sends.** Read‑only access to the store's systems.
- **Sensitive ticket** — refunds, chargebacks, disputes, damaged/wrong/missing items, or angry customers. These are **flagged and escalated to a human**, never auto‑handled.
- **Escalation** — alerting a human (via WhatsApp) that a ticket **needs their attention now**.
- **Fable** — the **name of the offline rebuild** on the `Fable_buttonsbebe` branch. Not deployed anywhere; it's a parallel dev/test track. See doc 07.
- **Emulator (Fable)** — a **fake local version** of Gorgias/Shopify/Redo/mailbox that lets the Fable code be developed and tested **offline**, without touching real services.
- **Golden set** — a fixed set of **example tickets with expected outcomes** used to test that the AI/pipeline behaves correctly. Part of the Fable test suite.
- **Safety invariant** — an **automated test that guards a safety rule** (e.g. "a sensitive ticket is never auto‑sent"). If a change breaks the rule, the test fails.
