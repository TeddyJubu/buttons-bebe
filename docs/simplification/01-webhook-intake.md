# 01 — Webhook intake + queue (`webhook/src/bb_webhook/`, intake side)

Analysis date: 2026-09-16, branch `refactor`. READ-ONLY analysis. All claims cite `file:line` in this repo.

## Snapshot

| File | LOC |
|---|---|
| `webhook/src/bb_webhook/database.py` | 880 |
| `webhook/src/bb_webhook/webhook_handler.py` | 516 |
| `webhook/src/bb_webhook/routers/webhook.py` | 210 |
| `webhook/src/bb_webhook/config.py` | 166 |
| `webhook/src/bb_webhook/app.py` | 94 |
| `webhook/src/bb_webhook/db.py` | 112 |
| `webhook/src/bb_webhook/logging_utils.py` | 104 |
| `webhook/src/bb_webhook/routers/health.py` | 69 |
| `webhook/src/bb_webhook/middleware/console_session.py` | 68 |
| `webhook/src/bb_webhook/middleware/rate_limit.py` | 57 |
| `webhook/src/bb_webhook/deps.py` | 43 |
| `webhook/src/bb_webhook/ops_status.py` | 32 |
| `webhook/src/bb_webhook/message_content.py` | 28 |
| Module total (incl. 2 `__init__`s) | ~2,380 |

Tests (in `webhook/`): `test_webhook.py` 130, `test_atomic_intake.py` 125, `test_database.py` 102, `test_retained_message_content.py` 116, `test_router_contract.py` 106, `test_webhook_tenant_mismatch.py` 66, `test_ops_status.py` 48 (693 total).

**Interface/seam:** `POST /webhook/gorgias/{tenant_id}` (HMAC header or `?secret=`, verified constant-time) → body cap 1 MiB → rate limit → `parse_event` normalization → dedupe → single-transaction `ingest_event` into SQLite `webhook_events` + `parsed_messages` + `job_queue` (`webhook/data/webhook.db`, WAL). `database.py` is a **shared seam**: `processor/orchestrator.py:38-53` imports it directly (`sys.path.insert` + `from bb_webhook.database import claim_job, ...`), and `tools/ops/backfill_ticket_status.py:22`, `tools/ops/reparse_ticket_tags.py:20` import normalizers from `webhook_handler`.

**Job:** accept Gorgias deliveries exactly once (idempotent, atomic), normalize template-rendered string payloads into typed rows, queue work for the processor, and expose health/readiness plus a sanitized ops summary. The module is defense-heavy by design (fail-closed parsing, replay windows, PII-redacted logging) — much of its size is deliberate guardrail, not fat.

## Reinvented wheels

