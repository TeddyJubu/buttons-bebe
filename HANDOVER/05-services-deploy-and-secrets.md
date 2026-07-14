# 05 · Services, Deployment & Secrets

> **SUPERSEDED (2026-07-14):** This historical handover chapter is not current operational documentation. Do not use its counts, runtime status, write-path descriptions, or instructions. Use the repository-root `CLAUDE.md`, the user-provided `AGENTS.md`, and live verification instead.

**What this doc covers:** the running services & ports, the systemd unit/timer inventory (with repo source paths vs. VPS‑only), how each external system authenticates, the full environment‑variable inventory and the two‑`.env` split, the Caddy reverse proxy, and the operate/verify runbook.

**Sources read:** `CLAUDE.md` (§3, §6, §7, §8, §10, §11); `env.example` & `.env.example` (identical — verified with `diff`); NAMES‑only enumeration of the real root `.env` (`grep -oE '^[A-Za-z_]+=' .env` — no values read); `kb/buttonsbebe-kb-mcp.service`, `kb/buttonsbebe-kb-sync.service`, `kb/buttonsbebe-kb-sync.timer`, `kb/buttonsbebe-kb-notices-gc.service`, `kb/buttonsbebe-kb-notices-gc.timer`, `tools/buttonsbebe-gorgias-mcp.service`, `tools/buttonsbebe-redo-mcp.service`, `whatsapp-connect/buttonsbebe-whatsapp-connect.service`, `kb-admin/buttonsbebe-kb-admin.service`; `server-fixes.sh`, `kb/setup.sh`, `kb/run_mcp.sh`, `kb/sync-products.sh`, `kb/update.sh`, `kb/search.sh`, `tools/run-gorgias.sh`, `tools/run-redo.sh`; `whatsapp-connect/Caddyfile`; `.gitignore`.

> ⚠️ **Read `CLAUDE.md` first.** It is the declared source of truth. Everything below is the *live* (post‑2026‑07‑06 rebuild) design. Ignore anything under `_VPS-FULL-BACKUP-20260706/` — that is the retired system and is git‑ignored.
>
> **Secrets note:** No secret *values* appear in this doc. Variable names were confirmed from placeholder files and from a names‑only grep of the real `.env`. Fill real values only in the un‑committed `.env` files on the VPS.

---

## 1. Ports & services (all bound to `127.0.0.1`)

Every service listens on loopback only; the public surface is fronted by Caddy (see §6). Unit names are from `CLAUDE.md §6`; the `.service`/`.timer` filenames in the repo match the deployed unit names 1:1.

| Port | Service | systemd unit | Source in repo |
|---|---|---|---|
| **8000** | Webhook receiver **+** `/dashboard` (FastAPI / `bb_webhook`, uvicorn) | `buttonsbebe-webhook` | ⚠️ **VPS‑only** — no `webhook/` source dir in this repo |
| **8077** | KB MCP — `search_kb` (LanceDB hybrid search) | `buttonsbebe-kb-mcp` | `kb/buttonsbebe-kb-mcp.service` |
| **8078** | Redo MCP — returns / refunds status | `buttonsbebe-redo-mcp` | `tools/buttonsbebe-redo-mcp.service` |
| **8079** | Gorgias MCP — read ticket / customer / order | `buttonsbebe-gorgias-mcp` | `tools/buttonsbebe-gorgias-mcp.service` |
| **8085** | WhatsApp connect (QR pairing + Hermes 2‑way bridge; Node + Baileys) | `buttonsbebe-whatsapp-connect` | `whatsapp-connect/buttonsbebe-whatsapp-connect.service` |
| **8087** | KB admin — editable‑KB API for the console *(not listed in `CLAUDE.md §6`; see note below)* | `buttonsbebe-kb-admin` | `kb-admin/buttonsbebe-kb-admin.service` |
| — | Job processor / orchestrator (the poll loop; runs Hermes once per ticket) | `buttonsbebe-processor` | ⚠️ **VPS‑only** — no `processor/` source dir in this repo |
| — | Product sync (Shopify → KB, then re‑index) — every 3 days | `buttonsbebe-kb-sync` (+ `.timer`) | `kb/buttonsbebe-kb-sync.service` / `.timer` |
| — | Notice‑board GC (drop expired notices) — every 15 min | `buttonsbebe-kb-notices-gc` (+ `.timer`) | `kb/buttonsbebe-kb-notices-gc.service` / `.timer` |
| — | Learning promotion (promote `KB/learned/` lessons) — nightly **03:30** | `buttonsbebe-kb-learn` (+ `.timer`) | ⚠️ **VPS‑only** — unit/timer file **not in repo** (behaviour per `CLAUDE.md §8`) |

