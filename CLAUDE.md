# CLAUDE.md — Buttons Bebe AI Support Agent — Architecture Map

> **Read this first.** Reflects the LIVE system on the VPS as of **2026-07-07**.
>
> ⚠️ This supersedes all earlier architecture notes. The pre-2026-07-06 design
> (a `/root/gorgias-webhook` pipeline, "shadow mode", a Supermemory/ChromaDB KB,
> WhatsApp/Baileys, an 8-tool `hermes-tools-mcp`, the "Mimo" model) was **retired**
> when the box was wiped and rebuilt. A full backup of the old system is in
> `_VPS-FULL-BACKUP-20260706/`. Do **not** trust the older docs (`GOAL.md`,
> old `PROJECT-SOURCE-OF-TRUTH.md`, `kb/README.md`, `docs/hermes-rearchitecture/`)
> for the current architecture — they describe the retired design.

## 1. What & why

An AI support agent for **Buttons Bebe** (a Shopify store, ~2,000 tickets/month in
**Gorgias**). For each incoming ticket it reads the message, pulls order/return/product
context, searches a knowledge base, and drafts a first-pass reply **as an internal note**
in Gorgias for a human to review and send. Client: **Chaim**.

## 2. Safety model (never violate)

1. **The AI never auto-sends.** The agent itself only ever *drafts*; it never sends a
   customer-facing message on its own. Drafts appear in the console Ticket feed for a human.
2. **Customer sends are human-initiated only.** From the console a human can edit a draft and
   then **Send reply** (customer-facing), **Draft as internal note** (staff-only), or
   **Request edit** (Hermes rewrites to an instruction). Send always requires a confirm click;
   sensitive tickets show a warning. Nothing goes to a customer without a human clicking Send.
3. **Sensitive tickets are flagged, not auto-handled.** Refunds, chargebacks, disputes,
   damaged/wrong/missing items, angry customers → flagged sensitive (warning in the UI); the
   human decides. (Older builds suppressed the draft entirely — now a draft is shown but
   clearly marked.)
4. **External data is READ-ONLY except Gorgias writes.** Shopify read, Redo read, Gorgias read.
   Writes to Gorgias (internal note, and now human-initiated public reply) are the only writes.
5. **Everything is logged.**

   > NOTE (2026-07-09): the earlier feedback/learning "review console" is superseded by the
   > per-ticket action buttons in the console Ticket feed. Endpoints:
   > `POST /dashboard/api/ticket/{id}/send|note|rewrite` (webhook app :8000). No internal note
   > is posted automatically anymore — drafts are shown in the console for review.

## 3. Where it runs

VPS **`srv1766050`** (2.25.137.77), Ubuntu. Everything lives under
**`/root/Buttonsbebe Agent/`**. The "brain" is **Hermes Agent** (Nous Research), running the
model **`glm-5.2` via Ollama Cloud** (`~/.hermes/config.yaml`).

## 4. End-to-end flow (the map)

```
Customer message
      │
      ▼
  GORGIAS (help desk)
      │  webhook on new ticket / message
      ▼
  WEBHOOK RECEIVER  (bb_webhook, FastAPI, 127.0.0.1:8000)      ── also serves /dashboard
      • verifies HMAC (WEBHOOK_SECRET), dedupes
      • enqueues a job  ──►  SQLite queue (webhook/data/webhook.db, WAL)
      │
      ▼
  PROCESSOR / ORCHESTRATOR   (systemd: buttonsbebe-processor)
      • polls the queue every ~2s; per job runs the brain once:
      │
      ▼
  HERMES  (hermes --yolo -z "process ticket …", one-shot per ticket)
      guided by  ~/.hermes/SOUL.md  +  the "buttonsbebe" Hermes skill,
      using three READ-ONLY MCP tools:
        ├─ buttonsbebe_kb      (:8077)  search_kb → policies · FAQ · 22 intents · 4,246 products · tickets   [LanceDB]
        ├─ buttonsbebe_redo    (:8078)  returns / refunds status
        └─ buttonsbebe_gorgias (:8079)  read ticket, messages, customer / order
      Hermes: read ticket → search KB → check returns → classify →
        • LOW risk  → draft a reply
        • SENSITIVE → escalate (no customer draft)
      │
      ▼
  WRITE-BACK   (processor/gorgias_writer.py → POST /api/tickets/{id}/messages, channel=internal)
      • posts the draft as an INTERNAL NOTE (staff-only) — the ONLY write in the system
      │
      ▼
  HUMAN reviews the note in Gorgias and sends / edits.

  (Escalation notify via the Baileys WhatsApp bridge, and the feedback/learning loop, are wired but STUBBED — see §8.)
```

