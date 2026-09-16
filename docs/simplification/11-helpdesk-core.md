# 11 — helpdesk core (`console-src/helpdesk-agent/helpdesk/`)

Module analyst report. Read-only analysis of the isolated client-demo data/AI organ.
All paths relative to repo root unless absolute. Line numbers verified against the
working tree on branch `refactor` (2026-09-16).

## Snapshot

| File | LOC | Job |
|---|---|---|
| `tickets.py` | 970 | First-party ticket store: seeds, intake, dedupe, persistence, transactions |
| `fixtures_demo_tickets.py` | 669 | 30 varied demo seed tickets (assert at :669) |
| `composer.py` | 451 | Draft/summary text organ (Caduceus tone, typed drafts, scenario drafts) |
| `mailbox.py` | 346 | AgentMail pull → `ingest_email`; guarded SDK import + fixture fallback |
| `tissues.py` | 271 | Handler registry (one handler per tool); hosts locked `send_reply` path |
| `dto.py` | 310 | Clerk DTO lock: Admin GraphQL 2026-07 field normalization |
| `mcp_server.py` | 194 | Hand-rolled MCP stdio JSON-RPC server (tools/list, tools/call) |
| `fixtures_live_holes.py` | 194 | Offline mirror of Cute Things live-store GIDs/orders |
| `intake.py` | 183 | email/chat intake: spam gate, join, `add_ticket` |
| `speakers.py` | 165 | Persona projection: inbound From is the customer, never mailbox login |
| `shop.py` | 161 | Rail ports: `CatalogShop` (fixtures) vs `LiveShop` (GraphQL), resolve/fallback |
| `join.py` | 134 | Subject/body → (customerId, orderId); live-first, fixture fallback |
| `auth.py` | 119 | 24h client-credentials token mint, pinned shop, no redirect follow |
| `cli.py` | 111 | CLI door (argparse → same `invoke()`), `serve` passthrough |
| `macros.py` | 105 | 3-macro search/apply (replace/append, never send) |
| `queries.py` | 95 | 5 read-only Admin GraphQL documents |
| `state_store.py` | 79 | SQLite single-snapshot inbox state: validate, connect, read, atomic write |
| `dispatch.py` | 63 | Single dispatch: write-gate, human-only gate, production blocklist, txn |
| `client.py` | 62 | urllib GraphQL POST + `assert_query_only` mutation guard |
| `names.py` | 65 | 17 stable tool names, CLI command map, shop labels, API version |
| `fixtures_sample.py` | 226 | Invented sample catalog (`demo-helpdesk.example`) |
| `fixtures_intake.py` | 117 | Demo mailbox messages + stable AgentMail message ids |
| `errors.py` | 44 | `HelpdeskError` → structured JSON; `REFUSED_WRITES` |
| `env.py` | 45 | Root `.env` reader (never in production); `mutations_enabled()` hard-off |
| `gids.py` | 33 | Full-GID validators (`gid://shopify/{Kind}/\d+`) |
| `send_access.py` | 24 | Fail-closed send lock (`SEND_ACCESS_ENABLED = False`) |
| `http.py` | 21 | HTTP door: `invoke()` with `actor="human"` default |
| `__init__.py` / `__main__.py` | 1 / 3 | Re-export / entry |
| **Total package** | **5,261** | (+ `bin/helpdesk` 8, `README.md` 52; `tests/` 2,680 across 12 files) |

Interface/seam: local MCP server (`helpdesk.*`, 17 tools, `mcp_server.py:30-102`
schemas) + CLI (`cli.py`) + HTTP door (`http.py:11`) — all three funnel into one
`dispatch.invoke()` (`dispatch.py:53-63`). The inbox preview server consumes only
the HTTP door (`console-src/inbox/review_server.py:33`) plus `helpdesk.tickets`
transactions (`review_server.py:34,123-125`) and `state_store.StoreUnavailable`
(`review_server.py:37`). Persistence seam: `HELPDESK_DB_FILE` (SQLite, production —
`review_server.py:24`) or legacy `HELPDESK_STORE_FILE`/`HELPDESK_SEEN_FILE` JSON
(dev/migration — `tickets.py:309-315`, `console-src/inbox/migrate_store.py:22-23`).

