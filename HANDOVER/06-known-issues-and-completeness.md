# 06 · Known Issues, Gaps & Repo Completeness

**What this doc covers:** the safe-handover chapter — what is broken, what is missing, what to trust. It reconciles the two internal audit files (`DEV-ISSUES.md`, `INCONSISTENCIES.md`) with what is *actually present in this Git repo*, flags the single largest gap (the repo is **not** a runnable system on its own), lists every doc you must not trust, and gives an exact **read-only** procedure to complete the repo from the VPS plus a day-one remediation checklist.

**Sources read:** `CLAUDE.md` (§1, §2, §5, §6, §8, §11); `DEV-ISSUES.md`; `INCONSISTENCIES.md`; `.gitignore`; `whatsapp-connect/server.js` + `whatsapp-connect/buttonsbebe-whatsapp-connect.service`; `console-src/index.html` + `dashboard/index.html`; `kb-admin/server.js` + `kb-admin/buttonsbebe-kb-admin.service`; `kb/buttonsbebe-kb-notices-gc.{service,timer}`; `kb/scripts/` (present vs. absent); `_VPS-FULL-BACKUP-20260706/` (`MANIFEST-files.txt`, `MANIFEST-sizes.txt`, `meta/`, `docker/` and `secrets/` listings — **not extracted**); sibling handover docs 02–05. **Verified with bash:** `git ls-files` (tracking), `git check-ignore`, `git branch -a`, `git ls-tree -r Fable_buttonsbebe`, directory listings. No secret *values* are printed below.

> ⚠️ **Trust order:** `CLAUDE.md` (rewritten 2026-07-07) is the **single current source of truth** for architecture. This doc is the current truth for *issues & completeness*. Anything under `_VPS-FULL-BACKUP-20260706/` and the retired docs listed in §4 describe a **deleted** system — do not follow them.

---

## 1. ⚠️ THE BIG COMPLETENESS GAP — cloning this repo does NOT give you a runnable system

**Read this before anything else.** This GitHub repo is the **content + tools + front-end + ops-scripts half** of the system. The **runtime half that actually receives tickets, runs the brain, and writes back to Gorgias is NOT in the repo** — it lives only on the VPS (`srv1766050` / `2.25.137.77`, under `/root/Buttonsbebe Agent/`). A fresh clone will **not** boot the live pipeline. You must pull the missing pieces from the VPS first (see §5).

**Verified absent from Git tracking** (`git ls-files | grep -E '^(webhook|processor)/'` returns **nothing**; no `~/.hermes` tracked):

| Missing piece | What it is | Why it matters | Where to get it |
|---|---|---|---|
| **`webhook/`** | FastAPI app `bb_webhook` (port 8000). Receives Gorgias webhooks, verifies HMAC, dedupes, enqueues the SQLite job queue, **and serves the Console** (`/console/api`, `/console/kbapi`, `/console/waapi`) + `/dashboard`. systemd `buttonsbebe-webhook`. | This is the entry point of the whole flow. Without it there is no receiver, no queue, no Console back-end. | VPS `rsync` (§5). Only a **copy** exists under `_VPS-FULL-BACKUP-20260706/` — that copy is the **retired** app, not the current one. |
| **`processor/`** | The orchestrator: `orchestrator.py`, `hermes_runner.py`, `gorgias_writer.py`, `classifier.py` (stub), `twilio_notifier.py`, `feedback_collector.py` (stub), `kb_client.py`. systemd `buttonsbebe-processor`. | This is the poll loop that runs Hermes once per ticket and posts the internal-note draft (the only write in the system). | VPS `rsync` (§5). |
| **`~/.hermes/`** | Hermes home: `config.yaml` (model `glm-5.2` + the three MCP tool registrations), `SOUL.md` (agent instructions), `skills/buttonsbebe/` (ticket workflow). | This is the brain's configuration + persona + skill. Without it the tools are registered nowhere and the agent has no instructions. Confirmed: **no `~/.hermes` content is tracked** (the only `hermes` match in the tree is a KB markdown file, `kb/hermes-SOUL-buttonsbebe-addition.md` — an *addition* to SOUL, not SOUL itself). | VPS `rsync` (§5), scrubbing secrets from `config.yaml`. |
| **Live learning scripts** | `webhook/src/bb_webhook/learning.py` (per-action lesson capture), `KB/scripts/auto_promote_learned.py`, `learn-nightly.sh`, and the `buttonsbebe-kb-learn` **service/timer** (nightly 03:30 promotion of `KB/learned/` lessons into indexed exemplars). | The nightly learning loop CLAUDE.md §8 describes is **VPS-only**. Verified absent: `kb/scripts/auto_promote_learned.py`, `learn-nightly.sh`, `buttonsbebe-kb-learn.{service,timer}` do **not** exist in the repo. *(The manual reviewer path `kb/scripts/review_learned.py` and the whole `feedback/` package **are** present.)* | VPS `rsync` (§5). |
| **`kb/products/` corpus + built LanceDB index** | ~4,246 products auto-synced from Shopify every 3 days, plus the compiled hybrid-search index. | The KB search returns product answers from this. Large + regenerated — better to **rebuild** than pull (see §5). | Regenerate: `kb/sync-products.sh` then `kb/scripts/index_kb.py` (or `kb/setup.sh`). |
| **`webhook/.env`** (and the runtime env) | Webhook/processor secrets (`WEBHOOK_SECRET`, `GORGIAS_*`, etc.). | Nothing authenticates without it. | Recreate from `.env.example` on the VPS; never commit. |

