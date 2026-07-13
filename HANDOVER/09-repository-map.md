# 09 · Repository Map

> **SUPERSEDED (2026-07-14):** This historical handover chapter is not current operational documentation. Do not use its counts, runtime status, write-path descriptions, or instructions. Use the repository-root `CLAUDE.md`, the user-provided `AGENTS.md`, and live verification instead.

**What this doc covers:** an annotated, path-by-path guide to every folder and important file in this repo — on **both** git branches — so a newcomer cloning from GitHub with zero context instantly knows what each thing is, which branch it lives on, whether it's tracked/gitignored, and whether to trust it.

**Sources read:** `git branch -a`; `git log --oneline`; `git ls-tree -r --name-only main`; `git ls-tree -r --name-only Fable_buttonsbebe` (and a diff of the two, both directions); `cat .gitignore`; `ls -la` at root; `du -sh` on the large dirs; `find` on `fable/`, `deploy/`, `data/`, `_VPS-FULL-BACKUP-20260706/`; and header reads of `CLAUDE.md`, `feedback/README.md`, `tools/README.md`, `kb-admin/server.js`, `whatsapp-connect/package.json`, `deploy/patch_app.py`, `server-fixes.sh`, `console-src/index.html`, `dashboard/index.html`, `qa-run/qa.html`, `INCONSISTENCIES.md`, `DEV-ISSUES.md`, the two `SPRINT-*.md`, and `git show Fable_buttonsbebe:fable/README.md`. All facts below were verified against the filesystem/git on 2026-07-13.

> **One-line orientation.** This repo is the *source* for the Buttons Bebe AI support agent. The **live production system runs on the VPS**, not from a clone — several live components (`webhook/`, `processor/`, `~/.hermes/`, the built KB index, `kb/products/`) are **not in the repo at all** (see §5). The authoritative architecture doc is **`CLAUDE.md`** at the repo root; read it first. Many *other* root docs describe a retired design — see §6.

---

## 1. Branch overview

Two branches. History is short and linear (3 commits on `main`); `Fable_buttonsbebe` diverged to build a from-scratch rewrite.

| Branch | Tracked files | What it is | When to use it |
|---|---|---|---|
| **`main`** *(default, checked out)* | **119** | The **LIVE Hermes system as deployed on the VPS**: KB content + build scripts (`kb/`), the two read-only MCP tools (`tools/`), the KB-editor API (`kb-admin/`), the WhatsApp bridge (`whatsapp-connect/`), the support **console** UI (`console-src/`, `dashboard/`), the (shadow) learning-loop package (`feedback/`), and the architecture/ops docs. | Default. Use for **anything touching the production agent** on `srv1766050` — KB edits, tool changes, console tweaks, deploy scripts. |
| **`Fable_buttonsbebe`** | **227** | A **superset-ish** of `main` that **adds the entire `fable/` tree** — a self-contained FastAPI **rebuild** of the help desk ("Fable", a Gorgias replacement) that runs 100% locally against emulators and never touches production. Also adds a `testing/` harness, extra planning docs, `deploy/vps-patches/` (real classifier + draft-cleaner hotfixes), and a `dashboard/DESIGN-SYSTEM.md` + console backups. | Use only for isolated Fable R&D. It is quarantined pending the dependency, egress, auth, and clean-install gates in handover **doc 10**; see doc 07 for the deep dive. |

> ⚠️ **Not a strict superset.** `Fable_buttonsbebe` branched *before* the Notice Board work landed on `main`, so **7 files exist only on `main`** and are absent from Fable: `console-src/index.html`, `SPRINT-notice-board-2026-07-12.md`, `kb/notices/notices.json`, `kb/scripts/notices_lib.py`, `kb/scripts/purge_notices.py`, `kb/buttonsbebe-kb-notices-gc.service`, `kb/buttonsbebe-kb-notices-gc.timer`. If you need the Notice Board feature *and* Fable together, they must be merged. Commit history: `76e6654` (Notice Board, main) → `d23ea25` (ticket-feed redesign) → `8702513` (initial commit).

---

## 2. Top-level annotated map (main branch)

Legend for **Type**: `live-source` = code running (or meant to run) in production · `KB-content` = knowledge-base markdown/data · `docs` = human docs/plans · `config` = config/templates · `secrets` = credentials/PII (never commit) · `retired` = old design, do not trust · `build-artifact` = generated/runtime leftovers.

