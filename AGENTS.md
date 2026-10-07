# AGENTS.md — Buttons Bebe AI Support Agent

> Reflects the live system as of **2026-09-25**. This file is the sole root
> source of truth (`CLAUDE.md` was merged into it and removed on 2026-09-16 —
> its KB/locks, learning-loop, and Fable-port background sections now live in
> §11–§12 below). Any doc describing `/root/gorgias-webhook`, "shadow mode",
> Supermemory/ChromaDB, an 8-tool `hermes-tools-mcp`, or the "Mimo" model
> describes a **retired** system (box wiped & rebuilt 2026-07-06).
> `_VPS-FULL-BACKUP-20260706/` holds plaintext secrets — gitignored, never
> commit or restore from it.

`main` is the release branch: a push to `main` that passes CI auto-deploys it
(see §8). The "unreleased 4 October changes" note that stood here was stale —
those commits are on `origin/main`. Git refs do not prove what the VPS runs;
check the installed release before treating these contracts as VPS behavior.

Edits in this checkout remain local until released. Source documents and
repository tests do not verify the code or configuration installed in production.

For support webapp run/edit tasks, use the project skill at
skills/buttonsbebe-support-webapp/SKILL.md for the current file map and
synthetic local preview. This AGENTS.md remains authoritative for safety and
operations.

## Inbox retirement — 25 September 2026

Inbox (`/inbox/`) is the sole ticket destination. The previous Inbox 1 interface,
server, launcher and service definition are deleted; the old unit is masked on
the VPS. `/inbox2/` bookmarks redirect to `/inbox/`, preserving ticket and view
parameters; retired API/assets return 410. Do not restore the embedded inbox or
its message bridge. Shared projection/Shopify Python modules and dependency
locks in `console-src/inbox/` remain required by Inbox. The backend retains its
internal `inbox2` service and data paths: use `helpdesk-inbox2` (:8767) and
`buttonsbebe-inbox2-shop`; the existing projection timer remains active. Older
Inbox 1 browser-control and preview notes are historical.

## 1. What & why

AI support agent for **Buttons Bebe** (Shopify store, ~2k tickets/month in
**Gorgias**). Per incoming ticket: read message → pull order/return/product
context → search KB → draft a reply for a human to review in the active Inbox.
The human decides whether to send it. Client: **Chaim**.

## 2. Safety model (never violate)

1. Hermes never sends a customer reply and never writes to Gorgias — it only
   returns draft text.
2. Hermes + its three MCP tools are strictly READ-ONLY (Gorgias read, Redo
   read, KB search). No credential loading, no direct API/curl fallbacks.
   Shopify, Redo, and normal Gorgias access are read-only everywhere; the only
   external writes are the human-initiated Gorgias send/note actions in (3).
3. The only external writes are human-triggered Gorgias reply and note actions
   on the webhook app (:8000). The signed session cookie and Caddy
   `forward_auth` protect the console routes; direct public `/dashboard*` access
   is denied. Both the legacy `/dashboard/api/ticket/{id}/send` route and the
   legacy `/dashboard/api/ticket/{id}/note` route also require the valid,
   session-bound Inbox send grant, checked by the server using the
   `X-Inbox-Send-Access` header, actor, and session ID. The note route has no UI.
   `POST /dashboard/api/ticket/{id}/rewrite` only returns draft text and does
   not write to Gorgias.
   The Inbox reply path is
   `POST /dashboard/api/inbox/ticket/{id}/send`, explicitly authorized by the
   owner on 2026-09-25. The Inbox starts read-only; its Read & write switch
   obtains a page-memory-only, session-bound grant from
   `POST /dashboard/api/inbox/send-access`. Grants expire after 30 minutes;
   switching off revokes the grant, and reload starts read-only. Every reply
   requires review and a final confirm click, current recipient/source checks,
   a durable operation ID, and an audit record. The grant adds no write ability
   to Hermes, the Inbox read API, MCP tools, or Shopify. No automatic send or
   resend.
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
6. The active Inbox displays observed Gorgias status, priority and assignee separately
   from owner-approved browser-only title, status, priority, assignment, snooze and
   read/unread controls (approved 2026-10-05). Rename and bulk organization save
   only local browser records; Reset removes those overrides. Opening a ticket
   saves a browser read marker under `bb-inbox-read-v1`; it never marks the ticket
   read in Gorgias. Edited provider tickets keep bounded, allowlisted last-observed
   summaries for browser view membership. These saved observations are labelled,
   preserve provider totals separately, and never authorize a draft or send.
   Opening a provider ticket calls the provider-facing read API rather than
   trusting a browser observation. The API can use its 15-second detail cache;
   a newer synced summary, a cache miss or expiry requires a provider refresh.
   Mixed older/unknown and newer observation clocks retain the previous browser
   summary until a fully nonregressing observation arrives. A
   provider-side status, priority, assignment, rename or read-state write still
   requires the owner's explicit authorization and an audit trail. The active
   views are All, Open, Closed, Assigned to me, Unassigned, Snoozed, Trash, and
   Spam. Assigned to me stays unavailable until the owner chooses an operator
   email; never invent provider state or an assignee.
