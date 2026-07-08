# Project Consistency Audit — Buttons Bebe Agent

**Date:** July 7, 2026 · **Scope:** local project docs vs. the live VPS (`srv1766050` / 2.25.137.77)

## The big picture (why inconsistencies exist)

There are effectively **three layers** in this project, built at different times, and the
docs haven't kept up:

1. **Old architecture (documented, but GONE).** Most docs (`CLAUDE.md`, `GOAL.md`,
   `PROJECT-SOURCE-OF-TRUTH.md`, `docs/hermes-rearchitecture/*`, `build/*`, `kb/README.md`)
   describe a `/root/gorgias-webhook` pipeline with "shadow mode," a Supermemory/ChromaDB
   knowledge base, an 8-tool `hermes-tools-mcp`, a WhatsApp/Baileys connector, a QA
   dashboard, and the "Mimo" model. **None of that exists on the current server** — the box
   was wiped and rebuilt (see `VPS-WIPE-AND-REBUILD-PLAN.md`, `_VPS-FULL-BACKUP-20260706/`).

2. **The running pipeline I did NOT build (pre-existing on the rebuilt box).** A webhook
   receiver on `127.0.0.1:8000` plus a **`buttonsbebe-processor` service** ("Hermes-powered
   job processor") that reads a SQLite queue (`webhook/data/webhook.db`; 53 done, 2 failed).
   This is the `bb_webhook` app under `/root/Buttonsbebe Agent/webhook/`.

3. **The tools I built this cycle (working).** A LanceDB knowledge base with auto-synced
   products, and three read-only Hermes MCP tools — `buttonsbebe_kb` (8077),
   `buttonsbebe_redo` (8078), `buttonsbebe_gorgias` (8079) — plus Hermes running on
   `glm-5.2` via Ollama Cloud.

The inconsistencies below are mostly (a) real config bugs in layer 2, and (b) docs from
layer 1 that now describe a system that no longer exists.

---

## HIGH — functional issues worth fixing

**H1. `SHOPIFY_SHOP` is wrong in the webhook config.**
`/root/Buttonsbebe Agent/.env` has `SHOPIFY_SHOP=buttons-bebe.myshopify.com` (correct), but
`/root/Buttonsbebe Agent/webhook/.env` has `SHOPIFY_SHOP=buttonsbebe` — **no hyphen, wrong
store**. If the webhook/processor ever calls Shopify directly, it targets a store that
doesn't exist. (Note: the Gorgias subdomain really is `buttonsbebe` with no hyphen — that's
correct — but the Shopify store is `buttons-bebe`. Easy to conflate.)

**H2. Shopify auth method mismatch between the two subsystems.**
- The product sync (layer 3) uses the **client-credentials grant** (`SHOPIFY_CLIENT_ID` +
  `SHOPIFY_CLIENT_SECRET`, in the main `.env`).
- The webhook app (layer 2) expects a static **`SHOPIFY_ADMIN_API_TOKEN`**, which is
  currently **EMPTY** in `webhook/.env`.
So the webhook's Shopify path can't authenticate. Either it's vestigial (the old Phase-1
design read order data through Gorgias, not Shopify directly) or it needs the client-cred
values too. **Needs a decision: does the processor call Shopify directly?**

**H3. Redo credentials are missing from the webhook/processor.**
`REDO_API_KEY` / `REDO_STORE_ID` exist only in the **main** `.env`, not in `webhook/.env`
(0 matches). The MCP Redo tool works (it reads the main `.env`), but the **running processor
has no Redo access**. If the processor is meant to include return status in its drafts, it
can't.

**H4. Processor code bug + failing jobs.**
`buttonsbebe-processor` logs a `RuntimeWarning: coroutine 'Connection.execute' was never
awaited` at `webhook/src/bb_webhook/database.py:114` (`conn.execute("PRAGMA busy_timeout")`
not awaited), and the queue shows **2 failed jobs**. Worth a code look.

---

## MEDIUM — documentation contradicts reality

**M1. `CLAUDE.md` (the project "memory") describes the old, deleted architecture.**
It talks about `gorgias-webhook`, DRAFTER/shadow mode, Supermemory, `integrations/`,
`hermes-tools-mcp`, WhatsApp QR, the dashboard, and Mimo — none of which are on the current
box. Anyone (or any AI) reading it to "resume" will be badly misled.

