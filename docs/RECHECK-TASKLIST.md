# Recheck task list (2026-10-07)

Source: [RECHECK-2026-10-07.md](RECHECK-2026-10-07.md). Branch `recheck`.

**Server** column: **no** = repository only; **yes** = needs the live VPS (deploy,
receiver install, Caddy, secrets, Hermes config) and the owner's go-ahead. Merging
to `main` auto-deploys, so even "no" items reach production when merged.

Every item ends with the check that proves it is done.

Deploy batch 1 implementation is complete locally: S-04, F-02, and D-01–D-12.
Focused checks passed (56 webhook, 48 processor, and 12 tool-contract tests).
Release verification remains pending: the required full offline gate exited 1
in the managed Mac sandbox (local socket binding was refused with `EPERM`;
the QA suite reported 1 failure, 6 errors, and 1 skip). The batch has not
passed the required exit-0 gate for a main merge. No commit, push, or deployment
was performed. D-13 and D-14 remain outside this batch.

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
| **S-04 — DONE** | Enforce the Inbox send grant server-side. | `webhook/src/bb_webhook/routers/console.py` | no (code) | Send and note routes now require the session-bound grant; the Inbox reuses an internal durable sender, with refusal and existing-flow regression tests. |
| S-05 | Re-hash the console password as PBKDF2 and delete the `crypt`/bcrypt branch, because Python 3.13 removed `crypt`. | `webhook/src/bb_webhook/console_auth.py:14-17,58-65` | yes (new hash in `.env`) / code no | Login works; `grep -n crypt console_auth.py` finds nothing |
| S-06 | Confirm the heartbeat timer is active; it is the only alert path. | `deploy/HEARTBEAT-INSTALL.md` | **yes** | `systemctl is-active buttonsbebe-heartbeat.timer` prints `active` |

## P1 — Make the docs tell the truth (repository only)

| ID | Fix | Where | Done when |
|---|---|---|---|
| **D-01 — DONE** | Replace the stale unreleased-changes note. | `AGENTS.md` | Records the actual local comparison (HEAD one commit ahead of origin/main), automatic main deployment, and unverified installed state. |
| **D-02 — DONE** | Describe webhook authentication accurately. | `AGENTS.md` §4 | Documents the deployed route's `?secret=` query check and the test-only HMAC helper. |
| **D-03 — DONE** | Correct the secret-history claim. | `AGENTS.md` §7 | Records the removed webhook-token file's history exposure and links pending rotation task S-01 without quoting the token. |
| **D-04 — DONE** | Complete the source inventory. | `AGENTS.md` §5–§6 | Lists all 19 service and 9 timer sources, public hosts, Redo worker, and intake from read-only configuration inspection. |
| **D-05 — DONE** | Identify the production model source. | `AGENTS.md`, `.env.example`, `hermes/config.example.yaml` | Names runtime Hermes configuration as authoritative and labels unchanged template model values as examples. |
| **D-06 — DONE** | Describe the current draft-review surface. | `AGENTS.md` §1, §2.3, §4 | Names the Inbox as the review destination and the grant-protected note route as API-only. |
| **D-07 — DONE** | Classify the live safety net accurately. | `AGENTS.md` §10 | Describes the deterministic classifier as live and able only to raise priority. |
| **D-08 — DONE** | Label demo-era facts and remove dead pointers. | `AGENTS.md`, historical demo README, recheck report | Marks old workspace facts as historical and removes obsolete organ/tissue path and WebMCP pointers. |
| **D-09 — DONE** | Label six historical documents. | Bridge setup, three helpdesk-design files, parity and old preview docs | Each begins with a historical banner directing readers to the active Inbox and current safety rules. |
| **D-10 — DONE** | Add missing environment-variable names. | `.env.example` | Adds blank placeholders for the five console, processor-result and WhatsApp variables without runtime values. |
| **D-11 — DONE** | Correct the six-tool Gorgias contract. | `tools/README.md`, `tools/ops/verify_live_mcp.py` | README and verifier include `list_inbox_tickets` and expect six tools; offline contract tests verify the source contract. |
| **D-12 — DONE** | Remove stale onboarding notes. | Project `SKILL.md`, `RETIRED.md` | Removes the outdated README warning and deleted review-launcher references. |
| D-13 | Move historical root docs to `docs/archive/`: `INCONSISTENCIES.md`, `DEV-ISSUES.md`, `SPRINT-*.md`, `UX-IMPROVEMENTS-TASKLIST.md`, `INBOX-REPLACE-TASKLIST.md`, `DESIGN-CRITIQUE.md`; also `HANDOVER/` and `docs/simplification/`. Business files (proposal, `.pptx`, competitive brief): **owner decision**. | repo root | The root holds only live docs |
| D-14 | Prune local git clutter: 200 `refs/t3`, 25 `refs/cline`, merged branches. Local only. | `.git` | `git for-each-ref refs/t3 refs/cline` is empty |

## P1 — Small code fixes (repository; reach production on merge)

| ID | Fix | Where | Server | Done when |
|---|---|---|---|---|
| F-01 | **Learning loop has no producer.** Either add an "Use as example" checkbox to the Inbox send confirmation (sending `approve_learning:true`), or remove the promise from the console. **Owner decision**; consent is needed anyway (Shopify API terms §2.3.24). | `console-src/inbox2/app.js:158`, `console-src/index.html:708` | no | A send with the box ticked writes `KB/learned/lesson-*.md` (browser test) |
| **F-02 — DONE** | Enforce the job timeout without overlapping Hermes runs. | `processor/orchestrator.py`, processor regression tests | no | A single worker runs Hermes off the event loop, retains timed-out work until it ends, drains before releasing the process lock, and distinguishes result-POST timeouts from job deadlines. |
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
