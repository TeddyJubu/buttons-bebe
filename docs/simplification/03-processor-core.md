# 03 — Processor core (orchestration loop + support modules)

Scope: `processor/orchestrator.py`, `config.py`, `logging_setup.py`,
`whatsapp_notifier.py`, `kb_client.py`, `draft_cleaner.py`,
`draft_generator.py`, `demo_safety.py`, `heartbeat.sh`, `run.sh`,
`shared/priority.py`, `shared/review_policy.py`, `buttonsbebe-processor.service`,
the retired trio (`gorgias_writer.py`, `feedback_collector.py`,
`classifier_shim.py`), and the module's tests. `hermes_runner/` and
`classifier/` are covered by a separate analyst; they appear here only as
callers/callees of the core. All analysis is read-only; every claim cites
`file:line`.

## Snapshot

| File | LOC (`wc -l`) |
|---|---|
| `processor/orchestrator.py` | 685 |
| `processor/draft_cleaner.py` | 669 |
| `processor/whatsapp_notifier.py` | 210 |
| `processor/gorgias_writer.py` (retired, fail-closed) | 194 |
| `processor/heartbeat.sh` | 189 |
| `processor/config.py` | 170 |
| `processor/kb_client.py` (dead) | 101 |
| `processor/feedback_collector.py` (retired, opt-in stub) | 99 |
| `processor/shared/priority.py` | 85 |
| `processor/draft_generator.py` (dead stub) | 68 |
| `processor/demo_safety.py` | 50 |
| `processor/run.sh` | 36 |
| `processor/shared/review_policy.py` | 37 |
| `processor/classifier_shim.py` (retired shim) | 13 |
| `processor/buttonsbebe-processor.service` (stale copy) | 21 |
| `processor/logging_setup.py` | 45 |
| Tests in scope (`test_loop_responsive`, `test_periodic_recovery`, `test_result_durability`, `test_priority`, `test_priority_selection`, `test_heartbeat`, `test_whatsapp_notifier`, `test_internal_no_reply`, `test_draft_cleaner`, `test_draft_cleaner_wiring`, `test_e2e`*, `test_feedback_retirement`) | 3,914 |

*`test_e2e.py` is a live-VPS diagnostic, excluded from the offline gate by its
`# offline-gate: skip` marker (`processor/test_e2e.py:2`;
`tools/verify_release.sh:200,204`).

**Interface / seam.** A singleton asyncio loop polls the SQLite `job_queue`
every ~2 s (`orchestrator.py:519-546`, default `config.py:63`) through a
**direct import** of the webhook's DB layer — `sys.path.insert` of
`webhook/src` at `orchestrator.py:38-40`, then
`from bb_webhook.database import claim_job, …` (`orchestrator.py:42-53`).
Results are written over an authenticated HTTP POST to the webhook's
`/dashboard/api/results` (`orchestrator.py:143-213`), then queue completion
re-verifies the durable row directly in the same DB
(`complete_job … require_result`, `orchestrator.py:640-644`;
`webhook/src/bb_webhook/database.py:440-463`). Owner alerts go to the
whatsapp-connect bridge (`whatsapp_notifier.py:72-210`) exactly once per job
via a DB claim (`orchestrator.py:569-594`). The webhook owns the schema; the
processor is its only consumer.

**Job description.** One process, one job at a time: claim the next pending
customer message (sensitive tickets may jump up to 25 older jobs,
`orchestrator.py:70,114-139`), run the deterministic classifier as an
escalate-only safety net, invoke Hermes headlessly, apply the shared final
review policy, clean the draft, persist it fail-closed, alert the owner for
HIGH/CRITICAL, and retry transient failures with backoff — while never
writing Gorgias and never sending anything externally except the owner alert.

## Reinvented wheels