**Findings / doc‑drift flags for the new team:**
- **`buttonsbebe-webhook` (8000) and `buttonsbebe-processor` have NO source in this repo.** `CLAUDE.md` places them at `/root/Buttonsbebe Agent/webhook` and `/root/Buttonsbebe Agent/processor` on the VPS, but there are no `webhook/` or `processor/` directories in the clone (confirmed). Their code, unit files, and `webhook/.env` live only on the VPS. **Obtain these from the VPS during handover.**
- **`buttonsbebe-kb-notices-gc` and `buttonsbebe-kb-admin` (port 8087) exist in the repo but are absent from the `CLAUDE.md §6` port table** — they were added later (2026‑07‑12, per file dates + `SPRINT-notice-board-2026-07-12.md`). Treat them as live; the `CLAUDE.md` table is slightly stale.
- **`buttonsbebe-kb-learn.timer` (nightly 03:30)** is described in `CLAUDE.md §8` (runs `KB/scripts/auto_promote_learned.py` then `learn-nightly.sh`) but **its unit/timer files are not in the repo** — VPS‑only.

---

## 2. systemd unit & timer inventory

### 2.1 Always‑on services (`Type=simple`, `Restart=on-failure`, `RestartSec=3`)

| Unit | Effective command (`ExecStart`) | Env set in unit | Source file |
|---|---|---|---|
| `buttonsbebe-kb-mcp` | `/root/kb-mcp-run.sh` → execs `"/root/Buttonsbebe Agent/KB/.venv/bin/python" ".../KB/scripts/kb_mcp_server.py"` | `KB_MCP_TRANSPORT=streamable-http`, `KB_MCP_HOST=127.0.0.1`, `KB_MCP_PORT=8077` | `kb/buttonsbebe-kb-mcp.service` (launcher repo src: `kb/run_mcp.sh`) |
| `buttonsbebe-gorgias-mcp` | `/root/gorgias-mcp-run.sh` → execs `".../tools/.venv/bin/python" ".../tools/gorgias_mcp.py"` | `GORGIAS_MCP_TRANSPORT=streamable-http`, `GORGIAS_MCP_HOST=127.0.0.1`, `GORGIAS_MCP_PORT=8079` | `tools/buttonsbebe-gorgias-mcp.service` (launcher repo src: `tools/run-gorgias.sh`) |
| `buttonsbebe-redo-mcp` | `/root/redo-mcp-run.sh` → execs `".../tools/.venv/bin/python" ".../tools/redo_mcp.py"` | `REDO_MCP_TRANSPORT=streamable-http`, `REDO_MCP_HOST=127.0.0.1`, `REDO_MCP_PORT=8078` | `tools/buttonsbebe-redo-mcp.service` (launcher repo src: `tools/run-redo.sh`) |
| `buttonsbebe-whatsapp-connect` | `/bin/bash -c 'exec __NODE__ ".../whatsapp-connect/server.js"'` (WorkingDirectory `.../whatsapp-connect`) | reads `whatsapp-connect/.env` for `WA_TOKEN`, `WA_PASSWORD`, `WA_SEND_SECRET`; sets `WA_PORT=8085`, `WA_AUTH_DIR=.../whatsapp-connect/auth`, `HERMES_BIN=__HERMES_BIN__` | `whatsapp-connect/buttonsbebe-whatsapp-connect.service` |
| `buttonsbebe-kb-admin` | `/bin/bash -c 'exec __NODE__ ".../kb-admin/server.js"'` | `KB_ADMIN_PORT=8087`, `KB_DIR=/root/Buttonsbebe Agent/KB` | `kb-admin/buttonsbebe-kb-admin.service` |
| `buttonsbebe-webhook` | uvicorn serving `bb_webhook` on `:8000` (per `CLAUDE.md`) | — (reads `webhook/.env`) | ⚠️ **VPS‑only** |
| `buttonsbebe-processor` | `python -m orchestrator` — polls the queue ~every 2s, runs `hermes --yolo` once per ticket (per `CLAUDE.md §4`) | — (reads `webhook/.env`) | ⚠️ **VPS‑only** |

