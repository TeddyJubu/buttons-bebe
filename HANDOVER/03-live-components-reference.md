# 03 · Live Components — Code Reference

**What this doc covers:** a "read the actual code" guide to every source module that IS present in this repo — the three read-only MCP tools (KB / Redo / Gorgias), the KB build & maintenance scripts, the WhatsApp-Connect bridge, the Console/Dashboard single-page UIs, and the `deploy/patch_app.py` patcher — with the exact functions, endpoints, env-var names, and how each runs (systemd unit / launcher / port).

**Sources read (all under repo root `/…/Shopify help desk/`):** `tools/gorgias_mcp.py`, `tools/redo_mcp.py`, `tools/_common.py`, `tools/README.md`, `tools/run-gorgias.sh`, `tools/run-redo.sh`, `tools/buttonsbebe-gorgias-mcp.service`, `tools/buttonsbebe-redo-mcp.service`; `whatsapp-connect/server.js`, `whatsapp-connect/package.json`, `whatsapp-connect/Caddyfile`, `whatsapp-connect/buttonsbebe-whatsapp-connect.service`; `kb/scripts/{kb_mcp_server,search_kb,kb_lib,index_kb,sync_products,notices_lib,purge_notices,review_learned}.py`, plus `kb/*.service`, `kb/*.timer`, and `kb/{run_mcp,update,sync-products,search}.sh`; `console-src/index.html`, `dashboard/index.html`, `kb-admin/server.js`, `kb-admin/buttonsbebe-kb-admin.service`; `deploy/patch_app.py`. Behaviour below is what the code literally does; where the running system depends on code that is NOT in this repo, it is flagged.

> **Repo-scope caveat (important for a fresh clone):** the **webhook/orchestrator app (`webhook/src/bb_webhook/…`, the `processor/`, and the `feedback/` review module wiring) that actually serves `/console/api/*` and `/dashboard/*` is NOT in this repo** — only a copy exists in `_VPS-FULL-BACKUP-20260706/`. So several endpoints the UI calls, and the file `deploy/patch_app.py` edits, live outside the code you can read here. Each such gap is called out below. (The `feedback/` **Python package** itself — `pii.py`, `review.py`, `config.py`, `store.py`, etc. — IS in the repo and is imported by `review_learned.py` and by the routes `patch_app.py` injects.)

---

## Summary table

| Module | Language | Port / Unit | One-line purpose |
|---|---|---|---|
| `kb/scripts/kb_mcp_server.py` | Python (FastMCP) | **8077** · `buttonsbebe-kb-mcp` | Exposes the single read-only `search_kb` MCP tool to Hermes. |
| `kb/scripts/search_kb.py` | Python | (library + CLI) | Hybrid (vector + keyword) KB search with Notice-Board overrides prepended. |
| `kb/scripts/kb_lib.py` | Python | (library) | Shared KB helpers: file discovery, `##`-chunking, local embeddings, row schema. |
| `kb/scripts/index_kb.py` | Python | (CLI via `update.sh`) | (Re)builds the LanceDB index (vectors + FTS) from the markdown KB. |
| `kb/scripts/sync_products.py` | Python | (CLI; timer `buttonsbebe-kb-sync`) | Pulls Shopify products → one markdown file each in `kb/products/`. |
| `kb/scripts/notices_lib.py` | Python | (library) | Notice Board: owner overrides that ride on top of every search result. |
| `kb/scripts/purge_notices.py` | Python | `buttonsbebe-kb-notices-gc` (15-min timer) | Physically drops expired notices (housekeeping). |
| `kb/scripts/review_learned.py` | Python | (manual CLI) | Human gate: promote captured `kb/learned/` packets into indexed `kb/tickets/` exemplars. |
| `tools/redo_mcp.py` | Python (FastMCP) | **8078** · `buttonsbebe-redo-mcp` | Read-only Redo Returns MCP tool (returns/RMAs). |
| `tools/gorgias_mcp.py` | Python (FastMCP) | **8079** · `buttonsbebe-gorgias-mcp` | Read-only Gorgias MCP tool (tickets / messages / customers). |
| `tools/_common.py` | Python | (library) | Shared `.env` loader for the Redo + Gorgias tools. |
| `whatsapp-connect/server.js` | Node (Express + Baileys) | **8085** · `buttonsbebe-whatsapp-connect` | WhatsApp QR pairing page, 2-way Hermes bridge, escalation-alert delivery. |
| `kb-admin/server.js` | Node (stdlib http) | **8087** · `buttonsbebe-kb-admin` | Owner API behind the console for editing KB files + Notice Board. |
| `console-src/index.html` | HTML/JS SPA | served as `/console` (via Caddy→:8000) | Support Console UI — **current** build (adds Notice Board tab). |
| `dashboard/index.html` | HTML/JS SPA | served as `/console` (via Caddy→:8000) | Same Support Console — **older** build (no Notice Board tab). |
| `deploy/patch_app.py` | Python | (deploy script, runs on VPS) | Idempotently injects the (now-superseded) feedback "review console" routes into the webhook `app.py`. |

