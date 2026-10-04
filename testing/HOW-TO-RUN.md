# Isolated production-model QA

Use this harness before a behavior-changing release. It runs the checked-in 48
synthetic scenarios through the actual processor prompt, command builder, token
extraction and cleaner. It never posts results to the queue, sends a reply,
creates a learning record, or calls customer Gorgias/Redo services.

For proposed KB edits, use the KB Python environment to run
`testing/qa_policy_snapshot.py --output <new private JSON>`. Pass the printed
SHA-256 and path using `--policy-overlay <snapshot> --policy-overlay-sha256
<digest> --kb-mode policies-only`. This keeps the existing reviewed allowlist
and substitutes proposed content only for policy/FAQ/intent hits already returned
by published search. Product data is unchanged. The preflight records the snapshot
hash. This tests replies against proposed content; it neither publishes articles
nor validates a rebuilt index's ranking.

The runner requires an explicit underlying Hermes executable, interpreter and
source directory. Do not use a production shell wrapper or copy the production
Hermes home. Root must prepare a private model-only JSON file outside the repo,
mode 0600. Its only required key is `model`, containing the existing `default`,
`provider`, and optional HTTPS `base_url` / `api_key`. For `openai-codex`, supply
one additional top-level `access_token` containing only the selected provider's
current access token. Never include a refresh token, full auth store, commerce
credentials, or production MCP configuration. The harness writes an isolated
manual OAuth pool entry; expiry requires a fresh access-only artifact and must
not refresh the production OAuth chain.

Prepare dependencies and run offline boundary tests:

```sh
uv venv /tmp/buttonsbebe-qa-venv --python 3.12
uv pip sync --python /tmp/buttonsbebe-qa-venv/bin/python --require-hashes testing/requirements-qa.lock
/tmp/buttonsbebe-qa-venv/bin/python -m unittest discover -s testing -p test_qa_harness.py
```

Start with one scenario, using a new private output directory outside the repo:

```sh
/tmp/buttonsbebe-qa-venv/bin/python testing/run_live_tests.py \
  --hermes /usr/local/lib/hermes-agent/venv/bin/hermes \
  --hermes-python /usr/local/lib/hermes-agent/venv/bin/python \
  --hermes-source /usr/local/lib/hermes-agent \
  --model-config /private/operator-provided-model.json \
  --output /private/qa-smoke --limit 1 --kb-mode policies-only
```

After reviewing the preflight receipt and smoke result, repeat with a different
new output directory and omit `--limit` for all 48 scenarios. `--ids R01,R02`
selects known scenario IDs for a new recovery run. Never overwrite prior evidence.
Run serially: the three local QA MCP ports must not overlap another run.

After the core 48, use `--suite reliability` in a new output directory to run
the ten additional acknowledgment, measurement, supplied-identifier, broken
gift-return portal, shipping-hours, pickup-readiness, holiday and cancellation
regressions. These do not replace or change the core 48-case catalog.

Every run creates a private HOME, disables native toolsets and memories, starts
exactly three loopback MCP stubs, and checks the actual Hermes tool definitions
before calling the model. Missing or extra tools abort. Gorgias and Redo use only
synthetic fixtures, with unknown identifiers rejected. The underlying processor
receives the unchanged three-toolset allowlist; no approval-bypass flag is used.
Process time and output are bounded; timeout kills the whole child process group.

`--kb-mode fixture` is the offline default. `policies-only` connects exclusively
to the existing local KB endpoint on port 8077. It retrieves at most 25 candidates
and admits only confirmed policies, FAQ, intents and products with checked-in
allowlisted paths. Tickets, learned examples and notices are dropped; unexpected
categories or paths abort. Only selected policy fields reach the model after
PII masking. The receipt records the mode and tool audit records filtered counts.
This is current policy grounding, **not full production retrieval equivalence**;
regex masking is not proof that arbitrary prose contains no personal information.

Review every result using TEST-PLAN.md. A captured response or an authenticated
marker alone is not a quality pass. Results retain raw synthetic model output
with masking, production extraction diagnostics and tool metadata for review.
Sensitive actionable cases must have a useful prefixed human-review draft and correct
priority/escalation. Pure thanks produce no new draft/alert. Staff-only unknowns
must carry a specific authenticated staff task and Needs staff input; an ordinary
knowledge gap is not urgency. Financial actions and unsupported promises are failures.
Inspect all 48, record explicit per-case judgments and unresolved defects, then
rerun changed cases and the full gate as warranted. No QA result authorizes Send.

## Review receipts

