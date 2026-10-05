# AGENTS.md — Buttons Bebe AI Support Agent

> Reflects the live system as of **2026-09-25**. This file is the sole root
> source of truth (`CLAUDE.md` was merged into it and removed on 2026-09-16 —
> its KB/locks, learning-loop, and Fable-port background sections now live in
> §11–§12 below). Any doc describing `/root/gorgias-webhook`, "shadow mode",
> Supermemory/ChromaDB, an 8-tool `hermes-tools-mcp`, or the "Mimo" model
> describes a **retired** system (box wiped & rebuilt 2026-07-06).
> `_VPS-FULL-BACKUP-20260706/` holds plaintext secrets — gitignored, never
> commit or restore from it.

This checkout also includes local, unreleased changes from 4 October 2026.
They have not been pushed or deployed. The code contracts below describe this
checkout; verify the installed version before treating them as VPS behavior.

Local source improvements made after that live-system date have not been deployed.
Repository tests do not verify installed production code or configuration.

For support webapp run/edit tasks, use the project skill at
skills/buttonsbebe-support-webapp/SKILL.md for the current file map and
synthetic local preview. This AGENTS.md remains authoritative for safety and
operations.

## Ticket replacement intake — isolated work, 28 September 2026

Before any work on `intake/`, read and update `INTAKE-TASKLIST.md`. This is the
active checklist for the standalone ticket replacement, separate from older
Inbox UI task lists. Record evidence before marking a task complete, and leave
unproven work unchecked with the next concrete step.

The owner authorized implementation and offline testing with exported data only.
On 2026-09-28 the owner additionally authorized us to make the read-only Gorgias
export ourselves. That export runs separately through the existing read-only MCP,
without credentials in the sandbox. It saves private files for explicit offline
import and does not authorize live intake or ongoing synchronization.
No live intake, provider writes, real email, AI API calls, alerts, deployment,
public hosting, service restarts, or push/merge to main are authorized for this
project. Do not copy credentials or load the production environment. Keep
`intake/` outside the production release inventory. The sandbox must have no
configuration switch that activates real delivery. Future activation requires
passing the checklist's proof gates and a new explicit owner instruction.
Exports and local databases can contain customer data: keep them in ignored,
private storage and out of source control, logs, screenshots and shared artifacts.

## Inbox retirement — 25 September 2026

Inbox (`/inbox/`) is the sole ticket destination. The previous Inbox 1 interface,
server, launcher and service definition are deleted; the old unit is masked on
the VPS. `/inbox2/` bookmarks redirect to `/inbox/`, preserving ticket and view
parameters; retired API/assets return 410. Do not restore the embedded inbox or
its message bridge. Shared projection/Shopify Python modules and dependency
locks in `console-src/inbox/` remain required by Inbox. The backend retains its
internal `inbox2` service and data paths: use `helpdesk-inbox2` (:8767) and
`buttonsbebe-inbox2-shop`; the existing projection timer remains active. Earlier
Inbox 1 browser-control, preview and WebMCP notes below are historical.

## 1. What & why

AI support agent for **Buttons Bebe** (Shopify store, ~2k tickets/month in
**Gorgias**). Per incoming ticket: read message → pull order/return/product
context → search KB → draft a reply **into the review console** (not into
Gorgias) where a human sends / notes / edits / discards. Client: **Chaim**.

## 2. Safety model (never violate)

1. Hermes never sends a customer reply and never writes to Gorgias — it only
   returns draft text.
2. Hermes + its three MCP tools are strictly READ-ONLY (Gorgias read, Redo
   read, KB search). No credential loading, no direct API/curl fallbacks.
   Shopify, Redo, and normal Gorgias access are read-only everywhere; the only
   external writes are the human-initiated Gorgias send/note actions in (3).