## 5. Components

- **Gorgias** — help desk. Source of tickets + customer/order context; destination for
  internal-note drafts.
- **Webhook receiver** — `/root/Buttonsbebe Agent/webhook` (FastAPI/`bb_webhook`, port 8000).
  Receives Gorgias webhooks (`POST /webhook/gorgias/{tenant}`), verifies the HMAC signature
  (`WEBHOOK_SECRET`), dedupes, enqueues jobs. Also serves a small `/dashboard`.
- **Job queue** — SQLite at `webhook/data/webhook.db`.
- **Processor / orchestrator** — `/root/Buttonsbebe Agent/processor` (systemd
  `buttonsbebe-processor`, runs `python -m orchestrator`). Polls the queue and runs Hermes
  once per ticket via `hermes_runner.py`; records the outcome; would trigger escalation.
- **Hermes (the brain)** — Nous Hermes Agent CLI. Model `glm-5.2` via Ollama Cloud. Guided
  by `SOUL.md` + the **`buttonsbebe`** Hermes skill (`~/.hermes/skills/buttonsbebe`).
- **Three MCP tool modules** (read-only, always-on HTTP services on localhost; each its own
  systemd service + port). See `tools/README.md` and `KB/SEARCH-ENGINE.md`.
- **Knowledge base** — `/root/Buttonsbebe Agent/KB`. Markdown content
  (`intents/ faq/ policies/ tickets/ products/`) indexed into **LanceDB hybrid search**
  (keyword + local multilingual embeddings). `products/` is **auto-synced from Shopify every
  3 days** (`sync-products.sh`, timer `buttonsbebe-kb-sync`). `learned/` is not indexed.
- **Write path** — `processor/gorgias_writer.py` posts the internal note (the only write).
- **Escalation → WhatsApp** — `processor/whatsapp_notifier.py` POSTs IMMEDIATE-ticket alerts
  to the owner's WhatsApp via the **whatsapp-connect** service (Node + Baileys, port 8085).
  The owner links their WhatsApp by scanning a QR at `https://srv1766050.hstgr.cloud/connect-whatsapp/<token>/`
  (auto-refreshing QR page). That same service also bridges the owner's WhatsApp messages to
  Hermes (2-way). Live once the owner scans; delivery URL is `WHATSAPP_SEND_URL` (processor drop-in).
- **Feedback loop** — `processor/feedback_collector.py` (store the human's real reply into
  `KB/learned/`). **STUB.**

## 6. Services & ports (all bound to 127.0.0.1)

| Port | What | systemd unit |
|---|---|---|
| 8000 | Webhook receiver + dashboard (uvicorn) | `buttonsbebe-webhook` |
| 8077 | KB MCP — `search_kb` | `buttonsbebe-kb-mcp` |
| 8078 | Redo MCP — returns | `buttonsbebe-redo-mcp` |
| 8079 | Gorgias MCP — read tickets/customers | `buttonsbebe-gorgias-mcp` |
| 8085 | WhatsApp connect (QR pairing + Hermes bridge) | `buttonsbebe-whatsapp-connect` |
| — | Job processor (the loop) | `buttonsbebe-processor` |
| — | Product sync (every 3 days) | `buttonsbebe-kb-sync` (+ `.timer`) |

Public entry (Caddy, HTTPS on `srv1766050.hstgr.cloud`): `/connect-whatsapp/*` → :8085,
everything else → :8000.

Hermes registers the three tools by URL in `~/.hermes/config.yaml` (`hermes mcp list`).

## 7. Credentials (.env) — note the split

Two env files (a known wart; see §11):

- **`/root/Buttonsbebe Agent/.env` (MAIN)** — `GORGIAS_*`, `SHOPIFY_SHOP` +
  `SHOPIFY_CLIENT_ID` + `SHOPIFY_CLIENT_SECRET` (client-credentials grant),
  `REDO_API_KEY`, `REDO_STORE_ID`. **Read by the 3 MCP tool modules.**
- **`/root/Buttonsbebe Agent/webhook/.env`** — `GORGIAS_*`, `WEBHOOK_SECRET`, `WEBHOOK_*`,
  `SHOPIFY_*`, `LOG_*`. **Read by the webhook app + processor** (`processor/config.py`).

Gorgias creds are duplicated across both files (kept in sync). **Redo lives only in MAIN** —
the processor reaches Redo *through the `buttonsbebe_redo` MCP tool* (which reads MAIN), so it
does not read Redo from its own `.env`.

