# 04 · Knowledge Base & Learning Loop

**What this doc covers:** the Buttons Bebe knowledge base (content layout + LanceDB hybrid search), the Shopify product auto-sync, the owner Notice Board override, and the learning loop that turns a human's real reply into reusable knowledge — with each piece marked LIVE or STUB.

**Sources read:** `kb/SEARCH-ENGINE.md`, `kb/hermes-SOUL-buttonsbebe-addition.md`, `kb/scripts/{index_kb,kb_lib,search_kb,sync_products,notices_lib,purge_notices,review_learned,kb_mcp_server}.py`, `kb/{setup,run_mcp,search,sync-products,update}.sh`, `kb/requirements.txt`, `kb/*.service` + `kb/*.timer`, `kb-admin/server.js` + `kb-admin/buttonsbebe-kb-admin.service`, all content dirs (`kb/intents/`, `kb/faq/`, `kb/policies/`, `kb/tickets/`, `kb/notices/`, `kb/learned/`), the full `feedback/` package (`README.md`, `collector.py`, `pii.py`, `pairing.py`, `similarity.py`, `validate.py`, `store.py`, `review.py`, `language.py`, `text_clean.py`, `config.py`, `gorgias_read.py`, `macro_signatures.txt`, `tests/test_all.py`), `SPRINT-feedback-collector.md`, `SPRINT-notice-board-2026-07-12.md`, and cross-checked against `CLAUDE.md` §4/§5/§8.

---

## 0. Read this before anything else — what is *not* in this clone

This repo is a **partial snapshot**, confirmed by `SPRINT-notice-board-2026-07-12.md` (risk R2) and by directory inspection. Several things `CLAUDE.md` describes as LIVE **cannot be found by cloning this repo** — they live only on the VPS at `/root/Buttonsbebe Agent/`. This matters most for the learning loop.

| Referenced in `CLAUDE.md` | Present in this repo? | Notes |
|---|---|---|
| `webhook/` (FastAPI receiver + `webhook/src/bb_webhook/learning.py`) | **No** | Whole tree absent from the clone. |
| `processor/` (orchestrator, `gorgias_writer.py`, `feedback_collector.py`, `classifier.py`) | **No** | Whole tree absent from the clone. |
| `KB/scripts/auto_promote_learned.py` (LIVE nightly promoter) | **No** | Only mentioned in `CLAUDE.md`. `grep` finds it in no file here. |
| `KB/learn-nightly.sh` + `buttonsbebe-kb-learn.timer` | **No** | Only mentioned in `CLAUDE.md`. |
| `kb/products/` (~4,200 synced product files) | **No** | Auto-generated on the VPS by `sync_products.py`; never committed. |
| `kb/README.md`, `kb/CONVENTIONS.md` | **No** | Referenced by `SEARCH-ENGINE.md` and `kb/tickets/README.md` but not in the repo. The file-format convention below is reconstructed from `kb/scripts/kb_lib.py` and the live front-matter. |

**Present and usable in this clone:** the entire `kb/` content + search engine + scripts + systemd units, the `feedback/` Python package + tests, `kb-admin/server.js` (Notice Board + KB-edit API), and both SPRINT docs.

**Path casing:** locally the folder is `kb/` (lowercase); on the VPS it is `KB/` (uppercase) — see `SPRINT-notice-board` R3. The Python scripts resolve paths relative to their own location, so both work. This doc uses lowercase `kb/` for repo paths and flags VPS-only absolute paths (`/root/Buttonsbebe Agent/...`) explicitly.

---

## 1. KB layout

The knowledge base is plain Markdown under `kb/`. Humans only ever edit Markdown; a script turns it into a search index.

### 1.1 Content folders and counts

| Folder | Count (verified) | Indexed? | What it holds |
|---|---|---|---|
| `kb/intents/` | **22** files (`intent-01…intent-22`) | Yes | One canonical "the customer asks X → do Y → say Z" pattern per file. |
| `kb/faq/` | **5** files | Yes | Grouped FAQs (shipping/tracking, returns/exchanges, sizing/care, order-changes/discounts, brands-we-carry). |
| `kb/policies/` | **17** files | Yes | Store policy source-of-truth (returns, refunds/disputes, shipping, sizing, warranty/defects, restocking, package protection, gifts, international, promo codes, agent-core-rules, escalation edge cases, etc.). |
| `kb/tickets/` | **5** exemplars + `README.md` | Yes (exemplars); `README.md` skipped | Hand-curated, fully anonymized resolved-ticket writeups used as teaching examples. This is also the **promotion target** for the learning loop. |
| `kb/products/` | **~4,200–4,246** (VPS only) | Yes | One Markdown file per Shopify product, auto-synced. **Not in this repo.** (`CLAUDE.md` §4 says 4,246; `SEARCH-ENGINE.md` says ~4,200 — the number floats with the catalog.) |
| `kb/notices/` | `notices.json` (currently `[]`) | Injected at search time, not index-time | Owner Notice Board overrides (see §4). |
| `kb/learned/` | `.gitkeep` + 1 legacy `owner-qa-*.md` | **No — deliberately excluded** | Holding pen for machine-captured material. Inert until a human promotes it. |

