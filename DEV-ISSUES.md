# Buttons Bebe Agent — Issues list (for the junior developer)

Server: `srv1766050` (2.25.137.77), everything under `/root/Buttonsbebe Agent/`.
Architecture reference: `CLAUDE.md` (single source of truth).
Two sections: **A. Open issues to fix**, then **B. Already fixed (context only)**.

---

## A. OPEN — please fix

### Code / logic

1. **Shopify auth: code still expects a static token (should use client-credentials).**
   - Where: `webhook/src/bb_webhook/config.py` (and `processor/config.py`) — they define
     `shopify_admin_api_token` (alias `SHOPIFY_ADMIN_API_TOKEN`), which is empty/unused.
   - Problem: the real Shopify auth is the **client-credentials grant** using
     `SHOPIFY_CLIENT_ID` + `SHOPIFY_CLIENT_SECRET` (see `KB/scripts/sync_products.py` for the
     working pattern: POST `/admin/oauth/access_token` with `grant_type=client_credentials`
     to mint a 24h token). The `.env` files now carry the client id/secret, but the app code
     doesn't read them.
   - Fix: add `shopify_client_id` / `shopify_client_secret` settings and a small token-minting
     helper; use it wherever the app needs Shopify. Remove the dead `SHOPIFY_ADMIN_API_TOKEN`.

2. **Redo tool mismatch — processor prompt calls a tool that doesn't exist.**
   - Where: `processor/hermes_runner.py` (the prompt text) references
     `buttonsbebe_redo: get_order, get_returns_for_order`. But the Redo MCP module
     (`tools/redo_mcp.py`) exposes `list_recent_returns`, `get_returns_for_order`, `get_return`
     — there is **no `get_order`**.
   - Problem: if Hermes follows the prompt and calls `get_order`, it fails (no such tool).
   - Fix: either add a `get_order(order_name)` tool to `tools/redo_mcp.py` (the Redo API does
     return order data), or correct the prompt to the tools that actually exist.

3. **`classifier.py` is a STUB (returns NORMAL for everything).**
   - Where: `processor/classifier.py`.
   - Problem: the deterministic risk gate isn't implemented; risk classification currently
     relies entirely on the LLM. Safety should have a code-level gate too.
   - Fix: implement the planned IMMEDIATE/HIGH/NORMAL classifier (refunds, chargebacks,
     disputes, damaged/wrong/missing items, cancellations, angry customers → IMMEDIATE).

4. **`feedback_collector.py` is a STUB (logs only).**
   - Where: `processor/feedback_collector.py`.
   - Problem: the "learn from the human's actual reply → `KB/learned/ticket-<id>.md`" loop
     doesn't store anything yet.
   - Fix: implement it (fetch the agent's real reply from Gorgias, find the prior AI draft,
     save the human version into `KB/learned/`).

