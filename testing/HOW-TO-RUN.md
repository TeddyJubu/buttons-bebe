# How to run the test suite (and get replies judged)

You have three files in this `testing/` folder:

- **`TEST-PLAN.md`** — the full plan: what we test, how we score, coverage targets.
- **`AI-REPLY-JUDGMENT.md`** — my scored review of the 20 replies already captured.
- **`scenarios.json`** — 48 ready-to-run test messages covering the intents + every
  sensitive category + tricky/adversarial cases.

## Why I couldn't run the new scenarios myself right now

The AI's brain runs on your server (the VPS). From this session I can't reach that server
(the network to it is blocked here), and the Gorgias/Shopify connectors aren't
authorized in this session. So I judged the 20 replies you already had saved, and prepared
everything so the rest can be run in one go.

## To run all 48 scenarios on the live AI (on the VPS)

`scenarios.json` uses the same shape as your existing `qa-run/results.json`. On the VPS,
loop each scenario's `message` through Hermes the same way the earlier run did, e.g.:

```bash
# on the VPS, in "/root/Buttonsbebe Agent"
# for each scenario: feed .message to a one-shot Hermes run and save the reply
hermes --yolo -z "process ticket: <message text>"
```

Save each reply back into the object as `hermes_output` (and optionally `secs`), producing
a `results.json`. (If helpful, I can write a small script that reads `scenarios.json`,
runs each one, and writes `results.json` — just ask.)

## To get them judged

Put the finished `results.json` in this `testing/` folder (or paste it to me) and say
"judge these." I'll score every reply with the rubric in `TEST-PLAN.md` §3 and update
`AI-REPLY-JUDGMENT.md` with a fresh scorecard and a prioritized fix list.

## Priority fixes already found (from the 20 replies)

1. **Stop the size-guessing** (#04) — highest content risk.
2. **Don't reply to empty/no-content messages** (#19).
3. **Strip AI meta-commentary from the note** (#01, #10) — cosmetic but a human reads it.