The 22 intents, 5 FAQs and 17 policies were confirmed by reading each file's `title:` front-matter.

### 1.2 File format (Markdown + YAML front-matter)

Reconstructed from `kb/scripts/kb_lib.py` (`load_rows`, `_chunks_by_heading`) because `kb/CONVENTIONS.md` is not in the clone. Every indexed file looks like:

```markdown
---
title: Return & Exchange Policy
category: policies
status: confirmed
source: derived-from-tickets      # optional
tags: [returns, exchanges, final-sale, refund-window]
---

## First section heading
Body text for this chunk…

## Second section heading
Body text for this chunk…
```

Front-matter fields read by the indexer (all optional, with defaults):

| Field | Default | Meaning |
|---|---|---|
| `title` | filename stem | Human title; also prepended to each chunk's searchable text. |
| `category` | top folder name | `intents` \| `faq` \| `policies` \| `tickets` \| `products` \| `learned`. |
| `status` | `confirmed` | e.g. `confirmed`, `needs_final_edit`, `review_pending`. |
| `source` | `""` | Provenance, e.g. `derived-from-tickets`, `shopify-sync`, `learned-from-ticket`. |
| `tags` | `[]` | Lower-cased, comma-joined. Used for the sensitivity flag below. |

**Chunking rule:** each `## ` (level-2, not `### `) heading starts one searchable **chunk**. Text before the first `## ` is treated as preamble and **ignored**; a file with no `##` becomes a single chunk. The text actually indexed for a chunk is `"{title} -- {heading}\n\n{chunk body}"`, so keyword and vector search both see the topic words. Each chunk gets a stable id `sha1("{relative_path}::{n}")[:16]`.

**Sensitivity flag (drives the `[SENSITIVE -> escalate]` marker):** a chunk is `sensitive: true` if its file's tags intersect `SENSITIVE_TAGS = {sensitive, escalation, refund, chargeback, dispute}` (`kb/scripts/kb_lib.py`). This is how the agent knows a topic is escalate-only rather than draft-able.

### 1.3 Indexed vs. not indexed

From `kb_lib.CONTENT_FOLDERS` and `_iter_content_files()`:

- **Indexed:** `intents/`, `faq/`, `policies/`, `tickets/`, `products/`.
- **Skipped:** the entire `learned/` folder (not in the list — intentional), any file named `readme.md` (case-insensitive), and any filename starting with `_` or `.`.
- **Injected at query time (not indexed):** Notice Board entries (§4).

> The `learned/` exclusion is the safety cornerstone of the learning loop: captured material is inert until a human/nightly job promotes it into an indexed folder.

---

## 2. The search engine — LanceDB hybrid

Fully local, no API keys, nothing leaves the box (`kb/SEARCH-ENGINE.md`). Built on **LanceDB** (embedded — no DB server) with a **local multilingual embedding model**.

### 2.1 Pieces

| File | Role |
|---|---|
| `kb/scripts/kb_lib.py` | Shared helpers: content discovery, `##` chunking, the embedding model, the `KBChunk` row schema. |
| `kb/scripts/index_kb.py` | (Re)builds the index. Invoked by `./update.sh`. |
| `kb/scripts/search_kb.py` | Runs a hybrid search; blends results; prepends notices. Invoked by `./search.sh`. |
| `kb/scripts/kb_mcp_server.py` | Wraps `search()` as the single MCP tool `search_kb` for Hermes. |
| `kb/lancedb/` | The built index (auto-generated; not committed). |
| `kb/.venv/` | Isolated Python env (auto-built by `./setup.sh`; not committed). |

### 2.2 The embedding model

Defined in `kb_lib.py`:

- **Library:** `fastembed` (`TextEmbedding`).
- **Model:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` — small, CPU-only, ~0.2 GB, 50+ languages **including Hebrew** (Buttons Bebe gets Hebrew tickets).
- **Vector dim:** 384. Downloaded once on first index; then fully offline.

### 2.3 How `index_kb.py` builds the index

`./update.sh` → `index_kb.py:main()`:

1. `load_rows()` reads every indexed file, splits into `##` chunks, builds one `KBChunk` dict per chunk (id, file, title, category, status, source, tags, heading, `sensitive`, text).
2. `embed_passages()` turns each chunk's text into a 384-d vector.
3. A non-blocking `.index_kb.lock` prevents overlapping rebuilds. The indexer connects to a sibling staging directory, creates the `kb` table, adds all rows, and builds the FTS index there.
4. Only after the complete staged build succeeds is it promoted to `kb/lancedb/`; failures restore the prior directory. Empty input and embedding-count mismatches leave the existing index untouched.
5. `table.create_fts_index("text", replace=True, use_tantivy=False)` builds the **keyword (BM25/full-text)** index — the other half of hybrid search. Falls back to the default tantivy path on older LanceDB via a `TypeError` guard.

### 2.4 How `search_kb.py` serves a query

`search(query, k=5)` runs **two searches and fuses them** (`RRF` = reciprocal rank fusion):