**About `_VPS-FULL-BACKUP-20260706/` — it is a reference, NOT the current source.** Verified from the manifests and `meta/`/`docker/` listings (tars **not** extracted): it snapshots the **OLD, RETIRED** pre-2026-07-06 system —
- `meta/unit-gorgias-webhook.txt`, `meta/unit-wa-connect.txt` (retired `wa-connect.service` bound port **8099**, `/opt/wa-connect/server.mjs`), `meta/unit-gw-backup-cleanup.txt`;
- `docker/teddy-agent-image.tar.gz`, `docker/vol-hindsight-data.tar.gz`, `docker/vol-pgvector_kb_pgdata.tar.gz`, `docker/kb-postgres.sql` (the retired **pgvector/hindsight** KB + dockerized agent);
- `secrets/hermes-config.yaml` (the retired 8-tool `hermes-tools-mcp` Hermes config), `secrets/root.env`, `secrets/etc-gorgias-wh-key`.

So the backup's `webhook`/agent is the **gorgias-webhook** design, **not** today's `bb_webhook`/`processor`. Use it only for forensic reference. It is git-ignored (`_VPS-FULL-BACKUP-*/`) and **contains plaintext secrets — never commit it**.

> **Net:** to stand up the live system you need (a) a VPS pull of `webhook/`, `processor/`, `~/.hermes/`, and the learn scripts/units; (b) fresh `.env` files; (c) a rebuilt product corpus + LanceDB index. See §5.

---

## 2. LIVE vs STUB — what actually works today (from `CLAUDE.md` §8, verified against the tree)