3. The only external writes are human-triggered console actions:
   `POST /dashboard/api/ticket/{id}/send|note|rewrite` on the webhook app
   (:8000). Publicly reached through the standalone `/console/login` page and
   an HttpOnly signed session cookie; Caddy `forward_auth` gates `/console/api/*`
   and the console's WhatsApp/KB-admin routes. Direct public `/dashboard*`
   access is denied. Send requires a confirm click; rewrite returns text to the
   console and never sends it. Inbox replies use the same human action service via
   `POST /dashboard/api/inbox/ticket/{id}/send`, explicitly authorized by the
   owner on 2026-09-25. The Inbox starts read-only; its Read & write switch
   obtains a page-memory-only, session-bound grant from
   `POST /dashboard/api/inbox/send-access`. Grants expire after 30 minutes;
   switching off revokes the grant, and reload starts read-only. Every reply
   requires review and a final confirm click, current recipient/source checks,
   a durable operation ID, and an audit record. This grants no writes to Hermes,
   the Inbox read API, MCP tools, or Shopify. No automatic send or resend.
4. Actionable requests get a grounded answer, necessary clarification, or
   verified customer action. Pure thanks get no new draft/alert and do not
   resolve the underlying case. Missing facts alone are normal staff review,
   never HIGH solely because retrieval failed or a review header appeared.
   Sensitive requests (refunds, disputes, defects, cancellations, urgent
   changes/follow-ups) retain HIGH/CRITICAL priority and an owner-alert attempt.
   Staff-only answers use authenticated missing_facts/staff_next_step metadata
   and Needs staff input; Use draft is disabled, manual composition stays open.
   Failed generation shows AI draft unavailable, never a generic acknowledgment.
   The singleton processor uses 240 seconds for Hermes and 270 for processing;
   transient failures have only two durable delayed retries (30/120 seconds).
   Authentication, invalid output and safety rejection require staff review.
   See docs/AI-REPLY-RELIABILITY.md for migrations and release checks.
5. Jobs, results, alerts, and learning actions are all logged.
6. The active Inbox displays observed status, priority and assignee without
   edit controls. Rename is unavailable. Opening a ticket saves a browser-only
   read marker under `bb-inbox-read-v1`; it never marks the ticket read in Gorgias.
   Any future first-party overrides remain local. A provider-side status,
   priority, assignment, rename or read-state write requires the owner's explicit
   authorization and an audit trail.
7. Ticket creation is unavailable in the active Inbox. The older local-ticket
   prototype is historical. A future local-only ticket must remain browser-local
   and notify nobody. Real Gorgias creation requires authorization for that exact
   write. A customer reply still requires human review and confirmation under (3).

## 3. Where it runs

- Production: VPS **`srv1766050`** (2.25.137.77), Ubuntu, everything under
  `/root/Buttonsbebe Agent/`. This repo mirrors that tree.
- Brain: **Hermes Agent** CLI (Nous Research), currently **`gpt-6-luna`** via
  the OpenAI Codex provider (`~/.hermes/config.yaml`; verified 2026-09-26).
- **A push to `main` that passes CI auto-deploys to production** — see §8.

## 4. End-to-end flow

```text
Gorgias webhook
  → bb_webhook FastAPI :8000        HMAC verify (WEBHOOK_SECRET), dedupe
  → SQLite job_queue                webhook/data/webhook.db (WAL)
  → buttonsbebe-processor           polls ~every 2s, one Hermes run per job
  → hermes -t buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias -z "…"
       ├─ buttonsbebe_gorgias :8079   ticket / messages / customer (read-only)
       ├─ buttonsbebe_redo    :8078   return & refund status (read-only)
       └─ buttonsbebe_kb      :8077   LanceDB hybrid search: policies · faq ·
                                      intents · products · tickets
  → <DRAFT:{token}>…</DRAFT> extracted, cleaned (draft_cleaner.py), stored in
    ticket_results, shown in the console Ticket feed
  → HUMAN clicks Send reply / Draft as internal note / Request edit (or ignores)
```

- Hermes returns the draft plus a `JSON_RESULT` and always reports
  `gorgias_priority_set=false`, `note_posted=false`. The processor may
  WhatsApp-alert the owner for HIGH/CRITICAL work but never writes Gorgias.
  (`processor/gorgias_writer.py`, the dormant write-back stub, was deleted
  2026-09-17, Wave 4 — history in git. Do not rebuild it without revisiting
  the safety model.)