| # | Hand-rolled | Existing wheel | Evidence | LOC saved | Switch risk |
|---|---|---|---|---|---|
| W2-1 | `record_event()` re-implements the `webhook_events` insert that `ingest_event()` already does atomically (`INSERT OR IGNORE` vs `ON CONFLICT DO NOTHING` — equivalent) | `ingest_event` is the shared home; `record_event` has **zero production callers** (tests + `app.py:27` facade name only) | `database.py:180-207` vs `database.py:236-244`; callers grep: `test_database.py:80`, `test_atomic_intake.py:81` | ~28 | Low — 2 test sites to rework |
| W2-2 | `enqueue_job()` duplicates `ingest_event`'s conditional job insert + id re-read (`WHERE NOT EXISTS` + `SELECT id`) | same single home in `ingest_event` (`database.py:263-279`); production callers: none (processor tests + `app.py:25` facade only) | `database.py:284-319` vs `database.py:263-279`; `processor/test_periodic_recovery.py:22`, `processor/test_priority_selection.py:35,92,168` | ~36 (or keep as test-seeder, see action 4) | Low-Med — 5 test call-sites in `processor/` |
| W1-1 | `_with_retry()` compatibility shim | superseded by `Database.execute`; **zero callers anywhere in repo** | `database.py:150-166` | 17 | None — dead code |
| W2-3 | Module-level `_check_rate_limit()` re-implements `SlidingWindowRateLimiter.allow()` in the same file (identical deque sweep + count) | the class, 40 lines above it; the *class* is used only by `test_rate_limit.py:17` while the *function* is live (`routers/webhook.py:58-60`) | `middleware/rate_limit.py:44-57` vs `rate_limit.py:32-41` | ~12 | Low — existing tests cover both paths |
| W1-2 | Manual `"Z" → "+00:00"` massage before `datetime.fromisoformat` (5 sites) | stdlib: `fromisoformat` accepts `Z` natively since Python 3.11; this package requires ≥3.12 (`webhook/pyproject.toml:5`) | `webhook_handler.py:101-102`, `webhook_handler.py:496`, `webhook_handler.py:513`, `routers/health.py:25`, `ops_status.py:23` | ~9 | None on 3.12 (guaranteed by `requires-python`) |
| W2-4 | Whole-function-dead query helpers with zero production callers: `get_pending_jobs`, `get_pending_agent_jobs`, `get_ticket_results`, `get_all_settings`, `get_parsed_stats`, `record_parsed_message` | deletion; `ingest_event` writes parsed rows, `get_result_stats`/`get_parsed_messages` serve the dashboard | `database.py:344-359`, `362-377`, `687-699`, `849-857`, `860-880`, `559-600`; callers: only `app.py:25-28` facade names | ~134 | None — no test references them either |
| W3-1 | `app.py` facade sets + `deps.resolve()` — hand-rolled monkeypatch seam resolving router dependencies through `sys.modules["bb_webhook.app"]` at call time | FastAPI's `app.dependency_overrides` is the platform feature for this; 28 resolve call-sites across routers exist only to honor test patches | `app.py:25-59`, `deps.py:21-43`; patch usage: e.g. `test_rate_limit.py:39-40`, `test_atomic_intake.py:100-106` | ~78 possible | High — 15+ patch sites across test files; pure churn unless tests migrate too |
| W2-5 | `.env` parsed twice: explicit `load_dotenv()` **and** pydantic-settings `env_file` both read the same root `.env` | one mechanism suffices: `load_dotenv` (line 23) already populates `os.environ` at import, which `Settings()` then reads | `config.py:23` + `config.py:29-33` | 1 | Low — but see note in risks section |
| W2-6 | `processor/logging_setup.py` re-implements `setup_logging` (same handler/formatter/quiet-noise logic, slightly divergent noise list) | `bb_webhook.logging_utils.setup_logging` is already the shared module the processor imports its formatters from | `processor/logging_setup.py:23-38` vs `logging_utils.py:65-80` | ~25 (cross-module; owned by processor analyst) | Low-Med |

## Duplication map

**Within the module**
- `record_event` ↔ `ingest_event` webhook_events insert (W2-1 above).
- `enqueue_job` ↔ `ingest_event` job insert + id read (W2-2).
- `_check_rate_limit` ↔ `SlidingWindowRateLimiter.allow` (W2-3).
- Z-timestamp parsing appears 3× inside `webhook_handler` (`_normalize_timestamp`, `is_event_too_old`, `is_event_in_future`) and again in `health.py:_age` and `ops_status.py:summary` — five copies of "parse ISO, handle Z". After W1-2 removes the Z-massage, a shared `parse_iso_utc()` is optional, not needed.
- `config.py:19-21` `{"1","true","yes","on"}` truthy-env parse vs `webhook_handler._coerce_bool` (`webhook_handler.py:31-46`, `("true","1","yes")`) — two spellings of the same coercion, different accepted sets. Cosmetic.
- `app.py:47-49` special-cases `_RATE_LIMIT_NAMES` outside the `_MODULE_NAMES` tuple structure — internal inconsistency in the facade itself.
- `is_event_too_old` and `is_event_in_future` (`webhook_handler.py:490-517`) both parse the same string separately; could be one `event_age_seconds() -> float | None`. ~10 LOC.