Job description (3 sentences): a stdlib-only demo data layer that seeds an inbox
baseline (38 seed tickets — 8 hand rows at `tickets.py:87-295` + 30 at
`fixtures_demo_tickets.py:127-667`; AGENTS.md:277's "35" predates the three typed-ask
seeds — hunch, from the requestType feature's later tests), ingests AgentMail/chat as
first-party tickets with dedupe, drafts Caduceus-tone replies from a look-only Shopify
Admin GraphQL rail with fixture fallback, and refuses every Shopify write and every
non-human send by construction.

## Reinvented wheels

| Hand-rolled | Existing wheel | Evidence file:line | LOC saved | Switch risk |
|---|---|---|---|---|
| `.env` parser in `mailbox.py` — **twice** (`_key_present` + `_load_key_into_environ`, near-identical loops) | each other (in-file merge, no new dep) | `mailbox.py:28-44` vs `mailbox.py:47-65` | ~18 | None — `_live_messages` calls them back-to-back (`mailbox.py:235-239`); loader's env side effect is what happens next anyway |
| `.env` parser in `env.py` (third copy in package; `tools/_common.py:10-19` is a fourth, other organ) | `python-dotenv` — **not installed** (inbox runtime pins only fastapi/uvicorn: `console-src/inbox/requirements.txt`) | `env.py:22-39` | 0 — keep | New dep for ~20 LOC fails the ≥100-line bar; parsers differ (key allow-list, no-prod guard) — rejected |
| Hand-rolled MCP JSON-RPC loop (37 LOC) | `mcp` SDK — used by sibling `tools/gorgias_mcp.py:33` (`FastMCP`), but **not installed** for this organ | `mcp_server.py:158-194` vs `tools/gorgias_mcp.py:33` | ~37 | New dep crosses the organ's stdlib-only isolation; loop is boring, covered by `tests/test_contracts.py:45,64` — rejected |
| urllib GraphQL POST, no retry, 30s timeout | `httpx` — **test-only dep** (`console-src/inbox/requirements-test.txt`), not in the helpdesk runtime | `client.py:26-62` | 0 — keep | Callers already fall back to fixtures on any `HelpdeskError` (`shop.py:132-141`, `join.py:89-106`, `composer.py:162-169`), so retry adds code with no observable demo benefit — rejected |
| Legacy JSON store writes via `write_text` (no tmp+rename) | stdlib `os.replace` atomic-write pattern (SQLite path already atomic: `state_store.py:77-79`, single upsert in `BEGIN IMMEDIATE` txn `tickets.py:939-949`) | `tickets.py:341`, `tickets.py:378` | −4 (adds safety) | None — pure stdlib, see Reliability risks #1 |
| `_scenario_draft` if/elif chain of hardcoded ticket-id → literal string | the package's own data-table pattern (`macros.py:16-45` MACROS tuple) | `composer.py:236-317` | ~15 net | Low — exact strings preserved; `tests/test_composer.py:172-235` asserts them verbatim |

W1 verdict: the organ is stdlib + optional `agentmail` by design; no installed wheel
is being reinvented. W3: nothing reimplements a platform feature — the MCP layer is
the platform surface itself.

## Duplication map

Within this package:

- **Canada-shipping copy ×3**: `composer.py:266-271` (t-demo-08), `composer.py:312-316`
  (t-jordan-ship/t-multi-snoozed), `composer.py:378-383` (keyword fallback) — near-identical
  template strings (~20 LOC).
- **Damage copy ×2**: `composer.py:305-310` (photo-known ids) vs `composer.py:371-376`
  (keyword fallback) — same text.
- **`.env` parsing ×3 in one package**: `env.py:22-39`, `mailbox.py:28-44`, `mailbox.py:47-65`.
- **`source` string literal ×3**: `"inbox" if os.environ.get("HELPDESK_PRODUCTION") == "1"
  else "sample"` at `tissues.py:58`, `:65`, `:75`.
- **API-version fallback literal ×3**: `env.get("SHOPIFY_API_VERSION") or "2026-07"` at
  `shop.py:67`, `join.py:55`, `join.py:76` — while the constant exists at `names.py:65`.
- **`_snippet` ×2**: `tickets.py:531-532` vs `intake.py:38-40`.
- **limit validation ×2**: `tickets.py:782-787` vs `mailbox.py:274-282`.
- **Partial-order fixture duplicated across catalogs**: `fixtures_live_holes.py:124-184`
  (`O_PARTIAL`/`C_PARTIAL`, GIDs 9004) restates `fixtures_sample.py:188-216`
  (`ORDER_PARTIAL`/`SKY`, same GIDs — verified `fixtures_live_holes.py:10,15` ≡
  `fixtures_sample.py:22,27`). Same content, different shop catalog; `_customer`/`_order`
  builders exist only in live-holes (`fixtures_live_holes.py:42-77`) while sample is
  hand-expanded.
- **Dead conditional**: `tickets.py:513` — `return out if out.get("ticketId") or
  out.get("messageId") else out` returns `out` on both arms.
- **Stale doc**: `README.md:4` says "fifteen v1 tools"; `names.py:23-41` defines 17 and
  `tests/test_contracts.py:28` asserts seventeen.

Vs other modules (named, not merged):

- **MCP protocol layer**: `mcp_server.py:158-194` hand-rolls JSON-RPC 2.0 tools/list +
  tools/call; sibling `tools/gorgias_mcp.py:33` delegates the identical protocol shape to
  the `mcp` SDK. Shared-shape evidence: both expose tool descriptors with write-refusal
  annotations (`mcp_server.py:143-154` "REFUSED." entries vs `tools/gorgias_mcp.py:1-13`
  "read-only: GET only" docstring). Sibling analyst 05 measures the tools side; this
  organ's 37 wire LOC are the price of the stdlib-only isolation — keep both.
- **HTTP client patterns**: this organ's `client.py`/`auth.py` (urllib, single attempt,
  fixture fallback) vs production `webhook/src/bb_webhook/gorgias_client.py` (httpx,
  `_retry_after` backoff, :46,:218). Different organs, different deps, different failure
  posture (demo falls to fixtures; production must deliver). No merge across the line.