> **`__PLACEHOLDER__` substitution:** the WhatsApp and KB‑admin unit files ship with `__NODE__` and `__HERMES_BIN__` tokens. These are substituted with real absolute paths at deploy time on the VPS (Node lives at a non‑fixed path; the `Buttonsbebe Agent` folder name contains a space). WhatsApp secrets come from `whatsapp-connect/.env` and are never substituted into the unit.
>
> **Space‑free launcher shims:** `/root/kb-mcp-run.sh`, `/root/gorgias-mcp-run.sh`, `/root/redo-mcp-run.sh` exist because the install path `"/root/Buttonsbebe Agent/"` contains a space that systemd's/Hermes' command runner can't pass through cleanly. Repo sources are `kb/run_mcp.sh`, `tools/run-gorgias.sh`, `tools/run-redo.sh`; each just `exec`s the venv Python on the corresponding server module.

### 2.2 Oneshot services + timers

| Timer (unit) | Schedule | Runs (oneshot service `ExecStart`) | Source |
|---|---|---|---|
| `buttonsbebe-kb-sync.timer` | `OnActiveSec=3d` + `OnUnitActiveSec=3d`, `Persistent=true` → **first run 3 days after enable, then every 3 days** | `"/root/Buttonsbebe Agent/KB/sync-products.sh"` (`Type=oneshot`, `TimeoutStartSec=1800`) | `kb/buttonsbebe-kb-sync.timer` + `.service` (script repo src: `kb/sync-products.sh`) |
| `buttonsbebe-kb-notices-gc.timer` | `OnBootSec=5min` + `OnUnitActiveSec=15min` → **~5 min after boot, then every 15 min** | `/usr/bin/python3 "/root/Buttonsbebe Agent/KB/scripts/purge_notices.py"` (`Type=oneshot`, `TimeoutStartSec=60`; system Python, no venv) | `kb/buttonsbebe-kb-notices-gc.timer` + `.service` |
| `buttonsbebe-kb-learn.timer` | **Nightly 03:30** (per `CLAUDE.md §8`) | `KB/scripts/auto_promote_learned.py` → `learn-nightly.sh` (rebuild index) | ⚠️ **VPS‑only** — files not in repo |

---

## 3. Authentication per external system

Three external systems, three different auth schemes. Where each credential is *read* depends on the two‑`.env` split (§4).

| System | Auth scheme | Credentials (var names) | Read by | Notes |
|---|---|---|---|---|
| **Shopify** | **Client‑credentials grant** — exchange client id + secret for a **24‑hour** Admin API token, minted on demand | `SHOPIFY_SHOP`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET` (opt: `SHOPIFY_API_VERSION`, `SHOPIFY_PRODUCT_QUERY`) | MCP tools (from **MAIN** `.env`); product‑sync script | Note the domains differ: Shopify store is `buttons-bebe.myshopify.com` (**hyphen**); Gorgias subdomain is `buttonsbebe` (**no hyphen**). See the `.env` wart in §5. |
| **Gorgias** | **Basic Auth** (email = username, API key = password) | `GORGIAS_SUBDOMAIN`, `GORGIAS_API_EMAIL`, `GORGIAS_API_KEY` | MCP tools (**MAIN**) **and** webhook + processor (**webhook/.env**) — duplicated, kept in sync | The only external system the platform **writes** to (staff internal note, and human‑initiated public reply). |
| **Redo** | **Bearer token** | `REDO_API_KEY`, `REDO_STORE_ID` | Redo MCP tool only, from **MAIN** `.env` | Lives **only** in MAIN. The processor reaches Redo *through the `buttonsbebe_redo` MCP tool* — it does **not** read `REDO_*` from `webhook/.env`. |

**Hermes brain / LLM:** model `glm-5.2` via **Ollama Cloud**, configured in `~/.hermes/config.yaml` (VPS). `env.example` exposes optional overrides `LLM_MODEL`, `LLM_BASE_URL` (`https://ollama.com/v1`), `LLM_API_KEY` for the processor path.