5. **Draft cleanup — the model leaks trailing self-commentary / duplicates the answer.**
   - Where: happens in the Hermes output; cleanest place to fix is `processor/hermes_runner.py`
     (or wherever the draft text is extracted before posting).
   - Problem: glm-5.2 sometimes appends text like *"The response above was complete…"* or
     repeats the whole draft twice (seen in QA #01, #04, #10). A human would have to trim it.
   - Fix: strip anything after the intended answer (e.g. cut at markers like "The response
     above was complete" / "The previous response was already complete", and de-duplicate
     repeated blocks) before the draft is posted. (Or evaluate a less chatty model.)

6. **Empty / no-content messages aren't handled.**
   - Where: the ticket-processing prompt / workflow (`processor/hermes_runner.py` + skill).
   - Problem: QA #19 — an **empty** customer message got a fabricated "thanks for your
     feedback" reply instead of recognizing there's nothing to answer.
   - Fix: guard for empty/whitespace-only/known-survey messages → don't draft; mark for
     human review or a no-op.

7. **One grounding slip — quoting an unconfirmed international shipping price.**
   - Where: KB content + prompt.
   - Problem: QA #03 — Hermes quoted a firm **"$35 USD"** international rate that is **not in
     the KB** (the KB says confirm international rates at checkout).
   - Fix: either add the real international rate to the KB (`policies/international-orders.md`)
     or reinforce "don't quote prices not in the KB" in the prompt/SOUL.

8. **Review the `--yolo` flag on the processor's Hermes call.**
   - Where: `processor/hermes_runner.py` (`hermes --yolo -z ...`).
   - Problem: `--yolo` auto-approves ALL tool calls with no confirmation. Safe today because
     the only write is a staff-only internal note, but it's worth a deliberate review/guardrail
     so a future tool can't be auto-invoked destructively.
   - Fix: restrict the toolset explicitly (e.g. `-t`) and/or confirm the write path
     (`processor/gorgias_writer.py`) is the only write and is gated.

9. **`.env` duplication — make one source of truth.**
   - Where: `/root/Buttonsbebe Agent/.env` (read by the MCP tools) and
     `/root/Buttonsbebe Agent/webhook/.env` (read by webhook + processor).
   - Problem: values live in two files and drifted (this caused a long Gorgias-key debugging
     detour — one file was edited while the code read the other).
   - Fix: consolidate to one `.env` (merge webhook-only vars into the main file, point both
     `config.py` files at it or symlink), then restart services and verify. See `.env.example`
     for the full variable list.

### Content (needs the owner / Chaim)

10. **KB policies still contain placeholder/DRAFT wording.**
    - Where: `KB/policies/*` and `KB/intents/*` (some marked `status: confirmed` but with
      conservative defaults pending owner confirmation).
    - Fix: get Chaim to confirm the real policy specifics (return window, who pays return
      shipping, sale-season rules, international rates, etc.) and replace placeholders.

11. **Minor KB accuracy — pickup vs return-bin location.**
    - Where: KB pickup/returns content.
    - Problem: QA #08 — the answer implied the 24/7 "side door bin" is at the 2133 Lakewood
      pickup spot, but the KB says the 24/7 return bin is at **6 Kenyon Drive**.
    - Fix: tighten the KB wording so the two locations aren't conflated.

### Security / hardening

12. **Strengthen the WhatsApp connect page protection.**
    - Where: `whatsapp-connect/server.js` (password gate) + the secret token in the URL.
    - Fix: use a strong, unique password (not a simple default) and rotate the URL token; keep
      the service bound to localhost (it already is) behind Caddy.

13. **Secrets hygiene.**
    - `.env` files hold live API keys — ensure they're `chmod 600`, never committed to git,
      and rotate the Gorgias key / Shopify secret if they were ever pasted into chat/notes.

---

## B. ALREADY FIXED (context — no action needed)

These were found and fixed during setup; listed so the dev has the history.

- **Paste artifacts in `.env`:** `SHOPIFY_SHOP` had a trailing `\`; `SHOPIFY_CLIENT_ID` and
  `SHOPIFY_CLIENT_SECRET` each had 2 trailing junk characters → auth failures. Cleaned.
- **`GORGIAS_SUBDOMAIN`** was set to a full URL (`https://buttonsbebe.gorgias.com`) → doubled
  domain. Fixed to the bare `buttonsbebe`.
- **Gorgias API key** was wrong/mismatched across the two `.env` files (401). Corrected and
  synced. (Reminder: Gorgias REST auth = Basic, username = the exact email on the REST-API
  page, password = the key; use `limit` for pagination, not `per_page`.)
- **Redo creds were commented out** in `.env` → not loaded. Uncommented; Redo works.
- **`SHOPIFY_SHOP` wrong in `webhook/.env`** (`buttonsbebe` vs `buttons-bebe.myshopify.com`).
  Corrected.
- **`database.py:114` missing `await`** on `conn.execute("PRAGMA busy_timeout=3000")`
  (un-awaited coroutine → RuntimeWarning, PRAGMA never applied). Fixed.
- **Webhook receiver was a hand-started `uvicorn` process** (not managed, not reboot-safe,
  ran stale code). Converted to the `buttonsbebe-webhook` systemd service.
- **`whatsapp_notifier.py`** POSTs escalations to the WhatsApp connect service
  (`WHATSAPP_SEND_URL`). (Note: only fires when a ticket is classified IMMEDIATE — see open
  item #3.)
- **Stale docs** describing the retired architecture were removed; `CLAUDE.md` is the current
  single source of truth (and is mirrored into Hermes' `SOUL.md`).

---

## Environment notes (so the dev isn't surprised)

- Python venvs need the `python3-venv` apt package (was missing).
- KB embeddings use `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` via
  `fastembed` (the `intfloat/multilingual-e5-small` name isn't supported in this fastembed
  version). Versions are pinned in `KB/requirements.txt`.
- `systemd Environment=` lines with spaces in the value must be quoted.
- Caddy's `file_server` can't read under `/root/` (perms) — static files go in `/var/www/`.
- Hermes model: `glm-5.2` via Ollama Cloud (`~/.hermes/config.yaml`). Works but is a bit
  verbose (see open item #5).
- Services (all systemd, localhost-bound): `buttonsbebe-webhook` (8000), `-kb-mcp` (8077),
  `-redo-mcp` (8078), `-gorgias-mcp` (8079), `-whatsapp-connect` (8085), `-processor`,
  `-kb-sync.timer`. Public entry via Caddy on `srv1766050.hstgr.cloud`.
