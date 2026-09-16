# 00 — Synthesis: cross-module simplification plan

Date: 2026-09-16, branch `refactor`. Inputs: the 13 per-module reports in this
directory (01–13). This file dedupes findings that repeat across reports,
resolves the conflicts between them, totals the LOC ledger, and ranks the
execution order. Per-action evidence and the one-smallest-test-per-change live
in the module reports; this is the index and the plan, not a re-dump.

Safety model is immutable throughout: Hermes + MCP tools read-only; only
human-triggered console actions write externally; fail-closed retired modules
stay dead; prompt-injection defenses stay tight; toolsets explicit allow-list;
Twilio banned. Every action below is compatible with all five.

## The one-paragraph verdict

The codebase is already mostly the boring, correct version of itself — the
loop, the locks, the idempotent send path, the fail-closed intake, the atomic
deploys all passed their deletion tests with "leave alone" verdicts. The real
work is: **~1,900 LOC of provably dead weight** (dead pre-Hermes modules, dead
DB helpers, three dead console surfaces, ten duplicate systemd units, dated
root one-shots), **a cluster of small reliability bugs on the alert path**
(the WhatsApp bridge can silently drop owner alerts and lie about its state;
a malformed request line kills kb-admin; one malformed lesson stalls nightly
KB learning indefinitely), **a set of doc-truth failures** (AGENTS.md and
`hermes/SOUL.md` both point at facts or files that no longer exist), and
**two gate-coverage holes** (the inbox server's security tests and 566 LOC of
ops scripts run in no automated check). Zero new dependencies were accepted
across all 13 reports — every candidate failed the ≥100-hand-rolled-lines bar.

## A. Cross-module clusters (deduped)

### A1. systemd unit drift — 10 module-dir copies vs `deploy/systemd/` (the single declared home)

Found independently by reports 03, 05, 06, 08, 09, 13. Full inventory:
7 byte-identical copies (`kb/` ×5, `tools/` ×2) and 3 drifted stale
(`processor/` — **missing the 10-line hardening block**, `whatsapp-connect/`
— old placeholders, `kb-admin/` — `__NODE__` placeholder). Nothing references
the module-dir copies; the CD receiver fingerprints only `deploy/systemd/`
(`buttonsbebe-deploy-receive.sh:98-113`).

**Conflict resolved:** report 06 said keep the `kb/` pair because
`deploy/tests/test_warehouse_safety_assets.py:207-228` pins both copies
in sync; report 13 said delete all 10 and edit the test in the same commit.
**Resolution: delete.** Three of ten copies have already drifted — the
two-home pattern is empirically failing, and the processor copy is actively
misleading (an operator installing from `processor/` deploys without
`NoNewPrivileges`/`UMask=0077`). The test edit is mechanical: drop the `kb/`
tuple entries at `test_warehouse_safety_assets.py:207-217` in the same commit.
`tools/ops/listener_inventory.py:12-14` and `whatsapp_dependency_switch.py:23`
use systemctl *names*, not repo paths — unaffected.

**Action:** one commit, −144 LOC, gate + deploy-test rerun. (Sources: 03-3,
05-3, 08-6, 09-7, 13-3/13-4.)

### A2. Dead console surfaces — three different dead UIs, −1,522 LOC

| Path | LOC | Evidence | Source |
|---|---|---|---|
| `dashboard/` (retired console snapshot) | −776 | not deployed (CD ships only `console-src/index.html`+`login.html`); only ref is the CI label matcher | 07-3 |
| in-package `webhook/src/bb_webhook/review_console.html` + `dashboard.html` | −632 | served by nothing, shipped in the wheel | 02-1 |
| `deploy/review_console.html` | −114 | runbook-history copy of a file nothing serves | 13-8 |

Same commit should drop `dashboard|` from `.github/workflows/pr-review.yml:46`
(matcher becomes a no-op) and add the two deploy paths to RETIRED.md rows.
The live console SPA is `console-src/index.html` (AGENTS.md:92) — after this
deletion there is exactly one console, which is the point.

### A3. Alert-path reliability — the owner's phone is the system's only push channel; treat it as one cluster

Five independent findings chain into one story: **alerts can be lost while
every dashboard stays green.**

1. **WhatsApp bridge drops alerts during its own reconnect window** — the
   bridge flaps every ~2.5 min; a fast 409 (definitively-not-sent) is never
   retried (`server.js:187-196,223-239`; `orchestrator.py:583-590` is
   `max_retries=0` by design). Fix: ~12-LOC hold-and-retry in the `/send`
   handler (09-2, P1).
