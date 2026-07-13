# 02 · Live System Architecture

**What this doc covers:** the current, LIVE architecture of the Buttons Bebe AI support agent — what each moving part does, how a ticket flows end-to-end, which parts run where, and (critically for this handover) which component **source lives in this Git repo** versus which lives **only on the VPS**.

**Sources read:** `CLAUDE.md` (the project source of truth, dated 2026-07-07/09), `tools/README.md`, `kb/SEARCH-ENGINE.md`, `whatsapp-connect/Caddyfile`, `whatsapp-connect/server.js`, `console-src/index.html`, `dashboard/index.html`, the in-repo `*.service`/`*.timer` unit files (`tools/`, `kb/`, `whatsapp-connect/`, `kb-admin/`), `SPRINT-notice-board-2026-07-12.md`, and a full directory census of the repo (excluding the `_VPS-FULL-BACKUP-20260706/` snapshot).

---

## 0. How to read this doc (repo-vs-VPS legend)

The team will **clone this repo from GitHub**, but the running system lives on a **VPS**. Not every running component has its source in the repo. Every component subsection below is tagged:

| Tag | Meaning |
|---|---|
| 🟢 **Source in repo** | The code is in this repository at the cited path; you can read/edit it after cloning. |
| 🔴 **Source NOT in repo** | The running code lives only on the VPS. **⚠️ Not in repo — see doc `06` for the VPS pull procedure** to retrieve it. |
| ⚪ **External SaaS / data** | A third-party service (Gorgias, Shopify, Redo) — no source to own; it is configured, not coded. |

> **Two "truth" warnings before you trust anything else in the repo.**
> 1. Per `CLAUDE.md` §1 and §11, **most other Markdown docs in this repo describe a RETIRED design** (the pre-2026-07-06 `/root/gorgias-webhook` + Supermemory/ChromaDB + Baileys + "Mimo" build). Do **not** trust `GOAL.md`, the old `PROJECT-SOURCE-OF-TRUTH.md`, `kb/README.md`, `docs/hermes-rearchitecture/`, or `build/`. The retired system's full backup is under `_VPS-FULL-BACKUP-20260706/` (ignore it for architecture).
> 2. `CLAUDE.md` itself is dated 2026-07-09; a few things have moved since (see **§10 Known discrepancies**). Where the repo and `CLAUDE.md` disagree, this doc flags it explicitly rather than guessing.

---

## 1. What the system is & why

An **AI support agent for Buttons Bebe**, a Shopify store that receives **~2,000 support tickets/month** in **Gorgias** (the help desk). **Client: Chaim.**

For each incoming ticket the agent:

1. reads the customer message,
2. pulls order / return / product context (Shopify + Redo + Gorgias, all read-only),
3. searches a curated knowledge base (policies, FAQ, intents, products, past tickets),
4. classifies risk, and
5. **drafts a first-pass reply.**

The defining product decision: **the AI drafts, a human sends.** The agent never emails a customer on its own. A draft is surfaced for a human in the support **Console** (and, depending on a toggle, as a staff-only **internal note** in Gorgias); a human reviews, edits if needed, and clicks **Send**. This keeps a fast-drafting LLM in the loop while a person owns every customer-facing word.

---

## 2. Safety model (never violate)

Reproduced faithfully from `CLAUDE.md` §2, with a short explanation of each rule.

1. **The AI never auto-sends.** The agent itself only ever *drafts*; it never sends a customer-facing message on its own. Drafts appear in the Console Ticket feed for a human.
   *→ The LLM has no "send to customer" capability. Its only possible write is a staff-only internal note.*

2. **Customer sends are human-initiated only.** From the Console a human can edit a draft and then **Send reply** (customer-facing), **Draft as internal note** (staff-only), or **Request edit** (Hermes rewrites to an instruction). Send always requires a confirm click; sensitive tickets show a warning. Nothing goes to a customer without a human clicking Send.
   *→ Verified in `console-src/index.html`: three buttons — `Request edit`, `Draft as internal note`, `Send reply →` — plus a hint that "Send reply" emails the customer directly and asks for confirmation.*