7. Owner-approved New ticket creates a private browser-only `local:<UUID>` ticket
   (approved 2026-10-05). It notifies nobody, never creates a Gorgias ticket, never
   looks up provider customer data, and cannot send a customer reply. Real Gorgias
   creation requires authorization for that exact write. Replies on real provider
   tickets still require human review and confirmation under (3).

## 3. Where it runs

- Production: VPS **`srv1766050`** (2.25.137.77), Ubuntu, everything under
  `/root/Buttonsbebe Agent/`. This repo mirrors that tree.
- Brain: **Hermes Agent** CLI (Nous Research). Production's model and provider
  are configured in `~/.hermes/config.yaml` (the runtime source of truth):
  **`gpt-6-luna`** via the OpenAI Codex provider, verified 2026-09-26.
- **A push to `main` that passes CI auto-deploys to production** — see §8.

## 4. End-to-end flow

```text
Gorgias webhook
  → bb_webhook FastAPI :8000        provider `?secret=` checked against WEBHOOK_SECRET; dedupe; HMAC verifier is exercised in tests
  → SQLite job_queue                webhook/data/webhook.db (WAL)
  → buttonsbebe-processor           polls ~every 2s, one Hermes run per job
  → hermes -t buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias -z "…"
       ├─ buttonsbebe_gorgias :8079   ticket / messages / customer (read-only)
       ├─ buttonsbebe_redo    :8078   return & refund status (read-only)
       └─ buttonsbebe_kb      :8077   LanceDB hybrid search: policies · faq ·
                                      intents · products · tickets
  → <DRAFT:{token}>…</DRAFT> extracted, cleaned (draft_cleaner.py), stored in
    ticket_results, and presented for human review in the active `/inbox/`
  → HUMAN reviews and confirms a reply in the Inbox (or leaves it unsent)
```

The legacy `POST /dashboard/api/ticket/{id}/note` route has no UI.

- Hermes returns the draft plus a `JSON_RESULT` and always reports
  `gorgias_priority_set=false`, `note_posted=false`. The processor may
  WhatsApp-alert the owner for HIGH/CRITICAL work but never writes Gorgias.
  (`processor/gorgias_writer.py`, the dormant write-back stub, was deleted
  2026-09-17, Wave 4 — history in git. Do not rebuild it without revisiting
  the safety model.)
- Prompt-injection hardening lives in `processor/hermes_runner/` (`prompt.py`, `extract.py`): run-token
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
| `processor/` | Orchestrator loop; `hermes_runner/` package (prompt, command build, draft extraction); `draft_cleaner.py`; `whatsapp_notifier.py`; `heartbeat.sh`. uv package. |
| `kb/` | KB markdown (`intents/ faq/ policies/ tickets/ products/ shopify/` — `shopify/` is shopify.dev platform background), LanceDB index/sync scripts, MCP server, systemd units/timers, `search.sh`. |
| `tools/` | Read-only Redo + Gorgias MCP modules, `run-gorgias.sh` / `run-redo.sh`, `verify_release.sh`, `verify_hermes_toolset.sh`. |
| `kb-admin/` | KB editor API (Node, :8087) with auth-safety tests. |
| `whatsapp-connect/` | Node + Baileys: QR pairing page, owner alerts, 2-way Hermes bridge (:8085). Lock changes deploy manually (`npm ci` runbook: `deploy/DEPENDENCY-READINESS.md`) — CD refuses dependency mutation. |
| `console-src/index.html` | **THE** console SPA source (includes Notice Board tab); deployed to the web root by CD. |
| `console-src/inbox2/` | Active `/inbox/` UI, credential-free read API and separate Shopify worker. |
| `console-src/inbox/` | Required shared projection/Shopify modules and locked dependencies. No retired Inbox UI. |
| `intake/` | Shared inbound message-content parsing helper and its tests. |
| ~~`dashboard/index.html`~~ | Deleted 2026-09-17 (Wave 2) — there is exactly one console surface now. |
| `deploy/` | Only supported Caddy config (`caddy/Caddyfile.redacted`), CD receive script (`cd/`), systemd units, ENV-consolidation + heartbeat runbooks, tests. |
| `testing/` | 48-scenario suite (`scenarios.json`), TEST-PLAN, judging rubric, HOW-TO-RUN. |
| `feedback/` | PII masking library + retired-poller tests. |
| `fable/` + branch `Fable_buttonsbebe` | Track B standalone prototype — quarantined background, **not** planned work. |
| `hermes/` | In-repo copies of `SOUL.md` + `skills/buttonsbebe`; `config.example.yaml` is a template (real `config.yaml` is gitignored). |