### The two‑`.env` split (`CLAUDE.md §7`)

There are **two** separate env files on the VPS (a known wart, §5):

| File (VPS path) | Read by | Contains | In repo? |
|---|---|---|---|
| **MAIN** — `/root/Buttonsbebe Agent/.env` | the **3 MCP tool modules** (KB / Redo / Gorgias) | `GORGIAS_*`, `SHOPIFY_SHOP` / `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET`, `REDO_API_KEY`, `REDO_STORE_ID` (plus deployed extras — see §4.2) | ❌ git‑ignored (`.env`). Placeholder shipped as `env.example` / `.env.example`. |
| `/root/Buttonsbebe Agent/webhook/.env` | the **webhook app + processor** (`processor/config.py`) | `GORGIAS_*`, `WEBHOOK_SECRET`, `WEBHOOK_*`, `SHOPIFY_*`, `LLM_*`, `PROCESSOR_*`, `KB_MCP_URL`, `LOG_*` | ⚠️ **VPS‑only** — the `webhook/` dir isn't in this repo at all. |

> `env.example` / `.env.example` is a **single merged reference** listing variables for *both* files ("ALL credentials & settings" per its header). Use the "Which `.env`" column in §4.1 to route each variable to the correct file when provisioning.

---

## 4. Environment‑variable inventory

### 4.1 Variables documented in `env.example` / `.env.example`

(The two example files are byte‑identical.) Uncommented = required/active default; commented (`#`) = optional override or stub.

| Variable | Which `.env` | Used by | Purpose | Example placeholder |
|---|---|---|---|---|
| `GORGIAS_SUBDOMAIN` | MAIN **+** webhook | Gorgias MCP, webhook, processor | Gorgias subdomain (part before `.gorgias.com`, **no hyphen**) | `buttonsbebe` |
| `GORGIAS_API_EMAIL` | MAIN **+** webhook | Gorgias MCP, webhook, processor | Basic‑Auth **username** (Gorgias → Settings → REST API) | *(from Gorgias)* |
| `GORGIAS_API_KEY` | MAIN **+** webhook | Gorgias MCP, webhook, processor | Basic‑Auth **password / API key** | *(from Gorgias)* |
| `SHOPIFY_SHOP` | MAIN (+ webhook) | Shopify calls in MCP tools; product sync | Full store domain (**with hyphen**) | `buttons-bebe.myshopify.com` |
| `SHOPIFY_CLIENT_ID` | MAIN | MCP tools; product sync | Client‑credentials app **client id** (mints 24h token) | *(from Shopify app)* |
| `SHOPIFY_CLIENT_SECRET` | MAIN | MCP tools; product sync | Client‑credentials app **client secret** | *(from Shopify app)* |
| `SHOPIFY_API_VERSION` *(commented)* | MAIN | MCP tools; product sync | Admin API version pin | `2026-04` |
| `SHOPIFY_PRODUCT_QUERY` *(commented)* | MAIN | product sync | Product filter; `""` = sync ALL incl. drafts | `status:active` |
| `REDO_API_KEY` | **MAIN only** | Redo MCP | Redo **Bearer** token | *(from Redo)* |
| `REDO_STORE_ID` | **MAIN only** | Redo MCP | Redo store identifier | *(from Redo)* |
| `WEBHOOK_SECRET` | webhook | webhook receiver | HMAC shared secret to verify Gorgias webhooks (`openssl rand -hex 32`) | *(32‑byte hex)* |
| `WEBHOOK_HOST` | webhook | webhook receiver | Bind host (loopback) | `127.0.0.1` |
| `WEBHOOK_PORT` | webhook | webhook receiver | Bind port | `8000` |
| `WEBHOOK_DB_PATH` | webhook | webhook + processor | SQLite job‑queue path (relative to `webhook/`) | `./data/webhook.db` |
| `KB_MCP_URL` | webhook | processor | URL of the KB `search_kb` tool | `http://127.0.0.1:8077/mcp` |
| `LLM_MODEL` | webhook | processor / Hermes | Hermes model name (Ollama Cloud) | `glm-5.2` |
| `LLM_BASE_URL` *(commented)* | webhook | processor / Hermes | LLM API base URL | `https://ollama.com/v1` |
| `LLM_API_KEY` *(commented)* | webhook | processor / Hermes | LLM API key | *(from Ollama Cloud)* |
| `PROCESSOR_POLL_INTERVAL` *(commented)* | webhook | processor | Queue poll interval (seconds) | `2.0` |
| `PROCESSOR_JOB_TIMEOUT` *(commented)* | webhook | processor | Per‑job timeout (seconds) | `120` |
| `PROCESSOR_MAX_RETRIES` *(commented)* | webhook | processor | Max retries per job | `3` |
| `PROCESSOR_STALE_MINUTES` *(commented)* | webhook | processor | Re‑queue jobs stuck this long | `10` |
| `LOG_FORMAT` | webhook | webhook + processor | Log format | `json` |
| `LOG_LEVEL` | webhook | webhook + processor | Log verbosity | `INFO` |