3. **Sensitive tickets are flagged, not auto-handled.** Refunds, chargebacks, disputes, damaged/wrong/missing items, angry customers → flagged sensitive (warning in the UI); the human decides. (Older builds suppressed the draft entirely — now a draft is shown but clearly marked.)
   *→ Sensitivity is detected today by Hermes (the LLM) and by KB tags (the KB marks `refund`/`dispute`/`sensitive`/`escalation` chunks with `[SENSITIVE -> escalate]`, per `kb/SEARCH-ENGINE.md`). The deterministic `classifier.py` gate is still a STUB (see §7).*

4. **External data is READ-ONLY except Gorgias writes.** Shopify read, Redo read, Gorgias read. Writes to Gorgias (internal note, and now human-initiated public reply) are the only writes.
   *→ All three MCP tool modules are GET-only (`tools/README.md`: "Everything here is read-only … no writes"). The only write paths in the whole system target Gorgias.*

5. **Everything is logged.**
   *→ Every Console action (Send / internal Note / Request-edit) is recorded as a "lesson" for the learning loop, and the processor records each job outcome.*

> **Note (from `CLAUDE.md` §2, 2026-07-09):** the earlier feedback/learning "review console" is superseded by the per-ticket action buttons in the Console Ticket feed. Endpoints (webhook app, :8000): `POST /dashboard/api/ticket/{id}/send|note|rewrite`. Per that note, **no internal note is posted automatically anymore — drafts are shown in the Console for review.**
> ⚠️ **Discrepancy to resolve on the VPS:** the newer in-repo Console front-end actually calls `/console/api/ticket/{id}/send|note|rewrite` (not `/dashboard/api/...`), and it exposes a **"Post drafts to Gorgias" toggle** ("On = agent posts drafts as internal notes; Off = draft-only safe review mode"). So auto-posting of the internal note is **configurable**, which reconciles the §4 flow (auto-post) with the §2/§8 note (no auto-post). See **§10**.

---

## 3. End-to-end flow

### 3.1 ASCII map

```
                         Customer message
                                │
                                ▼
                     ⚪ GORGIAS (help desk)
                                │  webhook: new ticket / message
                                ▼
   🔴 WEBHOOK RECEIVER  (bb_webhook, FastAPI, 127.0.0.1:8000)  ── also serves the Console
        • verifies HMAC (WEBHOOK_SECRET), dedupes
        • enqueues a job
                                │
                                ▼
                🟢/🔴 SQLite JOB QUEUE  (webhook/data/webhook.db, WAL)   [DB file lives on VPS]
                                │
                                ▼
   🔴 PROCESSOR / ORCHESTRATOR  (systemd buttonsbebe-processor)
        • polls the queue every ~2s; per job runs the brain once
                                │
                                ▼
   🔴 HERMES  (hermes --yolo -z "process ticket …", one-shot per ticket)
        guided by  ~/.hermes/SOUL.md  +  the "buttonsbebe" Hermes skill
        using three READ-ONLY MCP tools:
          ├─ 🟢 buttonsbebe_kb      (:8077)  search_kb → policies·FAQ·22 intents·~4,246 products·tickets  [LanceDB]
          ├─ 🟢 buttonsbebe_redo    (:8078)  returns / refunds status
          └─ 🟢 buttonsbebe_gorgias (:8079)  read ticket, messages, customer / order
        Hermes: read ticket → search KB → check returns → classify →
          • LOW risk   → draft a reply
          • SENSITIVE  → escalate (draft shown but flagged; owner alerted)
                                │
                                ▼
   🔴 WRITE-BACK  (processor/gorgias_writer.py → POST /api/tickets/{id}/messages, channel=internal)
        • (when "Post drafts to Gorgias" is ON) posts the draft as an INTERNAL NOTE — a Gorgias-only write
                                │
                                ▼
   🟢 CONSOLE UI (served by :8000)  — human reviews the draft in the Ticket feed
        • Request edit → Hermes rewrites   • Draft as internal note   • Send reply → (emails customer, confirm required)
                                │
                                ▼
   🟢/🔴 LEARNING LOOP — every Console action logs a "lesson"; nightly job promotes lessons into the KB
                                │
                                ▼
   🔴 ESCALATION → 🟢 WhatsApp (whatsapp-connect :8085 via Caddy)  — owner gets IMMEDIATE-ticket alerts
```

### 3.2 Step-by-step walkthrough