| # | Hand-rolled | Existing wheel | Evidence | LOC saved | Switch risk |
|---|---|---|---|---|---|
| W1 | Settings singleton: `_settings` global + `get_settings()` | `functools.lru_cache` — the sibling `webhook/src/bb_webhook/config.py:163-164` already uses `@lru_cache` for the identical pattern | `processor/config.py:163-170` vs `webhook/src/bb_webhook/config.py:163-164` | ~4 | Near zero: stdlib; all call sites unchanged; tests patch `orchestrator.get_settings` by name (`test_result_auth.py:17`), unaffected |
| W2 | Classification cache: FIFO dict + `_remember_classification` manual eviction | `functools.lru_cache` would fit, **but is a wash** (see below) | `orchestrator.py:71-81` | ~0 (wash) | Not recommended — see "Rejected on principle" |
| W3 | `setup_logging` formatter wiring duplicated from the webhook | `bb_webhook.logging_utils.setup_logging` — but that one reads the *webhook's* settings object; the processor needs its own log_level/format source | `processor/logging_setup.py:24-38` vs `webhook/src/bb_webhook/logging_utils.py:65-76` | ~8 | Low but touches logging at boot; both work today. Parameterizing the webhook's version is churn for ~8 lines — P3 at best |
| W4 | Result-URL allow-list (`scheme/host/port/path/query/fragment/userinfo`) | `demo_safety.demo_url_allowed` checks the same seven things — but only in demo mode; the orchestrator's guard must hold in **production** too | `orchestrator.py:174-180` vs `processor/demo_safety.py:22-50` | ~6 if unified | **Do not unify**: forcing one helper to serve "demo-only" and "always-on" modes would weaken a tested fail-closed guard (`test_result_auth.py:55`). Accepted duplication |

On the specific questions:

- **(a) `lru_cache` vs the hand-rolled cache.** The hand-rolled part is 9
  lines (`orchestrator.py:71-81`). A `@lru_cache(maxsize=512)` wrapper saves
  none of them once you keep the "don't cache parse failures" behavior
  (`orchestrator.py:100-109` — lru_cache does not cache raises, so parity is
  achievable, but the call sites in `_classify_for_selection` and
  `process_customer_message` still need message-id plumbing). **Eviction IS
  needed**: nothing ever removes a completed job's entry, and at ~2k
  tickets/month an unbounded dict grows ~50 entries/day; the 512 FIFO cap is
  a fine, boring bound. Tests also use `_classification_cache.clear()`
  directly (`test_priority_selection.py:25,121,141`). Net: keep as-is.
- **(b) fcntl singleton lock vs systemd.** systemd does not, by itself,
  guarantee single instance against a second manual `python -m orchestrator`
  or `run.sh` start, so defense-in-depth is justified. Crash behavior is
  correct: `flock` is released by the kernel on process death, so **no
  stale-lock handling is needed**; the leftover `.processor.lock` file is
  harmless and gitignored (`.gitignore:43`). One cosmetic nit: `os.open`
  without `O_TRUNC` before writing the pid (`orchestrator.py:233-235`) can
  leave trailing bytes from a previous longer pid — the content is never read
  by anything, so no fix is warranted.
- **(c) Retry/backoff.** Two small hand-rolled backoffs with different jobs:
  loop-crash backoff `min(2**errors, 60)` (`orchestrator.py:560-561`) and
  transport retry table `[2, 5, 10]` (`whatsapp_notifier.py:151`). **No
  jitter — correctly so**: this is a single-instance singleton loop; there is
  no herd to de-synchronize. Clock weirdness is handled properly: loop
  cadence/recovery use `time.monotonic` (`orchestrator.py:495,503,540`),
  while cross-process staleness comparisons use wall-clock in SQL
  (`webhook/src/bb_webhook/database.py:512-513`) — the only correct split,
  since a restarted process must see the old process's timestamps.
- **(j) 2 s SQLite polling.** Right call — say so and keep it. At ~2k
  tickets/month the queue receives a job roughly every 21 minutes; one
  `SELECT … LIMIT 25` per 2 s on a WAL database is negligible, and the same
  pass doubles as the recovery sweep. `LISTEN/NOTIFY` would add a persistent
  listener connection, reconnect logic, and a second failure mode for zero
  felt-latency gain. Do not add it.