- Prompt-injection hardening lives in `hermes_runner.py`: run-token
  `<DRAFT:token>` tags prove the draft is Hermes'; customer-supplied
  `<DRAFT>` blocks are neutralised and fail closed. Don't loosen casually.
- Toolsets are an explicit allow-list (`HERMES_TOOLSETS` in
  `processor/config.py`, built in `build_hermes_command()`), never `--yolo`
  (`HERMES_SKIP_APPROVAL=1` is a temporary unblock only). The `terminal` and
  `file` toolsets are out of scope, so there is nothing dangerous left to
  auto-approve. A misspelled toolset name silently drops the tool instead of
  erroring — run `tools/verify_hermes_toolset.sh` on the VPS after changing
  either.

## 5. Components (repo dirs)

| Dir | What |
|---|---|
| `webhook/` | FastAPI receiver + queue DB + console API (`src/bb_webhook/app.py`). uv package. |
| `processor/` | Orchestrator loop; `hermes_runner.py` (prompt, command build, draft extraction); `draft_cleaner.py`; `whatsapp_notifier.py`; `heartbeat.sh`. uv package. |
| `kb/` | KB markdown (`intents/ faq/ policies/ tickets/ products/ shopify/` — `shopify/` is shopify.dev platform background), LanceDB index/sync scripts, MCP server, systemd units/timers, `search.sh`. |
| `tools/` | Read-only Redo + Gorgias MCP modules, `run-gorgias.sh` / `run-redo.sh`, `verify_release.sh`, `verify_hermes_toolset.sh`. |
| `kb-admin/` | KB editor API (Node, :8087) with auth-safety tests. |
| `whatsapp-connect/` | Node + Baileys: QR pairing page, owner alerts, 2-way Hermes bridge (:8085). Lock changes deploy manually (`npm ci` runbook: `deploy/DEPENDENCY-READINESS.md`) — CD refuses dependency mutation. |
| `console-src/index.html` | **THE** console SPA source (includes Notice Board tab); deployed to the web root by CD. |
| `console-src/inbox2/` | Active `/inbox/` UI, credential-free read API and separate Shopify worker. |
| `console-src/inbox/` | Required shared projection/Shopify modules and locked dependencies. No retired Inbox UI. |
| ~~`dashboard/index.html`~~ | Deleted 2026-09-17 (Wave 2) — there is exactly one console surface now. |
| `deploy/` | Only supported Caddy config (`caddy/Caddyfile.redacted`), CD receive script (`cd/`), systemd units, ENV-consolidation + heartbeat runbooks, tests. |
| `testing/` | 48-scenario suite (`scenarios.json`), TEST-PLAN, judging rubric, HOW-TO-RUN. |
| `feedback/` | PII masking library + retired-poller tests. |
| `fable/` + branch `Fable_buttonsbebe` | Track B standalone prototype — quarantined background, **not** planned work. |
| `hermes/` | In-repo copies of `SOUL.md` + `skills/buttonsbebe`; `config.example.yaml` is a template (real `config.yaml` is gitignored). |

## 6. Services & ports (all bind localhost)

| Port | Service | systemd unit |
|---|---|---|
| 8000 | Webhook receiver + console API (uvicorn) | `buttonsbebe-webhook` |
| 8767 | Active Inbox local read API | `helpdesk-inbox2` |
| — | Separate Inbox customer enrichment worker | `buttonsbebe-inbox2-shop` |
| — | AI snapshot export timer | `buttonsbebe-inbox-projection.timer` |
| 8077 | KB MCP — `search_kb` | `buttonsbebe-kb-mcp` |
| 8078 | Redo MCP | `buttonsbebe-redo-mcp` |
| 8079 | Gorgias MCP | `buttonsbebe-gorgias-mcp` |
| 8085 | WhatsApp connect (QR + alerts + bridge) | `buttonsbebe-whatsapp-connect` |
| 8087 | KB admin API | `buttonsbebe-kb-admin` |
| — | Job processor | `buttonsbebe-processor` |
| — | Timers: product sync (1d) / notices GC / nightly learn (03:30) | `buttonsbebe-kb-sync` / `-notices-gc` / `-kb-learn` |