| Component / feature | Status | Primary file(s) | Notes |
|---|---|---|---|
| Webhook receive → SQLite queue → processor loop | **LIVE** | `webhook/` (VPS-only), `webhook/data/webhook.db` | End-to-end proven. Source not in repo. |
| Hermes runs per ticket using all 3 MCP tools | **LIVE** | `processor/hermes_runner.py` (VPS-only), `~/.hermes/` (VPS-only) | `hermes --yolo -z "…"` one-shot. |
| KB hybrid search incl. ~4,246 products (3-day refresh) | **LIVE** | `kb/scripts/{search_kb,index_kb,sync_products}.py`, `kb/buttonsbebe-kb-sync.{service,timer}` | Scripts in repo; built index + product corpus VPS-only. |
| Gorgias **read** (tools) | **LIVE** | `tools/gorgias_mcp.py` | Read-only. |
| Gorgias **write** = internal-note draft (the only write) | **LIVE** | `processor/gorgias_writer.py` (VPS-only) | Posts staff-only internal note. |
| WhatsApp escalation channel + owner QR pairing + 2-way bridge | **LIVE** (added 2026-07-07) | `whatsapp-connect/server.js` (:8085), `processor/twilio_notifier.py` (VPS-only) | Replaces the retired Twilio path; fires only when a ticket is classified IMMEDIATE — which today only Hermes can do (see classifier stub). |
| Console per-ticket actions (Send / internal Note / Request-edit) | **LIVE** (added 2026-07-09) | `console-src/index.html` → `/console/api/ticket/{id}/{send,note,rewrite}` (back-end VPS-only) | Front-end HTML in repo; Python endpoints VPS-only. |
| "Post drafts to Gorgias" safety toggle (`gorgias_writes_enabled`) | **LIVE** | `console-src/index.html:339/344/512`, `dashboard/index.html` | On = auto-post internal notes; Off = draft-only. Reconciles the auto-post vs. draft-only docs (see §4). |
| Notice Board (owner overrides on top of search) + 15-min GC | **LIVE** (added ~2026-07-12) | `kb-admin/server.js` (:8087), `kb/scripts/{notices_lib,purge_notices}.py`, `kb/buttonsbebe-kb-notices-gc.{service,timer}` | **Not in `CLAUDE.md` §6 port table** (drift — see §4). |
| Learning capture per Console action → `KB/learned/lesson-*.md` | **LIVE** (added 2026-07-09) | `webhook/src/bb_webhook/learning.py` (VPS-only) | Writes lesson + `_ledger.json`; surfaced at `/dashboard/api/learning`. |
| Nightly promotion of lessons → indexed exemplars (PII-masked) | **LIVE** (added 2026-07-09) | `KB/scripts/auto_promote_learned.py`, `learn-nightly.sh`, `buttonsbebe-kb-learn.timer` (all **VPS-only**), `feedback/pii.py` (in repo) | The promotion scripts/units are **not in the repo** (§1). |
| **`classifier.py` — deterministic risk gate** | **STUB** | `processor/classifier.py` (VPS-only) | **Returns NORMAL for everything.** Risk classification is currently done by **Hermes (the LLM)**, not a code gate. Safety-relevant — see issue #3. |
| **`feedback_collector.py` — poll-based reply capture** | **STUB** | `processor/feedback_collector.py` (VPS-only) | Logs only. **Superseded** by the Console-action capture (`learning.py` + nightly promotion). Kept for context; see issue #4. |
| Twilio SMS/WhatsApp (original design) | **Superseded** | `processor/twilio_notifier.py` (VPS-only) | Rewritten to POST the `whatsapp-connect` service; not a Twilio call anymore. |

---

## 3. Known issues register (merged `DEV-ISSUES.md` + `INCONSISTENCIES.md`, deduped)

IDs preserved so they map to the Phase-2 roadmap: `#1–#13` = `DEV-ISSUES.md` open items; `H/M/L#` = `INCONSISTENCIES.md`; `QA-#` = QA-run findings; `DRIFT-#` = new drift found by handover reviewers. **VPS-only** in *Affected files* means the file is not in this repo (pull it first, §5).

### 3a. OPEN — Security / secrets (do first)

| ID | Sev | Area | Issue | Affected file(s) | Suggested fix |
|---|---|---|---|---|---|
| **#12** | **High** | Security | **Fixed in repository; VPS rollout pending.** WhatsApp Connect now refuses missing/placeholder secrets, uses a separate `WA_SEND_SECRET`, authenticates and audit-logs `/send`, and reads only its dedicated env file. The live VPS must receive three rotated secrets and the processor caller must add send authentication before restart. | `whatsapp-connect/server.js`, `security.js`, `.service`; VPS-only `processor/twilio_notifier.py` | Generate unique `WA_TOKEN`, `WA_PASSWORD`, `WA_SEND_SECRET` in `whatsapp-connect/.env`; update the processor to send Bearer auth (or Basic password compatibility); deploy and verify unauthenticated POST returns 401. |
| **#13** | **High** | Security | Secrets hygiene. Live API keys live in `.env`. Rotate anything ever pasted into chat/notes; enforce `chmod 600`; never commit. | `/root/Buttonsbebe Agent/.env` + `webhook/.env` (VPS); repo-root `.env` + `.env.bak-20260708` (git-ignored, present on disk) | Rotate Gorgias key, Shopify client secret, Redo key; `chmod 600`; confirm `.gitignore` coverage (it is — verified). |