## Duplication map

Within the module:

- **`sys.path` insert of `webhook/src`** appears in `orchestrator.py:38-40`
  and `logging_setup.py:11-14` (and six test files). The second copy is
  load-bearing: `whatsapp_notifier` and `hermes_runner` import
  `logging_setup`, and tests import these modules directly without going
  through `orchestrator` first. Accept as the deliberate shared-seam.
- **DEMO_MODE truthiness parsing** exists three times: `processor/config.py:20-22`,
  `processor/demo_safety.py:14,19`, `webhook/src/bb_webhook/config.py:19-21`.
  All three agree on `{"1","true","yes","on"}`. Marginal (~3 LOC) — P3.
- **`processor/buttonsbebe-processor.service` vs
  `deploy/systemd/buttonsbebe-processor.service`**: two copies of the unit,
  **and they have diverged** — the deploy copy carries hardening
  (`NoNewPrivileges`, `PrivateTmp`, `ProtectKernel*`, `RestrictSUIDSGID`,
  `UMask=0077`; diff at `deploy/systemd/buttonsbebe-processor.service:20-29`)
  that the `processor/` copy lacks. `deploy/` is the canonical home for
  systemd units (AGENTS.md §5), nothing references the `processor/` copy, and
  the CD manifest does not ship units (`deploy/cd/source_release.py:15-31`
  ships app code only). The stale copy is a trap for anyone reading the wrong
  file.
- **Journal liveness-marker contract** (three sites, one producer, two
  consumers): the orchestrator emits `"Processor idle heartbeat"`
  (`orchestrator.py:544`), `"Job completed"` (`orchestrator.py:645`),
  `"Job processor starting"` / `"Queue stats at startup"` (`orchestrator.py:467,487`).
  `heartbeat.sh:147` greps for all four; `tools/ops/monitor.py:42` greps for
  two of them. The strings are documented at `config.py:67-71`, but nothing
  mechanically pins producer to consumers — see Reliability risk R2.

Vs other modules:

- **`whatsapp_notifier.py` vs `webhook/src/bb_webhook/notifications.py` — NOT
  duplicates.** `notifications.py:16-82` is a pure derivation of the console's
  human notification feed from ticket rows (no transport, no network);
  `whatsapp_notifier.py` is the phone-alert transport used by the processor
  for HIGH/CRITICAL jobs (`orchestrator.py:583`). Both live, different
  surfaces. **(f) answer:** live paths are — `whatsapp_notifier` → owner's
  phone via whatsapp-connect `/send` (`whatsapp-connect/server.js:223`);
  `notifications.py` → console Notification tab; `heartbeat.sh:95-119` → same
  WhatsApp contract for infra alerts (documented as intentionally shared,
  `heartbeat.sh:10-15`). A bash script must not import the Python it
  monitors; the contract duplication is the right kind.
- **`draft_generator.py` vs `draft_cleaner.py` — no role split; one is dead.**
  `draft_generator.py` is a placeholder stub from the pre-Hermes design
  (docstring: "STUB … To be implemented in the next step",
  `draft_generator.py:1-13`) whose "planned implementation" (direct LLM calls,
  pre-searched KB context) is exactly the architecture AGENTS.md §4 replaced:
  Hermes now drafts, using its own `buttonsbebe_kb` MCP (:8077), and
  `draft_cleaner.py` is the live last-mile safety gate
  (`hermes_runner/runner.py:10,224`). AST scan of every non-test `.py` in the
  repo finds **zero importers** of `draft_generator` (or `kb_client`) outside
  retired trees.