**All three MCP services (`8077` KB, `8078` Redo, `8079` Gorgias) bind `127.0.0.1` only and are strictly read-only (GET requests to external APIs; no writes).** They are registered with Hermes by URL as `buttonsbebe_kb`, `buttonsbebe_redo`, `buttonsbebe_gorgias`.

---

## 1. MCP tool modules (read-only, localhost-only)

### 1.1 KB search MCP — `kb/scripts/kb_mcp_server.py`

- **Purpose:** wraps the KB search engine as exactly **one** MCP tool for Hermes. Nothing else is exposed.
- **Tool exposed:** `search_kb(query: str, k: int = 5) -> list[dict]` — "Search the Buttons Bebe knowledge base … each result has a relevance score and a risk label (`sensitive: true` means escalate)." It simply calls `search()` from `search_kb.py`.
- **FastMCP server name:** `buttonsbebe-kb`.
- **Inputs/outputs:** in = query string + result count `k`; out = list of result dicts (see `search_kb.py`).
- **Env vars:** `KB_MCP_TRANSPORT` (`stdio` default | `streamable-http` | `sse`), `KB_MCP_HOST` (default `127.0.0.1`), `KB_MCP_PORT` (default `8077`).
- **How it runs:** systemd unit **`buttonsbebe-kb-mcp`** (`kb/buttonsbebe-kb-mcp.service`) sets `KB_MCP_TRANSPORT=streamable-http`, `KB_MCP_HOST=127.0.0.1`, `KB_MCP_PORT=8077` and `ExecStart=/root/kb-mcp-run.sh` (⚠️ launcher on VPS — see §6). In non-stdio mode the server pre-loads the embedding model (`_get_model()`) before serving so the first query is fast. Repo launcher source is `kb/run_mcp.sh` (execs `KB/.venv/bin/python scripts/kb_mcp_server.py`). CLI test path: `kb/search.sh "…"`.

### 1.2 KB hybrid search — `kb/scripts/search_kb.py`

- **Purpose:** the actual retrieval logic behind `search_kb`. Runs vector search and keyword search in parallel and blends them.
- **Key function:** `search(query, k=5) -> list[dict]`. Steps: (1) meaning search — `embed_query()` then `table.search(qv).metric("cosine").limit(POOL)`; (2) keyword search — `table.search(query, query_type="fts")` (BM25), degrading to empty list if the FTS index isn't ready; (3) blend both with **Reciprocal Rank Fusion** (`RRF_K=60`, `POOL=20` per method, return top `k`, default `K=5`).
- **Notice-Board overlay:** imports `notices_lib.as_search_results` and **prepends active owner overrides** to the results (`return notices + results`). Wrapped in try/except and a fallback stub so the board can never break search.
- **Inputs/outputs:** in = query, `k`; out = list of dicts `{score, file, title, category, status, sensitive(bool), heading, text}` (notice rows use `score=999.0`, `title="NOTICE BOARD"`).
- **Env vars:** none directly (uses `kb_lib` constants). **How it runs:** imported by `kb_mcp_server.py`; also a CLI `main()` (`./search.sh "question"`).

### 1.3 KB shared library — `kb/scripts/kb_lib.py`

- **Purpose:** everything the indexer and the search tool both need — file discovery, chunking, embeddings, and the index row schema.
- **Key constants:** `KB_DIR` (the `kb/` folder), `DB_DIR = kb/lancedb`, `TABLE = "kb"`, `CONTENT_FOLDERS = [intents, faq, policies, tickets, products]` (trust order, highest first), `SENSITIVE_TAGS = {sensitive, escalation, refund, chargeback, dispute}`. `learned/` is **deliberately not indexed**.
- **Embedding model:** `MODEL_NAME = sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` via `fastembed.TextEmbedding`, `VECTOR_DIM = 384`. Local, CPU, ~0.2 GB, multilingual (incl. Hebrew), no API key, nothing leaves the box. Lazy singleton `_get_model()`.
- **Key functions:** `embed_passages(list)`, `embed_query(str)`; `_chunks_by_heading(body)` (splits a file into one chunk per `##` section; preamble before the first `##` is ignored; whole body is one chunk if there is no `##`); `_iter_content_files()` (walks `CONTENT_FOLDERS`, skips files starting `_`/`.` and any `readme.md`); `load_rows()` (reads front-matter + chunks → row dicts; `sensitive` computed as `tags ∩ SENSITIVE_TAGS`; searchable text = `"{title} -- {heading}\n\n{chunk}"`).
- **Row schema `KBChunk` (LanceModel):** `id` (sha1 of `"{rel}::{i}"`, 16 hex), `file`, `title`, `category`, `status` (`confirmed`|`DRAFT`), `source`, `tags`, `heading`, `sensitive` (bool), `text`, `vector(384)`.
- **Env vars:** none. **How it runs:** pure library.

