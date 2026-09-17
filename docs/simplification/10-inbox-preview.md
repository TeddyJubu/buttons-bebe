# 10 — Inbox preview (`console-src/inbox/`)

Module analyst: inbox-preview. Read-only analysis; no code modified. All claims cite `file:line` relative to repo root. Hunches are labeled.

Safety model respected throughout: look-up path is Shopify Admin GraphQL only (get_customer/get_order/get_returns/list_past_orders), Send stays human-only and fail-closed, Gorgias stays an optional detachable sidecar, no refunds/cancels/send-promises in demo drafts.

## Snapshot

Served assets (per `console-src/inbox/static-manifest.json`, 22 entries, 5,884 LOC) plus operator-only exporters.

| File | LOC (wc -l) | Role |
|---|---|---|
| `console-src/inbox/review_server.py` | 197 | FastAPI server, 127.0.0.1:8766, tool route + static allowlist |
| `console-src/inbox/js/inbox.js` | 1281 | The organ: state, subscriptions, pagination, bridge poll |
| `console-src/inbox/styles.css` | 1004 | Theme |
| `console-src/inbox/js/tissues/*.js` (8 files) | 1,988 | list/thread/composer/rail/order/customer/returns/order-history/view |
| `console-src/inbox/js/util.js` | 468 | esc, formatting, rail labels, forbidden-control scan |
| `console-src/inbox/js/fixtures/demo-inbox.js` | 651 | 9 fixture tickets — **unserved, test-only** |
| `console-src/inbox/js/shop/*.js` | ~940 served (client/tools/clerk/production-shop) + 913 unserved |
| `console-src/inbox/js/webmcp.js` | 355 | `document.modelContext` registration (no Send verb) |
| `console-src/inbox/js/contracts.js` | 276 | DTO typedefs, mailbox topics, LOCKED_COPY |
| `console-src/inbox/export_shop_rail.py` | 427 | root-run Shopify → shop-rail.sqlite3 exporter |
| `console-src/inbox/export_projection.py` | 204 | root-run webhook.db → projection.sqlite3 exporter |
| `console-src/inbox/projection.py` | 51 | read-only SQLite query layer (mode=ro) |
| `console-src/inbox/shop_rail.py` | 59 | read-only rail reader (mode=ro) |
| `console-src/inbox/index.html` | 121 | mount + 104-line generated `<style id="support-theme">` |
| `console-src/inbox/migrate_store.py` | 32 | one-shot legacy JSON→SQLite import |
| `console-src/inbox/run-review.sh` | 11 | local launcher |
| Tests | ~4,600 | `tests/` (py), `test/` (14 JS + 1 py) |

Interface/seam: the organ (`js/inbox.js`) sees only a **shop** object (`read()`, `sendReply()`, `observedHistory`) — `js/shop/production-shop.js:20` sets `observedHistory: true` and `production-shop.js:24` throws on `sample`/`fixture` sources, so the served app can never fall back to fixtures. Data crosses the process boundary as three SQLite files: live store `HELPDESK_DB_FILE` (`review_server.py:24`), read-only projection (`projection.py:12`), read-only shop rail (`shop_rail.py`), all published atomically by root-run systemd timers.

Job description: an isolated, Caddy-authenticated customer-support inbox preview for Buttons Bebe. The server accepts a single tool verb per request (`review_server.py:133-170`), rejects every mutation and capability outside an explicit allowlist, and serves only files named in `static-manifest.json` (no-symlink + resolve check, `review_server.py:173-181`). A background projection (60s timer) and Shopify rail exporter (5m timer, `deploy/systemd/buttonsbebe-inbox-shop-rail.timer`) feed read-only snapshots in; the organ renders tickets, threads, drafts, and a look-only customer/order/returns rail. Send is locked at three layers: `js/send-access.js:1` (`SEND_ACCESS_ENABLED = false`), `helpdesk/send_access.py`, and the server short-circuit at `review_server.py:146-148` — a click shows "Activate the send access." and nothing else.

