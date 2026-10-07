# Recheck task list (2026-10-07)

Source: [RECHECK-2026-10-07.md](RECHECK-2026-10-07.md). Branch `recheck`.

**Server** column: **no** = repository only; **yes** = needs the live VPS (deploy,
receiver install, Caddy, secrets, Hermes config) and the owner's go-ahead. Merging
to `main` auto-deploys, so even "no" items reach production when merged.

Every item ends with the check that proves it is done.

---

## Done — retired code cleanup (this session)

Snapshot first: local tag `archive/retired-code-2026-10-07` (not pushed) holds
everything removed. Offline gate after the change: `bash tools/verify_release.sh`
→ **exit 0**.

- [x] **C-01** Removed `gorgias-webhook/` (132 files), `teddy/` (50), `qa_v3/` (51), `qa-run/` (2), `kb-editor/` (7). Nothing outside them referenced them; no CI, gate or CD entry.
- [x] **C-02** Removed `shopify/` (no production importer). Dropped it from the gate syntax roots and its test loop (`tools/verify_release.sh`); `tools/graphql_guard.py` docstring now names the one live caller.
- [x] **C-03** Removed `processor/hermes_runner.py`, a shim the `hermes_runner/` package always shadowed. `processor/test_feedback_retirement.py` now scans the live package and asserts the shim stays gone.
- [x] **C-04** Removed `cursor.md` (review notes on retired code). It contained a webhook token, which **remains in git history** → S-01.
- [x] **C-05** Repointed docs: `AGENTS.md` §4/§5/§10, `RETIRED.md`, `skills/.../references/architecture.md`, ADR-015 note, `.gitignore`.

Result: 248 files, 37,817 lines removed; 8 files edited.

---

## P0 — Security (do first)

| ID | Fix | Where | Server | Done when |
|---|---|---|---|---|
| S-01 | Rotate the webhook secret exposed in `cursor.md` history. Update the Gorgias HTTP integration URL. Do the root `.env` rotation (Part 4 of the runbook). | `deploy/ENV-CONSOLIDATION-RUNBOOK.md`; Gorgias admin | **yes** | Old token returns 401 at `/webhook/gorgias/*`; new tickets still arrive |
| S-02 | Put `hermes.buttonsbebe.com` behind `forward_auth`, or remove the site block. | `deploy/caddy/sites/support.caddy:8-18` | **yes** (Caddy is a manual approval) | Unauthenticated `curl -I https://hermes.buttonsbebe.com` returns 401/404 |
| S-03 | Add `terminal` and `file` to the live Hermes `disabled_toolsets`. Mirror the same change in `hermes/config.example.yaml`. | `~/.hermes/config.yaml` on the VPS | **yes** | `tools/verify_hermes_toolset.sh` passes; `hermes tools list` shows neither |
| S-04 | Enforce the Inbox send grant server-side. Either make `action_send` an internal function with no public route, or require `X-Inbox-Send-Access` on `/ticket/{id}/send`. Check first that no UI still calls the bare route. | `webhook/src/bb_webhook/routers/console.py:147-169` | no (code) | A new test: POST `/ticket/{id}/send` with a valid session and no grant returns 403 |
| S-05 | Re-hash the console password as PBKDF2 and delete the `crypt`/bcrypt branch, because Python 3.13 removed `crypt`. | `webhook/src/bb_webhook/console_auth.py:14-17,58-65` | yes (new hash in `.env`) / code no | Login works; `grep -n crypt console_auth.py` finds nothing |
| S-06 | Confirm the heartbeat timer is active; it is the only alert path. | `deploy/HEARTBEAT-INSTALL.md` | **yes** | `systemctl is-active buttonsbebe-heartbeat.timer` prints `active` |

## P1 — Make the docs tell the truth (repository only)