## 6. Services, timers, ports, and public hosts

The checked-in `deploy/systemd/` inventory contains 19 service units and 9
timer units. Network listeners bind to localhost; one-shot jobs and workers do
not expose a listener.

| Port | Role | systemd service |
|---|---|---|
| 8000 | Gorgias webhook receiver and console API | `buttonsbebe-webhook` |
| 8767 | Active Inbox read API | `helpdesk-inbox2` |
| 8077 | KB MCP search tool | `buttonsbebe-kb-mcp` |
| 8078 | Redo MCP read tools | `buttonsbebe-redo-mcp` |
| 8079 | Gorgias MCP read tools | `buttonsbebe-gorgias-mcp` |
| 8085 | WhatsApp pairing, owner alerts, and Hermes bridge | `buttonsbebe-whatsapp-connect` |
| 8087 | KB administration API | `buttonsbebe-kb-admin` |
| — | Queue processor; runs Hermes jobs | `buttonsbebe-processor` |
| — | Read-only Shopify details worker for opened Inbox tickets | `buttonsbebe-inbox2-shop` |
| — | Read-only Redo details worker for opened Inbox tickets | `buttonsbebe-inbox2-redo` |
| — | Export observed Inbox history snapshot | `buttonsbebe-inbox-projection` |
| — | Export read-only Shopify customer/order rail snapshot | `buttonsbebe-inbox-shop-rail` |
| — | Nightly learning promotion and index rebuild | `buttonsbebe-kb-learn` |
| — | Remove expired Notice Board entries | `buttonsbebe-kb-notices-gc` |
| — | Sync Shopify products into the KB and rebuild its index | `buttonsbebe-kb-sync` |
| — | Read-only local operational health checks | `buttonsbebe-monitor` |
| — | Read-only Shopify content-write safety checks | `buttonsbebe-shopify-safety` |
| — | Send processor heartbeat and alert on a stale worker | `buttonsbebe-heartbeat` |
| — | Encrypted SQLite backup job | `buttonsbebe-backup` |

| Timer | Schedule from the checked-in unit |
|---|---|
| `buttonsbebe-backup.timer` | Every six hours at :15 UTC |
| `buttonsbebe-heartbeat.timer` | First check after 3 minutes, then every 5 minutes |
| `buttonsbebe-inbox-projection.timer` | First check after 30 seconds, then every 60 seconds |
| `buttonsbebe-inbox-shop-rail.timer` | First run after 2 minutes, then every 5 minutes |
| `buttonsbebe-kb-learn.timer` | Daily at 03:30 UTC |
| `buttonsbebe-kb-notices-gc.timer` | First run after 5 minutes, then every 15 minutes |
| `buttonsbebe-kb-sync.timer` | Every day |
| `buttonsbebe-monitor.timer` | First check after 45 seconds, then every 60 seconds |
| `buttonsbebe-shopify-safety.timer` | First check after 5 minutes, then every 15 minutes |

The supported Caddy sources are `deploy/caddy/Caddyfile.redacted` and the
read-only site fragments in `deploy/caddy/sites/`. They define these public
hosts and upstreams:

| Public host | Upstream and access boundary |
|---|---|
| `support.buttonsbebe.com` and `srv1766050.hstgr.cloud` | Main support site: authenticated `/console/`, `/inbox/`, console APIs, webhook ingress, and the WhatsApp connection route. |
| `hermes.buttonsbebe.com` | Hermes dashboard at `127.0.0.1:9119`. The Caddy fragment has no session check; public authentication is not verified here. S-02 tracks the required Caddy decision. |
| `wh.buttonsbebe.com` | Warehouse app at `127.0.0.1:4000`; Basic Auth protects the site except the Shopify webhook path, which the app verifies. |
| `exchange.buttonsbebe.com` | Exchange service at `127.0.0.1:4100`. |
| `https://support.buttonsbebe.com:8443` | Receiving workspace at `127.0.0.1:3210`; the proxy checks session and request origin. |