Caddy (`deploy/caddy/Caddyfile.redacted` is the only supported source;
`webhook/Caddyfile` is marked RETIRED): session-protected console at
`https://support.buttonsbebe.com/console/` (`/console/*`, rewritten internally
to `/dashboard/api/*`; `/console/kbapi` → :8087, `/console/waapi` → :8085).
`/console/login` and `/console/api/auth/*` are the only public console
bootstrap paths; all console data and mutation routes require the signed
session cookie. The other public allowlist is `/webhook/gorgias/*`,
`/health`, `/ready`, and `/connect-whatsapp/*`; everything else 404s.

## 7. Credentials

One root `.env` (consolidated 2026-07-08): both `processor/config.py` and
`webhook/src/bb_webhook/config.py` load it. `webhook/.env` is legacy, pending
removal on the VPS — do not add values there; see
`deploy/ENV-CONSOLIDATION-RUNBOOK.md`.

Shopify = client-credentials grant (`SHOPIFY_CLIENT_ID/SECRET`, mint 24h Admin
token); Gorgias = Basic (email + API key); Redo = Bearer. The console uses
`CONSOLE_PASSWORD_HASH` (PBKDF2) and `CONSOLE_SESSION_SECRET` from the root
`.env`; never commit `.env*` or anything from `_VPS-FULL-BACKUP-*/`. Hermes
skills never read env files — the authenticated MCP services are their only
runtime data path. No secret has ever been committed to git (full-history
token-prefix scan, 2026-07-29); what remains is VPS-side: merging the
leftover `webhook/.env` and rotating the credentials sitting in plaintext in
`_VPS-FULL-BACKUP-20260706/` — see `deploy/ENV-CONSOLIDATION-RUNBOOK.md`.

## 8. Verify before pushing — CI auto-deploys `main`

Pushing to `main` runs the `verify` workflow and, on success,
**auto-deploys that commit to production**
(`.github/workflows/deploy-production.yml`). Run the identical offline gate
first:

```bash
bash tools/verify_release.sh   # needs ripgrep + node; set PYTHON/PROCESSOR_PYTHON to prepared venvs
```

Gate facts (each exists because something slipped once):

- Syntax-checks first-party Python under `feedback kb processor testing tools
  webhook deploy` and asserts ≥40 files parsed, so a broken skip-list fails
  loudly instead of passing on nothing.
- Auto-discovers every `processor/test_*.py`; exclude a live-VPS diagnostic
  with the marker line `# offline-gate: skip` (`test_e2e.py` hits a real VPS
  this way).
- Runs unittest suites in `kb/tests`, `deploy/tests`, `tools.test_tool_contracts`,
  webhook notification tests, feedback tests; `node --test` for every
  whatsapp-connect test (incl. the root qs/startup-log files, skipped only
  when `node_modules` is absent) and kb-admin.
- **Fails on any active `twilio` reference** — escalation is the local
  WhatsApp bridge now; do not reintroduce Twilio.
- Runs all console browser tests without skips and all six Inbox browser suites
  with locked Playwright 1.63.0 and bundled Chromium. CI prepares them; the local
  gate installs nothing and rejects missing dependencies or browser overrides.
- Enforces **48** unique core IDs and **10** unique reliability IDs.

Focused runs:

```bash
(cd processor && uv run python -m unittest test_draft_cleaner -v)
(cd processor && uv run python -m unittest discover -p 'test_*.py' -v)
(cd whatsapp-connect && npm test)
```

Python ≥ 3.12, uv-managed (`uv.lock` in `processor/`, `webhook/`); Node 20 for
JS services. Model-quality review is separate: complete all 48 core and 10
reliability cases and grade each result. Run receipts capture source, Hermes and
approved KB identities before and after each run. The combined receipt checks
those recorded bindings and human verdicts against the current source. It does
not re-inspect the installed Hermes or KB content at check time, or prove that
the published KB index matches the approved files. Pending, failed or stale
source evidence cannot pass. See `testing/HOW-TO-RUN.md`. These paid/live-model checks were not run during
this local implementation. Nothing in this work authorizes a production change.

