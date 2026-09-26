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

Output directories contain a private model credential copy; never attach, commit,
or publish the directory wholesale. Share only reviewed results/receipts after
secret and PII checks. Remove the private HOME once evidence review is complete.
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
`qa_catalog.py` to snapshot only filenames from the reviewed Shopify product
folder. The snapshot rejects symlink boundaries, unexpected names, and documents
without the confirmed `shopify-sync` product front matter. It requires the
reviewed product-generator hash and never copies document bodies or URLs.

Pass the immutable snapshot through `--product-manifest PATH` together with
`--product-manifest-sha256 SHA256`. The loader admits only `products/product-*.md`
filenames; it cannot admit tickets, learned content, directories, or traversal.
The QA receipt records this snapshot's hash/count in addition to the merged
allowlist hash. Unknown result paths still stop the run. Local product files never enter the base allowlist. New product filenames require
a separately reviewed snapshot and a new run. This is a filename boundary, not
a snapshot of document contents: edits to an already allowed public product may
change retrieval. Keep KB writes paused for a reproducible run and retain tool
evidence; do not claim content immutability from this manifest.