Shopify auth = **client-credentials** (mint a 24h Admin API token from client id+secret).
Gorgias auth = **Basic** (email + API key). Redo auth = **Bearer** token.

## 8. LIVE vs STUB (what actually works today)

**LIVE & verified:**
- Webhook receiver → queue → processor loop.
- Hermes runs per ticket and uses all three MCP tools (proven end-to-end).
- KB hybrid search incl. **4,246 products** (auto-refreshed every 3 days).
- Gorgias **read** (tools) and **write** (internal note via `gorgias_writer`).

**LIVE (added 2026-07-07):**
- WhatsApp escalation channel — `whatsapp-connect` (port 8085) + the rewritten
  `whatsapp_notifier.py` POSTs to it with the dedicated Bearer secret. Owner links WhatsApp via the QR page; alerts then deliver.

**LIVE (added 2026-07-09) — learning loop:**
- Every console action (Send / internal Note / Request-edit) records a *lesson* to
  `KB/learned/lesson-*.md` via `webhook/src/bb_webhook/learning.py` (situation + AI draft +
  human's final text + kind + edited flag; a `_ledger.json` tracks totals). Endpoint:
  `GET /dashboard/api/learning` (shown as the console "Learning" card).
- Nightly (`buttonsbebe-kb-learn.timer`, 03:30) `KB/scripts/auto_promote_learned.py` masks
  PII (emails/phones/orders/addresses via `feedback/pii.py`, plus the known customer name) and
  promotes each lesson into an indexed `KB/tickets/exemplar-learned-*.md` (`status: confirmed`,
  `source: learned-auto`), then `learn-nightly.sh` rebuilds the index. SOUL tells Hermes to
  mirror these "Approved reply" exemplars while grounding facts in policy/faq/products.

**STUB / not yet implemented (planned):**
- `classifier.py` — returns NORMAL for everything. Risk classification is currently done by
  **Hermes (the LLM)**, not the deterministic code gate.
- `processor/feedback_collector.py` (the old poll-based capture) is superseded by the
  console-action capture above (`learning.py` + `auto_promote_learned.py`).

## 9. Key locations

- `KB/` — knowledge base + search engine (`KB/SEARCH-ENGINE.md`, `KB/README.md`, `scripts/`).
- `tools/` — Redo + Gorgias MCP modules (`tools/README.md`, `redo_mcp.py`, `gorgias_mcp.py`).
- `webhook/` — FastAPI receiver + queue DB (`src/bb_webhook/`).
- `processor/` — `orchestrator.py`, `hermes_runner.py`, `gorgias_writer.py`,
  `classifier.py`(stub), `feedback_collector.py`(superseded poller), `kb_client.py`.
- `~/.hermes/` — Hermes home: `config.yaml` (model + MCP registrations), `SOUL.md`
  (instructions), `skills/buttonsbebe/` (ticket workflow).
- Space-free launchers: `/root/kb-mcp-run.sh`, `/root/redo-mcp-run.sh`, `/root/gorgias-mcp-run.sh`.

## 10. Operate & verify

```
hermes mcp list                       # the 3 tools, all enabled
hermes mcp test buttonsbebe_kb        # (or _redo / _gorgias) → Connected, N tools
systemctl status buttonsbebe-processor buttonsbebe-kb-mcp buttonsbebe-redo-mcp buttonsbebe-gorgias-mcp
journalctl -u buttonsbebe-processor -n 50
cd "/root/Buttonsbebe Agent/KB" && ./search.sh "do you ship to canada"   # test KB
./sync-products.sh                    # manual product refresh (else every 3 days)
sqlite3 "/root/Buttonsbebe Agent/webhook/data/webhook.db" "select status,count(*) from jobs group by status"
```

## 11. Known gaps (from the 2026-07-07 audit — see `INCONSISTENCIES.md`)

- The deterministic `classifier.py` is still a stub; the old feedback poller is superseded by Console-action learning.
- **Doc drift:** many local files describe the retired design; **this file is the current truth.**
  Old `PROJECT-SOURCE-OF-TRUTH.md`, `kb/README.md`, `GOAL.md`, `docs/hermes-rearchitecture/`,
  `build/` should be archived or rewritten.
- **`.env` duplication** across two files. Shopify "code half": `webhook/config.py` still
  reads a static token field, not the client-cred keys — only matters if the webhook ever
  calls Shopify directly (it doesn't today).
- Confirm the exact **systemd unit for the :8000 webhook receiver**.
- The processor runs Hermes with **`--yolo`** (auto-approves tool calls). Safe today because
  the only write is a staff-only internal note, but worth knowing.
```