| ID | Fix | Where | Done when |
|---|---|---|---|
| D-01 | Replace the "4 Oct changes not pushed or deployed" note. | `AGENTS.md:12-14` | It matches `git rev-list --left-right --count HEAD...origin/main` |
| D-02 | Webhook auth: describe it as a `?secret=` query parameter (HMAC runs in tests only). | `AGENTS.md:113`, §4 | It matches `webhook_handler.py:252-287` |
| D-03 | Correct "no secret ever committed" and link S-01. | `AGENTS.md:200` | — |
| D-04 | Full inventory: 19 services, 9 timers, the public hosts (`hermes.`, `wh.`, `exchange.`, `:8443`), the Redo enrichment worker, and `intake/` in §5. | `AGENTS.md` §5–§6 | Lists match `ls deploy/systemd` and `deploy/caddy/sites/*` |
| D-05 | Name one model source of truth; fix the other two to match. | `AGENTS.md:105`, `.env.example:29`, `hermes/config.example.yaml:1-5` | All three agree |
| D-06 | Drafts are reviewed in the Inbox, not a console "Ticket feed"; say the `/note` route has no UI. | `AGENTS.md` §1, §2.3, §4 | — |
| D-07 | Reclassify the classifier as a live, escalate-only safety net. | `AGENTS.md:275-278` | — |
| D-08 | Mark the demo-era "Learned Workspace Facts" as historical; drop the `docs/tissues/*` and `webmcp.js` references. | `AGENTS.md:353-388` | `git grep docs/tissues` returns nothing outside history |
| D-09 | Add a historical banner to: `deploy/GORGIAS-BRIDGE-SETUP.md`, `helpdesk-design/{LOCK,PRODUCTION-DESIGN,INTAKE}.md`, `docs/TICKET-FEED-INBOX-PARITY.md`, `docs/simplification/10-inbox-preview.md`. | — | Each starts with the banner |
| D-10 | Add the variable *names* the code reads but `.env.example` omits: `CONSOLE_PASSWORD_HASH`, `CONSOLE_SESSION_SECRET`, `PROCESSOR_RESULT_SECRET`, `WA_SEND_SECRET`, `WA_PASSWORD`. | `.env.example` | — |
| D-11 | Gorgias MCP has 6 tools, not 5; fix the README and the live verifier's expected set and count. | `tools/README.md`, `tools/ops/verify_live_mcp.py:20-56` | `tools.test_tool_contracts` passes |
| D-12 | Remove the stale "README describes an older design" and `run-review.sh` notes. | `skills/.../SKILL.md:14`, `RETIRED.md:22` | — |
| D-13 | Move historical root docs to `docs/archive/`: `INCONSISTENCIES.md`, `DEV-ISSUES.md`, `SPRINT-*.md`, `UX-IMPROVEMENTS-TASKLIST.md`, `INBOX-REPLACE-TASKLIST.md`, `DESIGN-CRITIQUE.md`; also `HANDOVER/` and `docs/simplification/`. Business files (proposal, `.pptx`, competitive brief): **owner decision**. | repo root | The root holds only live docs |
| D-14 | Prune local git clutter: 200 `refs/t3`, 25 `refs/cline`, merged branches. Local only. | `.git` | `git for-each-ref refs/t3 refs/cline` is empty |

## P1 — Small code fixes (repository; reach production on merge)