| Path | Type | Tracked? | One-line description |
|---|---|---|---|
| `CLAUDE.md` | docs | tracked | **THE source of truth** for the live architecture (dated 2026-07-07). Read first; supersedes all other root docs. |
| `DEV-ISSUES.md` | docs | tracked | Junior-dev issue list — open bugs to fix + already-fixed context, keyed to `CLAUDE.md`. |
| `INCONSISTENCIES.md` | docs | tracked | 2026-07-07 audit of doc-vs-reality drift; explains the three-layer history and what's retired. |
| `SPRINT-feedback-collector.md` | docs | tracked | Sprint plan (v2) for turning on the learning loop safely (the `feedback/` package). |
| `SPRINT-notice-board-2026-07-12.md` | docs | tracked | Sprint plan for the Notice Board (owner overrides). **main-only.** |
| `console-src/` | live-source | tracked | **Newest** support-console SPA (single `index.html`, **includes the Notice Board panel**). Added by the Notice Board commit; supersedes `dashboard/` as source of truth. **main-only.** |
| `dashboard/` | live-source | tracked | The console SPA as served at **`/dashboard`** (webhook :8000). One feature behind `console-src/` — **no** Notice Board panel (79-line diff). |
| `deploy/` | live-source / ops | tracked | VPS deploy helpers: `patch_app.py` (idempotently injects review endpoints into the webhook `app.py`) + `review_console.html`. On `main`, `deploy/vps-patches/` is **untracked** (working tree holds only `__pycache__`). |
| `feedback/` | live-source | tracked | The **learning-loop** Python package (capture → review → promote). Wired but **SHADOW/stub** per `CLAUDE.md §8`; the live learning path is now `webhook/src/bb_webhook/learning.py` (VPS-only). See §3. |
| `kb/` | KB-content + live-source | tracked | The **knowledge base**: markdown content (`intents/ faq/ policies/ tickets/ notices/`), the index/search/MCP **scripts**, and the KB **systemd units**. Maps to `KB/` on the VPS (note case). See §3. |
| `kb-admin/` | live-source | tracked | Zero-dependency Node API (`server.js`, :8087) letting the console read/edit KB markdown and re-index; excludes `products/` + `learned/`, path-hardened. |
| `qa-run/` | docs / build-artifact | tracked | Static QA-results viewer (`qa.html`) + `results.json` — a saved Hermes QA run for review. Not part of the runtime. |
| `tools/` | live-source | tracked | The **Gorgias + Redo read-only MCP** modules (`gorgias_mcp.py`, `redo_mcp.py`, `_common.py`) + their systemd units + launchers. See §3. |
| `whatsapp-connect/` | live-source | tracked | Node + Baileys service (:8085): WhatsApp QR-pairing page + 2-way Hermes bridge for owner escalations. See §3. |
| `fable/` | build-artifact | **untracked** | On `main`, only **runtime leftovers** from a local Fable run: `__pycache__/`, `.pytest_cache/`, and `server/data/fable.db` — **zero `.py` files**. The real Fable source lives on the **`Fable_buttonsbebe`** branch (§4). |
| `data/` | secrets (PII) | **gitignored** | Customer-PII **ticket CSV exports** (`tickets-2026-06-23--*.csv`). Ignored via `data/`. Never commit. |
| `_VPS-FULL-BACKUP-20260706/` | retired + secrets | **gitignored** | **994 MB** full backup of the **OLD retired system** (docker/, fs/, meta/, **secrets/** in plaintext). Ignored via `_VPS-FULL-BACKUP-*/`. Do **not** trust for architecture; do **not** commit. |
| `.env` | secrets | **gitignored** | Live MAIN agent credentials (Gorgias/Shopify/Redo). Ignored via `.env`. |
| `.env.bak-20260708` | secrets | **gitignored** | Dated backup of `.env` (contains secrets). Ignored via `.env.bak-*`. |
| `.env.example` | config | tracked | Env **template** (no secret values). |
| `env.example` | config | tracked | **Identical duplicate** of `.env.example` (byte-for-byte). Redundant; one should be removed. |
| `server-fixes.sh` | config / ops | tracked | Run-on-the-VPS bash script that fixes the server-only issues from `INCONSISTENCIES.md`; backs up every file it touches. |
| `Buttons-Bebe-Phase-2-3-Plan.pptx` | docs | **untracked** | Planning deck (~504 KB). Not committed. |
| `HANDOVER/` | docs | tracked (new) | **This handover set** — `README.md` + docs `01`–`11` + `_assets/` (extracted roadmap-deck text). |
| `.gitignore` | config | tracked | Ignore rules (see §6 for the full list of what it hides). |
| `.DS_Store` | build-artifact | **gitignored** | macOS cruft. |

---

## 3. Expanded sub-maps (main branch)

### `kb/` — knowledge base + search engine (~352 KB)
Content is plain markdown; a build step indexes it into a LanceDB hybrid index (the **built index is NOT in the repo** — §5). On the VPS this is `KB/` (uppercase).

| Path | What |
|---|---|
| `kb/SEARCH-ENGINE.md` | How the hybrid (keyword + local embeddings) search works. |
| `kb/hermes-SOUL-buttonsbebe-addition.md` | The block appended to Hermes' `SOUL.md` for this store. |
| `kb/requirements.txt` | Pinned Python deps for the KB scripts, Shopify sync, shadow learning, and KB MCP. |
| `tools/requirements.txt` | Pinned Python deps for the Redo and Gorgias MCP services. |
| `tools/setup.sh` | Creates the isolated `tools/.venv` from its manifest. |
| `kb/intents/` | **22** intent playbooks (`intent-01..22-*.md`) — canned handling per common request. |
| `kb/faq/` | 5 customer-facing FAQ docs (shipping, returns, sizing, brands, order-changes). |
| `kb/policies/` | 17 policy docs (returns/refunds, shipping, package protection, escalation, sizing, etc.) — the factual ground truth. |
| `kb/tickets/` | 5 hand-written **exemplar** reply templates + `README.md`. This folder is **indexed** and must stay PII-free (promotion target for the learning loop). |
| `kb/notices/` | `notices.json` — owner **override** notices (auto-expiring). **main-only** feature. |
| `kb/learned/` | Captured raw lessons (`.gitkeep` + one owner-QA sample). **Deliberately NOT indexed** — the PII/quality gate before promotion. |
| `kb/scripts/` | `kb_lib.py`, `index_kb.py`, `search_kb.py`, `kb_mcp_server.py` (the **:8077 KB MCP**), `sync_products.py`, `notices_lib.py`, `purge_notices.py`, `review_learned.py` (human promote gate). |
| `kb/*.sh` | `search.sh`, `setup.sh`, `update.sh`, `run_mcp.sh`, `sync-products.sh` — convenience wrappers. |
| `kb/buttonsbebe-kb-mcp.service` | systemd unit for the KB MCP (:8077). |
| `kb/buttonsbebe-kb-sync.service` / `.timer` | Product re-sync from Shopify (every 3 days). |
| `kb/buttonsbebe-kb-notices-gc.service` / `.timer` | Garbage-collect expired notices. **main-only.** |

### `tools/` — Gorgias + Redo MCP modules (~32 KB, all read-only/GET)
| Path | What |
|---|---|
| `tools/README.md` | Module/port/service/Hermes-name table + ops commands. |
| `tools/gorgias_mcp.py` | **Gorgias** read MCP (:8079) — tickets, messages, customer. |
| `tools/redo_mcp.py` | **Redo** returns/refunds read MCP (:8078). |
| `tools/_common.py` | Shared helper; reads the **MAIN** `.env`. |
| `tools/buttonsbebe-gorgias-mcp.service`, `buttonsbebe-redo-mcp.service` | systemd units. |
| `tools/run-gorgias.sh`, `tools/run-redo.sh` | Space-free launchers. |

> The **KB MCP** (the 3rd tool, :8077) is **not** here — its source is `kb/scripts/kb_mcp_server.py`.

### `feedback/` — the learning-loop package (~164 KB; currently SHADOW/stub)
`README.md` is excellent — read it. Capture → review → promote; nothing reaches the live KB without a human. Modules: `config.py`, `gorgias_read.py` (GET-only), `pairing.py` (find AI-draft/human-reply pairs), `text_clean.py`, `language.py`, `similarity.py` (hint only), `pii.py` (highlighter; **does not catch names**), `store.py` (SQLite cursor/ledger), `collector.py` (capture), `review.py`, `validate.py`, `macro_signatures.txt`, and `tests/test_all.py`. The human gate lives in `kb/scripts/review_learned.py`.

> **Status note:** `CLAUDE.md §8` marks the old poll-based `feedback_collector` **superseded** by the console-action capture (`webhook/src/bb_webhook/learning.py`, VPS-only) + nightly `auto_promote_learned.py`. Treat this `feedback/` package as the **design/reference + shadow tooling**, not the live path.

### `whatsapp-connect/` — WhatsApp bridge (~28 KB, Node + Baileys)
`server.js` (:8085 — QR-pair page + Hermes 2-way bridge), `package.json` (deps: `@whiskeysockets/baileys`, `express`, `pino`, `qrcode`), `buttonsbebe-whatsapp-connect.service` (systemd), `Caddyfile` (public `/connect-whatsapp/*` route).

### `console-src/` vs `dashboard/` — the support console
Both are a **single-file SPA** titled "Buttons Bebe — Support Console" (same design tokens). They differ by ~79 lines: **`console-src/index.html` is newer and contains the Notice Board panel (38 "notice" refs); `dashboard/index.html` has none (0).** `dashboard/` is what has historically been **served at `/dashboard`** (webhook :8000, ticket-feed-redesign era); `console-src/` is the up-to-date source that must be deployed to bring Notice Board live. Reconcile these two before the next deploy.

### `kb-admin/` — KB editor API (~12 KB)
`server.js` (zero-dep Node, :8087) + `buttonsbebe-kb-admin.service`. Backs the console's KB-editing screens via Caddy at `/console/kbapi/*`; only `intents/ faq/ policies/ tickets/` are writable (products/learned excluded), path-traversal-hardened.

### `qa-run/` — QA results viewer (~56 KB)
`qa.html` (dark static viewer, "Hermes QA — Buttons Bebe") + `results.json`. A saved evaluation run for human review — not wired into the runtime.

### `deploy/` — VPS deploy helpers (~36 KB)
`patch_app.py` (idempotently patches the webhook `app.py` to add feedback-review routes) and `review_console.html`. **`deploy/vps-patches/` is NOT tracked on `main`** — the working tree holds only `__pycache__/` (`classifier.pyc`, `draft_cleaner.pyc`). The actual patch **sources live on `Fable_buttonsbebe`** (see §4): `classifier.py`, `draft_cleaner.py`, `heartbeat.sh`, `README.md`.

---

## 4. Fable branch sub-map (`Fable_buttonsbebe`)

The Fable branch adds **115 files** beyond `main`. The bulk is the self-contained **`fable/`** rebuild (runs locally against emulators; never touches production). **Detailed docs are in handover doc 07 — cross-reference it.** Compact map:

| Area (branch path) | Contents (summary) |
|---|---|
| `fable/server/` | FastAPI app. `main.py` + `app/`: `pipeline.py`, `intake.py`, `tickets.py`, `actions.py`, `risk.py`, `draft_cleaner.py`, `kb_search.py`, `context.py`, `models.py`, `db.py`, `config.py`, `audit.py`, `stats.py`, `channels_email.py`, `gorgias_compat.py`, `migration.py`. |
| `fable/server/app/brains/` | Pluggable "brain" backends: `base.py`, `mock.py`, `anthropic.py`, `anthropic_stub.py`, `hermes_stub.py`. |
| `fable/emulators/` | Fake upstreams that mimic real APIs: `shopify/app.py` (+ `seed/` customers/orders/products JSON + `generate_seed.py`), `redo/app.py`, `gorgias/app.py`, `mailbox/app.py` (catches outbound email), plus `run-emulators.sh` / `stop-emulators.sh`. |
| `fable/console/` + `fable/widget/` | Console UI (`index.html`, `app.js`, `style.css`) and chat widget (`widget/widget.js`, `demo-store.html`) — note there are two widget copies (`console/widget/` and top-level `widget/`). |
| `fable/docs/` | `API-CONTRACT.md`, `RESEARCH-gorgias-api.md`, `RESEARCH-shopify-api.md`, `SPRINT-PLAN.md`, `TESTING-STRATEGY.md`. |
| `fable/scripts/` | `demo.sh`, `run-all.sh`, `run-server.sh`, `stop-server.sh`, `seed-demo.sh`, `seed_demo.py`, `migrate-from-gorgias.sh`, `test.sh`. |
| `fable/tests/` | Full pytest suite: `unit/` (risk, brains, config, cursors, draft_cleaner, compat mappers), `integration/` (~20 files: pipeline, intake, actions, safety-invariants, contracts, migration, stats, …), `e2e/test_live_stack.py`, `conftest.py`, `pytest.ini`. |
| `fable/README.md`, `fable/.env.fable` | Plain-English guide + Fable env template. |
| `fable/logs/` | `server.log`, `server.pid` — **runtime artifacts committed to the branch** (should be gitignored). |
| **Non-`fable/` Fable-branch additions** | `testing/` harness (`run_live_tests.py`, `scenarios.json`, `results-*.json`, several `*-JUDGMENT.md`, `HOW-TO-RUN.md`, `_harness/AGENT-BRIEF.md`); `deploy/vps-patches/` (`classifier.py`, `draft_cleaner.py`, `heartbeat.sh`, `README.md` — the **real** classifier that supersedes the `main` stub); `dashboard/DESIGN-SYSTEM.md` + 4 `dashboard/index.html.bak-*` backups; root docs `CONTINUE-HERE.md`, `DESIGN-CRITIQUE.md`, `IMPROVEMENT-PLAN.md`, `SPRINT-2-PLAN.md`, `TESTING-READINESS.md`, `Buttons-Bebe-Competitive-Brief.html`. |

---

## 5. What is NOT in the repo (VPS-only)

These live only under `/root/Buttonsbebe Agent/` on the VPS (`srv1766050`) and must be pulled from the server — **cross-reference handover doc 06 for the exact pull procedure.**

- **`webhook/`** — the FastAPI webhook receiver + dashboard server + SQLite job queue (`webhook/data/webhook.db`), and the **live learning capture** `webhook/src/bb_webhook/learning.py`. Not in either branch.
- **`processor/`** — the orchestrator loop (`orchestrator.py`, `hermes_runner.py`, `gorgias_writer.py`, `kb_client.py`, plus the `classifier.py` / `whatsapp_notifier.py` / `feedback_collector.py` modules). Not in either branch.
- **`~/.hermes/`** — Hermes home: `config.yaml` (model + MCP registrations), `SOUL.md`, `skills/buttonsbebe/`. Not in the repo.
- **`kb/products/`** — the ~4,246 auto-synced Shopify product markdown files (regenerated every 3 days by the KB sync timer). Only the *sync script* is in the repo, not the output.
- **The built LanceDB index** — the vector/keyword search index generated from `kb/`. A build artifact; rebuilt on the VPS.
- **`webhook/.env`** — the processor/webhook secret file (separate from the MAIN `.env`; see `CLAUDE.md §7`).

> The three MCP **tool sources** *are* in the repo (`tools/` + `kb/scripts/kb_mcp_server.py`); it's the **webhook/processor/Hermes** layer and generated data that are VPS-only.

---

## 6. Retired / do-not-trust & sensitive areas

**Never commit, never trust as architecture:**

- **`_VPS-FULL-BACKUP-20260706/`** — 994 MB snapshot of the **retired** pre-2026-07-06 system (the old `/root/gorgias-webhook` pipeline, Supermemory/ChromaDB KB, WhatsApp/Baileys 8-tool build, "Mimo" model). Contains **plaintext secrets** in `secrets/`. Gitignored via `_VPS-FULL-BACKUP-*/`. Reference only for forensic "what did the old box have" questions — **not** for how anything works today.
- **`data/`** — customer-**PII** ticket CSV exports. Gitignored via `data/`. Do not commit, do not paste into prompts/logs.
- **`.env`, `.env.bak-20260708`** (and any `webhook/.env` you pull) — live secrets. Gitignored (`.env`, `.env.bak-*`, `**/.env`).

**Stale / doc-drift (per `CLAUDE.md §11` and `INCONSISTENCIES.md`):** the retired-design docs — old `PROJECT-SOURCE-OF-TRUTH.md`, `GOAL.md`, `kb/README.md` (if present on the VPS), `docs/hermes-rearchitecture/`, `build/` — describe the **gone** architecture and should be treated as historical. In this clone, **`CLAUDE.md` is the single current source of truth**; when any other doc disagrees with it, `CLAUDE.md` wins.

**`.gitignore` hides:** `.env`, `data/`, `build/hermes/kb-rag/vault/exemplars/`, `**/.env`, `*.env.local`, `__pycache__/`, `*.pyc`, `_VPS-FULL-BACKUP-*/`, `.env.bak-*`, `.DS_Store`, `**/node_modules/`, `**/.venv/`.

---

### Companion handover docs
02 · Live architecture — 03 · Live components reference — 04 · Knowledge base & learning — 05 · Services, deploy & secrets — **06 · VPS pull procedure** (referenced in §5) — **07 · Fable rebuild deep dive** (referenced in §4). (This is doc 09.)