2. **Bridge reports `state="connected"` while disconnected** — the close
   branch never resets state; console shows green, `sendAlert`'s guard passes
   on a dead socket (09-1, P1, 1 LOC).
3. **Zombie reconnect with no cap** — a failing `startSock` loops forever
   with systemd seeing a healthy unit (09-3, P1, ~5 LOC).
4. **The dead-man's switch itself is unwatched** — `monitor.py` and
   `ops_status.py` check the backup and inbox-projection timers but not
   `buttonsbebe-heartbeat` (03-4, P2, +2 LOC), and nothing pins the journal
   marker strings producer to consumers (03-8, ~8-line source-shape test).
5. **Console failure notifications collide** — a second failure on the same
   `message_id` arrives pre-acknowledged because the id embeds only
   `message_id` (02-9, P1, 1-2 LOC + test).

Related, same theme: `learn-nightly.sh`'s `set -e` makes one malformed lesson
poison-pill all nightly KB learning indefinitely (06-4, P1, 1 LOC); and
`log_event` bypasses log-level filtering so `LOG_LEVEL` can't quiet it (01-3,
P1, 3 LOC).

### A4. Doc-truth repairs — documents that point at things that don't exist

| Doc failure | Fix | Source |
|---|---|---|
| `hermes/SOUL.md:24-26` tells the live Hermes brain to maintain **CLAUDE.md, deleted on 2026-09-16** | re-point at AGENTS.md — repo mirror *and* the VPS copy (mirror is a copy; rides a deploy note) | 05-1, P1 |
| Gorgias skill lists 4 tools; the service exposes 5 (`list_recent_tickets` missing) | +1 line | 05-2, P1 |
| `AGENTS.md:277` "35 seed tickets" (actual: 38), `:278,282` old JSON store paths (actual: SQLite under `/var/lib`) | 3-line edit | 10-5, 11 |
| `deploy/LOCAL-MONITOR.md:46-47` calls the heartbeat "disabled/legacy" vs AGENTS.md §"live" | 1-line fix — a hung processor is exactly the failure mode this doc error hides | 13-1, P1 |
| `deploy/GORGIAS-BRIDGE-SETUP.md` is referenced twice (AGENTS.md:282, INTAKE.md:8) but **never existed in git history** | write the activation doc: env ladder, the three code locks env flags can't flip, and the `verify_secret`-before-`accept` wiring snippet (12-1, P1) | 12-1 |
| `kb/update.sh` "vault/", `SEARCH-ENGINE.md` "every 3 days", helpdesk README "fifteen tools" (17) | cosmetic doc edits | 06-7, 11-9 |

### A5. Gate-coverage holes — code the release gate never touches

1. **`test/test_review_server.py` runs in NO automated gate.** It holds the
   inbox preview's Send-lock, static-traversal, and symlink tests — the two
   things that module exists to guarantee. The gate discovers only
   `console-src/inbox/tests` (`verify_release.sh:189`). Fix: one `git mv`
   into `tests/`, zero content change (10-1, P1).
2. **566 LOC of untracked ops scripts** (`tools/ops/backfill_ticket_status.py`,
   `reparse_ticket_tags.py` + their synthetic tests) — invisible to CI and
   review; the gate would run them once tracked (`verify_release.sh:171`).
   **[Done — Wave 1, PR #28 committed them; they now run in the gate.]**
   Commit or delete; committing is likely right (the backfill is live schema
   work) (13-2, P1).
3. **whatsapp-connect's two root test files** (`qs-security.test.js`,
   `startup-log.test.js`) run in CI but not the offline gate — gate glob
   covers only `test/` (09-4, P2, +2 LOC with a `node_modules` guard).
4. **Real-Caddy contract tests skip in CI** — three suites need the `caddy`
   binary; CI installs only ripgrep (`ci.yml:48`). One word in the apt-get
   line (13-10, P3; the receiver's fingerprint discipline is the real
   backstop, so this is cheap polish, not a hole in production safety).

### A6. Small correctness bugs found by direct experiment

