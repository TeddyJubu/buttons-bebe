# KB Search Engine (LanceDB) — how to run and maintain it

This explains the **search engine** that sits on top of the content. For the
content itself (what's in `intents/`, `faq/`, `policies/`, `tickets/`) and the
file format, see `README.md` and `CONVENTIONS.md`.

You do not need to be technical. **You only ever edit the markdown files.**

---

## The three commands

Run these from inside the `KB` folder:

| Command | What it does | When |
|---|---|---|
| `./setup.sh` | Installs the engine (one time) | Once, at the start |
| `./update.sh` | Re-reads your content and rebuilds the search index | After you add/edit any content file |
| `./search.sh "your question"` | Shows what the agent would find | Any time, to check quality |

Example:

```
./search.sh "how long does shipping take"
./search.sh "can I get a refund"
```

Each result shows the file, the section, a relevance score, and — for topics
tagged `refund`/`dispute`/`sensitive`/`escalation` — a **`[SENSITIVE -> escalate]`**
marker, so it's obvious which answers must go to a human.

---

## What gets indexed

- **Indexed:** `intents/`, `faq/`, `policies/`, `tickets/`, `products/` — each `##`
  section in a file becomes one searchable chunk (this matches `CONVENTIONS.md`).
- **Not indexed:** the `learned/` folder (review-only), folder `README.md` files,
  and any file whose name starts with `_`.

So the trusted, human-curated content is searchable, and the "learned" material
stays switched off until someone promotes it.

---

## Products (auto-synced from Shopify)

The `products/` folder is **filled automatically from your Shopify store** — you
don't hand-write these files.

- **How:** `scripts/sync_products.py` mints a fresh 24-hour Shopify token from the
  app's client id + secret (client-credentials grant), exports the catalog via
  Shopify's bulk API, and writes one concise markdown file per product (name,
  sizes/options, variants, prices, availability, description, link).
- **What's synced:** active/published products by default (currently ~4,200).
  To include everything, set `SHOPIFY_PRODUCT_QUERY=` (empty) in `.env`.
- **Refreshes automatically every 3 days** via a systemd timer
  (`buttonsbebe-kb-sync.timer`) — it re-syncs, re-indexes, and reloads the service.
- **Fails closed:** malformed/inactive records, orphan variants, an empty export,
  or a catalog retaining less than 75% of the current product files leave the
  existing corpus untouched. The sync lock and index lock remain held through
  the validated rebuild; if rebuilding fails, the previous product directory is
  restored and the last-known-good index stays live. After independently
  verifying an intentional large catalog reduction, a one-off run may set
  `SHOPIFY_ALLOW_LARGE_CATALOG_SHRINK=1`.
- **Run it on demand:** `./sync-products.sh`  (from the KB folder).
- **Check the schedule / last run:** `systemctl list-timers buttonsbebe-kb-sync.timer`
  and `journalctl -u buttonsbebe-kb-sync -n 30`.
- **Credentials** live in the agent's `.env` (`SHOPIFY_SHOP`, `SHOPIFY_CLIENT_ID`,
  `SHOPIFY_CLIENT_SECRET`); the scope needed is `read_products`.

## How it works (30-second version)

`./update.sh` reads your files, splits each into `##` sections, and stores every
section two ways: as **keywords** and as a **meaning fingerprint**. A question is
then searched **both** ways at once and the results are blended — so it matches
exact words (order numbers, SKUs, Hebrew/other languages) **and** paraphrases.

Under the hood: **LanceDB** (an embedded hybrid-search library — no server to run)
plus a **small local multilingual model** for the "meaning" side. Everything runs
on this server: **no API keys, no per-search cost, nothing leaves the box.**

---

## Files

```
KB/
  intents/ faq/ policies/ tickets/   <- YOUR CONTENT (edit these)
  learned/                            <- review-only, not searched
  scripts/
    kb_lib.py        shared helpers (reads files, makes fingerprints)
    index_kb.py      builds the index   (used by ./update.sh)
    search_kb.py     runs a search      (used by ./search.sh)
    kb_mcp_server.py OPTIONAL — connects the KB to the agent (a later step)
  lancedb/           the search index (auto-built; don't edit by hand)
  .venv/             the isolated install (auto-built by ./setup.sh)
  requirements.txt  setup.sh  update.sh  search.sh
  README.md  CONVENTIONS.md  SEARCH-ENGINE.md
```

---

## Connected to Hermes (always-on service)

This KB is wired into Hermes as the `search_kb` tool, served by a small
**always-on background service** so the tool is ready instantly on every session
(no cold-start, no "tool not available" misses).

- **Service:** `buttonsbebe-kb-mcp` (systemd). Runs the connector as an HTTP MCP
  server on **localhost:8077** (not exposed to the internet), with the search
  model kept loaded in memory. Verified connect time ~70 ms.
- **Registered in Hermes** as MCP server `buttonsbebe_kb` via the URL
  `http://127.0.0.1:8077/mcp`. The agent sees exactly one read-only tool, `search_kb`.
- **The model:** Hermes uses its already-configured Ollama Cloud model — no extra
  provider needed for the KB (search itself is fully local and provider-free).
- **Manage the service:**
  - status:  `systemctl status buttonsbebe-kb-mcp`
  - restart (only after editing the scripts):  `systemctl restart buttonsbebe-kb-mcp`
  - logs:    `journalctl -u buttonsbebe-kb-mcp -n 50`
- **After you edit content:** run `./update.sh`. The rebuild is staged and verified
  for exact source-text and sensitivity-label parity before a reader-locked
  promotion, so a failed rebuild leaves the last-known-good index live. The
  service picks up the new index automatically — no restart needed.
- **Verify anytime:**  `hermes mcp test buttonsbebe_kb`  (expect "Connected, 1 tool").
- **Rollback:**  `hermes mcp remove buttonsbebe_kb`  and
  `systemctl disable --now buttonsbebe-kb-mcp`.
  (Hermes config backups are at `~/.hermes/config.yaml.bak-*`.)

Verified end-to-end: Hermes searches the KB on its own for Buttons Bebe questions,
drafts grounded replies that cite the source file. Sensitive topics like refunds
still get a clearly marked safe draft; they are elevated for human review and are
never sent or posted automatically.

The older per-session (stdio) mode still exists as a fallback (`run_mcp.sh` with no
env vars), but the always-on service is what's used because it's reliable every run.

## Important notes

- This is the production LanceDB index for the current KB. Retired search
  backends are not part of the live system.
- After you edit content, run `./update.sh` so the agent searches the latest version.
- A backup copy of all this also lives in your project folder on your Mac.