### 4.2 Variables present on the deployed box but **NOT** in the example files

Confirmed by a **names‑only** grep of the real MAIN `.env` and by reading the committed unit files (no values were read). Flag these for the new team — the example file is out of date relative to the running box.

| Variable | Where it lives | Used by | Purpose |
|---|---|---|---|
| `SHOPIFY_API_TOKEN` | MAIN `.env` (present) | (webhook code path) | **Static Admin token — the "code half" wart** (see §5). Present but the client‑credentials flow is the live path. |
| `GORGIAS_SANDBOX_API_KEY` | MAIN `.env` (present) | dev/testing | Gorgias sandbox Basic‑Auth key |
| `GORGIAS_SANDBOX_EMAIL` | MAIN `.env` (present) | dev/testing | Gorgias sandbox username |
| `GORGIAS_SANDBOX_BASE_URL` | MAIN `.env` (present) | dev/testing | Gorgias sandbox base URL |
| `WA_TOKEN` | `whatsapp-connect/.env` | WhatsApp connect (`:8085`) | Random 32+ character path segment for the QR pairing URL `/connect-whatsapp/<token>/` |
| `WA_PASSWORD` | `whatsapp-connect/.env` | WhatsApp connect (`:8085`) | Random 16+ character gate on the connect page |
| `WA_SEND_SECRET` | `whatsapp-connect/.env` + processor secret config | WhatsApp connect + VPS processor | Separate random 32+ character credential required by the escalation send endpoint |
| `WA_PORT` | `whatsapp-connect` unit | WhatsApp connect | Listen port (`8085`) |
| `WA_AUTH_DIR` | `whatsapp-connect` unit | WhatsApp connect | Baileys auth/session dir (`.../whatsapp-connect/auth`) |
| `HERMES_BIN` | `whatsapp-connect` unit (`__HERMES_BIN__`) | WhatsApp connect | Path to the Hermes CLI for the 2‑way bridge |
| `KB_ADMIN_PORT` | `kb-admin` unit | KB admin | Listen port (`8087`) |
| `KB_DIR` | `kb-admin` unit | KB admin | KB content root (`/root/Buttonsbebe Agent/KB`) |
| `KB_MCP_TRANSPORT` / `KB_MCP_HOST` / `KB_MCP_PORT` | `kb-mcp` unit | KB MCP | Transport (`streamable-http`) / bind host / port `8077` |
| `GORGIAS_MCP_TRANSPORT` / `_HOST` / `_PORT` | `gorgias-mcp` unit | Gorgias MCP | Transport / host / port `8079` |
| `REDO_MCP_TRANSPORT` / `_HOST` / `_PORT` | `redo-mcp` unit | Redo MCP | Transport / host / port `8078` |
| `WHATSAPP_SEND_URL` | processor **systemd drop‑in** (VPS) | processor `whatsapp_notifier.py` | Delivery URL the processor POSTs escalation alerts to (the WhatsApp‑connect send endpoint). Per `CLAUDE.md §5` + `DEV-ISSUES.md`. Not in `env.example`. |
| `WA_SEND_SECRET` | processor **systemd drop‑in** (VPS) | processor `whatsapp_notifier.py` | Dedicated Bearer credential matching the WhatsApp-connect `WA_SEND_SECRET`; never put it in the URL. |