### 3b. OPEN — Code / logic

| ID | Sev | Area | Issue | Affected file(s) | Suggested fix |
|---|---|---|---|---|---|
| **#3** | **High** | Safety | `classifier.py` is a **STUB (returns NORMAL for everything)** — the deterministic risk gate is not implemented; risk relies entirely on the LLM. | `processor/classifier.py` (VPS-only) | Implement IMMEDIATE/HIGH/NORMAL gate (refunds, chargebacks, disputes, damaged/wrong/missing, cancellations, angry → IMMEDIATE). Note: WhatsApp escalation only fires on IMMEDIATE, so today nothing deterministic ever escalates. |
| **#1 / H2** | Med | Shopify auth | App code expects a **static `SHOPIFY_ADMIN_API_TOKEN`** (empty/unused); real auth is the **client-credentials grant** (`SHOPIFY_CLIENT_ID`+`SHOPIFY_CLIENT_SECRET`). Working pattern in `kb/scripts/sync_products.py`. Only bites if the webhook/processor calls Shopify directly (today it doesn't). | `webhook/src/bb_webhook/config.py`, `processor/config.py` (both VPS-only); ref `kb/scripts/sync_products.py` | Add `shopify_client_id/secret` settings + a token-minting helper, or delete the dead token var. Decide first (see M5) whether the processor ever calls Shopify. |
| **H1** | Med | Config | `SHOPIFY_SHOP` **wrong** in `webhook/.env` (`buttonsbebe`, no hyphen) vs. correct `buttons-bebe.myshopify.com` in main `.env`. (Gorgias subdomain *is* `buttonsbebe` — easy to conflate.) | `webhook/.env` (VPS) | Correct to `buttons-bebe.myshopify.com`. |
| **H3** | Med | Config | Redo creds (`REDO_API_KEY`/`REDO_STORE_ID`) exist only in **main** `.env`, not `webhook/.env`, so the **running processor has no direct Redo access** (it reaches Redo via the MCP tool, which reads main `.env`). | `webhook/.env` (VPS) | If the processor should read returns directly, add `REDO_*`; otherwise document that Redo is tool-only. |
| **#2** | Med | Tool contract | Processor prompt references a Redo tool **`get_order` that doesn't exist**. Redo MCP exposes `list_recent_returns`, `get_returns_for_order`, `get_return` — no `get_order`. If Hermes follows the prompt it fails. | `processor/hermes_runner.py` (VPS-only), `tools/redo_mcp.py` | Add `get_order(order_name)` to `tools/redo_mcp.py`, or fix the prompt to the tools that exist. |
| **#8** | Med | Safety hardening | Processor runs Hermes with **`--yolo`** (auto-approves ALL tool calls). Safe today (only write is a staff-only note) but a future tool could be auto-invoked. | `processor/hermes_runner.py` (VPS-only) | Restrict the toolset explicitly (`-t`); confirm `gorgias_writer.py` is the only write path. |
| **#5** | Med | Output quality | glm-5.2 **leaks trailing self-commentary / duplicates the draft** (QA-#01, #04, #10) — e.g. *"The response above was complete…"*, or the whole answer twice. | `processor/hermes_runner.py` (VPS-only) | Strip after known markers + de-dup repeated blocks before posting; or evaluate a less chatty model. |
| **#6** | Med | Robustness | **Empty / no-content messages not handled** (QA-#19): an empty customer message got a fabricated "thanks for your feedback" reply. | `processor/hermes_runner.py` + `~/.hermes/skills/buttonsbebe` (VPS-only) | Guard empty/whitespace/known-survey messages → no draft; mark for human. |
| **#4** | Low | Learning | `feedback_collector.py` is a **STUB (logs only)** — but **superseded** by the LIVE Console-action capture (`learning.py` + nightly promotion) per `CLAUDE.md` §8. | `processor/feedback_collector.py` (VPS-only) | Remove/retire the stub to avoid confusion; the learning loop is the console path now. |
| **H4** | Resolved* | Bug | `RuntimeWarning: coroutine … never awaited` at `database.py:114` (`PRAGMA busy_timeout` un-awaited) + 2 failed queue jobs. | `webhook/src/bb_webhook/database.py:114` (VPS-only) | *`DEV-ISSUES.md` §B reports this **already fixed** (added `await`). Re-verify on the pulled source. |