- **env/config**: `env.py` (credential keys, `.env` only in dev) vs `bridge/config.py:9-16`
  (`_truthy`/`_present` flag helpers, os.environ only) vs `tools/_common.py:10-19`
  (VPS-path `.env` loader). Three small loaders with different sources and rules; the
  only true in-package duplication is mailbox's pair (above).
- **SQLite read-only connect pattern**: `state_store.py:50-53` vs inbox
  `projection.py:17-19` (both: `mode=ro` URI + `PRAGMA query_only`). 3 lines each,
  different databases (store vs observed-history projection) — module 10 owns the latter.
- **From-address handling**: `speakers.py:43-51` `split_from` (parse) vs
  `mailbox.py:97-131` `format_from` (build) — complementary halves of one round-trip,
  not duplicates.

## Reliability risks

1. **Non-atomic legacy JSON writes brick the dev boot.** Scenario: `?pull=1` ingests a
   ticket → `_persist_store()` `write_text` (`tickets.py:378`) is interrupted (crash,
   full disk) → `intake_tickets.json` truncated → next boot `_load_persisted_store`
   raises `StoreUnavailable` (`tickets.py:388-389`) and the preview refuses to start
   until an operator deletes the file. Consequence: demo down, fail-closed but manual
   recovery. Minimal fix: write to `path.with_suffix(".tmp")` then `os.replace`
   (stdlib) in `_persist_seen` (`tickets.py:341`) and `_persist_store`
   (`tickets.py:378`); ~6 LOC.
2. **Broad `except Exception` in the live-mail path hides SDK breakage.** Scenario:
   agentmail SDK ships a breaking rename → any error inside `_live_messages`
   (`mailbox.py:263-264`) returns `None` → `load_messages` silently serves fixtures
   (`mailbox.py:267-271`). Consequence: live mail never ingests; the only signal is the
   `"source": "fixture"` label in the pull result (`mailbox.py:339-346`). Fail-to-
   fixtures is the designed posture (AGENTS.md:274) so the fallback is right, but a
   one-line `print(..., file=sys.stderr)` at the except would make drift debuggable.
   Minimal fix: +2 LOC, no behavior change.