> `.env` files are git‑ignored (`.gitignore`: `.env`, `**/.env`, `.env.bak-*`), and the full VPS backup (`_VPS-FULL-BACKUP-*/`, which contains plaintext secrets) is git‑ignored too. Never commit a filled‑in `.env`.

---

## 5. Known `.env` warts (`CLAUDE.md §11`)

1. **Duplication across two files.** Gorgias credentials (`GORGIAS_SUBDOMAIN` / `GORGIAS_API_EMAIL` / `GORGIAS_API_KEY`) live in **both** MAIN `.env` and `webhook/.env` and must be **kept in sync by hand**. Redo lives only in MAIN. This split is intentional but fragile — a mismatch silently breaks either the MCP tools or the webhook/processor.
2. **Shopify "code half" — a stray static token.** The live Shopify auth is **client‑credentials** (mint a 24h token from id+secret). But `webhook/config.py` still reads a **static** `SHOPIFY_API_TOKEN` field rather than the client‑cred keys, and that variable **is present in the real MAIN `.env`** (confirmed by name). It only matters *if the webhook ever calls Shopify directly* — which it does **not** today. Clean up: converge the webhook onto the client‑credentials path and drop the static token.
3. **`SHOPIFY_SHOP` hyphen trap.** The correct value has a hyphen (`buttons-bebe.myshopify.com`); the Gorgias subdomain does not (`buttonsbebe`). A past bug set the webhook's `SHOPIFY_SHOP` to the hyphen‑less form; `server-fixes.sh` (fix **H1**) rewrites it on the VPS. Watch for this when provisioning.
4. **WhatsApp deployment coordination.** The repository now requires three separate secrets from the dedicated `whatsapp-connect/.env` and contains no weak defaults. The VPS processor must send `WA_SEND_SECRET` as Bearer or Basic authentication; rotate the old token/password and configure that caller before restarting WhatsApp Connect.
5. **VPS‑only env & source.** `webhook/.env` and the entire `webhook/` and `processor/` source trees are **not in this repo** — retrieve them from the VPS. The example file is also drifting (§4.2 lists live vars it omits).