**Across modules**
- `processor/orchestrator.py:38-53` imports `bb_webhook.database` directly — this seam is live and load-bearing (WIRING FACT confirmed). `processor/orchestrator.py:657,671` also imports `requeue_failed_job` lazily.
- `processor/logging_setup.py` duplicates `logging_utils.setup_logging` (W2-6).
- `processor/config.py:16-33` is a deliberate twin of `webhook/config.py:17-33` (same `load_dotenv` + `_DEMO_MODE_AT_IMPORT` dance + overlapping Gorgias/Shopify fields). Unifying couples two systemd units' configs — see Rejected.
- `tools/ops/backfill_ticket_status.py:22` and `tools/ops/reparse_ticket_tags.py:20` import `_normalize_ticket_status`/`_normalize_ticket_tags` from `webhook_handler` — *good* reuse, confirms `webhook_handler` as the right shared home for normalizers.
- `session_store.py:9-17` owns its own schema (`console_sessions`) alongside `database.py:_SCHEMA`; `routers/health.py:13` `_REQUIRED_TABLES` hardcodes the union of both. Deliberate seam, guarded by `test_router_contract.py:75-102` (lifespan order). No action.
- `logging_utils._redact` (`logging_utils.py:14-30`) vs `feedback/` PII masking — different jobs (log-line redaction vs exemplar masking); not the same wheel.

## Reliability risks

1. **`log_event` bypasses log-level filtering (verified empirically).** It hand-builds a `LogRecord` and calls `logger.handle()` (`logging_utils.py:95-105`); `Logger.handle` skips the `isEnabledFor` gate that normal `logger.info()` passes through. Reproduced: with root level ERROR, `log()` emits nothing but the `log_event` pattern still emits. Consequence: `LOG_LEVEL=WARNING/ERROR` in `.env` cannot quiet the 2+ INFO lines every webhook emits (`routers/webhook.py:101,186`), so log-volume control is silently broken. Fix (2 lines): `if not logger.isEnabledFor(level): return` at the top of `log_event`.
2. **Rate limiting is per-proxy, not per-IP, in production.** uvicorn runs without `--proxy-headers` (`deploy/systemd/buttonsbebe-webhook.service:10`), so `request.client.host` is `127.0.0.1` for every Caddy-proxied request (`routers/webhook.py:57`); the per-IP sliding window (`rate_limit.py:37,53`) buckets all public traffic into one 60/min budget. Consequence: a Gorgias retry burst >60/min gets 429s for *all* tenants/clients simultaneously. At ~2k tickets/month (AGENTS.md §1) this won't trigger in practice — document it, or add `--proxy-headers` (Caddy already sends accurate `X-Forwarded-For {remote_host}`, `deploy/caddy/sites/support.caddy:151-153`). Config change lives in `deploy/`, not this module.
3. **Query-string webhook secret rides in URLs.** Method 2 of `verify_signature` (`webhook_handler.py:282-286`) compares `?secret=` in constant time — correct — but the secret then appears in proxy logs. Caddy already deletes `request>uri` from its access logs (`deploy/caddy/sites/support.caddy:34-41`), and the app silences uvicorn.access (`logging_utils.py:79`), so both layers are covered today. No code change; keep the Caddy redaction as a load-bearing dependency of this design.
4. **Two busy_timeout budgets.** `init_db` sets 5000 ms (`database.py:132`); every `Database` connection sets 3000 ms (`db.py:47`, `db.py:92`). Harmless (retry loop on top), but the inconsistency invites wrong mental math during lock incidents. One-character align, P3.
5. **`skipped` job status is declared but never written.** Schema comment lists it (`database.py:47`); no writer or reader anywhere (repo-wide grep, zero hits). Misleading only. Drop the comment token or leave.
6. **NOT a risk (checked):** intake atomicity is correct — `ingest_event` uses one `BEGIN IMMEDIATE` transaction with `ON CONFLICT DO NOTHING` + rowcount as the single gate (`database.py:235-281`), `claim_job` is a single conditional `UPDATE ... WHERE status='pending'` (`database.py:429-437`), and the router's `is_duplicate` pre-check (`routers/webhook.py:114`) is advisory only. Also checked: HMAC uses `hmac.compare_digest` in all three places (`webhook_handler.py:276,283`, `result_auth.py:12`); body caps are triple-layered (Caddy 256 KB `support.caddy:172`, app 1 MiB `routers/webhook.py:20`); `ConsoleSessionMiddleware` uses Starlette `BaseHTTPMiddleware` properly with fail-closed defaults.