Each run writes `run.json`: suite, catalog hash, captured IDs, whether the run
was complete, and the bindings it ran against. Bindings are working-tree content
hashes (plus HEAD and dirty state) of first-party processor, webhook, Inbox and
console review code, KB search scripts, approved policies, Hermes SOUL/skills,
QA code and catalogs; the Hermes executable/source digest; the selected interpreter
file hash and nonsecret model/runtime settings read from the actual isolated profile;
and the approved KB
snapshot identity (KB mode, pinned policy overlay and product manifest). Tests,
`.env` files, runtime data, unapproved lessons and dependency copies are
excluded. Bindings are recomputed after the last scenario, re-verifying overlay
bytes and every pinned product file. The profile is read again after the final
scenario, so changes to the selected model, provider, endpoint or supported runtime
settings invalidate the run. Model API keys and the separate OAuth auth file never
enter the identity or its hash. Private shard homes and local MCP port differences
are ignored, so equivalent isolated runs still match. Endpoint URLs carrying user
credentials, query parameters or fragments are refused; unsupported model settings
are refused rather than omitted from evidence. The run also lists each KB section shown
to the model as file, heading and content hash. Create a judgments file per
run with `testing/qa_receipt.py template --run <dir>/run.json --output <file>`,
then replace every `pending` with the reviewer's `PASS`, `NEEDS_WORK` or `FAIL`
and list blocking defects. Combine full core and reliability runs with
`qa_receipt.py build`. It rejects partial runs, bindings that changed during a
run, a section whose content changed within or between the runs, core and
reliability runs with different source, Hermes, model/runtime or approved KB snapshot, and
judgments for a different run. The two suites may observe different sections.
The combined receipt keeps recorded nonsecret identities, hashes, IDs and verdicts; defect text and model
output stay private.

New runs, judgments and combined receipts use schema 3. Older evidence cannot be
upgraded by copying a model name into it: schema 2 receipts did not record the actual
profile before and after a run. Rerun both suites and grade the new captures. The
template, combine and check commands fail closed on older or missing model identity.

`qa_receipt.py check <receipt>` recomputes the state against the current
checkout. It rejects stale sources, pending verdicts, `NEEDS_WORK`, `FAIL` and
blocking defects. `--review-only` accepts a complete review that did not pass;
that means "review complete", not "release passed". The release criterion this
supports — full 48 plus 10 runs, all PASS, on one source fingerprint — is
stricter than the current root rule of a clean 48-case run. No such receipt
exists yet for the current source. `check` has no Hermes, model-profile, overlay or manifest paths,
so it does not re-verify installed Hermes, model settings or KB snapshot content; that happens
before and after each run. Observed section hashes detect differing served text,
but do not prove that an index already stale before the run matches approved files. CI
checks only catalog shape and receipt logic; it never runs a model or records a
verdict.

Output directories contain a private model credential copy; never attach, commit,
or publish the directory wholesale. Share only reviewed results/receipts after
secret and PII checks. Before removing a private HOME, inspect the production
Hermes launcher and interpreter paths. A production interpreter can reside in
a QA tools directory even when the launcher uses the production Hermes profile.
Preserve any such interpreter subtree until a separate, verified move to a
durable runtime is complete. Remove only QA-owned credential and evidence files
whose paths are not production runtime dependencies; never delete the whole
HOME based on its directory name.
Historical `results-live.json`, `results-sim.json`, and LIVE-RUN-JUDGMENT.md are
archived evidence from earlier behavior, not proof of the current release.


Offline release gates include QA boundary tests (synthetic executables and
localhost fixture MCPs only; no model request). Set `QA_PYTHON` to the interpreter
prepared from `testing/requirements-qa.lock` with `--require-hashes`; CI prepares
a separate `.qa-venv`. The harness uses the production bounded process helper
with a private working directory and explicit QA HOME/HERMES_HOME/HERMES_CONFIG.
It supplies the isolated QA environment instead of using root's profile.


Hermes startup preflight explicitly discovers MCPs before inspecting schemas and
preserves the interpreter's venv path (resolving its symlink bypasses that venv).
It accepts either the exact ten business definitions plus twelve standard
metadata helpers, or Hermes' normal three progressive-disclosure bridge definitions. For the bridge presentation,
both the dispatcher catalog and agent executor scope must equal the ten business
capabilities plus the twelve standard MCP resource/prompt metadata helpers, and unknown/out-of-scope bridge calls must fail before dispatch.
The metadata helpers must match Hermes-generated schemas exactly, and all three
resource, resource-template, and prompt catalogs must be empty. No native tool
capabilities are enabled. Production tool presentation is unchanged.

Live synced products are runtime data and are not all tracked in Git. Before a
policy-mode run that includes catalog retrieval, an operator may use
`qa_catalog.py` to snapshot the reviewed Shopify product folder. The manifest
records the folder path and a content hash per product file; it never copies
document bodies or URLs. The snapshot rejects symlink boundaries, unexpected
names, and documents without the confirmed `shopify-sync` product front matter.
It requires the reviewed product-generator hash.

Pass the immutable snapshot through `--product-manifest PATH` together with
`--product-manifest-sha256 SHA256`. The loader admits only `products/product-*.md`
filenames; it cannot admit tickets, learned content, directories, or traversal.
Every load re-hashes each listed product file and stops on any difference, so
the run fails if product content changes before its final recheck. Unknown
result paths still stop the run. Local product files never enter the base
allowlist. New product filenames or edited products require a new reviewed
snapshot and a new run. The published index may still serve text indexed before
the snapshot; keep KB writes paused for a reproducible run and retain tool
evidence.