| Bug | Fix | Source |
|---|---|---|
| `new URL(req.url, "http://x")` throws uncaught on `GET //` → **kb-admin process dies** (empirically verified) | 3-line try/catch → 400 + one raw-socket test | 08-1, P1 |
| `/reindex` never drains child stdio → pipe-buffer deadlock, `reindex.running` sticks forever | one spawn option (`stdio: ignore`) + one chatty-child test | 08-2, P1 |
| Helpdesk legacy JSON persists use bare `write_text` → truncated `intake_tickets.json` bricks dev boot with `StoreUnavailable` | tmp + `os.replace` (+6 LOC, mirrors the SQLite path's atomicity) | 11-1, P1 |
| SPA swallows 401 as "could not load" → owner keeps acting on stale drafts believing they're live | ~8 LOC reusing the ops-card "Sign in again" pattern | 07-1, P1 |
| `_extract_json_block` hand-rolls a JSON scanner | `json.JSONDecoder().raw_decode` (−22 LOC, fail-closed identical) | 04-1, P1 |

### A7. Enforcement tests to add (convert discipline into invariants)

Each is the one smallest test, specified in its report: hostile-input render
tests for `row()`/`noticeCard()`/notifications (07-2, ~25 LOC — the SPA's XSS
escaping is correct today but entirely un-enforced); `log_event` level guard
(01-3); notification collision (02-9); journal-marker source-shape pin
(03-8); inbox manifest guard (10-4, ~20 LOC); hermes mirror-drift check
(05-4, ~15 LOC extending the existing VPS verifier); heartbeat-timer coverage
assertion (03-4); raw-socket 400 test (08-1); chatty-child reindex test
(08-2); helpdesk atomic-write test (11-1).

## B. Resolved conflicts between reports

1. **Rate-limiter dedupe direction.** 01-5 keeps the class and makes the
   live function delegate; 02-3 kills the class and keeps the function.
   Both cite the same evidence (class is test-only, function is production).
   **Resolution: 02-3** — kill the class, keep the function (it's the
   production signature and the test-patch seam), port the class tests to
   the function. −18 LOC, one file.
2. **`kb/` systemd-unit pair.** Resolved to deletion in A1 above.
3. **Three Gorgias adapters** (02, 05, 12 all touched it independently).
   **All three reached the same verdict: do not extract.** They disagree on
   UA, timeout, auth construction, and error shape, live in three runtimes
   with three dependency domains, and carry three safety roles (dormant
   demo / human-gated production write / read-only production). The
   duplication IS the safety boundary — a bug in one shared client reaches
   all three blast radii. Closed; do not revisit.
4. **`export_shop_rail.py` vs helpdesk Shopify client (~240 LOC, report 10).**
   Rejected by its own analyst: the exporter runs as root outside the
   hardened sandbox; sharing a live import blurs the trust boundary the
   deploy hardening draws. Closed.
5. **Legacy human review gate.** 06-1/06-2 propose retiring
   `review_learned.py` (232 LOC) + `feedback/review.py` (199 LOC) + the
   console `/dashboard/api/review/*` routes (~60 LOC) together: all three
   operate on `ticket-*.md` packets that nothing writes anymore (live
   capture writes `lesson-*.md`; the nightly auto-promotion is the
   deliberate design per AGENTS.md §11). The console review list is
   permanently empty — a misleading dead UI. **Owner decision required**
   (one question: "is the v1 human gate retired?"); recommendation: retire
   all three together, note in AGENTS.md §10 + RETIRED.md.
6. **Prompt-safety literals.** 02-10 (share the `<DRAFT` marker
   neutralization between `rewrite_runner` and `hermes_runner`) and 04-3
   (import the SENSITIVE prefix from `draft_cleaner` instead of 3 literals)
   are one theme: single source of truth for prompt-safety strings. Do both
   in one commit under one test run (two safety layers change together).
   The webhook→processor import direction is already legal
   (`rewrite_runner.py:10`).
7. **Classification cache, 2 s polling, `heartbeat.sh` vs Python, sys.path
   DB seam, `to_thread`.** All were assessed and rejected by report 03 with
   measured or test-pinned evidence (e.g. `test_loop_responsive.py` exists
   to stop the `to_thread` change). Closed.

## C. LOC ledger

Aggregating each report's own numbers (production code; tests and docs
counted separately), with the B.1/B.2 resolutions applied:

| Tier | Content | Net Δ |
|---|---|---|
| **P1 — bug fixes** | A3 alert cluster + A6 correctness bugs + A4 doc repairs | **≈ +40** (small adds: hold-and-retry, URL guard, atomic writes, 401 banner) |
| **P1 — deletions** | `dashboard/` + in-package HTML + `deploy/review_console.html` (−1,522); processor dead modules `kb_client`+`draft_generator` (−169); webhook dead DB helpers + Z-massage (−144); raw_decode (−22); mailbox loader merge (−18) | **≈ −1,875** |
| **P1 — visibility** | git mv server tests (0); ops scripts committed (+566 tracked) or deleted (−566) | ±566 |
| **P2 — care + tests** | unit copies ×10 (−144); root hygiene `env.example`/`server-fixes.sh`/`gw_backup_cleanup.sh` (−148); legacy gate retirement if approved (−491); `resetUiState` (−85); composer dedupe+table (−40); gorgias-client `_request` helper (−25); context-check dedupe (−20); pydantic result model (−35); guards/ fold (−36); rate-limiter collapse (−18); SPA/AGENTS doc fixes (±3); missing-index self-heal (+12); mirror-drift check (+15); manifest guard (+20); XSS render tests (+25); npm ci + gate glob (+5) | **≈ −1,000** |
| **P3 — later/optional** | GraphQL lexer share (−120); v1 token removal (−20); trio deletion (−293); inbox view-id unification (±5); dead CSS/litter (−30); pairing-page retirement (−150, owner UX); caddy-in-CI (+1 word); unit hardening ×8 (+80) | **≈ −500** |

**Headline: ≈ −2,835 LOC net (P1+P2) — the −3,700 figure in earlier drafts
double-counted the ±566 ops-visibility row (now resolved as +566 *tracked*
LOC, a visibility win, not a deletion) — of which ~−2,200 is pure deletion
of verified-dead code, for ~+100 LOC of reliability fixes and ~+130 LOC of
new tests.** No new dependencies. No architecture changes.

## D. Execution order

Verification for every step is the same spine — `bash tools/verify_release.sh`
full run before push (CI auto-deploys `main` on green) — plus each action's
one-smallest-test named in its report.

### Wave 1 — P1 reliability bugs (independent, small, each shippable alone)

| # | Action | Report |
|---|---|---|
| 1.1 | WhatsApp bridge: state regression + hold-and-retry + reconnect cap (one commit; three tests) | 09-1/2/3 |
| 1.2 | kb-admin: URL try/catch → 400 + reindex stdio drain (one commit; raw-socket + chatty-child tests) | 08-1/2 |
| 1.3 | `log_event` `isEnabledFor` guard | 01-3 |
| 1.4 | Notification id collision (`failed:{message_id}:{job_id}`) | 02-9 |
| 1.5 | `learn-nightly.sh` unconditional index after promote | 06-4 |
| 1.6 | Helpdesk legacy-JSON atomic writes | 11-1 |
| 1.7 | SPA 401 → "Sign in again" banner | 07-1 |
| 1.8 | Heartbeat timer into `monitor.py` + `ops_status.py`; marker source-shape test | 03-4/8 |
| 1.9 | `git mv` inbox server tests into the gate + `run-review.sh` local store paths | 10-1/2 |
| 1.10 | Doc truth: LOCAL-MONITOR heartbeat fix, AGENTS.md seed/store facts, SOUL.md pointer (repo side), Gorgias skill line, bridge-setup doc | 13-1, 10-5, 05-1/2, 12-1 |
| 1.11 | Commit or delete the untracked ops scripts (owner: is the backfill complete?) | 13-2 |

Note on 1.10: the `hermes/` mirror edits need a matching VPS-side edit —
flag it in the deploy notes; report 05-4's drift check makes recurrence
mechanical.

### Wave 2 — verified-dead deletions (mechanical, gate-proven)

| # | Action | Report |
|---|---|---|
| 2.1 | `dashboard/` + in-package HTML twins + `deploy/review_console.html` + CI matcher edit + RETIRED.md rows | 07-3/4, 02-1, 13-8 |
| 2.2 | `processor/kb_client.py` + `draft_generator.py` (AST-verified zero importers) | 03-1/2 |
| 2.3 | Webhook dead DB helpers (7 functions, zero callers) + Z-massage (5 sites) | 01-1/2 |
| 2.4 | systemd unit copies ×10 + `test_warehouse_safety_assets.py` tuple edit, same commit | 13-3/4 |
| 2.5 | Root hygiene: `env.example`, `server-fixes.sh`, `gw_backup_cleanup.sh`, empty `deploy/vps-patches/` | 13-5/6/7 |
| 2.6 | `_extract_json_block` → `raw_decode` (with its one new test) | 04-1 |
| 2.7 | mailbox `.env` loader merge | 11-2 |
| 2.8 | Rate-limiter collapse (resolved direction, B.1) | 02-3 |