- **`kb_client.py` — (d) DEAD CODE, confirmed by the deletion test.** It is
  the retired pre-Hermes processor-side KB search: an AST walk over all live
  trees (excluding `_VPS*`, `gorgias-webhook/`, `teddy/`, `fable/`,
  `kb-editor/`, `qa_v3/`, `dashboard/`) finds no importer of `kb_client`;
  git history shows it came across in the 2026-07-14 sanitize commit
  (8ba1e78) and was **never** imported by any version of `orchestrator.py` or
  `hermes_runner*` in any commit. Hermes searches the KB itself via MCP
  (AGENTS.md §4 flow, `:8077 buttonsbebe_kb`). Its only surviving consumer is
  `config.py:48 kb_mcp_url`, whose only other reader is the demo-mode
  validator (`config.py:119`) — which stays as a fail-closed demo tripwire.
- Note for the classifier analyst (caller-side fact): the orchestrator never
  passes `kb_results`/`order_data` to `classify` (`orchestrator.py:99,304`),
  so the classifier's `kb_sensitive` branch (`classifier/engine.py:53-56`)
  is unreachable from the live path. Hunch-labeled — its removal belongs to
  the classifier module's report.

## Reliability risks

| # | Scenario | Consequence | Minimal fix | Evidence |
|---|---|---|---|---|
| R1 | `heartbeat.timer` gets masked/disabled during maintenance (systemd units are VPS-side, not CD-managed) | The dead-man's switch itself silently stops; a later processor death alerts nobody, and the console ops view can't show it — `monitor.py` watches only `buttonsbebe-backup` and `buttonsbebe-inbox-projection` timers | Add `buttonsbebe-heartbeat` to `TIMERS` in `tools/ops/monitor.py:17-18` and to `CHECKS` in `webhook/src/bb_webhook/ops_status.py:8-13` | `tools/ops/monitor.py:17-18`; `webhook/src/bb_webhook/ops_status.py:8-13` |
| R2 | Someone rewords the `"Processor idle heartbeat"` / `"Job completed"` log messages (e.g. during a logging refactor) | Both watchdog readers break silently: `heartbeat.sh:147` and `monitor.py:42` stop seeing markers, `test_heartbeat.py` still passes because it stubs `journalctl` with fixed strings (`test_heartbeat.py:35,37`) | One source-shape test asserting `orchestrator.py` contains the exact marker strings (same technique as `test_feedback_retirement.py:34-38`) | `orchestrator.py:544,645`; `processor/heartbeat.sh:147`; `tools/ops/monitor.py:42`; `processor/test_heartbeat.py:35,37` |
| R3 | WhatsApp bridge hangs during an owner alert (`send_whatsapp` runs synchronously on the event loop, `urlopen` timeout 15 s) | The whole loop stalls up to 15 s; the idle-heartbeat marker is delayed. With the live path's `max_retries=0` (`orchestrator.py:589`) there are no sleeps, so worst case is a 15 s gap — far under the 10-min stale window; a false "down" alert cannot result | Document the bound; no code change. Do NOT wrap in `to_thread` casually — the sibling `test_loop_responsive.py:30-55` documents measured regressions from threading this loop | `whatsapp_notifier.py:164`; `orchestrator.py:583-589`; `processor/heartbeat.sh:39` (10-min window) |
| R4 | `_save_result_to_webhook` POST fails (webhook app down) at 3 attempts | Job fails with an operator-visible reason after bounded retries; no draft lost, no duplicate alert — this is correct fail-closed behavior, listed to confirm it is covered | None | `orchestrator.py:650-674`; `test_result_durability.py:36-50` |
| R5 | Processor killed by SIGKILL mid-job | Kernel releases the flock (no stale lock); on restart, `requeue_stale_jobs` reclaims the claim after `stale_job_minutes`, and the durable-result check prevents re-running Hermes if the draft had already committed | None — verified by tests | `orchestrator.py:228-253,476-483`; `webhook/src/bb_webhook/database.py:483-524`; `test_periodic_recovery.py:26-55`; `test_result_durability.py:53-77` |
| R6 | Clock steps backward >10 min on the VPS | Wall-clock staleness comparisons (`database.py:512-513`) see claims as younger than they are; abandoned jobs simply age out later. Safe direction; loop cadence itself is `time.monotonic` | None | `webhook/src/bb_webhook/database.py:512-513`; `orchestrator.py:495,503` |

