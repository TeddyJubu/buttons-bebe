# Run the isolated 48-scenario model QA on an authorized server

This replaces the older copy-and-run notes. Those notes named a model and
passed `--hermes`, an option the current runner no longer supports. They do not
establish the current server, Hermes version, or model. Use
[HOW-TO-RUN.md](HOW-TO-RUN.md) for the current preparation, safety rules, and
receipt checks.

Only run the model-backed gate after the owner explicitly authorizes it and
provides the target environment, the exact Hermes interpreter and source paths,
and a private model-only configuration. Do not infer those details from old
notes. The model-only file stays private and must not include a production Hermes
profile, commerce credentials, or a refresh token.

The QA launcher takes the exact interpreter with `--hermes-python` and the
pinned Hermes source directory with `--hermes-source`. It derives Hermes'
launcher from that source; it does not accept a shell executable or wrapper via
`--hermes`. Keep the interpreter path exactly as supplied so a virtual
environment's path is not lost by resolving its symlink.

After following the dependency and offline-test steps in HOW-TO-RUN, set these
values from the current owner-approved environment. The example paths for the
QA virtual environment and model file are placeholders unless the owner confirms
them for this run.

```sh
QA_PYTHON=/path/to/prepared/qa-venv/bin/python
QA_HERMES_PYTHON=/path/to/verified/hermes-python
QA_HERMES_SOURCE=/path/to/pinned/hermes-source
QA_MODEL_CONFIG=/private/operator-provided-model.json
QA_RUN_ID=unique-operator-chosen-id
QA_OUTPUT="/private/qa-smoke-$QA_RUN_ID"

"$QA_PYTHON" testing/run_live_tests.py \
  --hermes-python "$QA_HERMES_PYTHON" \
  --hermes-source "$QA_HERMES_SOURCE" \
  --model-config "$QA_MODEL_CONFIG" \
  --output "$QA_OUTPUT" --limit 1 --kb-mode fixture
```

Review the private preflight and smoke receipts as described in HOW-TO-RUN. If
the harness stops safely, the authenticated verdict is missing, or expected
synthetic tool reads are absent, stop and report the issue. Do not bypass a
failed check. A smoke is not a quality judgment and does not pass the full gate.

For all 48 scenarios, choose a different output directory that does not already
exist and repeat the command without `--limit 1`. For a selected recovery run,
pass `--ids R05,E01,E12` and still use a fresh output directory. You may set
`--timeout 240` when the owner approves a longer per-case bound. If using
`--kb-mode policies-only`, first verify the approved local KB endpoint and
snapshot procedure in HOW-TO-RUN; otherwise keep the default fixture mode.

The harness uses synthetic Gorgias and Redo fixtures and never sends a customer
reply. Its output is still private review evidence: do not copy or publish a
whole output directory. Transfer only owner-approved, reviewed extracts through
the designated channel. An offline or model-backed QA result does not authorize
Send or any production change.