### 1.4 Redo Returns MCP — `tools/redo_mcp.py`

- **Purpose:** read-only Redo (returns/RMAs) exposed as a Hermes MCP tool.
- **FastMCP server name:** `buttonsbebe-redo`. **Hermes name:** `buttonsbebe_redo`.
- **Tools exposed (all read-only GET):**
  - `list_recent_returns(limit=10)` — recent returns across the store.
  - `get_returns_for_order(order_name)` — returns for a Shopify order (`#12345` or `12345`; queried as `shopify_order_name`).
  - `get_return(return_id)` — one return by Redo id.
- **Upstream call:** `GET https://api.getredo.com/v2.2/stores/{REDO_STORE_ID}/returns…` with header `Authorization: Bearer {REDO_API_KEY}` and `User-Agent: ButtonsBebe-Hermes/1.0`, 20 s timeout. `_trim()` keeps support-relevant fields (`id, status, state, order_name, refund_amount, items, tracking…`) and falls back to the whole object. Errors are returned as `{"error": …}` dicts — exceptions never cross the MCP boundary.
- **Inputs/outputs:** in = limit / order name / return id; out = `{count, returns:[…]}` or a single trimmed return, or an error dict.
- **Env vars:** `REDO_API_KEY`, `REDO_STORE_ID` (read via `_common.load_env`), plus `REDO_MCP_HOST` (default `127.0.0.1`), `REDO_MCP_PORT` (default `8078`), `REDO_MCP_TRANSPORT` (`stdio` default).
- **How it runs:** unit **`buttonsbebe-redo-mcp`** (`tools/buttonsbebe-redo-mcp.service`) sets `REDO_MCP_TRANSPORT=streamable-http`, host `127.0.0.1`, port `8078`, `ExecStart=/root/redo-mcp-run.sh` (⚠️ on VPS). Repo launcher source `tools/run-redo.sh` → execs `tools/.venv/bin/python redo_mcp.py`.

### 1.5 Gorgias helpdesk MCP — `tools/gorgias_mcp.py`