## Simplification actions

| # | Action | Files | LOC Δ | Risk | Priority | Longevity gain |
|---|---|---|---|---|---|---|
| 1 | Delete dead `kb_client.py` (pre-Hermes KB search; zero importers; Hermes searches via MCP `:8077`) | `processor/kb_client.py` | −101 | Very low (AST deletion test run; gate re-run) | P1 | Removes a whole stale subsystem and its misleading "processor searches KB" story from the tree |
| 2 | Delete dead stub `draft_generator.py` (pre-Hermes placeholder promising a direct-LLM draft engine that contradicts AGENTS.md §4) | `processor/draft_generator.py` | −68 | Very low | P1 | Kills the false "to be implemented" trail for future maintainers/AI agents |
| 3 | Delete the stale, divergent systemd unit copy (missing the hardening the canonical deploy unit has) | `processor/buttonsbebe-processor.service` | −21 | Low — zero references found; `deploy/systemd/` stays canonical | P2 | Ends the two-copies-drift trap on a safety-relevant config file |
| 4 | Make the watchdog watch itself: add `buttonsbebe-heartbeat` timer to ops monitoring | `tools/ops/monitor.py`, `webhook/src/bb_webhook/ops_status.py` | +2 | Low | P2 | Closes the only silent-failure path in the alerting chain (R1) |
| 5 | Replace the hand-rolled settings singleton with `@lru_cache`, matching the webhook's own config | `processor/config.py` | −4 | Near zero | P2 | One boring stdlib pattern across both siblings instead of two idioms |
| 6 | Delete truly-unused LLM fields (`llm_base_url`, `llm_model`, `llm_api_key` — zero consumers anywhere, not even the demo validator). Keep `shopify_shop`/`gorgias_subdomain` (demo tripwire reads them, `config.py:109-111`) and `kb_mcp_url` (`config.py:119`) | `processor/config.py:51-54` | −5 | Low | P3 | Config stops advertising a direct-LLM path the processor no longer has |
| 7 | Full deletion of `gorgias_writer.py` + `feedback_collector.py`, keeping the source-shape assertions of `test_feedback_retirement.py:34-43` (they guard `hermes_runner.py`/`orchestrator.py`, not the stub); update RETIRED.md rows and AGENTS.md §4/§10 to say "deleted, history in git". Safety-neutral: an `ImportError` is as loud as the guarded `RuntimeError`, and RETIRED.md already records the policy | `processor/gorgias_writer.py`, `processor/feedback_collector.py`, `processor/test_feedback_retirement.py`, `RETIRED.md`, `AGENTS.md` | −293 (net, after doc edits) | Medium: doc/test churn on a safety-flagged boundary; **keeping the files fail-closed is also defensible** — this is tree hygiene, not a safety fix | P3 | If kept: zero action is also acceptable; do not let this displace P1/P2 |
| 8 | Pin the liveness-marker contract with one source-shape test (R2) | new: extend `processor/test_heartbeat.py` or `test_loop_responsive.py` | +~8 | None | P3 | Makes the producer/consumer marker rename fail loudly instead of silently disarming the watchdog |

`classifier_shim.py` is **not** a deletion candidate: the release gate passes
it to the 10,000-sample classifier-parity check on every run
(`tools/verify_release.sh:220-222`, resolved from git history by
`tools/compare_classifier.py:11-13,249-262`). Cost of keeping: 13 lines.

## Rejected on principle

- **Switching the classification cache to `functools.lru_cache`** — LOC wash,
  no reliability gain; the 9-line FIFO is boring, correct, and directly
  testable via `_classification_cache.clear()` (`test_priority_selection.py:25`).
- **`LISTEN/NOTIFY` instead of 2 s polling** — extra listener connection and
  reconnect logic for zero felt latency at ~1 job/21 min.