3. **Hardcoded read-only tool set in `invoke()` must track new tools by hand.**
   `dispatch.py:60` embeds `{"helpdesk.list_tickets", "helpdesk.get_ticket",
   "helpdesk.write_gate_status", "helpdesk.bridge_status"}` to pick txn mode. A future
   read tool omitted from the set takes a `BEGIN IMMEDIATE` write transaction
   (unnecessary cross-process lock contention, `tickets.py:939`); a write tool
   mis-added fails closed (`tickets.py:932` raises `StoreUnavailable` — safe but
   confusing). Minimal fix: name the set `READ_TOOLS` in `names.py` beside
   `TOOL_NAMES` (`names.py:23-41`) so it is reviewed on every tool addition; 0 LOC.
4. **`seen_message_id` flat-id legacy path can over-dedup.** A bare message id recorded
   by the older writer (`tickets.py:478`) counts as seen for agentmail forever
   (`tickets.py:489-491`); if AgentMail ever reuses ids across mailboxes, mail is
   silently skipped (`mailbox.py:308-310`). Low likelihood in the demo; the gorgias
   guard at `tickets.py:490` shows the asymmetry is deliberate. No change proposed —
   documenting the boundary.
5. **Token cache is process-global, not thread-local.** `auth.py:18,113-115` — two
   threads can mint concurrently; worst case is a double mint, no correctness risk.
   No change proposed.

## Simplification actions