## Reinvented wheels

| # | Hand-rolled | Existing wheel | Evidence | LOC saved | Switch risk |
|---|---|---|---|---|---|
| W1 | 17 near-identical UI-reset blocks (`selectedId=null; body=""; strip=""; summarizeText=""; discarded=false; selectedMacroId=""; macrosOpen=false`) across mailbox subscriptions and select* methods | A single `resetUiState()` helper in the organ | `js/inbox.js:438,782,796,808,820,832,844,1002,1016,1028,1040,1052,1064` (14 sites; sample block `js/inbox.js:779-786`) | ~85 (7 lines × 13 collapsible sites, keeping one-off resets explicit) | Low — pure refactor inside one file; each site currently also calls `refreshList()` so the helper resets state only, not the fetch. Existing organ tests (`test/inbox-organ.test.js`, 1,020 LOC) cover every affected path |
| W2 | `shop_rail.py` re-inlines the exact read-only-connect recipe (`mode=ro`, `query_only`, busy timeout, row factory) that `projection.py:17-21` already exports as `connect()` | `from projection import connect` (or move `connect()` to a shared 10-line `sqlite_ro.py`) | `shop_rail.py:26-34` vs `projection.py:17-21` | ~8 | Low — same pattern, same DB discipline; keep per-file stale thresholds (rail 6h at `shop_rail.py:53`, projection 180s at `projection.py:29`) |
| W3 | `export_shop_rail.py` duplicates the helpdesk package's Shopify plumbing: env loader, token mint, `_RefuseRedirects`, GraphQL transport, query documents, clerk DTO mappers | `console-src/helpdesk-agent/helpdesk/env.py:28-40`, `auth.py:49-53,70-103`, `client.py:27-62`, `queries.py:10-95`, `dto.py:237-310` | `export_shop_rail.py:34-57` (load_shopify_env), `:77-95` (mint_token), `:29-31` (_RefuseRedirects), `:98-118` (graphql), `:121-152` (documents), `:155-230` (clerk mappers) | ~240 if shared — **but see Rejected: do not consolidate** | **High.** The exporter runs as root *outside* the hardened inbox sandbox (`deploy/systemd/buttonsbebe-inbox-shop-rail.service:13-17`), while `helpdesk/client.py` is pinned to the shop and inboxes the sandbox must never import at runtime. Hunch (labeled): the duplication is deliberate isolation — root tooling and sandboxed runtime should not share a live import. Not proposed as an action beyond a possible "extract the four pure functions into a shared `shopify_graphql.py` imported by both" P3, which I do **not** recommend without the owner's sign-off on the trust boundary |
| W4 | Lockfile/manifest drift checks live in a bespoke `tools/check_inbox_locks.py`; test-vs-runtime pin equality is hand-enforced | None better — this is already the boring, dependency-free option; requirements are hash-locked (`requirements.txt`, `requirements-test.txt`) and CI installs from `requirements-test.lock` (`.github/workflows/ci.yml:33-34`) | — | 0 | N/A — recorded to show it was checked, not to change it |

No new dependency proposed. FastAPI + uvicorn are already installed and pinned (`requirements.txt`), and the JS side uses zero runtime dependencies (stdlib `node:test` for tests; organ is hand-rolled ES modules by design — see Rejected).

## Duplication map

**Within the module**

- The 17 reset blocks (W1) — `js/inbox.js:438…1064`.
- `shop_rail.py:26-34` vs `projection.py:17-21` (W2).
- View-id list defined three times: `js/webmcp.js:12-19` (`VIEW_IDS` open/mine/unassigned/all/snoozed/closed), `js/view-model.js:1` (`views` mine/unassigned/all/snoozed/closed — no "open" entry, handled implicitly by `ticketInView`), and the server-side tuple `console-src/helpdesk-agent/helpdesk/tickets.py:36` (`VIEWS`). They agree today; nothing enforces agreement. Evidence the served path sidesteps `view-model.js` entirely: `js/inbox.js:73-74` forces `viewId: "all"` and `availableViews=[{id:"all"}]` whenever `shop.observedHistory` is true (which is always, `production-shop.js:20`).
- `js/util.js` rail label constants (TRACKING/GIFT_CARDS/…, `util.js` MISSING_LABEL block) mirror `helpdesk/dto.py` label strings — cross-language duplication that must exist (Python DTO ↔ JS render); contracts exist but no test asserts label parity (labeled hunch: low-value to enforce; noted only).