- **`asyncio.to_thread` for Hermes/classify/alerts** — measured regression
  documented in `test_loop_responsive.py:30-55` (lost owner alerts, 5
  concurrent duplicate runs); the file exists to stop exactly this change.
- **Extracting `bb_webhook.database` into a shared package** — the
  `sys.path` seam is deliberate (webhook owns the schema); a shared package
  means a third CD component, two pyproject edits, and test churn for no
  reliability gain. Accept as-is. **(i) answer: accept the seam.**
- **Unifying `heartbeat.sh`'s curl sender with `whatsapp_notifier.py`** — the
  dead-man's switch must not depend on the Python runtime it monitors.
- **`sd_notify`/`WatchdogSec` instead of `heartbeat.sh`** — a busy-failing
  loop can still call `sd_notify`, so the watchdog would miss the
  "active but completing no work" failure mode `heartbeat.sh:143-157`
  specifically catches; the bash script also handles the correlated
  WhatsApp-down case (`test_heartbeat.py:171-190`).
- **Adding jitter to the backoffs** — single-instance loop; no thundering herd.
- **Dropping `httpx` from `processor/pyproject.toml` together with action 1**
  — `test_e2e.py:37` (live VPS diagnostic) still imports it.
- **Unifying the always-on result-URL guard with `demo_safety.demo_url_allowed`**
  — different activation modes; merging would complicate a tested fail-closed
  guard (`test_result_auth.py:55`).

## Verification

| Action | Existing coverage or the one smallest new test |
|---|---|
| 1 (delete `kb_client.py`) | Existing: AST scan confirms zero importers in live trees (performed this analysis). After deletion, `bash tools/verify_release.sh` — the gate syntax-checks ≥40 files and auto-discovers every `processor/test_*.py` (`tools/verify_release.sh:96-117,193-210`), so a missed import fails loudly. No new test needed. |
| 2 (delete `draft_generator.py`) | Same as 1: zero importers (AST scan); gate re-run covers it. |
| 3 (delete `processor/buttonsbebe-processor.service`) | Existing: grep shows the only self-references; `deploy/HEARTBEAT-INSTALL.md:14-16` and `deploy/recovery-plan.example.json:20` point at `/etc/systemd/system/` and `deploy/systemd/`, never the `processor/` copy. No test affected. |
| 4 (monitor the heartbeat) | One smallest new test: assert `ops_status.CHECKS` ⊇ the keys `monitor.py` emits for timers (a ~6-line unit assertion in `webhook/test_ops_status.py`), so adding the heartbeat check and forgetting the console view fails the gate. |
| 5 (`@lru_cache` settings) | Existing tests patch `orchestrator.get_settings` by name (`test_result_auth.py:17,41,51`; `test_result_durability.py:131`), unaffected by the decorator. One smallest new test if desired: `self.assertIs(get_settings(), get_settings())` in `processor/test_priority.py`. |
| 6 (drop dead LLM fields) | Existing: the demo validator (`config.py:102-139`) does not read `llm_*` (verified by consumer count: 0). Gate re-run exercises `ProcessorSettings()` construction in every processor test import. |
| 7 (trio deletion, if taken) | Keep `test_feedback_retirement.py:34-43` assertions (they read `hermes_runner.py`/`orchestrator.py` source, not the deleted files); replace the stub-behavior test (`:25-32`) with an import-absence check. Gate runs this file (`tools/verify_release.sh:193-210`). |
| 8 (marker pin) | One smallest new test: read `orchestrator.py` source and assert the exact strings `"Processor idle heartbeat"`, `"Job completed"`, `"Job processor starting"`, `"Queue stats at startup"` appear — mirroring `test_feedback_retirement.py:34-38`. Put it in `processor/test_heartbeat.py` next to the stubs that hardcode the same strings. |

Bottom line: two dead pre-Hermes modules and a stale unit copy are the only
unambiguous deletions (P1/P2, ~190 LOC); the loop, lock, retry, polling, and
durability design are already the boring, correct versions of themselves.