| ID | Fix | Where | Server | Done when |
|---|---|---|---|---|
| F-01 | **Learning loop has no producer.** Either add an "Use as example" checkbox to the Inbox send confirmation (sending `approve_learning:true`), or remove the promise from the console. **Owner decision**; consent is needed anyway (Shopify API terms §2.3.24). | `console-src/inbox2/app.js:158`, `console-src/index.html:708` | no | A send with the box ticked writes `KB/learned/lesson-*.md` (browser test) |
| F-02 | **270 s job timeout is not enforced.** Run Hermes with `await asyncio.to_thread(...)` inside `process_customer_message` so `wait_for` can actually time out; fix the misleading log text. | `processor/orchestrator.py:270-330,463-473,671-677` | no | A test with a fake slow runner raises `TimeoutError` at the job budget |
| F-03 | **Retire `console-src/helpdesk-agent/` (8.8k lines shipped with no service).** The order matters, because the installed receiver refuses a missing component: (1) remove it from `deploy/cd/source_release.py:27,40`, `deploy/tests/test_receiver_recovery.py:120`, `tools/verify_release.sh:104,227-228`, and delete `console-src/inbox/migrate_store.py`; (2) the operator installs the new receiver; (3) a later PR deletes the folder. | as listed | **yes** for step 2 | Deploy succeeds; `/opt/buttonsbebe/inbox/console-src/helpdesk-agent` is gone |
| F-04 | Give Hermes only the per-ticket Gorgias tools: an agent mount without bulk `list_inbox_tickets`. Bound Redo `list_recent_returns(limit)` with `StrictInt` ≤ 50. | `tools/gorgias_mcp.py:115-133`, `tools/redo_mcp.py:112` | yes (MCP restart) | The tool-contract test lists 4 agent tools; `limit=10_000` is rejected |
| F-05 | Declare `pyyaml` in the webhook package (today it arrives only through `uvicorn[standard]`). Replace the import-order-dependent `processor.*` import in `rewrite_runner.py`. | `webhook/pyproject.toml`, `learning.py:140`, `rewrite_runner.py:10-17` | no | `uv pip install` into a clean venv, then the learning tests pass |
| F-06 | Delete dead code: `enqueue_job` and `get_next_pending_job` (`database.py:245-315`), the unused `projection.query` branches (`console-src/inbox/projection.py:33-57`), the unused `operatorEmail` read (`app.js:756`), and `support-theme.css` selectors that target the retired Inbox. | as listed | no | Gate exit 0 |
| F-07 | Drop provider credentials the processor loads but never uses (Gorgias API key, Shopify secret). | `processor/config.py:40-55,142-162` | no | Processor tests pass; `grep` shows no use |
| F-08 | List `buttonsbebe-inbox2-redo` in the console health groups. | `console-src/index.html` (`opsHealthSummary`) | no | Ops test covers all monitored units |
| F-09 | Hash only `*.caddy` and `*.service` in the receiver fingerprint, so a README edit no longer freezes deploys. | `deploy/cd/buttonsbebe-deploy-receive.sh:106` | **yes** (receiver install) | A docs-only change under `deploy/caddy` deploys |

## P2 — Consolidation (medium; this is the foundation for the SaaS core)

Each step is one PR, keeps the gate green, and passes the deletion test.

- [ ] **K-01** A single `bb_core` package (db, `draft_generation`, the Hermes contract, `draft_cleaner`, priority). The webhook and processor become two entrypoints; remove every `sys.path` edit.
- [ ] **K-02** The processor calls `finish_attempt` in-process. Delete the HTTP result POST (`orchestrator.py:145-225`), `PROCESSOR_RESULT_SECRET` and the hard-coded ports.
- [ ] **K-03** One `providers/` package (Shopify mint, GET client, GraphQL guard, env loader, one MCP JSON-RPC client) replacing 4 Shopify, 6+ Gorgias and 3 MCP client copies.
- [ ] **K-04** One `HermesClient.run(prompt, timeout)` shared by the processor and the rewrite path.
- [ ] **K-05** Versioned migrations (`PRAGMA user_version`), with all DDL in one module.
- [ ] **K-06** A loopback read-only draft endpoint on the webhook replaces the 60 s `export_projection` re-export.
- [ ] **K-07** Prompt split: platform contract stays in code; merchant facts (sign-off, locations, store name) move into data rendered *after* the static prefix, which makes prompt caching possible.
- [ ] **K-08** One UI shell: the Inbox as home and admin as routes, with shared tokens and helpers as ES modules.
- [ ] **K-09** Worker concurrency: an async subprocess plus a lease column on `job_queue` and N workers.
- [ ] **K-10** Off-host encrypted backups that include KB edits and `learned/`.

## P3+ — SaaS core

Report §7, phases P3–P6: Postgres with row-level security on `tenant_id`, a tenant registry, KMS-wrapped tokens, users/orgs/roles, server-side ticket state, a per-tenant KB on pgvector plus full-text search, a direct tool-calling loop, a Gorgias OAuth app, consented per-tenant learning, containers and staging. These start after the owner decisions in report §8.

## Suggested order

1. S-04 and F-02 (code, no server) and P1 docs D-01…D-12 → one PR.
2. S-01, S-02, S-03, S-06 together in one owner-approved server session.
3. F-03 (three-step helpdesk-agent retirement) and F-09 (receiver change) → one receiver install.
4. Remaining F items, then P2 in K order.