**vs other modules (name of other path)**

- `export_shop_rail.py` vs `console-src/helpdesk-agent/helpdesk/` (W3, ~240 LOC) — deliberately not consolidated; see Rejected.
- `export_projection.py` is the *good* pattern already: it is a thin CLI over `projection.py` (`export_projection.py:14` imports `connect, VERSION, DEFAULT_PATH`), answering question (a). `export_shop_rail.py:20` likewise imports from `projection` and `shop_rail`; its extra weight is the Shopify plumbing, not store code.
- Generated theme block: `index.html:11-114` (~104 lines) is generated from `console-src/support-theme.css` by `tools/build_support_theme.py`, enforced by `tools/verify_release.sh:190` (`--check`). Known divergence already owned by module 7: `index.html:16` `--line: rgba(28,25,22,.12)` vs `styles.css:6` `.08` (action 6 in `docs/simplification/07-console-spa.md:190`) — not re-proposed here.
- `index.html` carries `body[data-support-page="console"]` CSS rules for a page it is not — dead weight for this inbox-only page (small; fold into the module-7 theme pass if convenient).

## Reliability risks

1. **Server-level security tests run in no automated gate.** `test/test_review_server.py` (82 LOC: Send lock, 400/413 bodies, static traversal/symlinks, capabilities 403, storage-failure 503) lives in `test/`, but the Python gate discovers only `console-src/inbox/tests` (`tools/verify_release.sh:189`, `-s console-src/inbox/tests -p 'test_*.py'`) and the JS gate globs only `*.test.js` (`verify_release.sh:192`). CI mirrors the same commands (`.github/workflows/ci.yml:33-34`). Only `PRODUCTION.md:85` documents running it, manually. → Consequence: a regression in the Send lock or the static allowlist — the two things this module exists to guarantee — ships silently. → Minimal fix: move the file to `console-src/inbox/tests/test_review_server.py` (one `git mv`, zero content change; it uses unittest and its imports resolve identically) so the existing discovery picks it up. **P1.**