### Wave 3 — P2 care + tests (each touched with its suite green)

| # | Action | Report |
|---|---|---|
| 3.1 | XSS render tests (row/noticeCard/notifications) | 07-2 |
| 3.2 | Prompt-safety literals: SENSITIVE prefix import + marker-contract share (one commit) | 04-3, 02-10 |
| 3.3 | `guards/` fold into `patterns.py`; delete `_raw_output_preview` | 04-2/4 |
| 3.4 | `GorgiasClient._request()` helper (429-only POST policy preserved) | 02-2 |
| 3.5 | `send_intents` context-check dedupe | 02-5 |
| 3.6 | pydantic result model (error keys byte-identical) | 02-4 |
| 3.7 | `resetUiState()` in inbox organ | 10-3 |
| 3.8 | Composer template dedupe + scenario data table (verbatim-string tests stay green) | 11-3/4 |
| 3.9 | KB missing-index self-heal / friendly error | 06-3 |
| 3.10 | Legacy human-gate retirement — **owner decision first** (B.5) | 06-1/2 |
| 3.11 | Hermes mirror-drift check in VPS verifier | 05-4 |
| 3.12 | Inbox manifest guard; AGENTS.md fact edits if not in 1.10 | 10-4/5 |
| 3.13 | whatsapp: npm ci on deploy + gate glob for root tests; logging convention + error middleware + `NODE_ENV` | 09-4/5/8 |
| 3.14 | kb-admin: `.backups/` relocation + retention, save audit log, dead `loadNotices` | 08-3/4/5 |
| 3.15 | Bridge: `html.escape` parity + `URLError` mapping | 12-2/3 |
| 3.16 | Processor: `@lru_cache` settings, dead LLM config fields, `record_event` removal, env_file drop | 03-5/6, 01-4/6 |
| 3.17 | Promote-lock constant in `kb_lib.py` | 06-5 |

### Wave 4 — P3 / owner decisions (do opportunistically)

GraphQL lexer share (06-6, cross-module with `shopify/` charter caveat —
share only the lexer, keep per-caller policies); parity-gate retirement
(04-5, needs owner sign-off + ADR note); v1 session-token removal after
bcrypt rotation confirmed (02-7); pairing-page retirement (09-12, owner UX);
caddy in CI (13-10); unit hardening ×8 (13-12, rides a fingerprint
re-approval); processor trio full deletion (03-7); the P3 litter (dead CSS,
duplicate test method, dead `_match_keywords`, stale view-id triplication,
`--proxy-headers` decision, Redo charset guard, `requests.Session`, etc. —
see each report's P3 rows).

## E. What all 13 reports deliberately refused to change

For the record, the load-bearing "no" list — these were assessed, not
skipped, and any future revisit should start from the reports' evidence:

- **No new dependencies.** Every candidate (itsdangerous, passlib, tenacity,
  slowapi, express for kb-admin, graphql-core, python-dotenv, PyYAML,
  redis/queues, DOMPurify, frameworks/bundlers) failed the bar or broke a
  pinned/stdlib-only/isolation constraint.
- **No merges across safety boundaries.** Three Gorgias adapters; session vs
  result auth; root exporter vs sandboxed helpdesk; `bridge/config.py` vs
  `helpdesk/env.py`; heartbeat bash vs Python; wxcard-style lock unification.
- **No "fixes" to deliberate fail-closed behavior.** The ambiguous-POST
  no-auto-resend invariant; the uncertain-alert no-retry policy; strict
  stdout decode; unbounded classifier main view; `to_thread` on the loop;
  inbox Send lock.
- **No churn on verdicts already handed down by ADRs** (014/015 package
  splits), the 1,000-line file guard, the sys.path DB seam, or the
  test-enforced tool contracts.
- **Nothing from RETIRED.md paths comes back.** All 13 prompts enforced this;
  nothing proposed revives `gorgias-webhook/`, `kb-editor/`, `qa_v3/`,
  `dashboard/` (as a live surface), or the fail-closed writer trio.

---

**Status:** **Wave 1 (11 P1 reliability rows) executed on branch `refactor`,
PR #28 — one commit per row, gate green before each push; the follow-up
review on that PR fixed a further ~20 findings in the same files.** Waves
2–4 remain analysis-only; no production code beyond Wave 1 has been
modified.