On the main support host, `/console/login` and `/console/api/auth/*` are the
console bootstrap paths. Caddy session checks gate console data routes;
`/console/kbapi` maps to :8087 and `/console/waapi` maps to :8085. Direct
public `/dashboard*` paths return 404. The other public paths are
`/webhook/gorgias/*`, `/health`, `/ready`, and `/connect-whatsapp/*`; other
paths return 404. The Inbox's opened-ticket Shopify and Redo details are
provided by separate local workers, `buttonsbebe-inbox2-shop` and
`buttonsbebe-inbox2-redo`.

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
runtime data path. A webhook token was committed in the now-removed `cursor.md`
and remains in Git history. Rotation is pending under
[S-01 in the recheck task list](docs/RECHECK-TASKLIST.md); do not reproduce or reuse the token. Keep the
remaining environment consolidation and rotation work in
`deploy/ENV-CONSOLIDATION-RUNBOOK.md`.

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

- `processor/classifier.py` is a live, escalate-only safety net. Hermes also
  classifies; deterministic rules can raise priority but never lower it.

**Retired but present — fail-closed; don't "fix" them back to life:**

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
`INCONSISTENCIES.md`, `DEV-ISSUES.md`. Use root `README.md` and `docs/README.md` for current onboarding. The retired
`gorgias-webhook/`, `teddy/`, `qa_v3/`, `qa-run/`, `kb-editor/` and `shopify/` trees were
removed on 2026-10-07; recover them from tag `archive/retired-code-2026-10-07`.

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

## Historical demo and workspace notes

These notes describe an earlier synthetic helpdesk demo and older workspace
snapshots. They are not current production contracts. For current behavior,
use §§2–6 and check the named source files before relying on a detail.

- Inbox preview: run the synthetic, loopback-only helper in skills/buttonsbebe-support-webapp/scripts/serve_inbox_preview.py and open http://127.0.0.1:8878/inbox/. Production is https://support.buttonsbebe.com/inbox/.
- Final client host is a Hostinger VPS; treat cutover as fresh install + DNS/proxy + webhook URL change, not a lift-and-shift of this box.
- `helpdesk.pull_mailbox` needs Python package `agentmail` plus `AGENTMAIL_API_KEY`; if the package is missing it can fall back to fixtures and never ingest live mail.
- Live tickets use the real intake From display name as `customerName` (e.g. the human’s Gmail), not the Ada/Sam scenario labels.
- Demo ticket messages may include image attachments; the thread shows small expandable thumbs and keeps the composer bottom-anchored (PR 37 / `helpdesk-design/LOCK.md`). Order rail line items show 48×48 product thumbnails from Shopify `lineItems.image.url` (PR 13).
- Demo inbox baseline is 38 seed tickets (8 hand rows in `helpdesk/tickets.py` + 30 in `fixtures_demo_tickets.py`); normal boot does not auto-pull mail — use `?pull=1` (optional `force=1` for fixtures).
- Cross-boot AgentMail dedupe persists seen message ids inside the inbox store: SQLite single-snapshot (`HELPDESK_DB_FILE`, the `seen` key written by `state_store.write` in each transaction) in production, or legacy JSON `HELPDESK_SEEN_FILE` when that fallback is set.
- `helpdesk/composer.py` `fixture_draft()` supplies Caduceus scenario language for demo ticket ids; draft-by-type covers privacy/unsubscribe asks; still no refund/cancel/send promises.
- This demo’s look-up path is Shopify Admin GraphQL only (`get_customer` / `get_order` / `get_returns` / `list_past_orders`); Redo and KB belong to production Hermes. Gorgias is an optional detachable bridge sidecar (`console-src/helpdesk-agent/bridge/`, `deploy/GORGIAS-BRIDGE-SETUP.md`), not a peer organ; defaults `GORGIAS_BRIDGE_ENABLED=0` / `HELPDESK_OUTBOUND_ENABLED=0`; intake tickets persist in the SQLite single-snapshot store (`HELPDESK_DB_FILE`, production default `/var/lib/buttonsbebe-inbox/inbox.sqlite3`), with legacy `HELPDESK_STORE_FILE` JSON as an explicit fallback.
- Surge CLI is installed globally on this VPS (`surge` on PATH); publish a folder that contains `index.html`.