## 9. Operate on the VPS

```bash
hermes mcp list && hermes mcp test buttonsbebe_kb
systemctl status buttonsbebe-processor buttonsbebe-kb-mcp buttonsbebe-redo-mcp \
  buttonsbebe-gorgias-mcp buttonsbebe-kb-admin
journalctl -u buttonsbebe-processor -n 50
cd "/root/Buttonsbebe Agent/KB" && ./search.sh "do you ship to canada"
./sync-products.sh                     # manual product refresh (else daily)
sqlite3 "/root/Buttonsbebe Agent/webhook/data/webhook.db" \
  "select status,count(*) from job_queue group by status"   # table is job_queue, not jobs
```

## 10. Live vs retired code, and which docs to trust

**Live:** the whole pipeline above; learning loop (every console action →
`KB/learned/lesson-*.md` via `webhook/src/bb_webhook/learning.py`; nightly
PII-masked promotion to indexed `KB/tickets/exemplar-learned-*.md` + index
rebuild); Notice Board override layer (immediate effect, no reindex; GC timer
purges expired notices); heartbeat dead-man's switch (`processor/heartbeat.sh`,
`deploy/HEARTBEAT-INSTALL.md`); KB admin (:8087).

**Retired but present — fail-closed; don't "fix" them back to life:**

- `processor/classifier.py` — advisory deterministic rules only; Hermes also
  classifies; the processor can raise priority but never lower it.
- `feedback/collector.py` — retained legacy collector; writes `ticket-*.md`
  packets only under `FEEDBACK_LEGACY_OPT_IN=1` (bounded rollback test).

**Deleted from the tree — history in git only; do not rebuild them:**

- Legacy v1 human gate (`kb/scripts/review_learned.py`, `feedback/review.py`,
  console `/dashboard/api/review/*`) — **deleted 2026-09-17** (Wave 3.10, owner
  decision). It consumed `ticket-*.md` review packets that only the retained
  legacy collector writes (`feedback/collector.py`, under
  `FEEDBACK_LEGACY_OPT_IN=1`); the live learning path writes `lesson-*.md` and
  promotes nightly (§11).
- The retired processor stubs `feedback_collector.py` (superseded poller),
  `gorgias_writer.py` (dormant write-back), and `classifier_shim.py`
  (parity lookup) — **deleted 2026-09-17** (Wave 4, owner decision, report
  03-7/04-5). Safety stays intact: an ImportError on a forbidden path is as
  loud as the guarded RuntimeError; RETIRED.md records the policy.

**Doc trust order:** this file (sole root source of truth) → `HANDOVER/`
(good onboarding, but dated 2026-07-13 *before* the Fable port: its
"webhook/processor source is not in the repo" claims are outdated) →
`PORTFROMFABLETASKLIST.md`, `IMPROVEMENT-PLAN.md`, `TESTING-READINESS.md`
(context; see §12). **Superseded — do not implement from:**
`INCONSISTENCIES.md`, `DEV-ISSUES.md`. Use root `README.md` and `docs/README.md` for current onboarding. Older
`gorgias-webhook/` and `teddy/` layouts are historical.

## 11. Knowledge base & learning loop (deep details)

Live KB root on the VPS: `/root/Buttonsbebe Agent/KB`. Sources: `intents/`,
`faq/`, `policies/`, `tickets/`, `products/`, plus lower-trust Shopify
platform background in `shopify/`. Index: LanceDB hybrid vector + FTS search.

- Product source is the active Shopify catalog, refreshed daily by
  `buttonsbebe-kb-sync.timer`. Product sync stages and validates the catalog,
  holds the sync/index locks through rebuild, restores the previous corpus on
  failure, and promotes a new index only after exact content validation.
