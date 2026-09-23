# Run the 48-scenario live-model gate (4 parallel shards)

Copy everything below the line into an agent that can spawn subagents well.
It is self-contained. The box is already staged and the brain fix is live.

---

You are running a quality gate for the Buttons Bebe help-desk. Work on the
box over `ssh chaim` (root). The box is already staged. Your job: run all
48 synthetic scenarios through the real model and report. Change nothing
in production. Merge nothing. Send nothing to any customer.

## Hard rules (never break)

- READ ONLY toward production: no merges, no deploys, no Gorgias writes,
  no Shopify writes, no refunds, no cancels. The harness itself never sends.
- Never print secrets. The model key stays on the box. Never `cat` the
  model config, `config.yaml`, or `auth.json` under any output dir.
- Every run uses a NEW output dir. Never reuse or overwrite one.
- If any shard prints `QA stopped safely`, or a scenario lacks
  `authenticated_verdict: true`, stop that shard and report. Do not force.
- Human grading per `testing/TEST-PLAN.md` is still required. You produce
  evidence and a first-pass table, never the final release verdict.

## Phase 0: verify staging (do this first, stop if anything misses)

```sh
ssh chaim "grep -c read_only_hint /usr/local/lib/hermes-agent/tools/mcp_tool_registration.py; ls /root/mcp_tool_registration.py.bak-20260924"
ssh chaim "ls -l /private/operator-provided-model.json /private/bb-qa/testing/run_live_tests.py /private/bb-qa/bb_webhook/logging_utils.py /tmp/buttonsbebe-qa-venv/bin/python"
```

Expect: hint count 2 or more, backup present, key file `-rw-------`, all
paths present. If the venv is missing, rebuild it (idempotent):

```sh
ssh chaim "/root/.local/bin/uv venv /tmp/buttonsbebe-qa-venv --python 3.12 && /root/.local/bin/uv pip sync --python /tmp/buttonsbebe-qa-venv/bin/python --require-hashes /private/bb-qa/testing/requirements-qa.lock"
```

Then run the offline boundary tests (must say OK):

```sh
ssh chaim "cd /private/bb-qa && DEMO_MODE=1 /tmp/buttonsbebe-qa-venv/bin/python -m unittest discover -s testing -p test_qa_harness.py 2>&1 | tail -n 3"
```

## Phase 1: one smoke (must be grounded before sharding)

```sh
ssh chaim "cd /private/bb-qa && DEMO_MODE=1 /tmp/buttonsbebe-qa-venv/bin/python testing/run_live_tests.py --hermes /usr/local/lib/hermes-agent/venv/bin/hermes --hermes-python /usr/local/lib/hermes-agent/venv/bin/python --hermes-source /usr/local/lib/hermes-agent --model-config /private/operator-provided-model.json --output /private/qa-s3 --limit 1 --kb-mode policies-only"
```

Use a fresh output name if `/private/qa-s3` already exists. Confirm in
`results.json`: `process_returncode` 0, `authenticated_verdict` true, and
`tool_calls` NON-empty (ticket + KB + order reads). Empty `tool_calls`
means the brain fix regressed: stop everything and report.

## Phase 2: four shards in parallel (one subagent each)

Each shard is one serial run. Ports must not overlap. Shard map (12 each):

- P1: base-port 18877, out `/private/qa-p1`, ids R01-R12
- P2: base-port 19277, out `/private/qa-p2`, ids R13-R22,S01,S02
- P3: base-port 19677, out `/private/qa-p3`, ids S03-S12,E01,E02
- P4: base-port 20077, out `/private/qa-p4`, ids E03-E14

Command per shard (example P1):

```sh
ssh chaim "cd /private/bb-qa && DEMO_MODE=1 /tmp/buttonsbebe-qa-venv/bin/python testing/run_live_tests.py --hermes /usr/local/lib/hermes-agent/venv/bin/hermes --hermes-python /usr/local/lib/hermes-agent/venv/bin/python --hermes-source /usr/local/lib/hermes-agent --model-config /private/operator-provided-model.json --output /private/qa-p1 --ids R01,R02,R03,R04,R05,R06,R07,R08,R09,R10,R11,R12 --kb-mode policies-only --base-port 18877"
```

Expect about 60 to 90 seconds per scenario, so about 15 to 20 minutes per
shard. Success prints `Captured 12 synthetic cases`. Each run writes
`preflight.json` (gate proof), `results.json` (grades input), and
`tool-audit.jsonl` (what the model actually read).

## Phase 3: aggregate and report

Confirm 48 total results across the four `results.json` files with unique
ids. Then deliver a per-case table: id, verdict true/false, model called,
priority and action, draft present and clean, reads performed. Flag every
case where the draft does not match the scenario `expect` text, every
sensitive case missing its prefix, and every financial action or promise.
Do not declare the release passed or failed. The owner grades.

## Notes from the previous session

- Proven reference: scenario R01 (WISMO onesie) returns priority normal,
  action drafted, verdict true, with ticket, message, KB, customer, and
  return reads in `tool_calls`.
- Output dirs contain a model credential copy: never attach, commit, or
  publish them whole. Share reviewed extracts only. Shred them after the
  owner finishes review.
- Full fault history: `testing/QA-HARNESS-HERMES-SKEW.md`.
