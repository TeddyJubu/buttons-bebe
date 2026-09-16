# 04 — Processor brain (hermes_runner + classifier)

Read-only analysis of `processor/hermes_runner/` and `processor/classifier/`
(+ `processor/classifier_shim.py`). Every claim cites file:line. Hunches are
labelled. Branch `refactor`, 2026-09-16.

## Snapshot

| File | LOC | Role |
|---|---|---|
| `processor/hermes_runner/__init__.py` | 42 | facade re-exporting public + 20 private names |
| `processor/hermes_runner/constants.py` | 91 | result templates, action/priority tables, token mint |
| `processor/hermes_runner/extract.py` | 290 | token-authenticated draft + JSON_RESULT extraction |
| `processor/hermes_runner/process.py` | 102 | bounded subprocess: caps, deadline, group kill/reap |
| `processor/hermes_runner/prompt.py` | 287 | prompt build + control-marker neutralisation |
| `processor/hermes_runner/runner.py` | 299 | orchestrate one ticket end-to-end |
| `processor/hermes_runner.py` | 14 | flat shim → 3 public names |
| `processor/classifier/__init__.py` | 36 | facade + `python -m classifier` self-test entry |
| `processor/classifier/compat.py` | 45 | metaclass so facade setattr reaches owner modules |
| `processor/classifier/data.py` | 137 | loads `tables/*.yaml` (JSON), Rule dataclass, flat aliases |
| `processor/classifier/engine.py` | 191 | `classify()` — the decision function |
| `processor/classifier/matching.py` | 87 | bounded log-safe match helpers |
| `processor/classifier/patterns.py` | 51 | 6 coordinated compiled regexes |
| `processor/classifier/views.py` | 187 | bound/fold/filter views, caps & exclaiming logic |
| `processor/classifier/selftest.py` | 52 | runs `selftest.json` corpus |
| `processor/classifier/guards/` (4 files) | 71 | three one-regex files + `__init__` |
| `processor/classifier/tables/` (10 YAML) | 1,044 | 125 labelled rules + caps/intents data |
| `processor/classifier/selftest.json` | 532 | 106 golden labelled messages |
| `processor/classifier_shim.py` | 13 | parity-history pointer |
| Tests (10 files) | 4,424 | incl. `test_classifier_rules.py` 3,353 |

Module Python ≈ 1,995 LOC + 1,576 data. Interface/seam: builds the Hermes
CLI command from the explicit toolset allow-list, runs it as a bounded
subprocess, extracts the run-token-authenticated draft and JSON verdict from
stdout, and deterministically classifies sensitivity/priority as an
escalate-only first pass. Job: turn one webhook payload into one
human-reviewable, authenticated draft plus a cautious verdict — failing
closed to owner-notifying, non-sendable results whenever attribution is
uncertain.

Context the numbers need: the classifier package split (`ba138d5`, 2026-08-23)
and the Hermes split (ADR-015) landed the same day as a 1,000-line
file-size guard (`tools/check_python_file_sizes.sh:11`, tests excluded at
:67) — the pre-split `classifier.py` was 1,606 lines (git `ba138d5^`).
ADR-014/ADR-015 document the splits as deliberate, and
`tools/compare_classifier.py` runs a 10,000-sample parity check in the
release gate (`tools/verify_release.sh:216-225`).

## Reinvented wheels