2. **`run-review.sh` ↔ systemd drift breaks local preview.** `deploy/systemd/helpdesk-inbox.service:11-13` sets `HELPDESK_DB_FILE`, `INBOX_PROJECTION_PATH`, `SHOP_RAIL_PATH`; `run-review.sh:7-10` sets mutation/bridge flags but none of those three, so `review_server.py:24` falls into `/var/lib/...` defaults. Verified on this machine: launching via `run-review.sh` reaches `StoreUnavailable Inbox storage is unavailable` on the first tool call. → Consequence: the documented local entry point cannot work off-VPS; operators edit env by hand and the script rots. → Minimal fix: have `run-review.sh` default the three vars to sibling `./local/` paths under the script dir (5 lines), leaving explicit env overrides intact. **P1** (cheap, and it makes risk 1's test runnable locally).

3. **Hand-maintained manifest can drift silently in one direction.** `static-manifest.json` is the security allowlist (`review_server.py:47`, `PRODUCTION.md:26-27`). I verified the current import closure programmatically: every served file's imports resolve to manifest entries, and no manifest entry is missing on disk. But nothing in the gate checks that *new* JS files added to the tree are either added to the manifest or provably test-only. → Consequence: a future tissue gets written, forgets the manifest, and 404s at runtime with no CI signal (or worse: someone "fixes" it by adding the file to the manifest without review). → Minimal fix: one small test in `test/` walking `js/` and asserting every non-manifest `.js` is listed in a `TEST_ONLY` set (the six known files). ~20 LOC, no new tool. **P2.**

4. **Stale-doc drift in AGENTS.md.** `AGENTS.md:277` says "Demo inbox baseline is 35 seed tickets" and `AGENTS.md:278,282` reference the old JSON store paths under `console-src/inbox/data/` — the directory no longer exists (stores are SQLite under `/var/lib`, `review_server.py:24`; seed reality is `SEED_TICKETS = 38` from 30 demo + sample, `tickets.py:295-296`; JS fixtures contribute 9 more, test-only, `js/fixtures/demo-inbox.js:600` et al.). → Consequence: agents/operators follow wrong facts (the analyst brief itself carried the 35/JSON premises). → Minimal fix: 3-line doc edit. **P2.**

5. **Projection staleness is per-request but unmonitored.** `/ready` fails when the projection is >180s stale (`review_server.py:121-130`, `projection.py:29`) and the rail tolerates 6h (`shop_rail.py:53`); both correct-by-design. But if the 60s timer (`deploy/systemd/buttonsbebe-inbox-projection.timer:6-7`) dies, the only signal is `/ready` going red — nothing restarts or alerts on a *repeatedly failing* oneshot (systemd oneshots have no Restart=). Labeled hunch: this is acceptable for a preview; noting so it's a conscious choice. No action proposed.

## Simplification actions

| # | Action | Files | LOC Δ | Risk | Priority | Longevity gain |
|---|---|---|---|---|---|---|
| 1 | `git mv test/test_review_server.py tests/test_review_server.py` so unittest discovery (verify_release.sh:189) and CI run it | `console-src/inbox/test/test_review_server.py` → `console-src/inbox/tests/` | 0 (moved) | Low — unittest already; imports unchanged | **P1** | The Send-lock and static-allowlist guarantees become unskippable |
| 2 | Teach `run-review.sh` to default the three store paths to a local directory | `console-src/inbox/run-review.sh` | +5 | Low — explicit env still wins | **P1** | Local preview works off-VPS; script stops rotting |
| 3 | Collapse the 17 reset blocks into `resetUiState()` | `console-src/inbox/js/inbox.js` | −85 | Low — covered by inbox-organ.test.js | **P2** | One place to add the next composer field |
| 4 | Manifest guard test: non-manifest `.js` must be in an explicit TEST_ONLY set | new `console-src/inbox/test/manifest-guard.test.js` (~20 LOC) | +20 | Low | **P2** | New files can't silently 404 |
| 5 | Fix stale AGENTS.md facts (35→38 seeds; JSON→SQLite paths) | `AGENTS.md:277,278,282` | ±3 | Zero | **P2** | Docs match reality for the next 12 agents |
| 6 | `shop_rail.py` uses `projection.connect` (or shared 10-line helper) | `console-src/inbox/shop_rail.py` | −8 | Low | P3 | One DB-open recipe |
| 7 | Single source for view ids: `view-model.js` exports the list, `webmcp.js:12` and (ideally) a comment at `tickets.py:36` reference it | `js/webmcp.js`, `js/view-model.js`, `helpdesk/tickets.py` (comment only) | ±5 | Low | P3 | Three-way silent drift becomes impossible on the JS side |
| 8 | **Done (Wave 4): deleted both files** — the adapter mirrored the server-side review-context contract that webhook's `send_intents.review_context` enforces with its own tests, was imported by nothing, and targeted `/console/api/...` which the inbox page's own origin (Caddy `/inbox/*` → review_server:8766) does not serve | — | −62 | Zero | P3 | Removes a "what is this?" question for every future reader |

## Rejected on principle

- Wiring real Send in any form — fail-closed lock at `js/send-access.js:1`, `helpdesk/send_access.py`, `review_server.py:146-148` is the module's reason to exist.
- Migrating stores to SQLite / replacing hand-rolled HTTP with FastAPI — **already done** in commit f5745a8 (2026-09-07): `helpdesk/state_store.py:47-62` (WAL, `BEGIN IMMEDIATE` via `tickets.py:922-948`), `review_server.py:29,88`. The brief's premises (b) and (c) were dated; nothing to do.
- Consolidating `export_shop_rail.py` with the helpdesk Shopify client (W3) — root-run exporter vs pinned-shop sandboxed runtime; sharing a live import blurs the trust boundary the deploy hardening draws (`buttonsbebe-inbox-shop-rail.service` vs `helpdesk-inbox.service:39` `SystemCallFilter=~connect`).
- Deleting `migrate_store.py` or the ~70 LOC legacy-JSON paths in `tickets.py:314-467` — still referenced by the documented VPS migration runbook (`PRODUCTION.md:35-55`); retire only after the VPS migration is confirmed complete.
- Deleting the JS fixture stack (1,746 LOC: `js/fixtures/demo-inbox.js` 651, `fixture-shop.js` 361, `helpdesk-shop.js` 353, `live-catalog.js` 199, `review-blocks.js` 123, `tissues/view.js` 59) — unserved (verified absent from the manifest; `production-shop.js:24` hard-fails fixture sources) but the organ's 1,020-LOC test suite runs against `helpdesk-shop.js` (`test/inbox-organ.test.js:1020`). Relocation to a `test/fixtures/` scope is cosmetic; not worth churn now.
- Any new JS framework/bundler — zero-dependency hand-rolled ES modules are a feature here (served bytes are tiny, no supply chain); a framework would not delete ≥100 hand-rolled lines of *equivalent* logic.
- Enabling the Gorgias bridge (`review_server.py:105-108` correctly 503s while `GORGIAS_BRIDGE_ENABLED=0`, `helpdesk-inbox.service:17`). (Action 8 deleted the `dormant/canonical-review.js` adapter, removing the revival hazard outright.)

## Verification

- **Action 1 (move server tests into the gate):** the file *is* the verification — it already contains the Send-lock test (`test/test_review_server.py` asserts the lock short-circuit), static traversal/symlink rejection, 413 oversize, capability 403s, storage-failure 503. After the move, `python -m unittest discover -s console-src/inbox/tests -p 'test_*.py'` (verify_release.sh:189's own command) runs it; no new test needed.
- **Action 2 (run-review.sh local paths):** smallest check — start via the script on a non-VPS machine and hit one tool verb; today it reproducibly returns `StoreUnavailable`, after the fix it must serve `list_tickets` from the local store. No automated test needed beyond the moved `tests/test_review_server.py` (its storage-failure test already exercises the fallback path).
- **Action 3 (resetUiState):** existing coverage — `test/inbox-organ.test.js` drives every subscription and `selectView/selectChannel/selectStatus/selectAssignee/selectTag/selectTicket` method (file is 1,020 LOC over the organ's full API) and `test/production-inbox.test.js` (447 LOC) asserts the observed-history facets these resets guard. Run `node --test console-src/inbox/test/*.test.js` (already in `verify_release.sh:192`); zero new tests required.
- **Action 4 (manifest guard):** the new test self-verifies — it fails the moment someone adds `js/tissues/new-thing.js` without either manifest or TEST_ONLY registration. The pattern is proven in `test/manifest-guard.test.js`, which already asserts manifest membership; extend that file rather than creating a new one if preferred.
- **Action 5 (doc fix):** no test — verified numbers cited here from `tickets.py:295-296` (SEED_TICKETS = 38 = 30 `fixtures_demo_tickets.py` + sample) and `js/fixtures/demo-inbox.js` (9 test-only fixtures); recheck with the same two reads.
- **Actions 6-7:** existing suites — `tests/test_shop_rail.py` (115 LOC) for the connect change; `test/webmcp.test.js:60` ("never Send") and the view-list consumers for action 7. Action 8 landed as deletion (Wave 4): both files removed, decision recorded in the row-8 table entry above.

No code was modified by this analysis.