### 3c. OPEN — Content (needs the owner / Chaim)

| ID | Sev | Area | Issue | Affected file(s) | Suggested fix |
|---|---|---|---|---|---|
| **#7** | Med | Grounding | Hermes quoted a firm **"$35 USD" international rate not in the KB** (QA-#03); KB says confirm rates at checkout. | `kb/policies/` + prompt/SOUL | Add the real rate to `kb/policies/international-orders.md`, or reinforce "don't quote prices not in the KB". |
| **#10** | Med | Content | KB policies/intents still carry **placeholder/DRAFT wording** (some marked `status: confirmed` with conservative defaults). | `kb/policies/*`, `kb/intents/*` | Have Chaim confirm real specifics (return window, who pays return shipping, sale-season rules, intl rates) and replace placeholders. |
| **#11** | Low | Content | **Pickup vs. return-bin conflation** (QA-#08): answer implied the 24/7 side-door bin is at the 2133 Lakewood pickup spot, but the KB says the 24/7 bin is at **6 Kenyon Drive**. | KB pickup/returns content | Tighten wording so the two locations aren't conflated. |

### 3d. OPEN — Configuration / cleanup

| ID | Sev | Area | Issue | Affected file(s) | Suggested fix |
|---|---|---|---|---|---|
| **#9 / L1** | Med | Config | **Two `.env` files** — main (read by MCP tools) and `webhook/.env` (read by webhook+processor). Values drifted and caused a real multi-hour Gorgias-key detour (edited one, code read the other). | `/root/Buttonsbebe Agent/.env`, `webhook/.env` (VPS); ref `.env.example` | Consolidate to one source of truth (merge + point both `config.py` at it, or symlink); restart + verify. |
| **M5** | Med | Architecture | Historically ambiguous **which "brain" is production** (the `bb_webhook` processor vs. interactive Hermes). Largely resolved by the current `CLAUDE.md` (processor runs Hermes one-shot per ticket), but confirm on the pulled source. | `processor/` (VPS-only) | Confirm processor uses the 3 MCP tools + current `SOUL.md`; document. Everything else in this class depends on this. |
| **L4** | Low | Config | `.env.example` documents Gorgias + Shopify (client-cred) + Redo but **not** the webhook app vars (`WEBHOOK_SECRET`, `TWILIO_*`, `OWNER_WHATSAPP`, `SHOPIFY_ADMIN_API_TOKEN`). Also note a duplicate `env.example` (no dot) exists at repo root (identical content per doc 05). | `.env.example`, `env.example` | Complete the example with all runtime vars; keep one canonical example file. |

### 3e. Resolved / superseded (context only — no action)

- **M1** — "CLAUDE.md describes the old architecture": **resolved.** `CLAUDE.md` was rewritten 2026-07-07 and is now the current source of truth.
- **M2** — "`PROJECT-SOURCE-OF-TRUTH.md` out of date": **resolved by deletion** (file no longer in repo — verified).
- **M3 / L2** — "`kb/README.md` / `kb/CONVENTIONS.md` contradict `SEARCH-ENGINE.md`": **resolved by deletion** (both files gone — verified). `kb/SEARCH-ENGINE.md` remains and is current.
- **M4** — "model status stale": **resolved.** Hermes runs `glm-5.2` via Ollama Cloud, verified end-to-end.
- **L3** — "archive stale files": **mostly done** (GOAL.md, PROJECT-SOURCE-OF-TRUTH.md, docs/hermes-rearchitecture/, build/, gorgias-skill/ are gone). The large `_VPS-FULL-BACKUP-20260706/` remains (git-ignored, intentional reference).
- **`DEV-ISSUES.md` §B** — fixed during setup: `.env` paste artifacts, `GORGIAS_SUBDOMAIN` full-URL, wrong Gorgias key, commented-out Redo creds, `SHOPIFY_SHOP` in `webhook/.env`, the `database.py:114` await, and the hand-started uvicorn → `buttonsbebe-webhook` systemd unit.