## Simplification actions

| # | Action | Files | LOC Δ | Risk | Priority | Longevity gain |
|---|---|---|---|---|---|---|
| 1 | Delete zero-caller dead helpers: `_with_retry`, `record_parsed_message`, `get_pending_jobs`, `get_pending_agent_jobs`, `get_ticket_results`, `get_all_settings`, `get_parsed_stats`; trim their names from the `app.py` facade sets | `database.py`, `app.py:25-28` | −135 | None (no tests reference them — their zero coverage is the evidence) | **P1** | One obvious home per query; smaller seam for the processor |
| 2 | Remove `"Z"→"+00:00"` massage (5 sites) — `fromisoformat` handles it on ≥3.11 | `webhook_handler.py:101-102,496,513`, `routers/health.py:25`, `ops_status.py:23` | −9 | None | **P1** | Deletes a pre-3.11 workaround from 5 places |
| 3 | Add `isEnabledFor` guard to `log_event` (fixes risk 1) | `logging_utils.py:88-105` | +3 | None | **P1** | stdlib log-level contract works again |
| 4 | Delete `record_event`; rework its 2 test uses (`test_database.py:69-83` — superseded by `test_atomic_intake.py:47-52`; `test_atomic_intake.py:81` — seed partial state with a raw `Database().execute` INSERT instead); trim facade name. Keep `enqueue_job` as processor-test seeder (5 call-sites) — fold its dedupe doc-comment into `ingest_event`'s | `database.py:180-207`, `app.py:27`, 2 tests | −28 (±20 test churn) | Low | **P2** | Single intake path; legacy-partial scenario still testable |
| 5 | Dedupe rate limiter: `_check_rate_limit` delegates to one module-level `SlidingWindowRateLimiter`; keep `_rate_window` export for the test patch seam or update `app.py:47-49` + 2 tests | `middleware/rate_limit.py` | −12 | Low | **P2** | One limiter implementation |
| 6 | Drop `env_file` from `SettingsConfigDict` — `load_dotenv` at import already populates `os.environ`, which `Settings()` reads; one parse instead of two | `config.py:29-33` | −1 | Low — verify with smoke test below | **P2** | One .env-reading mechanism |
| 7 | `deps`/facade → FastAPI `dependency_overrides` migration (W3-1) | `app.py`, `deps.py`, all routers, 15+ test patches | −78 | High churn, behavior-identical | **P3** | Platform-standard test seam |
| 8 | Align `busy_timeout` 3000→5000 in `db.py` | `db.py:47,92` | 0 | None | **P3** | One lock-wait budget |
| 9 | Unify `processor/logging_setup.py` on `logging_utils.setup_logging` (cross-module; hand to processor analyst) | `processor/logging_setup.py` | −25 | Low | **P3** | One logging config |

Answers to the specific questions: **(a)** `db.py` is live, not a leftover — it's the retry/connection layer `database.py:18` builds on; direct users: `routers/health.py:9`, `session_store.py:6`, `send_intents.py:18`, `routers/console.py:222`. The pair is a two-layer design (queries / connections), confusingly named. **(b)** Atomicity is NOT hand-rolled — see risk 6; the only read-check is the advisory `is_duplicate`. **(c)** Constant-time everywhere. **(d)** Standardized already (mirrors `processor/config.py`); the only hand-rolling is the double `.env` parse (action 6). **(e)** `middleware/` = session/origin guard + rate limiter; the session middleware is a proper use of Starlette's platform feature, not a reinvention — per-route `Depends` would *add* LOC across 20+ routes. **(f)** `message_content.py` is 28 tight LOC on stdlib `html.parser`, fully tested (`test_retained_message_content.py:7-13`) — complexity matches its job; no action.