- **Purpose:** read-only Gorgias exposed as a Hermes MCP tool. **Writes (posting internal notes) are intentionally NOT exposed here** — the note write-back happens elsewhere (the processor's `gorgias_writer.py`, on the VPS), not through this tool.
- **FastMCP server name:** `buttonsbebe-gorgias`. **Hermes name:** `buttonsbebe_gorgias`.
- **Tools exposed (all read-only GET):**
  - `list_recent_tickets(limit=10)` — trims to `id, subject, status, channel, created/updated_datetime` (caps limit at 30).
  - `get_ticket(ticket_id)`.
  - `get_ticket_messages(ticket_id, limit=30)` — the conversation (caps at 50).
  - `get_customer(customer_id)` — "includes synced Shopify order context."
  - `search_customer(email)` — find a customer by email.
- **Upstream call:** `GET https://{SUBDOMAIN}.gorgias.com/api…` with **HTTP Basic auth** `(GORGIAS_API_EMAIL, GORGIAS_API_KEY)` and an explicit `User-Agent: ButtonsBebe-Hermes/1.0` — the code comment notes Gorgias's WAF returns 403 to the default urllib UA. `_bare_subdomain()` normalizes the subdomain (strips scheme, `/`, and a trailing `.gorgias.com`). Errors returned as dicts, never raised.
- **Inputs/outputs:** in = ids/email/limits; out = raw Gorgias JSON (or the trimmed ticket list), or an error dict. Note (from `tools/README.md`): Gorgias pagination uses `limit`, not `per_page`.
- **Env vars:** `GORGIAS_SUBDOMAIN`, `GORGIAS_API_EMAIL`, `GORGIAS_API_KEY` (via `_common.load_env`), plus `GORGIAS_MCP_HOST` (default `127.0.0.1`), `GORGIAS_MCP_PORT` (default `8079`), `GORGIAS_MCP_TRANSPORT` (`stdio` default).
- **How it runs:** unit **`buttonsbebe-gorgias-mcp`** (`tools/buttonsbebe-gorgias-mcp.service`) sets `GORGIAS_MCP_TRANSPORT=streamable-http`, host `127.0.0.1`, port `8079`, `ExecStart=/root/gorgias-mcp-run.sh` (⚠️ on VPS). Repo launcher source `tools/run-gorgias.sh` → execs `tools/.venv/bin/python gorgias_mcp.py`.

### 1.6 Shared env loader — `tools/_common.py`

- **Purpose:** one tiny helper shared by `redo_mcp.py` + `gorgias_mcp.py` for reading the agent `.env`.
- **Key function:** `load_env() -> dict` — reads, in order, `/root/Buttonsbebe Agent/.env` then `/root/Buttonsbebe Agent/webhook/.env`; **first non-empty value wins** (so MAIN `.env` takes precedence). `_clean()` strips paste artifacts (surrounding quotes/space, trailing backslashes, CR). Skips blanks / comments / lines without `=`.
- **Env vars:** reads whatever keys the callers ask for (Redo + Gorgias creds). **How it runs:** pure library.

### 1.7 `tools/README.md`

Documents the two integration modules as their own module + MCP server + service + port + Hermes tool, all read-only, sharing one venv (`tools/.venv`) and `_common.py`. Confirms Redo=`8078`/`buttonsbebe-redo-mcp`/`buttonsbebe_redo` and Gorgias=`8079`/`buttonsbebe-gorgias-mcp`/`buttonsbebe_gorgias` (both LIVE), points at the KB tool as a separate module on `8077`, and lists the `systemctl` / `journalctl` / `hermes mcp test` management commands.

---

## 2. KB build & maintenance scripts

### 2.1 Index builder — `kb/scripts/index_kb.py`

- **Purpose:** (re)build the LanceDB search index from the markdown KB. Old index is overwritten each run, so it always matches current files.
- **Flow (`main`):** a non-blocking lock prevents overlapping rebuilds; `load_rows()` → `embed_passages()` for each chunk → build the table and FTS index in a sibling staging directory → promote the completed directory with rollback to the prior `lancedb/` index on failure. Vector cardinality is checked before staging.
- **Inputs/outputs:** in = markdown under the content folders; out = the `kb/lancedb/kb` table. **Env vars:** none.
- **How it runs:** CLI. Invoked by `kb/update.sh` (`./.venv/bin/python scripts/index_kb.py`) and by `kb/sync-products.sh` after a product sync. The Node kb-admin `/reindex` route (see §5) shells out to `update.sh`.

### 2.2 Shopify product sync — `kb/scripts/sync_products.py`

- **Purpose:** fetch Buttons Bebe products from Shopify and write one concise markdown file per product into `kb/products/` (KB conventions: front-matter + a single `## Product details` section = one chunk).
- **Key functions:** `load_creds()` (reads `.env` candidates), `mint_token()` (client-credentials grant → 24 h Admin API token), `gql()` (Admin GraphQL POST), `run_bulk_export()` (Shopify **Bulk Operations** query for `products`, then polls `currentBulkOperation` until `COMPLETED`), `download_jsonl()`, `split_records()` (separates products from their variants via `__parentId`), `write_files()` (clears old `product-*.md`, writes `product-{handle}.md` with title/type/vendor, in-stock flag, options, up to 60 variants w/ SKU + price + availability, truncated description, product URL, tags).
- **Inputs/outputs:** in = Shopify catalog; out = markdown files in `kb/products/` (returns count written).
- **Env vars:** `SHOPIFY_SHOP` (required), `SHOPIFY_CLIENT_ID` (required), `SHOPIFY_CLIENT_SECRET` (required), `SHOPIFY_API_VERSION` (default `2026-04`), `SHOPIFY_PRODUCT_QUERY` (default `status:active`; set `""` for all). **Shopify auth = client-credentials**, so no manual token needed.
- **How it runs:** CLI via `kb/sync-products.sh` (runs `sync_products.py`, then `index_kb.py`, then `systemctl restart buttonsbebe-kb-mcp`). Scheduled by unit **`buttonsbebe-kb-sync.service`** (`Type=oneshot`, `ExecStart="/root/Buttonsbebe Agent/KB/sync-products.sh"`, 1800 s timeout) + **`buttonsbebe-kb-sync.timer`** (`OnActiveSec=3d`, `OnUnitActiveSec=3d`, `Persistent=true`) → **every 3 days**.

### 2.3 Notice Board library — `kb/scripts/notices_lib.py`

- **Purpose:** owner-posted notices that **override all other KB answers while live**, with optional expiry. Storage is `kb/notices/notices.json`; the schema is a **shared contract** with the Node `kb-admin/server.js` (both read/write the same JSON).
- **Key functions:** `load_all()`, `active_notices(now)`, `is_active(n)` (expired the instant `expires_at <= now`), `add_notice(text, expires_at, created_by)`, `remove_notice(id)`, `purge_expired(now)` (physical delete; returns count), `as_search_results(now)` (shapes each active notice like a `search_kb` result: `score=999.0`, `title="NOTICE BOARD"`, `heading="Owner override"`, text prefixed with the loud `OVERRIDE_PREFIX` marker). Writes are atomic (temp file + `os.replace`). Every reader is fail-safe (empty board on any error), so a missing/corrupt file can never break customer search.
- **Notice record:** `{id: "n_<ms>_<hex>", text, created_at, expires_at|null, created_by}`.
- **Env vars:** none. **How it runs:** library — consumed by `search_kb.py` (overlay) and `purge_notices.py` (GC); the Node `kb-admin` service writes the same file.

### 2.4 Notice GC — `kb/scripts/purge_notices.py`

- **Purpose:** housekeeping — physically drop expired notices (expiry is already enforced at read time in `notices_lib`).
- **Key behaviour:** imports `notices_lib.purge_expired()`, prints how many were removed. **Stdlib only** → no virtualenv needed.
- **Env vars:** none. **How it runs:** unit **`buttonsbebe-kb-notices-gc.service`** (`Type=oneshot`, `ExecStart=/usr/bin/python3 "…/KB/scripts/purge_notices.py"`) + **`buttonsbebe-kb-notices-gc.timer`** (`OnBootSec=5min`, `OnUnitActiveSec=15min`) → **every 15 minutes**.

### 2.5 Learned-feedback review gate — `kb/scripts/review_learned.py`

- **Purpose:** the **only** (and deliberately manual) path from `kb/learned/` (a holding pen the indexer ignores) into `kb/tickets/` (indexed, must be PII-free). Turns captured packets into live "exemplar" files.
- **Commands (`argparse`):** `list` (pending packets), `show <ticket_id>`, `approve <ticket_id> --pii-cleared [--dry-run]`, `reject <ticket_id> [--purge]`, `reindex`, `stats`.
- **Safety by design:** `approve` **refuses without `--pii-cleared`** and always prints the PII highlighter findings first (the regex cannot catch names — the human is the control). On approve it writes a **DRAFT** exemplar `kb/tickets/exemplar-learned-{id}-{slug}.md` with `status: needs_final_edit` and identifiers masked to placeholders; the reviewer edits it, sets `status: confirmed`, then runs `reindex` (batched separately so the index is never half-built). `reject` archives to an underscore folder (never indexed) or `--purge` deletes.
- **Repo dependencies (present):** `from feedback import config, pii, store` — uses `config.LEARNED_DIR`, `config.TICKETS_DIR`, `config.ARCHIVE_DIR`, `config.KB_ROOT/update.sh`; `pii.mask()` / `pii.summary()`; `store.stats()`.
- **Env vars:** none. **How it runs:** manual CLI (`python kb/scripts/review_learned.py …`); no systemd unit.

> **Note (per `CLAUDE.md` §8):** this poll/packet-based review flow is **superseded** by the console per-ticket action buttons + the nightly `auto_promote_learned.py` learning loop. `review_learned.py` still works but is not the primary path anymore.

**KB shell helpers (thin wrappers, all `cd` into `kb/` and use `.venv`):** `kb/run_mcp.sh` (→ VPS `/root/kb-mcp-run.sh`, starts the MCP server), `kb/update.sh` (re-index), `kb/sync-products.sh` (sync + re-index + restart), `kb/search.sh "q"` (CLI search test).

---

## 3. WhatsApp Connect — `whatsapp-connect/`

### 3.1 `server.js` (Node · Express + Baileys)

- **Purpose:** serve a live, auto-refreshing WhatsApp **QR pairing page**; once the owner links their phone, run a **2-way Hermes bridge** (owner's self-chat ⇄ Hermes) and **deliver escalation alerts** POSTed by the support pipeline.
- **Runtime/state:** binds `127.0.0.1:${WA_PORT}` (default `8085`). Uses `@whiskeysockets/baileys` `useMultiFileAuthState` (creds in `WA_AUTH_DIR`). State machine `starting | qr | connected`; QR rendered to a data-URL via `qrcode`. On `loggedOut` it **wipes the auth dir and regenerates a fresh QR** (avoids an infinite re-login loop); on other disconnects it reconnects.
- **Security model (in code):** `messages.upsert` ignores everything except `msg.key.fromMe` **and** `remoteJid === ownerJid` — i.e. **only the owner's own "Note to Self" chat can reach Hermes**; a stranger messaging the number can never reach the AI. Bot-sent message ids are tracked (`botSentIds`) so Hermes never replies to itself.
- **Hermes bridge:** `forwardToHermes(text, jid)` runs `execFile(HERMES_BIN, ["-z", text], {timeout:150000, maxBuffer:4MB})` and sends stdout back (sliced to 4000 chars).
- **Alert delivery:** `sendAlert(text)` resolves a destination via `destJid()` — either the linked owner account (`mode:"linked"`, default/most secure) or a specific typed number (`mode:"number"`), read from `WA_NOTIFY_FILE` (`notify.json`). Fails with 409 if not connected / no destination.
- **HTTP endpoints:**
  - `GET  {BASE}/status` — *(Basic-auth gated)* `{state, qr, owner}` (`BASE = /connect-whatsapp/${WA_TOKEN}`).
  - `POST {BASE}/send` — `{text}` → deliver an alert. Requires the dedicated `WA_SEND_SECRET` as a Bearer token or HTTP Basic password; the path token alone cannot authorize a send.
  - `GET  {BASE}/` — *(Basic-auth gated)* the pairing HTML page (`{BASE}` redirects to `{BASE}/`; any other `/connect-whatsapp/*` → 404).
  - `GET  /wa/status` — `{state, qr, owner, notify}`.
  - `GET  /wa/notify` · `PUT /wa/notify` — read / set alert destination (`{mode:"linked"|"number", number}`; validates the number).
  - `POST /wa/test` — send a test alert to the current destination.
  - `POST /wa/logout` — unlink WhatsApp and force a fresh QR.
  - The `/wa/*` JSON API is (per the file's own header comment) reached only via Caddy at **`/console/waapi/*`** behind the console's auth gate — which is why those routes carry no WhatsApp password of their own.
- **Auth:** the pairing page uses HTTP Basic with `WA_PASSWORD`; the escalation sender uses the separate `WA_SEND_SECRET`. Comparisons are constant-time. `WA_TOKEN`, `WA_PASSWORD`, and `WA_SEND_SECRET` must be different, non-placeholder secrets or the service refuses to start.
- **Env vars:** `WA_PORT` (8085), `WA_TOKEN` (secret path segment), `WA_PASSWORD` (pairing-page password), `WA_SEND_SECRET` (escalation sender credential), `WA_AUTH_DIR` (`./auth`), `WA_NOTIFY_FILE` (`./notify.json`), `HERMES_BIN` (`hermes`).
  - ⚠️ **Deployment requirement:** generate all three secrets in the dedicated `whatsapp-connect/.env`, rotate the former path/password values, and configure the VPS-only processor to authenticate its escalation POST before restarting the service. Keep this file separate from MAIN `.env` so Hermes cannot inherit unrelated commerce credentials.
- **How it runs:** unit **`buttonsbebe-whatsapp-connect`** — see §3.4.

### 3.2 `package.json`

CommonJS package `buttonsbebe-whatsapp-connect` v1.0.0. Dependencies: `@whiskeysockets/baileys ^6.7.9`, `express ^4.19.2`, `pino ^9.5.0`, `qrcode ^1.5.4`. (Install with `npm install` in `whatsapp-connect/`.)

### 3.3 `Caddyfile` (public entry / reverse proxy)

For host `srv1766050.hstgr.cloud` (Caddy auto-manages Let's Encrypt TLS):
- `handle /connect-whatsapp/*` → `reverse_proxy 127.0.0.1:8085` (the WhatsApp service).
- `handle { … }` (everything else) → `reverse_proxy 127.0.0.1:8000` (the webhook / dashboard FastAPI app) with `Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto` set.
- Global: `request_body max_size 256KB`, security headers (`X-Content-Type-Options nosniff`, `Referrer-Policy no-referrer`), `encode gzip zstd`, JSON access log to `/var/log/bb-webhook/caddy.log`.
- ⚠️ **Gap to verify:** this repo Caddyfile does **not** contain the `/console/waapi/*` → `:8085` (nor `/console/kbapi/*` → `:8087`) mappings that `server.js`/`kb-admin` comments rely on. Those routes are either added by a fuller Caddy config on the VPS or proxied by the `:8000` app. Confirm on the live box.

### 3.4 `buttonsbebe-whatsapp-connect.service`

`WorkingDirectory=/root/Buttonsbebe Agent/whatsapp-connect`; `ExecStart=/bin/bash -c 'exec __NODE__ ".../server.js"'`; `Restart=on-failure`. The unit reads `whatsapp-connect/.env` through `EnvironmentFile` for `WA_TOKEN`, `WA_PASSWORD`, and `WA_SEND_SECRET`; it sets `WA_PORT=8085` and `WA_AUTH_DIR` directly. The `__HERMES_BIN__` and `__NODE__` tokens are **placeholders substituted at deploy time** (absolute executable paths).

---

## 4. Console / Dashboard UI — `console-src/index.html` & `dashboard/index.html`

### 4.1 What they are

Both files are the **same single-file SPA** titled *"Buttons Bebe — Support Console"* — vanilla HTML/CSS/JS, no framework, no build step. They are the **source** of the owner-facing console that is deployed on the VPS at `/var/www/console/index.html` (per `SPRINT-notice-board-2026-07-12.md`) and fronted by Caddy on `:8000`.

- **`console-src/index.html` (529 lines) is the CURRENT build** — it adds the **"Notice Board"** tab (post/expire owner overrides).
- **`dashboard/index.html` (470 lines) is the OLDER build** — identical app **minus** the Notice Board feature. A full diff is ~79 lines and is *exactly* the notices additions: `noticesData` state, the notices nav icon, `notices` in the `pages`/`subs` maps + tab routing, and `noticesView()` / `loadNotices()` / `postNotice()` / `fmtWhen()` / delete handler. Treat `console-src/` as authoritative; `dashboard/` is a superseded snapshot kept in the repo.

**Tabs (current build):** Overview, Ticket feed, Connections, Knowledge base, Notice Board, Notifications, Settings. Data auto-loads via `jget()` (`fetch`, `no-store`) and the header "Agent live" dot re-boots/refreshes on click; the Notifications tab polls `/console/waapi/status` every 3 s.

### 4.2 Backend endpoints the UI calls

Three base paths (defined at the top of the script): `API="/console/api"`, `KBAPI="/console/kbapi"`, `WAAPI="/console/waapi"`.

**`/console/api/*` — served by the webhook app (`:8000`; source NOT in this repo):**
- `GET  /console/api/stats` — overview metrics.
- `GET  /console/api/tickets?limit=60` — ticket feed (each with its AI draft).
- `GET  /console/api/settings` · `PUT /console/api/settings` (`{gorgias_writes_enabled}`) — the writes on/off toggle.
- `GET  /console/api/learning` — learning-loop card data.
- `POST /console/api/ticket/{id}/send` — **send the (edited) draft to the customer** (public reply).
- `POST /console/api/ticket/{id}/note` — post the draft as an **internal note** in Gorgias.
- `POST /console/api/ticket/{id}/rewrite` — `{draft, instruction, message_text}` → Hermes rewrites to the instruction, returns `{draft}`.

**`/console/kbapi/*` — served by `kb-admin/server.js` (`:8087`):**
- `GET /list`, `GET /file?path=`, `POST /save`, `POST /new`, `POST /reindex`, `GET /reindex-status`.
- `GET /notices`, `POST /notices`, `POST /notices/delete` (Notice Board CRUD).

**`/console/waapi/*` — served by `whatsapp-connect/server.js` `/wa/*` (`:8085`):**
- `GET /status`, `PUT /notify`, `POST /test`, `POST /logout`.

### 4.3 Relationship to the webhook app's `/dashboard`

The `CLAUDE.md` architecture map documents the ticket-action endpoints as **`POST /dashboard/api/ticket/{id}/send|note|rewrite`** on the webhook app (`:8000`). The **shipped console instead calls `/console/api/ticket/{id}/…`**. So the same webhook FastAPI app exposes the ticket actions, but the deployed console is mounted under a `/console/*` prefix (and `/console/kbapi`, `/console/waapi` are fanned out to the kb-admin and whatsapp-connect services). ⚠️ **The webhook app source is not in this repo** (only in `_VPS-FULL-BACKUP-20260706/`), so the exact `/console` ↔ `/dashboard` routing/mounting cannot be verified here — **the new team should confirm it against the live `bb_webhook/app.py`.** Practically: `dashboard/index.html` reflects the older `/dashboard`-era console; `console-src/index.html` is the current one.

### 4.4 Supporting service — `kb-admin/server.js` (Node, `:8087`, unit `buttonsbebe-kb-admin`)

Zero-dependency Node HTTP server bound to `127.0.0.1:8087`, exposed behind the console auth gate at `/console/kbapi/*`. Lets the console read/edit KB markdown and re-index. **Only `intents/ faq/ policies/ tickets/` are writable** (`products/` and `learned/` excluded); paths are strictly validated (`safePath()` — no traversal, `folder/name.md` only). Routes: `GET /list`, `GET /file`, `POST /save`, `POST /new` (backs up on overwrite), `POST /reindex` (spawns `KB/update.sh`), `GET /reindex-status`, `GET /notices`, `POST /notices`, `POST /notices/delete` (writes the same `notices.json` as `notices_lib.py`, atomic swap). Env: `KB_ADMIN_PORT` (8087), `KB_DIR`. Unit `buttonsbebe-kb-admin.service` runs `__NODE__ "…/kb-admin/server.js"` (node path substituted at deploy).

---

## 5. `deploy/patch_app.py`

- **Purpose:** an **idempotent deploy-time patcher** that injects the (older) **feedback "review console"** routes into the live webhook app at `/root/Buttonsbebe Agent/webhook/src/bb_webhook/app.py` (a file that lives on the VPS, **not** in this repo).
- **What it patches in:**
  1. A `sys.path` **shim** (inserts the agent root, `parents[3]`, so `from feedback import …` works), placed right after the existing `from pathlib import Path` import.
  2. A block of **routes** inserted just before the anchor `@app.post("/webhook/gorgias/{tenant_id}")`:
     - `GET  /dashboard/review` → serves `review_console.html` (read from beside `app.py`; repo source is `deploy/review_console.html`).
     - `GET  /dashboard/api/review/list`
     - `GET  /dashboard/api/review/packet/{ticket_id}`
     - `POST /dashboard/api/review/approve/{ticket_id}` (body `{pii_cleared, note, why}`)
     - `POST /dashboard/api/review/reject/{ticket_id}` (`?purge=`)
     - `POST /dashboard/api/review/reindex`
     - all delegating to `from feedback import review as _rv`.
- **Safety:** no-ops if `/dashboard/api/review/list` is already present; errors out if the `from pathlib import Path` or webhook-receiver anchors are missing; **backs up** `app.py` to `app.py.bak-review-<timestamp>`, then `py_compile`s the result and **reverts on compile failure**.
- **Env vars:** none. **How it runs:** `python deploy/patch_app.py` on the VPS (edits a VPS file in place).
- ⚠️ **Status:** per `CLAUDE.md` §8, this poll-based "review console" is **superseded** by the console's per-ticket action buttons + the `learning.py` capture. Document it as historical/optional; the routes it adds may or may not be wired on the current box.

---

## 6. Space-free launchers (⚠️ on the VPS, not in this repo)

The `*.service` units above call three wrapper scripts by **space-free absolute paths** because the deploy directory `"/root/Buttonsbebe Agent/…"` contains a space that systemd/Hermes command runners can't pass cleanly. These deployed launchers live on the VPS:

| VPS launcher (⚠️ on VPS) | Repo source | Execs |
|---|---|---|
| `/root/kb-mcp-run.sh` | `kb/run_mcp.sh` | `KB/.venv/bin/python KB/scripts/kb_mcp_server.py` |
| `/root/redo-mcp-run.sh` | `tools/run-redo.sh` | `tools/.venv/bin/python tools/redo_mcp.py` |
| `/root/gorgias-mcp-run.sh` | `tools/run-gorgias.sh` | `tools/.venv/bin/python tools/gorgias_mcp.py` |

The repo `run-*.sh` / `run_mcp.sh` files are the source-of-truth copies; the `/root/*.sh` versions are what the units actually invoke.

---

## 7. Appendix — services & ports (from the repo unit files)

| Port | Bind | Service unit (repo file) | Component |
|---|---|---|---|
| 8077 | 127.0.0.1 | `buttonsbebe-kb-mcp` (`kb/…`) | KB `search_kb` MCP |
| 8078 | 127.0.0.1 | `buttonsbebe-redo-mcp` (`tools/…`) | Redo returns MCP |
| 8079 | 127.0.0.1 | `buttonsbebe-gorgias-mcp` (`tools/…`) | Gorgias read MCP |
| 8085 | 127.0.0.1 | `buttonsbebe-whatsapp-connect` (`whatsapp-connect/…`) | WhatsApp pairing + Hermes bridge + alerts |
| 8087 | 127.0.0.1 | `buttonsbebe-kb-admin` (`kb-admin/…`) | Console KB editor + Notice Board API |
| — | — | `buttonsbebe-kb-sync` `.service`+`.timer` | Product sync every 3 days |
| — | — | `buttonsbebe-kb-notices-gc` `.service`+`.timer` | Expired-notice GC every 15 min |
| 8000 | 127.0.0.1 | *(unit not in this repo)* | Webhook receiver + `/console`/`/dashboard` (FastAPI) — source in `_VPS-FULL-BACKUP` only |

Public entry: **Caddy** on `srv1766050.hstgr.cloud` → `/connect-whatsapp/*` to `:8085`, everything else to `:8000`.