---

## 4. Documentation drift / do-not-trust map

**Single source of truth = `CLAUDE.md`** (rewritten 2026-07-07). Everything below either describes the **retired** pre-2026-07-06 design or is a place where the live system diverges from CLAUDE.md.

### 4a. Retired docs — do NOT trust (they describe the deleted `gorgias-webhook` / Supermemory / ChromaDB / `hermes-tools-mcp` / WhatsApp-Baileys / "Mimo" design)

| Doc | Still in repo? | Verdict |
|---|---|---|
| `PROJECT-SOURCE-OF-TRUTH.md` (old) | **No — deleted** | Retired. Was the "definitive" doc; superseded by `CLAUDE.md`. |
| `GOAL.md` | **No — deleted** | Retired. |
| `kb/README.md` | **No — deleted** | Retired (Supermemory/`kb_client.py`/ingestion design). Use `kb/SEARCH-ENGINE.md` (LanceDB) instead. |
| `kb/CONVENTIONS.md` | **No — deleted** | Retired (incomplete category list). |
| `docs/hermes-rearchitecture/` | **No — dir absent** | Retired. |
| `build/` | **No — dir absent** | Retired. |
| `gorgias-skill/` | **No — dir absent** | Retired (replaced by the `buttonsbebe` Hermes skill in `~/.hermes/skills/`). |
| `_VPS-FULL-BACKUP-20260706/` | **Yes (git-ignored)** | **Reference only** — a snapshot of the retired system (see §1). Do not treat as current source; contains plaintext secrets. |

> Good news: most retired docs are **already deleted** from the working tree (verified). Risk remains if the new team resurrects them from old Git history or from the backup tar — treat any such file as retired unless `CLAUDE.md` confirms it.

### 4b. Live drift found by handover reviewers (real, verified in-repo)

| ID | Sev | Drift | Evidence | Reconciliation |
|---|---|---|---|---|
| **DRIFT-1** | Med | Console front-end calls **`/console/api/...`** (also `/console/kbapi`, `/console/waapi`), but `CLAUDE.md` §2/§5/§8 document **`/dashboard/api/...`**. | `console-src/index.html:153` and `dashboard/index.html:153`: `const API="/console/api", KBAPI="/console/kbapi";` | The live webhook app appears to mount the ticket-action endpoints under a **`/console/*`** prefix (older `/dashboard` era = `dashboard/index.html`; current = `console-src/index.html`). Back-end source is VPS-only — **confirm the real route prefix against `bb_webhook/app.py`** after pulling (§5). Any agent following CLAUDE.md's `/dashboard/api` paths will hit the wrong (or legacy) route. |
| **DRIFT-2** | Low | A live **"Post drafts to Gorgias"** toggle exists, which CLAUDE.md doesn't mention — it makes auto-posting *configurable*. | `console-src/index.html:344` (label + hint "On = agent posts drafts as internal notes on tickets. Off = draft-only (safe review mode)"), `:339`/`:512` (`gorgias_writes_enabled`, `PUT /console/api/settings`) | **Reconciles the safety docs:** `CLAUDE.md` §4 shows an auto-posted internal note, while §2/§8 (2026-07-09) say drafts are shown for review and nothing is auto-posted. Both are true depending on this toggle. **Default should be OFF (draft-only)** for a safe handover — verify the live default. |
| **DRIFT-3** | Low | Live services **missing from `CLAUDE.md` §6 port table**: `buttonsbebe-kb-admin` (Notice Board / editable-KB API, **port 8087**) and the `buttonsbebe-kb-notices-gc` **timer** (15-min GC of expired notices). | `kb-admin/server.js:13` (`KB_ADMIN_PORT || 8087`), `kb-admin/buttonsbebe-kb-admin.service:8`, `kb/buttonsbebe-kb-notices-gc.{service,timer}`, `kb/scripts/purge_notices.py` | Both are real and in the repo; just undocumented in §6. Add them to the port/timer inventory. (Doc 05 already lists 8087.) |