**M2. `PROJECT-SOURCE-OF-TRUTH.md` is out of date.**
It still describes shadow mode, the dashboard, WhatsApp, `redo_lookup.py`, and Shopify
"direct enrichment," and says nothing about the LanceDB KB, the product sync, the three MCP
tools, the `bb_webhook` processor, or the Ollama Cloud model. It's meant to be the definitive
doc, so this is the most important one to reconcile.

**M3. The KB folder contradicts itself.**
`kb/README.md` and `kb/CONVENTIONS.md` describe a **Supermemory + `kb_client.py` + ingestion
worker** design, while `kb/SEARCH-ENGINE.md` (in the same folder) describes the **LanceDB**
system that's actually deployed. Two different architectures, same folder.

**M4. Model-status docs are stale.**
`PROJECT-SOURCE-OF-TRUTH.md` says the model is "a decision pending / GLM out of credits."
Reality: Hermes runs on `glm-5.2` via Ollama Cloud and it works (verified end-to-end).

**M5. The relationship between the running processor and the new tools is undocumented/
unverified.** The processor is labelled "Hermes-powered," but its code wasn't found under
`webhook/src` (entry point is elsewhere), so it's unclear whether it uses the new MCP tools
(`search_kb`, Redo, Gorgias) and the updated `SOUL.md`, or runs its own separate logic. These
two "brains" (the processor vs. the interactive Hermes I wired) need to be reconciled — today
it's ambiguous which one is "the agent."

---

## LOW — cleanup / minor

**L1. Split, duplicated `.env` files are fragile.** Gorgias creds live in *both* `.env`
files (now synced); Shopify creds are split (client-cred in main, empty token in webhook);
Redo only in main. This caused a real 45-minute detour earlier (editing one file while the
tool read the other). Consider one source of truth, or clearly document the split.

**L2. `kb/CONVENTIONS.md` category list is incomplete.** It lists categories as
`policies / faq / tickets / learned`, but actual content also uses `intents` and now
`products`. The schema doc lags the content.

**L3. Many stale local files describe the old system** and should be archived or deleted to
avoid confusion: `GOAL.md`, `Buttons-Bebe-AI-Customer-Service-Agent.md`,
`NEXT-SESSION-HANDOFF.md`, `POST-FEEDBACK-PLAN.md`, `VPS-WIPE-AND-REBUILD-PLAN.md`,
`OFFLINE-TEST-FINDINGS.md`, `STEP3-FINDINGS.md`, `redo_lookup.py`, `verify-*.py`,
`test-*.sh`, `offline_harness.py`, `build/`, `docs/hermes-rearchitecture/`,
`whatsapp-connect/`, `gorgias-skill/`, and the large `_VPS-FULL-BACKUP-20260706/` backup.

**L4. `.env.example` is incomplete.** The main example documents Gorgias + Shopify
(client-cred) + Redo, but not the webhook app's variables (`WEBHOOK_SECRET`, `TWILIO_*`,
`OWNER_WHATSAPP`, `SHOPIFY_ADMIN_API_TOKEN`) that the running processor uses.

---

## Recommended order of fixes

1. **Decide the architecture question (M5):** is the production agent the `bb_webhook`
   processor, the interactive Hermes + MCP tools, or both? Everything else depends on this.
2. **Fix the quick config bugs (H1, H3):** correct `SHOPIFY_SHOP` in `webhook/.env`, and add
   `REDO_*` there if the processor should use returns. (Safe, ~2 minutes.)
3. **Resolve the Shopify auth question (H2):** confirm whether the processor calls Shopify
   directly; if yes, give it client-cred values; if no, remove the misleading token var.
4. **Fix the processor DB bug (H4).**
5. **Reconcile the docs:** rewrite `PROJECT-SOURCE-OF-TRUTH.md` to match reality (M2), fix or
   retire `kb/README.md` + `CONVENTIONS.md` (M3, L2), refresh or archive `CLAUDE.md` (M1), and
   archive the stale files (L3).

None of the HIGH items affect the three MCP tools or the KB — those are working and verified.
They're about the older webhook/processor layer and the documentation.