- Search readers hold a shared promotion lock, so they see the previous or the
  new complete index, never a partial swap.
- `learned/` stores raw console lessons and is never indexed. Every human
  console action writes a unique `lesson-*.md` packet and updates the learning
  ledger under a lock. At 03:30 UTC, `buttonsbebe-kb-learn.timer` masks known
  names and identifier patterns, promotes distinct `source: learned-auto`
  exemplars to `tickets/`, and rebuilds the KB. PII masking is best-effort;
  generated exemplars remain reviewable and purgeable.
- Active lesson writing and promotion resolve one `LearningPaths` bundle without
  importing the legacy feedback credential configuration. Relative overrides
  anchor to the repository; empty overrides use the default. Promotion rejects a
  root that differs from its active indexed corpus. Masking recognizes supplied
  two-letter names such as Bo and Li as whole Unicode words. It remains best-effort:
  a caller that supplies no customer name cannot obtain known-name coverage.
- The Notice Board is a locked, immediate override layer and requires no
  reindex. Expired notices are removed by `buttonsbebe-kb-notices-gc.timer`.

## 12. Background reading (Fable port, 2026-07-29)

Ported from the `Fable_buttonsbebe` branch on 2026-07-29. **Track A** in these
documents is the live system described above; **Track B** is Fable, a
standalone help-desk prototype that stayed on its branch and is not part of
`main`. Treat Track B material as background, not as planned work.

| Document | What it is |
|---|---|
| `PORTFROMFABLETASKLIST.md` | The port itself — what came across from Fable, what deliberately did not, and the status of each task. Start here. |
| `IMPROVEMENT-PLAN.md` | The reasoning behind the reliability and quality work (draft cleaner, heartbeat, classifier coverage, one `.env`). |
| `DESIGN-CRITIQUE.md` | Code-level review of the console and the old dashboard. |
| `TESTING-READINESS.md` | Defines "ready to ship": a clean 48-scenario run is the gate before any live change. |
| `Buttons-Bebe-Competitive-Brief.html` | Point-in-time market snapshot. Background only. |
| `testing/TEST-PLAN.md` · `testing/HOW-TO-RUN.md` | The 48 scenarios, the A–E rubric, and how to run them against the live model. |
| `deploy/HEARTBEAT-INSTALL.md` | Installing the dead-man's switch on the VPS. |
| `deploy/ENV-CONSOLIDATION-RUNBOOK.md` | Merging the two `.env` files and rotating secrets. VPS work, not yet done. |

Deliberately **not** ported: `SPRINT-2-PLAN.md` and `CONTINUE-HERE.md` are
finished-sprint logs specific to the Fable branch — on `main` they would read
as current work.

## Learned User Preferences

- Prefers verifying visual work in the browser (inbox UI or hosted pages), not CLI-only reports; when hosting, wants a public link plus a screenshot that it actually renders.
- Explicit Shopify catalog/seed requests count as naming a write; still no refunds, cancels, or `customerCreate` unless named.
- Prefers kid-simple architecture explanations using the organ/tissue analogy; use Excalidraw or the click-to-enter 3D sim; keep organs and wires accurate to this demo, not production Hermes (no Gorgias, Redo, or KB as peer organs).
- Prefers Surge (`*.surge.sh`) for quick public static hosting; do not use Cloudflare tunnels for that.
- Prefers inbox chrome in the live preview (browser element select + screenshots) over design canvases; folds Gorgias-style Views filters (Assigned to me, Unassigned, All, Snoozed, Closed, Trash, Spam) into the ticket list toolbar (no separate views column); list and rail both collapse to thin strips with a clear expand control.
- Prefers the conversation pane to keep the reply box visible: bottom-anchored composer, compact expandable attachment thumbs, and a full-width AI draft strip with Use draft / Regenerate / Dismiss under the text.
- Prefers AI drafts that answer the ticket’s actual ask or request type; mismatched draft content undermines trust.
- Wants the detachable Gorgias bridge left off until credentials are added and they explicitly activate it.
- Omit credentials and demo data; keep Shopify read-only. Inbox defaults to read-only; the owner explicitly authorized a page-scoped Read & write toggle and individually confirmed Gorgias customer replies on 2026-09-25.
- Cite production as `support.buttonsbebe.com` (`/console/`, `/inbox/`); never present `helpdesk.teddyonfriday.com` as the deploy or production host.