| # | Action | Files | LOC Δ | Risk | Priority | Longevity gain |
|---|---|---|---|---|---|---|
| 1 | Atomic legacy-JSON persists (tmp + `os.replace`) | `tickets.py:341,378` | +6 | Low | P1 | Crash can no longer wedge the demo store; matches the SQLite path's atomicity (`state_store.py:77-79`) |
| 2 | Merge `_key_present`/`_load_key_into_environ` into one loader; call it once in `_live_messages` | `mailbox.py:28-65,235-239` | −18 | Low | P1 | Removes a true duplicated wheel inside one file; live path unchanged |
| 3 | Deduplicate Canada/damage templates into named constants used by all branches | `composer.py:266-271,305-316,371-383` | −25 | Low (strings asserted in `tests/test_composer.py:172-235`) | P2 | One canonical Caduceus copy per scenario |
| 4 | Data-drive `_scenario_draft` as a `{ticket_id: template}` table (like `macros.py:16-45`); keep exact strings | `composer.py:236-317` | −15 | Low | P2 | Adding a demo scenario becomes a table row, not a new elif |
| 5 | Delete dead conditional `tickets.py:513`; hoist `"inbox" if HELPDESK_PRODUCTION…` into one `_source_label()` helper; reference `names.API_VERSION` instead of the `"2026-07"` literal ×3 | `tickets.py:513`; `tissues.py:58,65,75`; `shop.py:67`, `join.py:55,76` | −8 | None | P2 | Invariants stop being re-typed |
| 6 | stderr note in `_live_messages`' broad except (risk #2) | `mailbox.py:263` | +2 | None | P2 | Silent fixture drift becomes observable |
| 7 | Name the read-only set `READ_TOOLS` in `names.py`; import in `dispatch.py:60` | `names.py`, `dispatch.py:60` | 0 | None | P3 | New-tool review point (risk #3) |
| 8 | Share `_customer`/`_order` builders between the two fixture catalogs (move to a tiny `fixtures_common.py`); keep catalogs separate | `fixtures_live_holes.py:42-77`, `fixtures_sample.py` | −20 | Low | P3 | One schema for both catalogs; isolation labels (`sample` vs `live-holes`) untouched |
| 9 | Fix `README.md:4` "fifteen" → seventeen; note the 38-seed baseline | `README.md:4` | 0 | None | P3 | Docs match `names.py`/tests |
| 10 | Rename `http.py` docstring to say "inbox HTTP door (actor=human)" to kill the client/GQL-name confusion | `http.py:1` | 0 | None | P3 | Naming accuracy; no churn to imports |

Honest deletion-test result: **no file in this package is a shallow pass-through.**
`dto.py`, `gids.py`, `speakers.py`, `join.py`, `dispatch.py`, `client.py` each carry real
invariants (DTO field locks `dto.py:18-21,180-196`; GID regex `gids.py:9-24`; persona
projection `speakers.py:103-165`; live-first join fallback `join.py:89-106`; write/human/
production gates `dispatch.py:39-49`; mutation guard `client.py:15-23`). `names.py` is
constants by intent — it IS the tool contract (17 names referenced across 40+ import
sites). `http.py` (21 LOC) is the only near-pass-through and is the deliberate human-
actor seam (`http.py:16` `actor: str = "human"` vs dispatch's `"agent"` default) that
`review_server.py:33` and the MCP==CLI==HTTP parity test (`tests/test_contracts.py:177`)
depend on. Collapsed shape available: none; total honest LOC available ≈ **−78** on
5,261 (≈1.5%). This package is dense, not bloated.

## Rejected on principle

- Add `python-dotenv`/`httpx`/`mcp` SDK to this organ — new deps for <100 LOC each,
  breaks the stdlib-only isolation the preview depends on.
- Prune the "unreachable" send path (`tissues.py:104-230` below `refuse_send`,
  `send_access.py:14-24`) — deliberate fail-closed future wiring per the safety model.
- Merge `composer`/`tickets.infer_*` with `processor/classifier.py` — separate organs;
  the keyword rules overlap in spirit only, and no import crosses the line (verified:
  `composer.py:5-7` states the boundary; no processor imports anywhere in the package).
- Merge `fixtures_sample` and `fixtures_live_holes` catalogs wholesale — different GID
  domains (invented 9001-9004 vs real Cute Things ids, `fixtures_live_holes.py:7-14`)
  and different isolation labels; only the builders may be shared (action 8).
- Drop the legacy JSON store backend — still the documented dev/`?pull=1` persistence
  seam (AGENTS.md:282) and the explicit migration input (`console-src/inbox/migrate_store.py`).
- Add GraphQL retry/backoff to `client.py` — callers' fixture fallback is the designed
  failure posture; retry is unobservable complexity in a demo.
- Rename `tissues.py`/`http.py` files — organ/tissue vocabulary is the agreed architecture
  language (AGENTS.md:262,281); renames are churn with zero LOC gain.
- Revive or borrow retired Gorgias HTTP patterns (RETIRED.md:12) for this organ.

## Verification

- **#1 (atomic writes)** — no existing test covers mid-write failure. ONE smallest new
  test in `tests/test_tickets.py`: set `HELPDESK_STORE_FILE` to a temp path, seed one
  intake ticket, patch `json.dumps` to raise, call `tickets._persist_store()`, assert
  the pre-existing file still parses and is unchanged. (~12 LOC; mirrors the
  `test_state_store.py:19` env-patch pattern.)
- **#2 (mailbox loader merge)** — existing: `tests/test_mailbox.py:163-182` (seen-file
  lifecycle) and the fixture-fallback contract `tests/test_mailbox.py` ("Fixtures when
  the live list is down", file docstring). Rerun `PYTHONPATH=. python3 -m unittest
  discover -s tests` — no new test needed; behavior identical by construction.
- **#3/#4 (composer dedupe + table)** — existing: `tests/test_composer.py:172-235`
  asserts every scenario draft verbatim, and `:249-409` covers typed drafts. The
  refactor must keep these green with zero edits — that is the acceptance gate.
- **#5 (dead conditional, source label, API version)** — existing:
  `tests/test_contracts.py:64` (same handler MCP vs CLI vs HTTP),
  `tests/test_mint.py:71-127` (auth/normalize), `tests/test_production_inbox.py:15`
  (production source labeling). No new test.
- **#6 (stderr note)** — existing: `tests/test_mailbox.py` fallback tests still pass;
  the print is observational only.
- **#7 (READ_TOOLS naming)** — existing: `tests/test_contracts.py:28` (seventeen tools)
  and `tests/test_state_store.py` (read-only txn refusal, `tickets.py:932` path).
- **#8 (fixture builders)** — existing: `tests/test_dto_lock.py:104-122` exercises both
  catalogs end-to-end through dispatch; `tests/test_tickets.py` (14 tests) pins the
  seed/GID join. No new test.
- **#9/#10 (docs/naming)** — no test; verified by review against `names.py:23-41`.