`server-fixes.sh` (run **on the VPS as root**, backs up every file it touches) addresses server‑only issues: **H1** fix `SHOPIFY_SHOP` hyphen in `webhook/.env`; **H4** add missing `await` to the `PRAGMA busy_timeout` call in `webhook/src/bb_webhook/database.py`; **H3** *report only* where `REDO_*` creds live (confirm they're MAIN‑only, not duplicated into `webhook/.env`); then `systemctl restart buttonsbebe-processor`.

---

## 6. Caddy reverse proxy (public HTTPS)

Source: `whatsapp-connect/Caddyfile` (deployed as the system Caddy config on the VPS). Caddy terminates TLS (auto Let's Encrypt) and is the **only** public entry point; all app services stay on `127.0.0.1`.

```
srv1766050.hstgr.cloud {
    handle /connect-whatsapp/* {
        reverse_proxy 127.0.0.1:8085          # WhatsApp connect (QR pairing + bridge)
    }
    handle {                                   # everything else
        reverse_proxy 127.0.0.1:8000 {         # webhook receiver + /dashboard
            header_up Host {host}
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }
    request_body { max_size 256KB }            # cap request bodies
    header {                                    # security headers
        X-Content-Type-Options nosniff
        Referrer-Policy no-referrer
    }
    encode gzip zstd
    log { output file /var/log/bb-webhook/caddy.log; format json }
}
```

| Public path (HTTPS `srv1766050.hstgr.cloud`) | → Upstream | Service |
|---|---|---|
| `/connect-whatsapp/*` | `127.0.0.1:8085` | WhatsApp connect (owner scans the auto‑refreshing QR at `/connect-whatsapp/<WA_TOKEN>/`) |
| everything else (incl. Gorgias webhook `POST /webhook/gorgias/{tenant}` and `/dashboard`) | `127.0.0.1:8000` | Webhook receiver + dashboard |

Reload after edits: `systemctl reload caddy`. Access log: `/var/log/bb-webhook/caddy.log` (JSON). Note the **256 KB** request‑body cap applies to incoming webhooks.

---

## 7. Operate & verify runbook

Reproduced from `CLAUDE.md §10`, with the local repo‑source scripts noted. Run on the VPS unless stated.

**A. Hermes ↔ MCP tools wired?**
```bash
hermes mcp list                    # expect the 3 tools (buttonsbebe_kb/_redo/_gorgias), all enabled
hermes mcp test buttonsbebe_kb     # → "Connected, N tools"  (repeat for _redo / _gorgias)
```
Hermes registers the tools by URL in `~/.hermes/config.yaml` (:8077 / :8078 / :8079).

**B. Services healthy?**
```bash
systemctl status buttonsbebe-processor buttonsbebe-kb-mcp buttonsbebe-redo-mcp buttonsbebe-gorgias-mcp
# also useful:
systemctl status buttonsbebe-webhook buttonsbebe-whatsapp-connect buttonsbebe-kb-admin
systemctl list-timers 'buttonsbebe-*'   # confirm kb-sync (3d), notices-gc (15m), kb-learn (03:30) are scheduled
```

**C. Tail the brain's logs:**
```bash
journalctl -u buttonsbebe-processor -n 50           # last 50 lines
journalctl -u buttonsbebe-processor -f              # follow live
```

**D. Test the knowledge base directly** (repo script: `kb/search.sh` → `.venv/bin/python scripts/search_kb.py "$@"`):
```bash
cd "/root/Buttonsbebe Agent/KB" && ./search.sh "do you ship to canada"
```

**E. Manually refresh products** (else the timer does it every 3 days; repo script: `kb/sync-products.sh`):
```bash
cd "/root/Buttonsbebe Agent/KB" && ./sync-products.sh
# runs: sync_products.py → index_kb.py → systemctl restart buttonsbebe-kb-mcp
```

**F. Inspect the job queue** (SQLite, WAL):
```bash
sqlite3 "/root/Buttonsbebe Agent/webhook/data/webhook.db" \
  "select status, count(*) from jobs group by status"
```

**G. First‑time / rebuild helpers** (repo scripts, run from the relevant folder — each builds its own `.venv`):
```bash
# KB search engine: create venv, install deps, build first index
cd "/root/Buttonsbebe Agent/KB" && ./setup.sh
# rebuild the KB index after editing content under the KB vault
cd "/root/Buttonsbebe Agent/KB" && ./update.sh          # → scripts/index_kb.py
```

---

### Appendix — repo → VPS deploy‑path map (quick reference)

| Repo source | Deployed to (VPS) |
|---|---|
| `kb/run_mcp.sh` | `/root/kb-mcp-run.sh` (space‑free launcher) |
| `tools/run-gorgias.sh` | `/root/gorgias-mcp-run.sh` |
| `tools/run-redo.sh` | `/root/redo-mcp-run.sh` |
| `kb/*.service` / `kb/*.timer`, `tools/*.service`, `whatsapp-connect/*.service`, `kb-admin/*.service` | `/etc/systemd/system/` |
| `whatsapp-connect/Caddyfile` | system Caddy config |
| `env.example` / `.env.example` | copy → `/root/Buttonsbebe Agent/.env` (MAIN) **and** `.../webhook/.env`, split per §3 |
| `KB/` content + `scripts/`, `sync-products.sh` | `/root/Buttonsbebe Agent/KB/` |
| *(not in repo)* | `/root/Buttonsbebe Agent/webhook/` and `/root/Buttonsbebe Agent/processor/` — **fetch from VPS** |
