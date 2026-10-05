# Orchestrate the 48-scenario model QA

This is a shard-orchestration prompt, not a self-contained server runbook. Read
[HOW-TO-RUN.md](HOW-TO-RUN.md) before starting; it is the source of truth for
preparation, launch identity, isolation, and receipt checks. The old prompt's
server, Hermes, and model claims are historical and must not be assumed current.

Run model-backed scenarios only after the owner explicitly authorizes that run
and supplies the current target, exact Hermes interpreter and pinned source
paths, a private model-only configuration, and a new private output root. If any
value is missing or a preflight fails, stop and report; do not guess or bypass it.

## Hard rules

- Do not merge, deploy, send to Gorgias, write Shopify data, refund, cancel, or
  contact a customer. The harness itself uses synthetic Gorgias and Redo
  fixtures.
- Never print or inspect the model configuration, Hermes `config.yaml`, auth
  store, or other credential files. Review generated captures and receipts
  privately for grading; do not attach or publish raw outputs or whole directories.
- Use a new output directory for every shard and retry. Never reuse evidence.
- If a shard stops safely, lacks `authenticated_verdict: true`, or has missing
  expected synthetic tool reads, stop that shard and report the exact issue.
- Produce evidence and a first-pass table only. The maintainer grades results
  using `testing/TEST-PLAN.md`; only the maintainer decides whether the gate
  passes.

## Launcher contract

The current runner accepts `--hermes-python` and `--hermes-source`. It derives
the supported launcher from the pinned Hermes source and binds it to that exact
interpreter. The removed `--hermes` option is not supported; do not add it or
substitute a shell wrapper. Preserve the interpreter path exactly as supplied.
Use the owner-verified values and preparation steps from HOW-TO-RUN.

The common invocation shape is:

```sh
"$QA_PYTHON" testing/run_live_tests.py \
  --hermes-python "$QA_HERMES_PYTHON" \
  --hermes-source "$QA_HERMES_SOURCE" \
  --model-config "$QA_MODEL_CONFIG" \
  --output "$QA_OUTPUT" --kb-mode fixture
```

Before dispatching shards, the owner or lead agent must provide these values:

```sh
QA_PYTHON=/path/to/prepared/qa-venv/bin/python
QA_HERMES_PYTHON=/path/to/verified/hermes-python
QA_HERMES_SOURCE=/path/to/pinned/hermes-source
QA_MODEL_CONFIG=/private/operator-provided-model.json
QA_RUN_ID=unique-owner-chosen-id
QA_OUTPUT="/private/qa-smoke-$QA_RUN_ID"
```

They are placeholders until replaced with paths verified for the approved run.
Do not infer a host, interpreter, source checkout, profile, or model from this
file's older revisions.

## Run plan

1. Follow the offline dependency and boundary-test steps in HOW-TO-RUN.
2. Run one smoke scenario into a new private output directory. Review the
   receipts and confirm the expected synthetic tool reads before sharding.
3. Within the already-authorized run, optional diagnostic sharding uses four
   serial shards. Give each shard an independent agent, output directory,
   and non-overlapping local MCP port range:

   | Shard | IDs | Base port |
   |---|---|---:|
   | P1 | R01–R12 | 18877 |
   | P2 | R13–R22, S01–S02 | 19277 |
   | P3 | S03–S12, E01–E02 | 19677 |
   | P4 | E03–E14 | 20077 |

Each shard runs one serial command with its own fresh output directory. For
example, after the owner assigns a unique value to `QA_OUTPUT_P1`:

```sh
"$QA_PYTHON" testing/run_live_tests.py \
  --hermes-python "$QA_HERMES_PYTHON" \
  --hermes-source "$QA_HERMES_SOURCE" \
  --model-config "$QA_MODEL_CONFIG" \
  --output "$QA_OUTPUT_P1" \
  --ids R01,R02,R03,R04,R05,R06,R07,R08,R09,R10,R11,R12 \
  --kb-mode fixture --base-port 18877
```

Use the same shape for each assigned ID set, base port, and unique output path.
Do not reuse paths after a failed or interrupted run.

These partial shard receipts support diagnostic review only. The current schema 4
release receipt requires one complete 48-case core run and one complete 10-case
reliability run with matching bindings, as described in HOW-TO-RUN. The receipt
builder does not combine four partial core runs. Use the full-suite procedure
directly when preparing a release; do not run diagnostic shards as extra paid work
unless they are part of the authorized scope.

## Report

Confirm there are 48 unique IDs across the four receipts. Prepare a per-case
table with the authenticated verdict, priority/action, whether a draft was
returned, draft cleanliness, and synthetic reads performed. Flag mismatches with
each scenario's expected behavior, unsafe promises, missing escalation, or
missing reads. Do not label the release passed or failed; the owner makes that
decision after human grading.

Do not copy private output directories wholesale. Follow HOW-TO-RUN for careful
review and cleanup of QA-owned evidence, preserving any interpreter or runtime
tree that the current owner verifies is still in use.
