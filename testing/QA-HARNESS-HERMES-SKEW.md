# Historical QA harness vs Hermes notes — snapshot from 2026-09-24

> This is an incident-history document, not a current runtime report or runbook.
> Its server paths, model names, installed-code claims, test counts, key status,
> and cleanup notes describe reports from 2026-09-24 only. They have not been
> re-verified for the current environment and do not identify the current server,
> Hermes source, interpreter, or model. Use [HOW-TO-RUN.md](HOW-TO-RUN.md) for
> the current supported procedure. The runner uses `--hermes-python` and
> `--hermes-source` and derives the launcher from the pinned source; it does not
> accept the old `--hermes` option.

## Status reported at the time

The 2026-09-24 report said the repo fix was present and described a 48/48
historical review, with 47 PASS and R18 NEEDS_WORK before a prompt adjustment.
Those results are archived evidence only, not a current gate result. The exact
installed file paths and backup listed in that report are not current
identification or authorization to inspect a server.

## Historical box facts (reported on 2026-09-24 only)

- Ollama key is valid (API returns 200) and `glm-5.2` is listed.
- Offline boundary tests: 15/15 pass.
- Business tool schemas: 10/10 match the live brain exactly.
- Over-the-wire `readOnlyHint is True` for every tool: passes.
- Production Hermes config sets no `trust` key, which the new Hermes treats
  as `full`. Production is unaffected by the readonly-hint issue below.

## Gap 1 (mechanical): Hermes split `tools.mcp_tool`

New homes on the box: `discover_mcp_tools` moved to
`tools.mcp_tool_discovery`, `shutdown_mcp_servers` to
`tools.mcp_tool_lifecycle`, `_build_utility_schemas` to
`tools.mcp_tool_schema`. Old paths warn they were removed on 2026-09-14.
`_tool_read_only_hints` still lives in `tools.mcp_tool`.

Stale spots: the `PROBE` string in `testing/qa_metadata.py` and the
`QA_BINDINGS` code string in `testing/qa_harness.py` (`prove_hermes_bindings`).
Symptom: `ImportError: cannot import name _build_utility_schemas`.

Fix used in the private copy: import from the new home first, fall back to
the old path. Assertions unchanged.

## Gap 2 (benign): explicit empty `required: []`

New Hermes emits `"required": []` where the old build omitted the key
(utility `parameters`, then nested `arguments` blocks, `get_prompt` x3).
Semantically identical to absent. Exact-equality checks fail on it.

Fix used in the private copy: drop `required == []` during canonicalization
on both sides, in `canonical_nullable_schema` and the probe-local `_norm`.
A real removed requirement still diffs, so the check keeps its teeth.
Recursion must map over `properties` VALUES and whole-value `parameters`
(plus `items`/`additionalProperties`/`anyOf`/`oneOf`/`allOf`/`not`);
descending only into named keys misses nested blocks.

## Gap 3 (needs a decision): readonly-hint table no longer populated

`prove_metadata` expects Hermes-internal `_tool_read_only_hints[group]` to be
`True` for all 10 business tools. On the new brain the table is empty before
discovery, and after discovery every business tool reads non-`True`, while
the independent wire check in the same run proves `readOnlyHint is True`.

Likely cause: the table is keyed by connection key now
(`_resolve_server_key`, see `tools/mcp_tool_handlers.py:56`), and the new
SDK stack parses annotations through a different path than the QA stub
client (mcp==1.29.1), so `_annotation_read_only_hint` returns False.
Because production runs at trust `full`, nothing enforces these hints live.

Decision for the maintainer: rewire the check to the new lookup/API, or
promote the wire-level assertion (already strict `is True`) and retire the
legacy-table comparison. Do not just delete it; something slipped once.

## Hygiene note

Staging with macOS `tar` carried AppleDouble `._*` files into the KB
allowlist. Use `COPYFILE_DISABLE=1` or `--no-xattr` when copying. Cleaned.

## Current reproduction procedure

Do not reuse commands, hostnames, or file paths from this historical report.
For a newly authorized run, prepare a fresh isolated environment and follow
HOW-TO-RUN. The supported command supplies the exact verified interpreter with
`--hermes-python`, the pinned Hermes source directory with `--hermes-source`,
and an owner-provided private model-only file. The harness derives the launcher
from that source. No current host, model version, key status, or installed Hermes
path is asserted here. Do not inspect or reuse the old model files or QA output
directories listed in the historical notes.

## Historical cleanup note

The preceding session listed private staging and output paths as cleanup
candidates. Their existence and ownership have not been checked since that
session. Do not remove them based on this note; any future cleanup needs a fresh
inventory and must preserve paths the current owner confirms are runtime
dependencies.
## Gap 4 (upstream Hermes bug, 48 blocked): readonly hints always False

`tools/mcp_tool_registration.py`: `_annotation_read_only_hint` reads the
bare camelCase attribute (`getattr(annotations, "readOnlyHint", None)`).
The file itself documents (schema-cache section) that mcp 2.0 renamed every
Tool field to snake_case and camelCase is serialization-only: bare camelCase
getattr returns None. So every MCP tool is recorded as NOT read-only. The
codebase already fixed the same bug class for `inputSchema` via an
`mcp_field` both-spellings helper; the hint reader never got it. One-line
upstream fix in that function.

Blast radius: production sets no `trust` key (treated as `full`), so the
approval gate never consults these hints live; services verified healthy.
But in QA (`trust: "untrusted"`, the correct posture) the call-time gate
refuses EVERY stub read. Proven by live smoke: returncode 0, verdict True,
but `tool_calls: []` and the model reporting all reads refused as
write-capable. Any 48-run now would grade 48 blind drafts. No test-side
workaround exists short of patching installed Hermes code or flipping QA
trust, both owner decisions. Do not just delete the check.

## Gap 5 (harness): sandbox never exports the provider key

The `ollama-cloud` provider requires `OLLAMA_API_KEY` in the environment
and ignores the config-file `api_key` (`No usable credentials found`). The
harness builds a minimal env and replaces `_run_environment`, so the key
never arrives. Private-copy fix: inject `OLLAMA_API_KEY` from the model
block into `self.env` in `Harness.__init__` (existing secret redaction
covers outputs). Upstream fix: export provider key env vars from the
model-only config for env-key providers.

## Smoke evidence (R01, WISMO onesie)

Fail-safe path verified working: on invocation failure the runner falls back
to priority high, sensitive draft, owner notify, and zero Gorgias writes.
On success path the draft extracts cleanly (marker_count 1, no overflow).
Human grading per TEST-PLAN still required for all 48 once unblocked.

## Box state (2026-09-24, after brain fix)

`/private/bb-qa` staged with private harness fixes, venv rebuilt,
`/private/qa-v1` holds the grounded R01 proof. Operator key at
`/private/operator-provided-model.json` (0600). Shred run outputs after
the owner finishes review; they contain a model credential copy.