---

## 5. Safe read-only VPS pull — complete the repo WITHOUT changing the VPS

**This is read-only.** `rsync`/`scp` here **copy FROM the VPS to your machine**; they change nothing on the server. Do **not** pass `--delete`, do **not** push, do **not** run `git` on the VPS. Run every command below **from the new team's laptop**, in the cloned repo root.

VPS: `root@2.25.137.77`, code under `/root/Buttonsbebe Agent/`. (Quote the path — it contains a space.)

### 5a. Pull the runtime source (secrets & data excluded)

```bash
# 1. FastAPI webhook app (serves /console + /dashboard + the queue)
rsync -avz \
  --exclude '.env' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude 'data/' --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' \
  root@2.25.137.77:'/root/Buttonsbebe Agent/webhook/' ./webhook/

# 2. Processor / orchestrator (orchestrator, hermes_runner, gorgias_writer, classifier, etc.)
rsync -avz \
  --exclude '.env' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude 'data/' --exclude '*.db' \
  root@2.25.137.77:'/root/Buttonsbebe Agent/processor/' ./processor/

# 3. Hermes home → ./hermes-home/  (SOUL.md + skills/; scrub config.yaml secrets — see checklist)
rsync -avz \
  --exclude '.env' --exclude '__pycache__/' --exclude 'platforms/' --exclude 'session/' \
  --exclude 'auth/' --exclude '*.log' \
  root@2.25.137.77:'~/.hermes/' ./hermes-home/
```

### 5b. Pull the VPS-only learning scripts & units

These live under `KB/scripts/` and the project root on the VPS but are **not** in the repo:

```bash
# nightly learning-promotion pieces (verified absent from repo)
scp root@2.25.137.77:'/root/Buttonsbebe Agent/KB/scripts/auto_promote_learned.py' ./kb/scripts/
scp root@2.25.137.77:'/root/Buttonsbebe Agent/KB/learn-nightly.sh'                 ./kb/
scp root@2.25.137.77:'/root/Buttonsbebe Agent/KB/buttonsbebe-kb-learn.service'     ./kb/
scp root@2.25.137.77:'/root/Buttonsbebe Agent/KB/buttonsbebe-kb-learn.timer'       ./kb/
```
*(Exact paths may differ — the `learning.py` lesson-capture module comes down with the `webhook/` pull in 5a as `webhook/src/bb_webhook/learning.py`. If a file isn't where expected, locate it read-only: `ssh root@2.25.137.77 "ls -la '/root/Buttonsbebe Agent/KB/scripts' '/root/Buttonsbebe Agent/KB'"`.)*

### 5c. Product corpus + LanceDB index — REGENERATE, don't pull

The `kb/products/` corpus (~4,246 items) and the built LanceDB index are large and derived. Rebuild locally instead of copying:

```bash
cd kb && ./sync-products.sh && python3 scripts/index_kb.py   # or ./setup.sh
```
(Requires the Shopify client-credentials in `.env` and the pinned `kb/requirements.txt` deps.)

### 5d. Before committing anything pulled — checklist

- [ ] **Scrub secrets.** Delete any `.env`, `*.env`, tokens, or keys that slipped through. **Open `hermes-home/config.yaml` and remove/redact any API keys, provider tokens, or model credentials** before it goes near Git (the retired backup kept its hermes config under `secrets/` for a reason).
- [ ] **Remove customer PII.** Do **not** commit `webhook/data/` or any `*.db` (the job queue holds raw customer message text), the WhatsApp `auth/`/`session/` dirs, `notify.json`, or **un-masked** `KB/learned/lesson-*.md` (PII is only masked at nightly promotion time).
- [ ] **Keep the existing `.gitignore` rules** (they already ignore `.env`, `**/.env`, `.env.bak-*`, `data/`, `__pycache__/`, `*.pyc`, `.DS_Store`, `_VPS-FULL-BACKUP-*/`). Add `webhook/data/`, `hermes-home/config.yaml` (or a secret-scrubbed variant), and any WhatsApp `auth/` path if not already covered.
- [ ] **Verify before staging:** `git status`, then `git check-ignore webhook/.env processor/.env hermes-home/config.yaml` — anything sensitive should print (i.e. be ignored). Run a secret scan (e.g. `git grep -nEi 'api[_-]?key|secret|token|password'` on the staged set) before the first commit.
- [ ] **Confirm the drift items (§4b)** against the freshly pulled `bb_webhook/app.py` (the real `/console` vs `/dashboard` route prefix) and update `CLAUDE.md` §5/§6.

---

## 6. Immediate remediation checklist — day one

Ordered by risk. Items 1–3 are security and should happen before the system is trusted with live traffic.

1. **Roll out the secured WhatsApp configuration (#12).**
   - Generate different random values for `WA_TOKEN` (32+ chars), `WA_PASSWORD` (16+ chars), and `WA_SEND_SECRET` (32+ chars) in the dedicated `whatsapp-connect/.env`; rotate the existing token/password and set file mode `600`.
   - Configure the VPS-only processor to authenticate `POST /connect-whatsapp/<WA_TOKEN>/send` with `Authorization: Bearer <WA_SEND_SECRET>` (preferred) or use the secret as the HTTP Basic password through `WHATSAPP_SEND_URL`.
   - Deploy the updated `server.js`, `security.js`, service unit, and dependencies; restart WhatsApp Connect only after the processor is ready. Verify missing/wrong auth returns 401 and a valid test alert succeeds.

2. **Rotate all live API keys and lock down `.env` (#13).**
   - Rotate the **Gorgias** API key, **Shopify** client secret, and **Redo** key (rotate anything that may have been pasted into chat/notes historically).
   - Note: a **populated `.env` and a `.env.bak-20260708`** exist on disk at the repo root. **Verified git-ignored and untracked** (`git check-ignore` flags both; only `.env.example` is tracked) — but they are real secret-bearing files on the local disk. Treat as secret, `chmod 600`, never commit, and delete the stale `.env.bak-20260708` once keys are rotated.

3. **Consolidate the two `.env` files into one source of truth (#9 / L1).**
   - Merge `webhook/.env` vars into the main `/root/Buttonsbebe Agent/.env` (or symlink), point both `config.py` files at it, restart services, verify. While here, fix `SHOPIFY_SHOP` in `webhook/.env` (**H1**) and decide whether the processor needs `REDO_*` (**H3**) / direct Shopify client-creds (**#1/H2**).

4. **Safety fast-follows (same week).**
   - Implement the deterministic `classifier.py` gate (**#3**) so escalation doesn't depend solely on the LLM (note: WhatsApp escalation only fires on IMMEDIATE).
   - Review the processor's `--yolo` flag and restrict the toolset (**#8**); confirm `gorgias_writer.py` is the only write path.
   - Set/verify the **"Post drafts to Gorgias" toggle default to OFF (draft-only)** until the team trusts output (**DRIFT-2**).

5. **Reconcile docs.**
   - Confirm the real Console route prefix (`/console` vs `/dashboard`, **DRIFT-1**) and add `buttonsbebe-kb-admin` (8087) + `buttonsbebe-kb-notices-gc.timer` to `CLAUDE.md` §6 (**DRIFT-3**).

> **Where the real Fable source lives (context):** the `fable/` directory on the **`main`** branch is effectively empty — verified **0 tracked files** and **0 `.py` files** in the working tree (only `__pycache__/*.pyc`, `.pytest_cache/`, and `*.db*` artifacts, all git-ignored). The actual Fable source (an alternative FastAPI stack + emulators + tests) — **59 Python files** under `fable/` — lives **only on the `Fable_buttonsbebe` branch** (`git ls-tree -r --name-only Fable_buttonsbebe | grep '^fable/.*\.py$'`). If the new team intends to work on Fable, check out that branch; the `main` checkout will look deceptively empty.