## Learned Workspace Facts

- Inbox preview: run the synthetic, loopback-only helper in skills/buttonsbebe-support-webapp/scripts/serve_inbox_preview.py and open http://127.0.0.1:8878/inbox/. Production is https://support.buttonsbebe.com/inbox/.
- Final client host is a Hostinger VPS; treat cutover as fresh install + DNS/proxy + webhook URL change, not a lift-and-shift of this box.
- `helpdesk.pull_mailbox` needs Python package `agentmail` plus `AGENTMAIL_API_KEY`; if the package is missing it can fall back to fixtures and never ingest live mail.
- Live tickets use the real intake From display name as `customerName` (e.g. the human’s Gmail), not the Ada/Sam scenario labels.
- Demo ticket messages may include image attachments; the thread shows small expandable thumbs and keeps the composer bottom-anchored (PR 37 / `helpdesk-design/LOCK.md`). Order rail line items show 48×48 product thumbnails from Shopify `lineItems.image.url` (PR 13).
- Demo inbox baseline is 38 seed tickets (8 hand rows in `helpdesk/tickets.py` + 30 in `fixtures_demo_tickets.py`); normal boot does not auto-pull mail — use `?pull=1` (optional `force=1` for fixtures).
- Cross-boot AgentMail dedupe persists seen message ids inside the inbox store: SQLite single-snapshot (`HELPDESK_DB_FILE`, the `seen` key written by `state_store.write` in each transaction) in production, or legacy JSON `HELPDESK_SEEN_FILE` when that fallback is set.
- Inbox WebMCP (`console-src/inbox/js/webmcp.js`): registers `document.modelContext` UI verbs (`select_view`, `select_ticket`, `use_draft`, `regenerate_draft`, `dismiss_draft`, plus summarize/macros); omits Send; isolated preview Send is fail-closed (`SEND_ACCESS_ENABLED=false` in `helpdesk/send_access.py`, click shows “Activate the send access.”); server `helpdesk.*` MCP/CLI stays for data/AI.
- `helpdesk/composer.py` `fixture_draft()` supplies Caduceus scenario language for demo ticket ids; draft-by-type covers privacy/unsubscribe asks; still no refund/cancel/send promises.
- Organ/tissue architecture: Excalidraw at `docs/tissues/organ-tissue.excalidraw`; click-to-enter 3D sim at `docs/tissues/architecture-3d-sim.html` (world in `architecture-world.js`): LEGO-house organs, inside-Inbox list/thread/rail wireframe, info card off by default; mail → helpdesk intake, Shopify look-only; Send is human-only and fail-closed on the isolated preview until send access is activated.
- This demo’s look-up path is Shopify Admin GraphQL only (`get_customer` / `get_order` / `get_returns` / `list_past_orders`); Redo and KB belong to production Hermes. Gorgias is an optional detachable bridge sidecar (`console-src/helpdesk-agent/bridge/`, `deploy/GORGIAS-BRIDGE-SETUP.md`), not a peer organ; defaults `GORGIAS_BRIDGE_ENABLED=0` / `HELPDESK_OUTBOUND_ENABLED=0`; intake tickets persist in the SQLite single-snapshot store (`HELPDESK_DB_FILE`, production default `/var/lib/buttonsbebe-inbox/inbox.sqlite3`), with legacy `HELPDESK_STORE_FILE` JSON as an explicit fallback.
- Surge CLI is installed globally on this VPS (`surge` on PATH); publish a folder that contains `index.html`.
- The historical Inbox1 projection captured allowlisted ticket state from webhook
  `raw_payload`; its Assigned to me operator setting is retired. The active Inbox
  reads observed state through the read-only Gorgias MCP and offers All, Open and
  Closed views. Never invent ticket state or an assignee.