## Rejected on principle

- **New dep for DB retries (tenacity/SQLAlchemy):** `db.py` is 112 LOC, aiosqlite-native, tested under contention (`test_atomic_intake.py:67-78`); fails the ≥100-hand-rolled-lines bar.
- **slowapi for rate limiting:** new dependency to replace 57 working LOC; in-process window is correct for a single-instance service.
- **Per-route `Depends` auth instead of `ConsoleSessionMiddleware`:** more code on 20+ routes and weaker central fail-closed posture (middleware also sets `Cache-Control: no-store` centrally).
- **Alembic-style migration framework:** the additive `PRAGMA table_info` migration in `init_db` (`database.py:137-144`) is 8 lines and runs at every startup — boring and adequate.
- **Merging `processor/config.py` into `webhook/config.py`:** the duplication is a deliberate twin enabling independent systemd-unit deploys; coupling them is worse than 2×25 lines.
- **Removing the `is_duplicate` pre-check from the router:** it changes the response contract for out-of-window redeliveries (200-duplicate vs 410-expired) and costs one cheap SELECT — the current ordering (dedupe before replay rejection) is the safer contract.
- **Rewriting `parse_event` coercions as a pydantic model:** the string-template coercion *is* the job; declarative validators would not shrink it and would churn the fail-closed logging.
- **Redis/shared store for the rate window or locks:** single-process uvicorn + SQLite WAL is the boring 10-year pattern here; no multi-instance requirement exists.
- **Reviving anything from `gorgias-webhook/`, `dashboard/`, etc.:** retired paths stay dead per the safety model.

## Verification

- **Action 1 (dead-helper deletion):** no test touches any of the 7 functions (repo-wide grep, only `app.py` facade names + definitions). Gate: full `uv run python -m unittest discover` in `webhook/` and `processor/`, plus `tools/verify_release.sh` (import syntax + ≥40-file check). No new test needed — there is nothing left to cover.
- **Action 2 (Z-massage removal):** existing tests already exercise `Z` timestamps end-to-end: `test_retained_message_content.py:21-24,33,52,68,86,102` (`parse_event` with `'...Z'`), `test_webhook_tenant_mismatch.py:54` (`is_event_too_old` on `Z`), `test_ops_status.py:22-29` (future-dated/stale `Z` handling incl. `age < -30` branch). No new test.
- **Action 3 (log_event guard):** ONE smallest new test (~10 lines): set root logger to WARNING, call `log_event(logger, "INFO", "x")`, assert no record reaches a test handler; then set INFO and assert it does. None exists today — the bypass was proven only manually.
- **Action 4 (record_event deletion):** concurrency-winner semantics stay covered by `test_atomic_intake.py:47-52` (16-way gather, one winner); rework `test_atomic_intake.py:80-86` to seed the partial row with a raw `INSERT INTO webhook_events` via `Database().execute` and keep the assertion that `ingest_event` returns None without repairing it.
- **Action 5 (rate limiter):** `test_rate_limit.py:14-29` (class) and `:32-73` (route path, patches `_check_rate_limit` and `_MAX_REQUESTS_PER_MINUTE`) both continue to pass unchanged if the function delegates while keeping its signature and `_rate_window` identity.
- **Action 6 (env_file drop):** no unit test can cover it without refactoring module-level `_ENV_PATH`/import-time dance; verify by smoke: `cd webhook && uv run python -c "from bb_webhook.config import get_settings; print(get_settings().webhook_port)"` against the real root `.env` on the VPS (or a temp `.env`), plus the full suite. Hunch-labeled: I verified `load_dotenv` runs at import before any `Settings()` construction (`config.py:23` vs `config.py:163-166`), so behavior is equal — but run the smoke before deploying.
- **Actions 7-9 (P3):** defer; action 9 belongs to the processor module analyst.