1. **Customer writes in.** A message lands in **Gorgias** (⚪ external SaaS).
2. **Gorgias fires a webhook** on the new ticket/message to `POST /webhook/gorgias/{tenant}` on the **webhook receiver** (🔴 `bb_webhook`, FastAPI, `127.0.0.1:8000`).
3. **Receiver verifies + enqueues.** It verifies the HMAC signature (`WEBHOOK_SECRET`), **dedupes**, and enqueues a job into a **SQLite queue** (`webhook/data/webhook.db`, WAL mode).
4. **Processor picks it up.** The **processor/orchestrator** (🔴 systemd `buttonsbebe-processor`, `python -m orchestrator`) polls the queue **every ~2 s** and, per job, runs the brain **once** via `hermes_runner.py`.
5. **Hermes runs one-shot.** It launches `hermes --yolo -z "process ticket …"` (🔴 Nous Hermes Agent CLI, model **`glm-5.2` via Ollama Cloud**), guided by `~/.hermes/SOUL.md` + the **`buttonsbebe`** skill. It calls the **three read-only MCP tools**: reads the ticket/customer/order (`buttonsbebe_gorgias` :8079), searches the KB (`buttonsbebe_kb` :8077), and checks returns/refunds (`buttonsbebe_redo` :8078).
6. **Classify → act.** Hermes classifies risk. **Low risk →** it drafts a grounded reply that cites the KB source. **Sensitive →** it escalates: a draft is still produced but clearly flagged, and the owner can be alerted over WhatsApp.
7. **Write-back (conditional).** `processor/gorgias_writer.py` (🔴) can **POST the draft as an internal note** (`channel=internal`) to Gorgias — the system's **only** write. Whether this happens automatically is governed by the Console's **"Post drafts to Gorgias"** toggle (see §2 note / §10).
8. **Human review in the Console.** A human opens the draft in the **Console Ticket feed** (🟢 `console-src/index.html`, served at `:8000`) and either **Send reply** (customer-facing email, confirm required), **Draft as internal note** (staff-only), or **Request edit** (Hermes rewrites per an instruction).
9. **Learn from the human.** Each Console action records a *lesson* (situation + AI draft + human's final text) which is promoted nightly into the KB as an "Approved reply" exemplar.

---

## 4. Component responsibilities

Each subsection is tagged with the repo-vs-VPS legend from §0.

### 4.1 Gorgias — help desk  ⚪ External SaaS
Source of tickets and customer/order context; destination for internal-note drafts and human-sent public replies. Auth = **Basic** (`GORGIAS_API_EMAIL` + `GORGIAS_API_KEY`, subdomain `GORGIAS_SUBDOMAIN`). Read access is exposed to Hermes via the Gorgias MCP tool (§4.6); writes go through `gorgias_writer.py` (§4.8). Note (`tools/README.md`): Gorgias pagination uses `limit`, not `per_page`.

### 4.2 Webhook receiver (`bb_webhook`)  🔴 **Source NOT in repo — ⚠️ see doc 06 for the VPS pull procedure**
FastAPI app on `127.0.0.1:8000` at `/root/Buttonsbebe Agent/webhook` (`src/bb_webhook/`). Receives `POST /webhook/gorgias/{tenant}`, verifies the HMAC (`WEBHOOK_SECRET`), dedupes, enqueues jobs, and **also serves the Console** (and its `/console/api/*`, `/console/kbapi/*`, `/console/waapi/*` back-end endpoints). systemd unit: **`buttonsbebe-webhook`** (uvicorn). The **front-end HTML** it serves *is* in the repo (`console-src/`, `dashboard/`); the **Python back-end serving it is not.**

### 4.3 Job queue (SQLite)  🔴 **DB file lives on the VPS**
SQLite database at `/root/Buttonsbebe Agent/webhook/data/webhook.db` (WAL mode). Written by the receiver (enqueue), drained by the processor (dequeue). Inspect on the VPS with:
`sqlite3 "…/webhook/data/webhook.db" "select status,count(*) from jobs group by status"`. The DB is a runtime artifact, not source.

### 4.4 Processor / orchestrator  🔴 **Source NOT in repo — ⚠️ see doc 06 for the VPS pull procedure**
`/root/Buttonsbebe Agent/processor`, systemd **`buttonsbebe-processor`**, runs `python -m orchestrator`. Polls the queue (~2 s), runs Hermes once per job via `hermes_runner.py`, records the outcome, and triggers escalation. Contains: `orchestrator.py`, `hermes_runner.py`, `gorgias_writer.py`, `kb_client.py`, and the stubs `classifier.py`, `twilio_notifier.py`, `feedback_collector.py` (§7). Reads config from `webhook/.env` via `processor/config.py`. Runs Hermes with **`--yolo`** (auto-approves tool calls) — safe today because the only write is a staff-only internal note.

### 4.5 Hermes — the brain  🔴 **Source/config NOT in repo — ⚠️ see doc 06 for the VPS pull procedure**
Nous **Hermes Agent** CLI, home at `~/.hermes/`. Model **`glm-5.2` via Ollama Cloud** (`~/.hermes/config.yaml`). Behaviour is steered by:
- **`~/.hermes/SOUL.md`** — global instructions (draft vs. escalate, cite sources, mirror "Approved reply" exemplars, honor the Notice Board override).
- **the `buttonsbebe` skill** (`~/.hermes/skills/buttonsbebe/`) — the per-ticket workflow.
- **three MCP tools registered by URL** in `config.yaml` (`hermes mcp list`).

None of `~/.hermes/` is in this repo. **However**, the repo *does* contain a source-controlled fragment intended to be merged into the server's SOUL: **`kb/hermes-SOUL-buttonsbebe-addition.md`** (🟢) — the Notice-Board instruction block.

### 4.6 The three read-only MCP tools

All three are always-on HTTP MCP services bound to `127.0.0.1`, each its own systemd unit + port, registered in Hermes by URL. **All GET-only, no writes** (`tools/README.md`).

| Tool (Hermes name) | Port | systemd unit | Source | What it does |
|---|---|---|---|---|
| `buttonsbebe_kb` | 8077 | `buttonsbebe-kb-mcp` | 🟢 `kb/scripts/kb_mcp_server.py` | one tool, `search_kb` — LanceDB hybrid search over the KB |
| `buttonsbebe_redo` | 8078 | `buttonsbebe-redo-mcp` | 🟢 `tools/redo_mcp.py` | `list_recent_returns`, `get_returns_for_order`, `get_return` |
| `buttonsbebe_gorgias` | 8079 | `buttonsbebe-gorgias-mcp` | 🟢 `tools/gorgias_mcp.py` | `list_recent_tickets`, `get_ticket`, `get_ticket_messages`, `get_customer`, `search_customer` |

Notes:
- **Redo** reads `REDO_API_KEY` + `REDO_STORE_ID` (Bearer auth) from the MAIN `.env`. **Gorgias** uses Basic auth from MAIN `.env`.
- Each unit's `ExecStart` points at a **space-free launcher on the VPS** — `/root/kb-mcp-run.sh`, `/root/redo-mcp-run.sh`, `/root/gorgias-mcp-run.sh` (🔴, needed because the project path `/root/Buttonsbebe Agent/` contains a space). The repo ships equivalent runners (`tools/run-gorgias.sh`, `tools/run-redo.sh`, `kb/run_mcp.sh`) and the `.service` files themselves.
- Verify on the VPS: `hermes mcp test buttonsbebe_kb` (expect "Connected, 1 tool").

### 4.7 Knowledge base (LanceDB)  🟢 **Source in repo: `kb/`**
Markdown content at `/root/Buttonsbebe Agent/KB` (repo: `kb/`), organized into `intents/ faq/ policies/ tickets/ products/`, indexed into **LanceDB hybrid search** — keyword **and** a small **local multilingual embedding model** blended, so it matches exact tokens (order numbers, SKUs, Hebrew) *and* paraphrases. Fully local: no API keys, no per-search cost, nothing leaves the box (`kb/SEARCH-ENGINE.md`).

- **Indexed:** `intents/`, `faq/`, `policies/`, `tickets/`, `products/` (each `##` section → one chunk). **Not indexed:** `learned/`, folder `README.md`s, and any file starting with `_`.
- **Products** (`products/`, currently **~4,246**) are **auto-synced from Shopify every 3 days** by `kb/scripts/sync_products.py` (via `sync-products.sh`), which mints a fresh 24 h Shopify token (client-credentials grant, scope `read_products`), bulk-exports the catalog, and writes one markdown file per product, then re-indexes. Timer: **`buttonsbebe-kb-sync.timer`** → oneshot **`buttonsbebe-kb-sync.service`**.
- Sensitive chunks (`refund`/`dispute`/`sensitive`/`escalation`) render a **`[SENSITIVE -> escalate]`** marker in search results.
- Repo scripts: `kb/scripts/{index_kb.py, search_kb.py, kb_lib.py, kb_mcp_server.py, sync_products.py, notices_lib.py, purge_notices.py, review_learned.py}`; helper shell: `kb/{setup.sh, update.sh, search.sh, sync-products.sh, run_mcp.sh}`. The built index (`kb/lancedb/`) and `.venv/` are runtime artifacts (VPS).

### 4.8 Write-back path  🔴 **Source NOT in repo — ⚠️ see doc 06 for the VPS pull procedure**
`processor/gorgias_writer.py` posts the draft to Gorgias as an **internal note** (`POST /api/tickets/{id}/messages`, `channel=internal`) — the **only write in the entire system**. Conditional on the Console "Post drafts to Gorgias" toggle. The human-initiated **public reply** (Send) is the second, human-triggered Gorgias write, issued through the Console back-end (also VPS-only).

### 4.9 WhatsApp escalation  🟢 **Source in repo: `whatsapp-connect/`** (+ 🔴 caller in `processor/`)
Node + **Baileys** service on `127.0.0.1:8085` (`whatsapp-connect/server.js`), systemd **`buttonsbebe-whatsapp-connect`**. Two jobs:
1. **Owner pairing** — the owner scans a QR at `https://srv1766050.hstgr.cloud/connect-whatsapp/<WA_TOKEN>/` (auth-gated, auto-refreshing QR page).
2. **Escalation delivery + 2-way bridge** — `POST /connect-whatsapp/<WA_TOKEN>/send` delivers IMMEDIATE-ticket alerts; the same service bridges the owner's WhatsApp replies back to Hermes. Additional routes: `/wa/status`, `/wa/notify`, `/wa/test`, `/wa/logout`.

The **caller** is `processor/twilio_notifier.py` (🔴, rewritten to POST here; delivery URL `WHATSAPP_SEND_URL` via a processor drop-in). The service unit ships env placeholders (`WA_PORT=8085`, `WA_TOKEN`, `WA_PASSWORD`, `WA_AUTH_DIR`, `HERMES_BIN`, `NODE`) that are patched at deploy time. **⚠️ Security:** the committed `.service` file contains a default `WA_PASSWORD` value — treat it as a placeholder to rotate, do not reuse it in production. (Name "twilio_notifier" is legacy; delivery is WhatsApp/Baileys, not Twilio.)

### 4.10 Console UI  🟢 **Source in repo: `console-src/index.html`, `dashboard/index.html`** (served by the 🔴 :8000 back-end)
Single-page "Buttons Bebe — Support Console" (`console-src/index.html`, 529 lines; an older `dashboard/index.html`, 470 lines, exists too — both titled "Support Console"). It is the human review surface: a **Ticket feed** with per-ticket **Request edit / Draft as internal note / Send reply →** buttons, a KB panel, a WhatsApp-connect panel, and a **"Post drafts to Gorgias"** safety toggle. Front-end calls back-end APIs under `/console/api`, `/console/kbapi`, `/console/waapi`. On the VPS it is served from `/var/www/console/index.html` (per `SPRINT-notice-board-2026-07-12.md`); `console-src/` is its repo source. **The HTML is in the repo; the Python endpoints it calls are in the VPS-only webhook app.**

### 4.11 Learning loop  🟢 **partial in repo** / 🔴 **partial VPS-only**
Captures the human's real reply and feeds it back into the KB.
- **Capture (VPS-only, 🔴):** every Console action calls `webhook/src/bb_webhook/learning.py`, which writes `KB/learned/lesson-*.md` (situation + AI draft + human's final text + kind + edited flag) and tracks totals in `_ledger.json`. Exposed at `GET /dashboard/api/learning` (Console "Learning" card).
- **Nightly promotion (VPS-only, 🔴):** `buttonsbebe-kb-learn.timer` (03:30) runs `KB/scripts/auto_promote_learned.py`, which masks PII and promotes each lesson into an indexed `KB/tickets/exemplar-learned-*.md` (`status: confirmed`, `source: learned-auto`), then `learn-nightly.sh` rebuilds the index. **⚠️ Verified NOT present in this repo:** `auto_promote_learned.py`, `learn-nightly.sh`, and `buttonsbebe-kb-learn.timer` are not in the checkout — retrieve from the VPS (doc 06).
- **PII masking (🟢 in repo):** `feedback/pii.py` (emails/phones/orders/addresses + known customer name) — the module used to scrub lessons. The broader `feedback/` package (`collector.py`, `pairing.py`, `similarity.py`, `store.py`, `review.py`, etc.) is present but is the **older poll-based feedback design**, now superseded by the Console-action capture (see §7).

### 4.12 In-repo components NOT in the CLAUDE.md §6 port table (verify)
These exist in the repo/on the VPS but post-date `CLAUDE.md` (2026-07-09) — flag and confirm on the VPS:
- **KB Admin API — `kb-admin/server.js` (🟢), port `8087`, unit `buttonsbebe-kb-admin`.** "Editable knowledge base API for the console," reached behind Console auth at `/console/kbapi/*`. Powers the **Notice Board** (owner-posted notices that override KB answers, with auto-expiry). Related in-repo pieces: `kb/notices/`, `kb/scripts/notices_lib.py`, notice-injection in `kb/scripts/search_kb.py`, and the GC unit **`buttonsbebe-kb-notices-gc.service` + `.timer`** (every ~15 min). Source: `SPRINT-notice-board-2026-07-12.md`.
- **`fable/` (⚠️ verify).** A directory tree (`server/app/brains`, `emulators`, `scripts`, `tests`) with a SQLite DB (`fable/server/data/fable.db`) and compiled `__pycache__`, **but zero `.py` source files** in the checkout. Not referenced by `CLAUDE.md`. Do **not** assume it is part of the live pipeline — treat as experimental/unknown until confirmed on the VPS.
- **`deploy/` (🟢).** `patch_app.py`, `review_console.html`, `vps-patches/` — deployment/patching helpers.
- **`data/` (🟢).** Two exported Gorgias ticket CSVs (2026-06-23) — sample data, not runtime.

---

## 5. Services, ports & systemd units

All services bind to **`127.0.0.1`** (localhost only); public access is via Caddy.

| Port | What | systemd unit | Source in repo? |
|---|---|---|---|
| 8000 | Webhook receiver **+ Console** (uvicorn) | `buttonsbebe-webhook` | 🔴 back-end no · 🟢 front-end `console-src/` |
| 8077 | KB MCP — `search_kb` | `buttonsbebe-kb-mcp` | 🟢 `kb/` |
| 8078 | Redo MCP — returns | `buttonsbebe-redo-mcp` | 🟢 `tools/redo_mcp.py` |
| 8079 | Gorgias MCP — read tickets/customers | `buttonsbebe-gorgias-mcp` | 🟢 `tools/gorgias_mcp.py` |
| 8085 | WhatsApp connect (QR pairing + Hermes bridge) | `buttonsbebe-whatsapp-connect` | 🟢 `whatsapp-connect/` |
| 8087 | KB Admin API / Notice Board (post-CLAUDE.md) | `buttonsbebe-kb-admin` | 🟢 `kb-admin/server.js` |
| — | Job processor (the loop) | `buttonsbebe-processor` | 🔴 `processor/` |
| — | Product sync (every 3 days) | `buttonsbebe-kb-sync` (+ `.timer`) | 🟢 `kb/sync-products.sh` |
| — | Notice Board GC (every ~15 min) | `buttonsbebe-kb-notices-gc` (+ `.timer`) | 🟢 `kb/` |
| — | Learned-lesson nightly promote (03:30) | `buttonsbebe-kb-learn` (+ `.timer`) | 🔴 **not in repo** |

**Caddy public entry** (HTTPS on `srv1766050.hstgr.cloud`, auto TLS via Let's Encrypt; config `whatsapp-connect/Caddyfile`):

```
srv1766050.hstgr.cloud {
    handle /connect-whatsapp/*  →  reverse_proxy 127.0.0.1:8085   # WhatsApp connect
    handle              (everything else)  →  reverse_proxy 127.0.0.1:8000   # webhook + Console
    request_body max_size 256KB
    header: X-Content-Type-Options nosniff · Referrer-Policy no-referrer
    log → /var/log/bb-webhook/caddy.log
}
```

Hermes registers the three MCP tools by URL in `~/.hermes/config.yaml`; check with `hermes mcp list`.

---

## 6. LIVE vs STUB (what actually works today)

Reproduced from `CLAUDE.md` §8, annotated.

**LIVE & verified**
- Webhook receiver → SQLite queue → processor loop.
- Hermes runs per ticket and uses all three MCP tools (proven end-to-end).
- KB hybrid search incl. **~4,246 products** (auto-refreshed every 3 days).
- Gorgias **read** (tools) and **write** (internal note via `gorgias_writer.py`).

**LIVE (added 2026-07-07)**
- WhatsApp escalation channel — `whatsapp-connect` (:8085) + `twilio_notifier.py` POST to it. Owner links WhatsApp via the QR page; alerts then deliver.

**LIVE (added 2026-07-09) — learning loop**
- Every Console action records a lesson (`learning.py` → `KB/learned/lesson-*.md` + `_ledger.json`; `GET /dashboard/api/learning`).
- Nightly (03:30) promotion masks PII and indexes each lesson as a `KB/tickets/exemplar-learned-*.md` "Approved reply" exemplar; SOUL tells Hermes to mirror these.

**LIVE (added 2026-07-12) — Notice Board** *(newer than CLAUDE.md; verify)*
- Owner-posted notices that override KB answers (with auto-expiry), served by `kb-admin` (:8087) and injected at the top of `search_kb` results.

**STUB / not yet implemented**
- `processor/classifier.py` — returns NORMAL for everything. Risk classification is currently done by **Hermes (the LLM)**, not a deterministic code gate.
- `processor/feedback_collector.py` — the old poll-based capture, **superseded** by the Console-action capture (`learning.py` + nightly promote).
- Name caveat: `processor/twilio_notifier.py` is LIVE but delivers via WhatsApp/Baileys, not Twilio.

---

## 7. Where it runs

- **VPS:** `srv1766050` (IP `2.25.137.77`), Ubuntu. Public host `srv1766050.hstgr.cloud`.
- **Project root on the box:** `/root/Buttonsbebe Agent/` (note the space in the path — the reason for the space-free `/root/*-mcp-run.sh` launchers).
- **Hermes home:** `~/.hermes/` (i.e. `/root/.hermes/`).
- **Credentials** (`CLAUDE.md` §7) live in **two `.env` files** — a known wart:
  - `/root/Buttonsbebe Agent/.env` (**MAIN**) — `GORGIAS_*`, `SHOPIFY_SHOP`/`SHOPIFY_CLIENT_ID`/`SHOPIFY_CLIENT_SECRET` (client-credentials), `REDO_API_KEY`, `REDO_STORE_ID`. Read by the **3 MCP tool modules**.
  - `/root/Buttonsbebe Agent/webhook/.env` — `GORGIAS_*`, `WEBHOOK_SECRET`, `WEBHOOK_*`, `SHOPIFY_*`, `LOG_*`. Read by the **webhook app + processor** (`processor/config.py`).
  - Gorgias creds are duplicated across both (kept in sync); Redo lives only in MAIN (the processor reaches Redo *through* the `buttonsbebe_redo` tool). Auth: Shopify = client-credentials (24 h token), Gorgias = Basic, Redo = Bearer.
  - **⚠️ Security note:** a populated `.env` (and `.env.bak-20260708`) is present in the repo working tree at the repo root. **No secret values are reproduced in this doc.** Confirm these are git-ignored / scrubbed before the repo is shared, and rotate anything that may have been committed. (`env.example` / `.env.example` are the safe templates.)

**Operate & verify on the VPS** (`CLAUDE.md` §10):
```
hermes mcp list                     # the 3 tools, all enabled
hermes mcp test buttonsbebe_kb      # (or _redo / _gorgias) → Connected, N tools
systemctl status buttonsbebe-processor buttonsbebe-kb-mcp buttonsbebe-redo-mcp buttonsbebe-gorgias-mcp
journalctl -u buttonsbebe-processor -n 50
cd "/root/Buttonsbebe Agent/KB" && ./search.sh "do you ship to canada"
sqlite3 "/root/Buttonsbebe Agent/webhook/data/webhook.db" "select status,count(*) from jobs group by status"
```

---

## 8. Source location matrix (repo vs VPS) — quick reference

| Component | Runs as | Source location | In this repo? |
|---|---|---|---|
| Webhook receiver `bb_webhook` (:8000) | `buttonsbebe-webhook` | `/root/Buttonsbebe Agent/webhook/src/bb_webhook/` | 🔴 **No — doc 06** |
| Console back-end APIs (`/console/api`, `/console/kbapi`, `/console/waapi`) | part of :8000 | `webhook/` (VPS) | 🔴 **No — doc 06** |
| Console front-end (HTML/JS) | served by :8000 | `console-src/index.html`, `dashboard/index.html` | 🟢 Yes |
| Job queue DB | file | `webhook/data/webhook.db` (VPS runtime) | 🔴 runtime file |
| Processor / orchestrator | `buttonsbebe-processor` | `/root/Buttonsbebe Agent/processor/` | 🔴 **No — doc 06** |
| `gorgias_writer.py`, `twilio_notifier.py`, `classifier.py`, `kb_client.py`, `feedback_collector.py` | in processor | `processor/` (VPS) | 🔴 **No — doc 06** |
| Hermes config / SOUL / skill | `~/.hermes/` | `config.yaml`, `SOUL.md`, `skills/buttonsbebe/` | 🔴 **No — doc 06** |
| KB MCP + search engine (:8077) | `buttonsbebe-kb-mcp` | `kb/` | 🟢 Yes |
| Redo MCP (:8078) | `buttonsbebe-redo-mcp` | `tools/redo_mcp.py` | 🟢 Yes |
| Gorgias MCP (:8079) | `buttonsbebe-gorgias-mcp` | `tools/gorgias_mcp.py` | 🟢 Yes |
| WhatsApp connect (:8085) | `buttonsbebe-whatsapp-connect` | `whatsapp-connect/` | 🟢 Yes |
| KB Admin / Notice Board (:8087) | `buttonsbebe-kb-admin` | `kb-admin/server.js`, `kb/notices/` | 🟢 Yes |
| PII masking | lib | `feedback/pii.py` | 🟢 Yes |
| Learning capture (`learning.py`) + nightly promote | timer/app | `webhook/…/learning.py`, `KB/scripts/auto_promote_learned.py`, `learn-nightly.sh` | 🔴 **No — doc 06** |
| Space-free MCP launchers | scripts | `/root/{kb,redo,gorgias}-mcp-run.sh` | 🔴 VPS (repo has `tools/run-*.sh`, `kb/run_mcp.sh`) |
| Caddy reverse proxy | `caddy` | `whatsapp-connect/Caddyfile` | 🟢 Yes |

---

## 9. Known gaps (from CLAUDE.md §11)

- The **stubs** in §6 (`classifier.py`, and the old `feedback_collector.py`).
- **Doc drift:** many repo docs describe the RETIRED design; `CLAUDE.md` is the current truth and this doc reconciles it against the actual files.
- **`.env` duplication** across two files. Shopify "code half": `webhook/config.py` still reads a static token field, not the client-cred keys — only matters if the webhook ever calls Shopify directly (it does not today).
- Confirm the exact systemd unit for the :8000 receiver (`CLAUDE.md` names it `buttonsbebe-webhook`).
- The processor runs Hermes with **`--yolo`** (auto-approves tool calls) — safe only because the sole write is a staff-only internal note.

---

## 10. Discrepancies this doc surfaced (resolve on the VPS during handover)

1. **Console API base path.** `CLAUDE.md` §2/§8 cite `POST /dashboard/api/ticket/{id}/send|note|rewrite` and `GET /dashboard/api/learning`, but the in-repo front-end (`console-src/index.html`, `dashboard/index.html`) calls **`/console/api/...`** (plus `/console/kbapi`, `/console/waapi`). Confirm the live route prefix on the VPS webhook app (likely renamed `/dashboard` → `/console`).
2. **Auto-post of the internal note.** `CLAUDE.md` §4 shows the processor auto-posting the draft as an internal note, while the §2/§8 note says drafts are only shown in the Console. The repo Console exposes a **"Post drafts to Gorgias" toggle** — so both are true depending on that setting. Confirm the toggle's default on the VPS.
3. **Learning-loop scripts absent from the checkout.** `auto_promote_learned.py`, `learn-nightly.sh`, `buttonsbebe-kb-learn.timer`, and the webhook `learning.py` are referenced by `CLAUDE.md` but are **not in this repo** — pull them from the VPS (doc 06).
4. **Post-CLAUDE.md components** (KB Admin :8087 / Notice Board / notices-gc timer) are live-in-repo but missing from the `CLAUDE.md` §6 port table.
5. **`fable/`** ships with no `.py` source (only a DB + `__pycache__`) and is unreferenced by `CLAUDE.md` — clarify whether it is dead, experimental, or has source only on the VPS.
6. **No Git remote is configured** in this working copy (`git remote -v` is empty; branch `main`). Confirm the GitHub URL the new team should clone from.
```