| # | Hand-rolled | Existing wheel | Evidence | LOC saved | Switch risk |
|---|---|---|---|---|---|
| W1 | Balanced-JSON char scanner `_extract_json_block` (escape/string/depth tracking) | `json.JSONDecoder().raw_decode` — stdlib, parses one object from a position, handles strings/escapes/nesting; slice to the 8k bound first so truncation raises `JSONDecodeError` → candidate skipped (same fail-closed path, `extract.py:129-131`) | `extract.py:56-82` | ~22 | Low — outcomes identical incl. truncation/garbage cases; token auth untouched (upstream in the marker regex, `extract.py:38-40`) |
| W2 | `[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]` literal hardcoded in two runner files | canonical `SENSITIVE_DRAFT_PREFIX` in `draft_cleaner.py:79` (already imported by `shared/review_policy.py`); no import cycle — `draft_cleaner` imports only `re`/`dataclasses` (`draft_cleaner.py:8-9`) | `constants.py:50`, `prompt.py:223` | ~2 + drift risk gone | Minimal — byte-identical today; `review_policy._HEADER` tolerates drift, but the prompt instructing the model and the fallback draft should share one source |
| W3 | `_neutralise_markers` compiles `re.compile(re.escape(marker), re.IGNORECASE)` per call ×4 markers | module-level precompiled pattern tuple (stdlib `re` idiom; `re`'s internal cache currently absorbs the cost) | `prompt.py:24` | ~6 | Low |
| W4 | `_fold_smart_quotes` 6-pass replace loop | `str.translate` with ord-keyed dict — stdlib, one pass | `views.py:98-102` | ~3 | Low |
| W5 | "extend with dedup" idiom `[m for m in X if m not in Y]` repeated 5× | one `_merge_unique(base, extra)` helper in `matching.py` | `engine.py:63-67`, `:69-71`, `:95-99`, `:112`, `:136-140` | ~8 | Low |

Note (not a wheel — keep): `matching.py:15` runs `re.search(pattern_str, …)`
over ~125 table patterns per classify, relying on stdlib `re`'s internal
compile cache. That is deliberate: the lists are mutable test seams
(`test_classifier_rules.py:943` asserts list identity; ADR-014 §2.1).
Precompiled caches would freeze that contract. Cost is ~125 dict lookups per
ticket — negligible.

## Duplication map

Within the module:

- **Compiled-regex definitions have two homes.** `patterns.py:8-44` holds six
  coordinated regexes; `guards/browsing.py:11-17`, `guards/order_context.py:8-19`,
  `guards/problem.py:8-24` hold exactly one regex each, same kind of content,
  in three extra files (+`guards/__init__.py`). Only consumers: `views.py:9-11`
  and the facade `_MODULES` tuple (`classifier/__init__.py:17,19-20`); the
  engine never imports guards (`engine.py:12-15`). One shared home: `patterns.py`.
- **SENSITIVE literal ×3** (W2 above): `draft_cleaner.py:79` (canonical),
  `constants.py:50`, `prompt.py:223`.
- **`test_selftest_passes` defined twice** — `test_classifier_rules.py:867-874`
  and again at `:876-878` (two classes, near-identical bodies).
- Overlapping-but-distinct (keep): `runner._no_draft_result` (`runner.py:83-96`)
  vs `shared/review_policy.final_review_result` both raise priority/notify/force
  side-effect flags false, but ADR-015 §2.3 requires separate failure paths;
  `_merge_verdicts` max-by-RANK (`extract.py:147-152`) reuses `RANK` rather
  than `shared.priority.escalate` — a list fold; fine.

Vs other modules:

- **Prompt safety text vs `kb/hermes-SOUL-buttonsbebe-addition.md`** — the
  SOUL file (Hermes' own soul) and the per-ticket prompt (`prompt.py:243-253`)
  both instruct read-only/sensitive-header behavior. Deliberate dual channel
  (soul + per-ticket prompt); only the prefix *literal* is code duplication
  (W2).
- **Quoted-history/boilerplate stripping exists at three trust boundaries**:
  the prompt tells the model to strip (`prompt.py:109-116`), the classifier
  strips defensively (`views.py:109-131`), `draft_cleaner` removes model
  self-talk (different agent). Defense-in-depth — not removable duplication.
- **Priority lattice**: single source of truth `shared/priority.py`
  (`RANK`/`escalate`/`at_least`), consumed by `runner.py:87`, `extract.py:134,150`,
  `constants.py:12`. One legacy tooth: the classifier's `"immediate"` label
  mapped to CRITICAL at the orchestrator boundary (`orchestrator.py:333-337`),
  documented in `shared/priority.py:1-10`. Removing it would churn
  `selftest.json` (43 `immediate` labels) + ADR-014 — not worth it.
- **`classifier_shim.py` duplicates the facade** (`classifier_shim.py:3-4`)
  but is load-bearing as the parity-harness marker: `verify_release.sh:221`
  passes it as `--old`, and `compare_classifier.py:617` treats a shim path as
  "fetch the real pre-split classifier from git history".

## Reliability risks

| # | Scenario → consequence → minimal fix | Evidence |
|---|---|---|
| R1 | A stray non-UTF-8 byte anywhere in Hermes stdout → strict decode raises → the *entire* run (possibly minutes of model work incl. a valid tagged draft) is discarded; generic fallback + owner ping; repeatable per ticket. Current behavior is deliberate fail-closed (pinned by test). Minimal fix: none now; if `error_type=UnicodeDecodeError` ever shows in prod logs, decode with `errors="replace"` — token auth still proves attribution, and a replacement char in a draft is human-visible. | `process.py:76` (`errors="strict"`), pinned `test_hermes_process.py:70-72` |
| R2 | Long ticket: prompt truncates the message at 3,000 chars while the classifier reads raw unbounded text → Hermes cannot see a complaint/request past the cap; draft may not answer the actual ask (owner preference: mismatched drafts undermine trust); classifier still escalates, human still gates. The model is at least told it was truncated (`prompt.py:49-50,286`). Minimal fix (hunch): raise the cap to ~8k — a token-cost/latency tradeoff, not a correctness bug. | `prompt.py:48-50` vs `engine.py:33` (unbounded main view) |
| R3 | Provider env rename (e.g. Ollama Cloud changes its key name) → Hermes auth fails as an opaque empty-output token-failure. The allow-list is a security feature (canary test pins it), so this is maintenance friction, not a bug. Minimal fix: none; document the two names. | `runner.py:104` (`OLLAMA_API_KEY`, `OPENAI_API_KEY`), pinned `test_hermes_process.py:104-113` |
| R4 | `parsed["_raw_output_preview"] = stdout[:500]` — 500 chars of model output (may echo customer text) ride on every result dict, but repo-wide grep finds **zero readers**: the orchestrator persists an explicit whitelist payload that omits it. Dead weight + unnecessary PII surface. Minimal fix: delete the line. | `runner.py:279`; whitelist at `orchestrator.py` `_save_result_to_webhook` payload (`"priority"… "draft_text"`, no preview key) |
| R5 | Timeout cleanup is bounded and reaps even on success/interrupt (TERM→0.3s→KILL→0.3s, group-signalled so SIGTERM-ignoring grandchildren die; EPERM retried then propagated, never swallowed). No zombie path found. Not a finding — recorded because the brief asks. | `process.py:15-33,78-102`; grandchild test `test_hermes_process.py:74-102` |

Positive findings worth stating: `run_bounded` is **not** a reinvented
`communicate()` — stdlib cannot group-kill descendants or cap output
(`process.py:37-47` docstring; `test_hermes_process.py:55-62` caps,
`:74-102` descendant kill). The 1MB/128k caps bound memory on long tickets;
marker counts are capped at 50 (`constants.py:26`); the tempered draft regex
is linear (`extract.py:49-53`).

## Simplification actions

| # | Action | Files | LOC Δ | Risk | Priority | Longevity gain |
|---|---|---|---|---|---|---|
| 1 | Replace `_extract_json_block` with `json.JSONDecoder().raw_decode` over `text[start:start+_MAX_JSON_BLOCK]`; truncation → `JSONDecodeError` → candidate skipped (identical fail-closed) | `hermes_runner/extract.py` | −22 | Low | P1 | stdlib parser replaces a hand parser; verification surface shrinks |
| 2 | Fold `guards/` (3 one-regex files) into `patterns.py`; update `views.py:9-11` and `classifier/__init__.py:17,19-20`; delete the directory. Regex bodies move (~35 lines), boilerplate dies | `classifier/guards/*` (del), `patterns.py`, `views.py`, `classifier/__init__.py` | −36, −4 files | Low-Med (import rewiring only; module-discovery tests are rglob-dynamic, `test_classifier_rules.py:54-76,1828-1849`) | P2 | one home for compiled regexes; fewest shallow files that exist |
| 3 | Import `SENSITIVE_DRAFT_PREFIX` in `constants.py`/`prompt.py` instead of literals (W2) | `hermes_runner/constants.py`, `hermes_runner/prompt.py` | ~0 | Minimal | P2 | kills a 3-way drift risk on a safety string |
| 4 | Delete the `_raw_output_preview` write (R4) | `hermes_runner/runner.py:279` | −1 | None | P2 | less dead PII-carrying state |
| 5 | Retire the T-FIX-3 parity gate step once it has been green across several deploys (it proves a one-time structural refactor, not ongoing safety — `selftest.json` + `test_classifier_rules.py` are the ongoing net); then delete `classifier_shim.py`, and CI no longer needs the full-history fetch (ADR-014 §5). Needs owner sign-off + ADR-014/RETIRED.md notes; `tools/` files owned by another module | `tools/verify_release.sh:216-225`, `processor/classifier_shim.py` (+ `tools/compare_classifier.py` ~640, other module) | −13 (mine) | Medium (release-gate change) | P2 | one-time proof stops re-running per release; removes a CI history constraint |
| 6 | Trim `hermes_runner/__init__.py` to the 3 public names + `_FALLBACK_RESULT` (its only external importer, `test_draft_cleaner_wiring.py:32`); delete `_PRIORITY_ORDER` (defined `constants.py:12`, imported `__init__.py:16`, used by nothing — runner/extract use `RANK` directly) | `hermes_runner/__init__.py`, `constants.py` | −32 | Low | P3 | facade states its real contract |
| 7 | Delete dead `_match_keywords` (defined, exported, zero callers repo-wide) | `classifier/matching.py:40-41,86` | −3 | None | P3 | dead code |
| 8 | Remove transitional `customer_text` params (always `del`-ed: `extract.py:97,106,190,195`), the `_extract_draft` wrapper (test-only, `extract.py:232-240`), and the vestigial always-0 echoes return (`extract.py:139`, discarded at `runner.py:190`); update `test_hermes_extract.py` call sites | `hermes_runner/extract.py`, `runner.py`, `test_hermes_extract.py` | −20 | Low (mechanical test edits) | P3 | honest signatures |
| 9 | Precompile `_MARKER_SUBSTITUTIONS` patterns at module level (W3) | `hermes_runner/prompt.py:19-25` | −6 | Low | P3 | idiom |
| 10 | `str.translate` for `_fold_smart_quotes` (W4) | `classifier/views.py:98-102` | −3 | Low | P3 | stdlib one-pass |
| 11 | Drop one of the two duplicate `test_selftest_passes` methods | `test_classifier_rules.py:876-878` | −5 | None | P3 | less test noise |
| 12 | (Hunch, from R2) Raise the prompt message cap 3k→~8k to cut draft-mismatch on long tickets | `hermes_runner/prompt.py:48` | ~0 | Cost/latency tradeoff | P3 | draft quality on long tickets |

Answers to the brief's specific questions: (a) `prompt.py` is one giant
f-string (lines 90-287) — string-heavy but single-purpose; data-driving it
would move the text to a file without shrinking it; its only true duplication
with the SOUL file is the prefix literal (W2). (b) `runner.py`/`process.py`
subprocess handling is solid — deadline enforced in the select loop, group
kill/reap on all paths, no zombie risk found; POSIX-only by design (Ubuntu
VPS, AGENTS.md §3). (c) `extract.py` is already anchored-regex; the one
hand-rolled piece (JSON block scan) is the W1 swap; fail-closed preserved.
(d) 14 files overstates it: ~857 real Python lines, and engine/views/matching/
patterns/data each carry distinct content — the only shallow files are
`guards/*` (action 2); a full re-collapse was explicitly rejected by ADR-014
§4. (e) `selftest.json`/`selftest.py` — keep, it is the cheap golden wheel.
(f) `classifier_shim.py` — keep until action 5 lands, then delete with the
gate step.

## Rejected on principle

- Untagged/dedgraded draft parsing, first/last-marker selection, echo
  matching — ADR-015 §4; empty token yields a never-match pattern
  (`extract.py:36-37`).
- Merging auth failure into the sendable fallback — a run that failed
  authentication must not create a holding reply (`constants.py:56-58`,
  `runner.py:75-80`).
- Bounding the classifier's main/raw view — caps must not be able to
  de-escalate a main match (ADR-014 §3; `engine.py:33`).
- Re-collapsing the classifier package into one file — rejected in ADR-014
  §4; the 1,000-line guard exists because of it
  (`tools/check_python_file_sizes.sh:11`).
- Adding PyYAML — tables are deliberately JSON-in-`.yaml`, stdlib-only
  (`classifier/data.py:14-17`).
- Precompiling table patterns into cached Pattern objects — would freeze the
  mutable-list mutation-test contract (`test_classifier_rules.py:943`;
  ADR-014 §2.1).
- Replacing `run_bounded` with `subprocess.communicate` — stdlib cannot
  group-kill descendants or cap output (`process.py:37-47`;
  `test_hermes_process.py:74-102`).
- Windows portability in `process.py` — deployment is Ubuntu-only
  (AGENTS.md §3); `killpg`/`start_new_session` are the point.
- Deleting `classifier_shim.py` today — `verify_release.sh:221` names the
  path; sequence it behind action 5.
- Loosening the strict stdout decode or the 1MB cap without an observed prod
  trigger — fail-closed is pinned (`test_hermes_process.py:70-72,59-62`).

## Verification

| Action | Covering test (existing) or the one smallest new test |
|---|---|
| 1 (raw_decode) | Existing: `test_hermes_extract.py:167-186` (each required field load-bearing → malformed candidates skipped), `:198-222` (overflow fails closed). New (only one needed, ~8 LOC): a verdict whose `reason` contains `}` and `{` inside the string, with extra text after the JSON object on the same line — assert exactly one valid block and the full reason survives. |
| 2 (guards merge) | Existing: `test_classifier_rules.py:1828-1849` (module discovery is filesystem/pkgutil-dynamic — adapts automatically), `:880-887` (every selftest case matches its label), `:867-874` (selftest passes). Run `(cd processor && uv run python -m unittest test_classifier_rules -v)`. No new test. |
| 3 (prefix import) | Existing: `test_hermes_readonly_prompt.py:48-59` (exact prompt strings), `test_hermes_process.py:134-135` (fallback draft), `test_final_review_policy.py:51` (auth failure never becomes a draft). No new test. |
| 4 (preview delete) | Repo-wide grep: zero readers of `_raw_output_preview`; run the processor suite. No new test (deleting an unread field). |
| 5 (parity gate retirement) | Procedural, not unit-testable: run `bash tools/verify_release.sh` before and after removing the step; owner sign-off; note the ADR-014 §6 one-change rollback. |
| 6–12 (P3s) | Full offline gate `bash tools/verify_release.sh` (AGENTS.md §8) or focused `(cd processor && uv run python -m unittest discover -p 'test_*.py' -v)`. |

The selftest golden corpus (106 messages; 46 normal/43 immediate/9 high-false/
8 high-true) plus the 181 test methods in `test_classifier_rules.py` pin all
classifier behavior through actions 2, 7, 10, and 11 without any new golden
data.