1. **Vector / meaning search:** `table.search(vec).metric("cosine").limit(20)`.
2. **Keyword / full-text search:** `table.search(query, query_type="fts").limit(20)` (falls back to vector-only if the FTS index isn't ready).
3. **Blend:** for each hit, `score += 1 / (RRF_K + rank + 1)` with `RRF_K = 60`; take the top `k = 5`.
4. **Prepend Notice Board:** active owner overrides (`notices_lib.as_search_results()`) are added to the front, wrapped in `try/except` so the board can never break search.

Each returned result is a dict: `score, file, title, category, status, sensitive, heading, text`. In `./search.sh` output, a `sensitive` chunk prints `  [SENSITIVE -> escalate]`.

Tuning constants live at the top of `search_kb.py`: `K=5`, `POOL=20`, `RRF_K=60`.

### 2.5 The MCP tool + always-on service

`kb/scripts/kb_mcp_server.py` exposes **exactly one** read-only tool:

```python
search_kb(query: str, k: int = 5) -> list[dict]
```

Transport is chosen by env var `KB_MCP_TRANSPORT`:

- `streamable-http` (production) — always-on HTTP MCP server on `127.0.0.1:8077`, keeps the model loaded in memory (pre-warmed before serving). Hermes connects by URL (`http://127.0.0.1:8077/mcp`), registered as MCP server `buttonsbebe_kb`.
- `stdio` (default/fallback) — Hermes spawns the script per session (cold-start each time).
- Env: `KB_MCP_HOST` (default `127.0.0.1`), `KB_MCP_PORT` (default `8077`).

### 2.6 Shell entry points & dependencies

| Script | Does |
|---|---|
| `kb/setup.sh` | One-time: create `.venv`, `pip install -r requirements.txt`, build the first index (downloads the model once). |
| `kb/update.sh` | Rebuild the index (`index_kb.py`). Run after editing content. The running service picks up the new index automatically (no restart needed). |
| `kb/search.sh "question"` | Test search: `./search.sh "how long does shipping take"`. |
| `kb/sync-products.sh` | Sync products + reindex + restart the KB service (see §3). |
| `kb/run_mcp.sh` | Launcher for the MCP server. Note: the deployed copy lives at the **space-free** path `/root/kb-mcp-run.sh` because the VPS folder name (`Buttonsbebe Agent`) contains a space the agent's command runner can't pass. |

`kb/requirements.txt` (pinned):

```
lancedb==0.34.0
fastembed==0.8.0
python-frontmatter==1.3.0
mcp==1.26.0
requests==2.32.4
PyYAML==6.0.3
```

The KB venv also runs the Shopify product sync and the shadow learning scripts,
so `requests`, `PyYAML`, and the Python MCP SDK are declared here rather than
being assumed to come from the separate `tools/.venv`.

### 2.7 systemd units for the KB (in `kb/`)

| Unit file | Type | Bind / schedule | Purpose |
|---|---|---|---|
| `buttonsbebe-kb-mcp.service` | simple | `KB_MCP_TRANSPORT=streamable-http`, `127.0.0.1:8077`, `ExecStart=/root/kb-mcp-run.sh`, `Restart=on-failure` | The always-on `search_kb` MCP server. |
| `buttonsbebe-kb-sync.service` | oneshot | `ExecStart="…/KB/sync-products.sh"`, `TimeoutStartSec=1800` | Product sync + reindex. |
| `buttonsbebe-kb-sync.timer` | timer | `OnActiveSec=3d`, `OnUnitActiveSec=3d`, `Persistent=true` | Runs the sync every 3 days. |
| `buttonsbebe-kb-notices-gc.service` | oneshot | `/usr/bin/python3 …/scripts/purge_notices.py`, `TimeoutStartSec=60` | Drops expired notices (stdlib only — no venv). |
| `buttonsbebe-kb-notices-gc.timer` | timer | `OnBootSec=5min`, `OnUnitActiveSec=15min`, `Persistent=true` | Runs the notice cleanup every 15 min. |

> The KB-edit / Notice API service (`kb-admin`, port **8087**, §4.5) is **not** listed in `CLAUDE.md` §6's port table — that table stops at 8085. Treat 8087 as a real-but-undocumented service (doc drift).

---

## 3. Product auto-sync from Shopify (LIVE)

`kb/scripts/sync_products.py` fills `kb/products/` from the live Shopify catalog. Marked **LIVE & verified** in `CLAUDE.md` §8.

### 3.1 What it does

1. **Mint a token** — `POST https://{shop}/admin/oauth/access_token` with `grant_type=client_credentials` (`SHOPIFY_CLIENT_ID` + `SHOPIFY_CLIENT_SECRET`) → a ~24h Admin API token. No long-lived token stored. Scope needed: `read_products`.
2. **Bulk export** — starts a `bulkOperationRunQuery` over `products(query: …)` (id, title, handle, productType, vendor, status, totalInventory, onlineStoreUrl, description, options, variants), then polls `currentBulkOperation` every 4s until `COMPLETED` (or errors on `FAILED/CANCELED/EXPIRED`). Built for large catalogs, no rate-limit babysitting.
3. **Download + split** — pulls the JSONL result; `split_records()` separates product nodes (`gid://shopify/Product/…`) from variant nodes (those carrying `__parentId`).
4. **Write files** — `write_files()` first **deletes all existing `product-*.md`** (so removed products don't linger), then writes one `kb/products/product-{handle}.md` per product.

### 3.2 What each product file looks like

Front-matter `category: products`, `status: confirmed`, `source: shopify-sync`, `tags: [product, <productType-slug>, <vendor-slug>]`, and a single `## Product details` section (= one chunk) with availability, sizes/options, up to 60 variants (`title`, SKU, price, in-stock/sold-out), a description truncated to ~800 chars, handle, and product-page URL. This matches the KB chunking convention so each product is one clean retrieval chunk.

### 3.3 Config & schedule

- **Credentials** are read from `.env` via `ENV_CANDIDATES = [<repo>/.env, kb/.env, <repo>/webhook/.env]` (first non-empty wins; values sanitized for paste artifacts). Required: `SHOPIFY_SHOP`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`.
- **Defaults:** `SHOPIFY_API_VERSION=2026-04`; `SHOPIFY_PRODUCT_QUERY=status:active` (only active/published products — ~4,200). Set `SHOPIFY_PRODUCT_QUERY=` (empty) to fetch **all** products.
- **On demand:** `./sync-products.sh` (runs sync → `index_kb.py` → `systemctl restart buttonsbebe-kb-mcp`).
- **Automatic:** `buttonsbebe-kb-sync.timer` every 3 days (§2.7). Check with `systemctl list-timers buttonsbebe-kb-sync.timer` and `journalctl -u buttonsbebe-kb-sync -n 30`.

---

## 4. The Notice Board — owner overrides (LIVE, shipped 2026-07-12)

A way for the store owner to post short notices that **override every other KB answer** while live, each with an optional deadline that makes it auto-expire. Built in the one-day sprint documented in `SPRINT-notice-board-2026-07-12.md`.

### 4.1 Why it exists

Product/policy data can be stale during a promotion. A notice like *"Same-day delivery, free shipping on all orders"* must beat the product data that still says *"7 days, $30"* — without rebuilding the index. So notices are **injected at the top of every `search_kb` result**, not indexed.

### 4.2 Storage & schema (`kb/notices/notices.json`)

A JSON array of notice records (shared contract between the Python `notices_lib.py` and the Node `kb-admin` API — **keep the schema stable**):

```json
{
  "id":         "n_1752350000000_ab12",
  "text":       "Same-day delivery, free shipping on all orders.",
  "created_at": "2026-07-12T20:40:00+00:00",
  "expires_at": "2026-07-14T00:00:00+00:00",   // null = stays until removed
  "created_by": "owner"
}
```

Currently the file is `[]` (no active notices) in this clone.

### 4.3 The helper (`kb/scripts/notices_lib.py`)

- `load_all()` / `active_notices()` / `is_active(n)` — reads the file; **any error returns `[]`** (fail-safe: a missing/corrupt board never breaks customer search).
- `add_notice()`, `remove_notice(id)`, `purge_expired()` — mutations; writes are **atomic** (temp file + `os.replace`).
- **Expiry is enforced at read time** in `is_active()` (`expires_at is None or expires_at > now`), so an expired notice is filtered the instant it's read — never a second late.
- `as_search_results()` shapes each active notice exactly like a `search_kb` result: `score: 999.0`, `title: "NOTICE BOARD"`, `category: notices`, `status: confirmed`, `sensitive: False`, `heading: "Owner override"`, and text prefixed with:

  > `[NOTICE BOARD — OWNER OVERRIDE. This is the current truth and OVERRIDES any conflicting policy, FAQ, or product detail below. Follow it exactly while it is posted.]`

  The `score: 999.0` keeps notices first if anything re-sorts.

### 4.4 Expiry cleanup (`kb/scripts/purge_notices.py`)

Stdlib-only script that calls `notices_lib.purge_expired()` to *physically* drop expired entries — pure housekeeping to keep the stored board and the console list tidy (expiry is already enforced at read time). Run by `buttonsbebe-kb-notices-gc.timer` every 15 minutes (§2.7).

### 4.5 How the console posts notices (`kb-admin/server.js`, port 8087)

A **zero-dependency Node** HTTP API bound to `127.0.0.1:8087`, exposed via Caddy behind the console's auth gate at `/console/kbapi/*` (`buttonsbebe-kb-admin.service`; its `ExecStart` has a `__NODE__` placeholder substituted at deploy time). It reads/writes the **same `notices.json`** as the Python side (Node re-implements load/write/active with matching atomic-swap semantics). Relevant routes:

| Method + path | Purpose |
|---|---|
| `GET /notices` | List all notices, each annotated `active: true/false`. |
| `POST /notices` | Add a notice (`text` required, optional `expires_at`). Generates the `n_<ts>_<rand>` id. |
| `POST /notices/delete` | Remove a notice by `id`. |

The same service also powers the console's **KB Markdown editor** for the editable folders (`/list`, `/file`, `/save`, `/new`, `/reindex`, `/reindex-status`) — restricted to `intents/ faq/ policies/ tickets/`, with `products/` and `learned/` excluded and strict path validation (no traversal, `.md` only). `POST /reindex` shells out to `kb/update.sh`.

### 4.6 What Hermes is told (`kb/hermes-SOUL-buttonsbebe-addition.md`)

The SOUL addition instructs the model:

- Always call `search_kb` before answering a Buttons Bebe question; answer only from what it returns; escalate if nothing relevant.
- **"Notice Board overrides everything."** A `NOTICE BOARD` result is the current truth and supersedes any conflicting policy/FAQ/product detail for as long as it appears.
- **Notices change facts only, never the safety rules:** still draft-only, never auto-send; refunds/disputes/damaged/wrong/missing items stay sensitive regardless of any notice.

---

## 5. The learning loop — end to end

**Goal:** turn a human agent's real reply into knowledge the agent can reuse — safely, and only after PII is handled. There are **two different designs** in play, and the distinction is the single most important thing to understand here.

> **The confusing part, stated plainly:** `CLAUDE.md` §8 says the LIVE loop is an **automatic, console-driven, nightly** path (`learning.py` → `auto_promote_learned.py`). But **none of that code is in this repo** — it is VPS-only. What *is* in this repo is a **different, older, poll-based, human-gated** design (the `feedback/` package + `kb/scripts/review_learned.py`), which `CLAUDE.md` §8 classifies as a **STUB/superseded** and which its own config runs in **SHADOW** mode. Do not assume the repo code is what's running.

### 5.1 The LIVE path (per `CLAUDE.md` §8, added 2026-07-09) — mostly NOT in this repo

```
Human clicks an action in the console Ticket feed  (Send reply / internal Note / Request-edit)
        │
        ▼
webhook/src/bb_webhook/learning.py          ⚠️ VPS-ONLY — not in this clone
   • records a "lesson" to  KB/learned/lesson-*.md
     (situation + AI draft + human's final text + kind + edited-flag)
   • a KB/learned/_ledger.json tracks totals
   • surfaced via  GET /dashboard/api/learning  (console "Learning" card)
        │
        ▼  nightly at 03:30, buttonsbebe-kb-learn.timer     ⚠️ VPS-ONLY
KB/scripts/auto_promote_learned.py           ⚠️ VPS-ONLY — not in this clone
   • masks PII (emails/phones/orders/addresses) via  feedback/pii.py  ✅ IN THIS REPO
     PLUS the known customer name
   • promotes each lesson → KB/tickets/exemplar-learned-*.md
     (status: confirmed, source: learned-auto)
        │
        ▼
KB/learn-nightly.sh                          ⚠️ VPS-ONLY — rebuilds the index
        │
        ▼
SOUL tells Hermes to MIRROR these "Approved reply" exemplars while grounding facts in policy/faq/products.
```

Key facts and cautions for the new team:

- **Auto-promotion.** Unlike the repo's `feedback/` design, this path promotes **automatically** each night and writes exemplars already at `status: confirmed` (`source: learned-auto`). The only PII guard is `feedback/pii.py` masking **plus a hardcoded known-customer-name mask** — and `pii.py` itself warns it **does not catch names in general** (§5.2, `pii.py`). This is a real risk surface to review on the VPS.
- **The masking module is shared.** `feedback/pii.py` (in this repo) is the masker the LIVE nightly job calls — so this repo file *is* load-bearing for production even though the surrounding LIVE scripts are not here.
- **Where to find the LIVE code:** `/root/Buttonsbebe Agent/webhook/src/bb_webhook/learning.py`, `/root/Buttonsbebe Agent/KB/scripts/auto_promote_learned.py`, `/root/Buttonsbebe Agent/KB/learn-nightly.sh`, and the `buttonsbebe-kb-learn.service/.timer` units — all on the VPS. **Pull these into the repo during handover; they are currently unversioned here.**

### 5.2 The `feedback/` package (IN this repo) — the poll-based, human-gated design

This is the v2 implementation from `SPRINT-feedback-collector.md`. Per its `config.py` it defaults to `FEEDBACK_ENABLED=shadow` and per `CLAUDE.md` §8 the old poll-based `feedback_collector` is a **STUB superseded by the console-action capture** in §5.1. It is fully built and unit-tested, but **not the live promotion path**. Its philosophy is *capture → review → promote*, with a **hard human gate** and **no auto-promotion**.

Module-by-module:

| File | Job |
|---|---|
| `feedback/config.py` | Loads the shared `.env` (Gorgias creds + `FEEDBACK_*` knobs) from `[/root/Buttonsbebe Agent/.env, …/webhook/.env, <repo>/.env]`. Defines KB paths: `LEARNED_DIR` (holding pen, not indexed), `TICKETS_DIR` (indexed promotion target), `ARCHIVE_DIR` (`kb/_archive_learned/` — leading underscore ⇒ never indexed). `ENABLED` defaults to `shadow`. |
| `feedback/gorgias_read.py` | **Read-only** Gorgias client (GET only — no write methods exist by design). Basic auth + explicit User-Agent (Gorgias' WAF 403s the default urllib UA). Lists tickets by `updated_datetime:asc`, fetches messages. |
| `feedback/pairing.py` | The heart. For one ticket's messages, finds the trustworthy pair: **AI draft** = `from_agent=true AND public=false` (internal note); **human reply** = `from_agent=true AND public=true` (public reply) sent *after* the draft. Prefers the bot's note (`FEEDBACK_BOT_EMAIL`/`_USER_ID`). Returns a `Pair` or a `Skip(reason)`. Skip reasons: `no_ai_draft`, `empty_draft`, `no_human_reply`, `empty_reply`, `macro`, `multi_turn`. **Never** decides capture on similarity. |
| `feedback/text_clean.py` | `clean_draft()` strips glm-5.2 self-commentary tails (e.g. "the response above was complete") and de-dupes the "answer emitted twice" block (DEV-ISSUES #5); `normalize()` does light whitespace/quote cleanup. Conservative: keeps text when unsure. |
| `feedback/language.py` | Cheap script detection (Latin vs Hebrew U+0590–U+05FF vs other). Answers one question: is the reply mostly non-Latin? If so, the char-level similarity hint is meaningless and must be suppressed. Returns `primary` (`en`/`he`/`other`/`unknown`) + `reliable_char_similarity`. |
| `feedback/similarity.py` | `difflib` ratio as a **display hint only — never a gate**. Bands: `close` (≥0.75), `partial` (0.4–0.75), `rewrite` (<0.4), `n/a` (unreliable: non-English or <40 chars). |
| `feedback/pii.py` | PII **highlighter**, not a guarantee. Regex-masks emails, URLs, phones, tracking/order numbers, zips, street addresses to placeholders (`[email]`, `[order]`, `[tracking]`, `[address]`, …). **Explicitly does not catch names** (unbounded; Hebrew/other scripts never match Latin patterns). `findings()`, `mask()`, `summary()`. The `summary().warning` always reminds a human to read for names. |
| `feedback/store.py` | Tiny SQLite state: a **high-water-mark cursor** (max `updated_datetime` handled) + a `processed` ledger (idempotent — overlap re-queries never double-write). Avoids the "fixed window silently drops tickets" bug (M1). |
| `feedback/collector.py` | The capture step. `process_ticket()` guards sensitive tickets, runs `pairing.evaluate`, attaches the similarity hint + PII summary, and writes `kb/learned/ticket-<id>.md` marked `review_pending: true`. `run_poll()` pulls tickets since the cursor (minus an overlap), processes new ones, advances the cursor. **Writes nothing to the indexed KB.** CLI: `python3 -m feedback.collector poll`. |
| `feedback/review.py` | Shared review/promote logic meant for **both** the CLI and a dashboard API. Tolerant of packet formats (its own and older Hermes-written files). `list_pending()`, `get_packet()`, `approve(pii_cleared=…)`, `reject(purge=…)`, `reindex()`. `approve` **refuses without `pii_cleared`**, masks PII, and writes a `needs_final_edit` exemplar. |
| `feedback/validate.py` | The go-live proof (M5). Shells out to the KB's own `search.sh` and checks whether a promoted exemplar is actually **retrieved** for its own question. `before_after(query, needle)` → `PASS`/`FAIL`. "A file got written" is *not* proof the agent improved. |
| `feedback/macro_signatures.txt` | One distinctive macro substring per line (currently only commented examples). Any human reply containing one is treated as a saved macro and skipped by `pairing.looks_like_macro`. Populate with Buttons Bebe's real macro phrases. |

**The human gate CLI — `kb/scripts/review_learned.py`** (this *is* in the repo). Commands: `list`, `show <id>`, `approve <id> --pii-cleared`, `reject <id> [--purge]`, `reindex`, `stats`. It is the **only** path from `kb/learned/` into indexed `kb/tickets/`, and it is deliberately manual:

- `approve` **refuses** unless you pass `--pii-cleared`, and prints the PII findings first so you actually look. It masks identifiers to placeholders and writes an exemplar at `status: needs_final_edit` (the human then edits, sets `status: confirmed`, and runs `reindex`).
- `reindex` is a **separate, batched** command so the index is never half-built mid-batch.
- Only touches `ticket-*.md` packets — it never clobbers the legacy `owner-qa-*.md` file.

### 5.3 The captured packet format (`kb/learned/ticket-<id>.md`)

Written by `collector.write_packet()`. Front-matter carries `status: review_pending`, `review_pending: true`, `source_ticket_id`, similarity band/reliability, `reply_language`, `flags`, and a PII-findings summary. Body sections: `## Customer situation`, `## AI draft (internal note, cleaned)`, `## Human reply as sent`, `## Similarity hint (display only)`, `## PII highlighter`, `## Reviewer checklist`. Because it lives in `learned/`, it is **not indexed** until promoted.

> **Legacy file note:** `kb/learned/` also contains one older `owner-qa-*.md` (front-matter `source_type: owner_qa`, `status: confirmed`) from a retired "owner Q&A" branch. It is a different shape from the collector's packets; the `feedback` tooling ignores it (only globs `ticket-*.md`). Described here structurally only — it contains a real customer name, so it is **not reproduced**.

### 5.4 Tests (`feedback/tests/test_all.py`)

Offline, no-network `unittest` suite (17 tests per `SPRINT-feedback-collector.md`). Covers: clean single-exchange pairing; `no_ai_draft` / `no_human_reply` / `empty_reply` / `macro` (metadata + signature file) / `multi_turn` skips; Hebrew reply → captured but band `n/a`; glm-tail strip + repeated-block dedupe; PII find/mask + the "names not caught" warning; similarity bands; and end-to-end capture writing the packet + ledger and blocking reprocessing; sensitive-ticket skip. Run: `python3 -m unittest feedback.tests.test_all -v`.

### 5.5 LIVE vs STUB — the authoritative table

| Component | Status (per `CLAUDE.md` §8 + files) | In this repo? |
|---|---|---|
| KB hybrid search (`search_kb`, LanceDB) | **LIVE & verified** | Yes |
| Product sync every 3 days | **LIVE & verified** | Yes (`sync_products.py`; output not committed) |
| Notice Board override + expiry + console API | **LIVE** (shipped 2026-07-12) | Yes (`notices_lib.py`, `purge_notices.py`, `kb-admin/server.js`) |
| Learning loop — **console-action capture** (`learning.py`) | **LIVE** (2026-07-09) | **No** (VPS-only) |
| Learning loop — **nightly auto-promote** (`auto_promote_learned.py` + `learn-nightly.sh` + `kb-learn.timer`) | **LIVE** (2026-07-09) | **No** (VPS-only) |
| PII masker (`feedback/pii.py`) used by the LIVE nightly job | **LIVE dependency** | Yes |
| Old poll-based capture (`processor/feedback_collector.py`) | **STUB — superseded** | No (`processor/` absent) |
| `feedback/` package (poll capture) + `review_learned.py` (human gate) | Built + tested, runs in **SHADOW**; superseded by the console path | Yes |
| `feedback/validate.py` go-live proof | Built; must PASS on the VPS before any STUB→LIVE flip | Yes |
| `classifier.py` (deterministic risk gate) | **STUB** — returns NORMAL; risk is done by the LLM today | No (`processor/` absent) |

---

## 6. Operate & verify (commands)

VPS-side (paths are the uppercase `KB/` on the server):

```bash
# KB search sanity
cd "/root/Buttonsbebe Agent/KB" && ./search.sh "do you ship to canada"

# rebuild the index after editing content
cd "/root/Buttonsbebe Agent/KB" && ./update.sh

# product sync on demand (else every 3 days)
cd "/root/Buttonsbebe Agent/KB" && ./sync-products.sh

# services / timers
systemctl status buttonsbebe-kb-mcp buttonsbebe-kb-admin
systemctl list-timers buttonsbebe-kb-sync.timer buttonsbebe-kb-notices-gc.timer
journalctl -u buttonsbebe-kb-mcp -n 50

# Hermes sees the tool
hermes mcp test buttonsbebe_kb        # expect "Connected, 1 tool"
```

Learning loop (the repo's SHADOW/human-gated path):

```bash
python3 -m feedback.collector poll                       # one capture pass (read-only)
python3 kb/scripts/review_learned.py list                # pending packets
python3 kb/scripts/review_learned.py show <ticket_id>
python3 kb/scripts/review_learned.py approve <ticket_id> --pii-cleared   # refuses without the flag
python3 kb/scripts/review_learned.py reindex             # after editing exemplars to status: confirmed
python3 -m feedback.validate "do you ship to canada" "<ticket-id-or-filename>"   # go-live proof
python3 -m unittest feedback.tests.test_all -v           # offline tests
```

Locally in this clone, run `./setup.sh` once in `kb/` first (builds `.venv` + downloads the model + first index).

---

## 7. Handover notes, doc drift & TBDs

1. **Version the LIVE learning code.** `webhook/src/bb_webhook/learning.py`, `KB/scripts/auto_promote_learned.py`, `KB/learn-nightly.sh`, and the `buttonsbebe-kb-learn.*` units exist only on the VPS. A clone-based dev will not see the loop that is actually running. Pull them into the repo (highest-priority handover gap).
2. **`webhook/` and `processor/` trees are absent** from this clone (`CLAUDE.md` describes them as core). The write-back path, queue, orchestrator, and classifier stub are not reviewable from here.
3. **Two learning-loop designs coexist.** The repo's `feedback/` package (human-gated, SHADOW) is *not* the same as the LIVE console→nightly auto-promote path. Decide which one the new team maintains; the auto-promote path's name-masking safety deserves review (`pii.py` cannot catch names, yet auto-promote writes `status: confirmed`).
4. **`kb/products/` is generated, not committed.** Expect an empty/absent `products/` after cloning until `./sync-products.sh` runs with valid Shopify creds.
5. **Missing referenced docs.** `kb/README.md` and `kb/CONVENTIONS.md` are cited by `SEARCH-ENGINE.md` and `kb/tickets/README.md` but are not in the repo. The file-format truth is `kb/scripts/kb_lib.py`.
6. **Port 8087 (`kb-admin`) is undocumented** in `CLAUDE.md` §6's port table. It is a real service (Notice Board + KB editor API, behind console auth at `/console/kbapi/*`).
7. **Folder casing** differs local (`kb/`) vs VPS (`KB/`); scripts are path-relative so both work, but deploy scripts should target the server path explicitly (`SPRINT-notice-board` R3).
8. **Product count is approximate** and drifts with the catalog (~4,200 in `SEARCH-ENGINE.md`, 4,246 in `CLAUDE.md` §4). Treat as "~4k active products," verified by `ls kb/products | wc -l` on the box. **TBD:** exact current count (can't be verified from this clone).
9. **`FEEDBACK_BOT_EMAIL` must be set** for exact AI-draft detection in the poll-based path; unset, `pairing` falls back to "first internal note" and flags `draft_identity_unverified`.
