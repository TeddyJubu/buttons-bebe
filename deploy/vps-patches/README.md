# VPS patch package — Buttons Bebe AI support agent

**Folder:** `deploy/vps-patches/`  ·  **Sprint 2, Stream V**  ·  **Branch:** `Fable_buttonsbebe`
**Target box:** VPS `srv1766050` (2.25.137.77), everything under `/root/Buttonsbebe Agent/`.

This is a **documentation + code package**. It describes three small, safety-focused patches for
the **live** processor and gives an operator the exact steps to apply them. It changes nothing on
its own — applying it is a deliberate, manual act.

Read this whole file before touching the server. It is written for whoever runs the deploy; every
command is meant to be copy-pasted as-is.

---

## 0. STOP — nothing here goes live until GATE 1 passes

> ### ⚠️ DO NOT APPLY ANY FILE IN THIS FOLDER UNTIL GATE 1 IS GREEN.
>
> Nothing in `deploy/vps-patches/` is ever applied automatically. There is no installer, no CI
> step, no cron that copies these files onto the box. A human applies them, by hand, **only after**
> the gate below is green.

**GATE 1 (from `TESTING-READINESS.md` §3) — all three must be true:**

1. **Fable test suite green** — 182 checks:
   ```bash
   ./fable/scripts/test.sh
   ```
2. **Draft-cleaner tests green** — the real QA leak cases (#01 / #04 / #10 / #19):
   ```bash
   pytest fable/tests/unit/test_draft_cleaner.py -v
   ```
3. **Live 48-scenario run shows zero regressions vs. the sim baseline** — run the
   `testing/` harness against the deployed VPS model (glm-5.2), score with the A–E rubric, and diff
   against `results-sim.json`. Pass means: **no case worse than baseline, all 12 sensitive
   cases still escalate, empty/spam → NO_DRAFT, no invented prices or sizes.** See
   `testing/HOW-TO-RUN.md`.

If any one of those is red, **do not deploy.** Fix first, re-run the gate, then deploy.

**Also true always:** the AI never auto-sends. These patches keep that invariant — the only write
to the outside world stays a **staff-only internal note** in Gorgias (`CLAUDE.md` §2, §4). Deploy in
a quiet hour; the heartbeat (below) ships first so we'd know immediately if the processor stops.

---

## 1. What is in this folder

| File | What it is | Replaces / adds |
|---|---|---|
| `classifier.py` | Deterministic risk gate (keyword + pattern rules, stdlib only). | Replaces the **stub** `processor/classifier.py` that returns NORMAL for everything. |
| `draft_cleaner.py` | Strips model self-talk, collapses duplicated drafts, returns NO_DRAFT for empty/spam. | New module; wired into `processor/hermes_runner.py`. |
| `heartbeat.sh` | Liveness check for the processor; WhatsApp alert if it goes down or quiet. | New; runs from a systemd timer. |
| `README.md` | This document. | — |

`classifier.py` and `draft_cleaner.py` are **stdlib-only** (only `re` and `dataclasses`), so they
drop straight into the processor's Python environment with **no new dependencies to install**.

> **Provenance note (important for the reader).** The current live processor code
> (`processor/hermes_runner.py`, `processor/classifier.py`, `processor/gorgias_writer.py`) is **not
> in this repo** — it lives only on the VPS. The backup folder `_VPS-FULL-BACKUP-20260706/` is a
> snapshot of the **older, retired** design (the `/root/gorgias-webhook` pipeline) and does **not**
> contain those files. So the wiring instructions in §3 are written against the architecture map in
> `CLAUDE.md` §4–§5. **Before editing, open the real files on the box and confirm the function and
> variable names match what's described here; adjust the import/spelling if the live code differs.**

---

## 2. File-by-file

### 2.1 `classifier.py` — the deterministic safety net

**What it replaces.** The live `processor/classifier.py` is a **stub**: it returns NORMAL for every
ticket, so today the *only* thing deciding whether a ticket is sensitive is the LLM (Hermes). If the
model has a bad day, nothing catches it (`IMPROVEMENT-PLAN.md` safety item #1; `CLAUDE.md` §8). This
file is a dumb-but-reliable code check that runs **before and after** the model so a refund /
chargeback / damaged-item ticket can never slip through to an auto-draft.

**Where it comes from.** It is a port of Fable's shared, unit-tested risk engine
`fable/server/app/risk.py` (Sprint 2 item V1), re-expressed in the processor's documented
**IMMEDIATE / HIGH / NORMAL** vocabulary and extended with the owner's Core-Rule sensitive
categories from `CLAUDE.md` §2 (wrong / damaged / missing items, cancellations). A parity test
(`TESTING-READINESS.md` §2, T5) keeps the two copies in sync — run it if you change either.

**Public API (the names the processor calls):**

```python
classify(text, subject=None) -> Classification
classify_priority(text, subject=None) -> "IMMEDIATE" | "HIGH" | "NORMAL"
is_sensitive(text, subject=None) -> bool
```

`Classification` is a dataclass with these fields:

| Field | Meaning |
|---|---|
| `priority` | `"IMMEDIATE"` (ping the owner) · `"HIGH"` (escalate, no ping) · `"NORMAL"` (benign). |
| `sensitive` | `True` for the always-escalate set. |
| `escalate` | `True` → route to a human, never auto-draft. |
| `auto_draft_allowed` | `True` **only** when `(not sensitive) and (not escalate)`. |
| `category` | best-guess topic, e.g. `"refund"`, `"chargeback"`, `"damaged_item"`. |
| `reason` / `reasons` / `matched` | human-readable audit trail: why it fired and the phrases that tripped it. |

The gate can **only escalate, never clear a flag** — over-flagging is deliberate and safe (a false
escalation costs a human one glance; a wrong auto-draft on a refund is customer-facing and
expensive). Empty / unintelligible messages return `HIGH` + `escalate` (not draftable), but are
**not** marked `sensitive`.

**How the processor imports it.** Once this file is installed as `processor/classifier.py`, the
orchestrator imports it the same way it imports the stub today. The exact import line depends on how
the processor package is laid out on the box (e.g. `from classifier import classify` or
`from processor.classifier import classify`). **Confirm the current stub's import path and function
signature, then keep it identical** so this is a true drop-in:

```bash
# See how the stub is imported / called today, so the replacement matches:
grep -rn "classifier" "/root/Buttonsbebe Agent/processor/"
grep -rn "def classify" "/root/Buttonsbebe Agent/processor/classifier.py"
```

The ported module keeps a name-compatible `classify(text, subject=None)` entry and adds the
`classify_priority()` / `is_sensitive()` convenience wrappers, all returning the same
IMMEDIATE / HIGH / NORMAL words the processor already documents. If the live stub's `classify()`
takes a different argument shape (for example a ticket-context object), adapt the call site or add a
thin wrapper rather than changing the ported logic.

**Self-test (no network, no LLM):**
```bash
python3 "/root/Buttonsbebe Agent/deploy/vps-patches/classifier.py"
# → prints "CLASSIFIER SELF-TEST OK (N checks passed)"
```

### 2.2 `draft_cleaner.py` — clean the draft, or produce none

**What it does.** Two stable functions:

```python
clean_draft(text: str) -> CleanResult      # clean an AI-produced draft
should_draft(message: str) -> ShouldDraft   # gate on the CUSTOMER message
```

- `clean_draft()` runs on the **AI draft**. It (1) cuts trailing model self-commentary from the
  first self-talk marker line on (e.g. "The response above was complete…" — QA #01/#04/#10), and
  (2) collapses a draft that is the same body repeated 2× or 3×. Both passes are conservative: a
  normal reply passes through **unchanged**. `CleanResult` has `.text`, `.no_draft` (True when
  nothing usable is left), and `.reasons`.
- `should_draft()` runs on the **customer message** and returns `ok=False` when there is nothing to
  answer — empty, whitespace, a bare "thanks", a lone emoji, punctuation only (QA #19). The pipeline
  must then create **no draft**.

**Where it gets wired in.** In `processor/hermes_runner.py` (the per-ticket flow) — see §3 for the
exact diff. `draft_cleaner.py` is the **one** cleaner module both tracks use: Fable imports it
directly (`fable/server/app/draft_cleaner.py`, the source of truth), and this folder ships a
**verbatim copy** for the live processor. Stdlib-only, so it needs no dependencies. **Do not edit
the copy here** — if the cleaner changes, re-copy the whole file from Fable so the two never drift.

### 2.3 `heartbeat.sh` — tell us if the processor dies

**What it monitors.** The `buttonsbebe-processor` systemd unit. Every few minutes (driven by a
timer) it checks two things:

1. `systemctl is-active buttonsbebe-processor` is `active`, and
2. the unit has produced **some** journal output in the last `PROCESSOR_STALE_MINUTES` (default 10) —
   i.e. it isn't silently hung.

If either check fails, it POSTs one short alert and remembers it did (state file
`/tmp/buttonsbebe-heartbeat.state`) so it does not spam. When the processor recovers, it sends a
single "back up" message and clears the state.

**Alert path.** The alert goes to the owner's WhatsApp via the **whatsapp-connect** service on
**port 8085** (`CLAUDE.md` §5). The script POSTs `{"text": "..."}` to `WHATSAPP_SEND_URL`. If that
variable is not set but a `WA_TOKEN` is present, it builds the URL as
`http://127.0.0.1:${WA_PORT:-8085}/connect-whatsapp/${WA_TOKEN}/send`. **Safety:** the script only
ever *reads* systemd/journal state and best-effort POSTs one message; if `curl`, `systemctl`, or the
env vars are missing it logs the reason and exits 0 — it can never take anything else down.

**Suggested systemd units (copy-paste).** Install the script, then create a service + timer.

Install:
```bash
install -m 755 "/root/Buttonsbebe Agent/deploy/vps-patches/heartbeat.sh" \
               "/root/Buttonsbebe Agent/processor/heartbeat.sh"
```

`/etc/systemd/system/buttonsbebe-heartbeat.service`:
```ini
[Unit]
Description=Buttons Bebe processor heartbeat (alerts if the support processor goes down)
After=network.target

[Service]
Type=oneshot
# Reuse the processor's env so WHATSAPP_SEND_URL / WA_TOKEN are available:
EnvironmentFile=-/root/Buttonsbebe Agent/webhook/.env
ExecStart=/root/Buttonsbebe Agent/processor/heartbeat.sh
# Never let a heartbeat failure look like a real failure:
SuccessExitStatus=0
```

`/etc/systemd/system/buttonsbebe-heartbeat.timer`:
```ini
[Unit]
Description=Run the Buttons Bebe processor heartbeat every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
Unit=buttonsbebe-heartbeat.service

[Install]
WantedBy=timers.target
```

Enable + verify:
```bash
systemctl daemon-reload
systemctl enable --now buttonsbebe-heartbeat.timer
systemctl list-timers | grep buttonsbebe-heartbeat
journalctl -u buttonsbebe-heartbeat -n 20 --no-pager
```

> The stale-output check assumes the processor logs at least once per few minutes. If it can be
> idle for long stretches with no journal line, either add a one-line per-loop heartbeat log to the
> processor, or raise `PROCESSOR_STALE_MINUTES` in the service `EnvironmentFile` to avoid false
> "down" alerts during genuinely quiet periods.

---

## 3. `hermes_runner.py` patch notes (V2 + V3)

> These are **diff-style instructions**, not a literal patch, because the live
> `processor/hermes_runner.py` is not in this repo (see the provenance note in §1). Open the real
> file, find the two spots described, and make the minimal edits below. Keep everything else the
> same.

### 3.1 Wire in the draft cleaner (V2)

**Goal:** the customer message is gated *before* we bother drafting, and the AI draft passes through
the cleaner *before* `gorgias_writer.py` posts the internal note. An empty result → **skip the
note** and log `NO_DRAFT`.

**Top of the file — add the import:**
```python
from draft_cleaner import clean_draft, should_draft   # ship-copy from deploy/vps-patches/
```
(Match the existing import style in the file — package-relative if the others are.)

**Before running Hermes for a ticket — gate the customer message:**
```python
# --- NEW: nothing to answer? don't draft. (QA #19) ---
gate = should_draft(customer_message)      # customer_message = latest customer text
if not gate.ok:
    log.info("NO_DRAFT ticket=%s reason=%s", ticket_id, gate.reason)
    return   # or: mark handled; do NOT run Hermes, do NOT post a note
```

**After Hermes returns its draft, before the write-back — clean it:**
```python
# --- NEW: strip self-talk / de-dupe before anything is posted. (QA #01/#04/#10) ---
cleaned = clean_draft(draft_text)          # draft_text = Hermes' raw output
if cleaned.no_draft:
    log.info("NO_DRAFT ticket=%s reason=%s", ticket_id, "; ".join(cleaned.reasons))
    return   # skip the internal note entirely
draft_text = cleaned.text                  # post the CLEANED text
```

**Then post as today** — `processor/gorgias_writer.py` posts `draft_text` as the internal note
(`POST /api/tickets/{id}/messages`, `channel=internal`). That remains the **only** write in the
system. Net effect: the cleaner sits *between* Hermes and `gorgias_writer`; nothing else changes.

> **Optional but recommended (belongs with V1):** also call `classify()` on the customer message and
> attach `classification.priority` / `classification.reason` to the note (or flag the ticket
> sensitive) so the human sees the deterministic risk verdict alongside the draft. This is the
> "runs before and after the model" belt-and-suspenders from `IMPROVEMENT-PLAN.md` #1. It does not
> change the write path.

### 3.2 Restrict the Hermes toolset (V3)

**Today** the processor launches Hermes with a bare `--yolo`, which auto-approves **any** tool call
with no confirmation (`CLAUDE.md` §11; `DEV-ISSUES.md` #8). Safe now because the only write is a
staff-only note — but one future tool away from trouble.

**Change:** bound Hermes to an **explicit allow-list** of just the three read-only MCP tools it
actually needs. `IMPROVEMENT-PLAN.md` #2 and `DEV-ISSUES.md` #8 both name the `-t` flag for this.

**Before:**
```python
subprocess.run([hermes, "--yolo", "-z", prompt], ...)
```
**After (intended):**
```python
subprocess.run(
    [hermes, "--yolo",
     "-t", "buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias",   # allow ONLY these three
     "-z", prompt],
    ...
)
```

Notes:
- Keeping `--yolo` is fine once the toolset is bounded — auto-approval now only applies to the three
  read-only tools. (Alternatively drop `--yolo` too if you want a hard confirm; that would require a
  non-interactive approval path, so the allow-list is the lower-risk change.)
- **Confirm the exact flag on the box first** — the plans call it `-t`; verify with
  `hermes --help` and `hermes mcp list` before editing, and use whatever the installed Hermes
  version actually accepts (a flag vs. an `allowed_tools` field in `~/.hermes/config.yaml`).
- After the change, re-run one ticket end-to-end and confirm all three tools still connect
  (`hermes mcp test buttonsbebe_kb` etc.) and the note still posts.

---

## 4. Env consolidation plan (V4)

**Problem.** There are two `.env` files and they have drifted before — someone edited one while the
code read the other (a real ~45-minute detour; `INCONSISTENCIES.md` L1). Consolidate to **one source
of truth.**

### 4.1 Where keys live today (`CLAUDE.md` §7)

| File | Keys it holds | Who reads it |
|---|---|---|
| **MAIN** `/root/Buttonsbebe Agent/.env` | `GORGIAS_*`, `SHOPIFY_SHOP`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`, `REDO_API_KEY`, `REDO_STORE_ID` | the 3 MCP tool modules |
| `/root/Buttonsbebe Agent/webhook/.env` | `GORGIAS_*`, `WEBHOOK_SECRET`, `WEBHOOK_*`, `SHOPIFY_*`, `LOG_*` | the webhook app + processor (`processor/config.py`) |

- **Duplicated across both:** the `GORGIAS_*` keys (base URL / username / API key). Kept in sync by
  hand today — this is the drift risk.
- **Only in MAIN:** `REDO_API_KEY` / `REDO_STORE_ID`. The processor reaches Redo *through* the
  `buttonsbebe_redo` MCP tool (which reads MAIN), so it doesn't need Redo in its own `.env`.
- **Dead field:** `webhook/config.py` still reads a static **`SHOPIFY_ADMIN_API_TOKEN`**, which is
  **empty** in `webhook/.env` (`INCONSISTENCIES.md` H2). The live Shopify path uses the
  client-credentials grant (`SHOPIFY_CLIENT_ID` + `SHOPIFY_CLIENT_SECRET`), not a static token, and
  the webhook doesn't call Shopify directly today. So `SHOPIFY_ADMIN_API_TOKEN` is vestigial —
  **remove it** (both the var and the line in `webhook/config.py` that reads it).
- **Watch out (`INCONSISTENCIES.md` H1):** `webhook/.env` has `SHOPIFY_SHOP=buttonsbebe` (wrong — no
  hyphen), while MAIN has the correct `SHOPIFY_SHOP=buttons-bebe.myshopify.com`. When you merge,
  keep the **correct** MAIN value. (The Gorgias subdomain really is `buttonsbebe` with no hyphen —
  don't "fix" that one.)

> **Reference caution — the only `.env` we can see is the OLD one.** The sample `.env` in
> `_VPS-FULL-BACKUP-20260706/` predates the rebuild and uses **different variable names**
> (`SHOPIFY_STORE`, `SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`, `REDO_API_SECRET`,
> `WEBHOOK_SECRET_TOKEN`, Telegram tokens, a Postgres password) — that is the retired design, not
> today's. Treat `CLAUDE.md` §7 as the source of truth for var names, and **confirm the actual names
> on the live box** (`grep -h '^[A-Z]' "/root/Buttonsbebe Agent/.env" "/root/Buttonsbebe Agent/webhook/.env"`)
> before merging.

### 4.2 Step-by-step consolidation

Do this in a quiet hour. **Back up first.**

```bash
cd "/root/Buttonsbebe Agent"

# 1. Timestamped backups of BOTH files (rollback safety).
cp .env          ".env.bak-$(date -u +%Y%m%d-%H%M%S)"
cp webhook/.env  "webhook/.env.bak-$(date -u +%Y%m%d-%H%M%S)"

# 2. See every variable currently defined in each file (spot duplicates/conflicts).
grep -hn '^[A-Za-z_][A-Za-z0-9_]*=' .env webhook/.env | sort

# 3. Edit MAIN (.env) to be the single source of truth: add the webhook-only keys
#    (WEBHOOK_SECRET, WEBHOOK_*, LOG_*, any SHOPIFY_* the webhook truly needs) into it.
#    Keep the CORRECT SHOPIFY_SHOP (buttons-bebe.myshopify.com). Do NOT copy the dead
#    SHOPIFY_ADMIN_API_TOKEN. Keep a single GORGIAS_* block (drop the duplicate).
nano .env

# 4. Point the webhook/processor at the merged file instead of its own copy.
#    Simplest + reversible: replace webhook/.env with a symlink to MAIN.
mv webhook/.env "webhook/.env.premerge-$(date -u +%Y%m%d-%H%M%S)"
ln -s "/root/Buttonsbebe Agent/.env" "webhook/.env"

# 5. Remove the dead token from the code that reads it.
grep -rn "SHOPIFY_ADMIN_API_TOKEN" webhook/
#   → delete the line in webhook/config.py that reads it (and the var if present).

# 6. Restart the services that read env, then verify (see §6).
systemctl restart buttonsbebe-webhook buttonsbebe-processor \
                  buttonsbebe-kb-mcp buttonsbebe-redo-mcp buttonsbebe-gorgias-mcp
```

> If `processor/config.py` or `webhook/config.py` loads a **hard-coded path** to `webhook/.env`, the
> symlink in step 4 makes both files resolve to MAIN with no code change. If instead the app loads
> env by directory (e.g. `load_dotenv()` from the app's own folder), keep the symlink — it's the
> lowest-risk option because the file still "exists" where the code expects it.

### 4.3 Rollback

If anything misbehaves after restart:
```bash
cd "/root/Buttonsbebe Agent"
rm -f webhook/.env                                   # remove the symlink
cp "webhook/.env.premerge-<timestamp>" webhook/.env  # restore the original file
cp ".env.bak-<timestamp>" .env                       # restore MAIN if you edited it
systemctl restart buttonsbebe-webhook buttonsbebe-processor \
                  buttonsbebe-kb-mcp buttonsbebe-redo-mcp buttonsbebe-gorgias-mcp
```
You are back to the exact two-file state you started from. Then verify (§6).

---

## 5. Secret hygiene checklist (V7)

Some secrets have been pasted into chats, notes, and backups over the life of this project. Treat
any secret that has ever left a `.env` file as **compromised** and rotate it.

- [ ] **Rotate anything ever pasted into a chat, note, ticket, or doc.** In particular, the backup
      file `_VPS-FULL-BACKUP-20260706/fs/root-projects/root/.env` (checked into this repo) contains
      **live-looking plaintext secrets** — an OpenRouter API key, Shopify API key/secret, a Redo API
      secret, Telegram bot tokens, a webhook secret token, and a Postgres password. Rotate every one
      of those at its provider, even the ones the current design no longer uses, and scrub them from
      the repo history if this repo is ever shared.
- [ ] **Rotate the current live credentials** the agent actually uses: `GORGIAS_API_KEY`,
      `SHOPIFY_CLIENT_SECRET`, `REDO_API_KEY`, `WEBHOOK_SECRET`, and the WhatsApp `WA_TOKEN`. Update
      the merged `.env` (§4), then restart the services (§6) and confirm each tool still connects.
- [ ] **Lock down file permissions** on both env files (owner read/write only):
      ```bash
      chmod 600 "/root/Buttonsbebe Agent/.env"
      chmod 600 "/root/Buttonsbebe Agent/webhook/.env"   # skip if it's now a symlink to MAIN
      ```
- [ ] **Verify with `ls -l`** — the mode must read `-rw-------` (owner only, no group/other):
      ```bash
      ls -l "/root/Buttonsbebe Agent/.env" "/root/Buttonsbebe Agent/webhook/.env"
      ```
- [ ] **Confirm no secrets in the systemd units** — the units reference the env via
      `EnvironmentFile=`; they should never contain a secret value inline.

---

## 6. Verify after applying (mirror of `CLAUDE.md` §10)

Run these on the VPS after any of the changes above. All should be healthy.

```bash
# --- MCP tools: all three registered, enabled, and connecting ---
hermes mcp list
hermes mcp test buttonsbebe_kb          # then buttonsbebe_redo, buttonsbebe_gorgias
                                        # → each: "Connected, N tools"

# --- Services: everything active ---
systemctl status buttonsbebe-processor buttonsbebe-webhook \
                 buttonsbebe-kb-mcp buttonsbebe-redo-mcp buttonsbebe-gorgias-mcp \
                 buttonsbebe-whatsapp-connect

# --- Processor logs: recent activity, no tracebacks ---
journalctl -u buttonsbebe-processor -n 50 --no-pager

# --- Job queue: how many jobs are queued / done / failed ---
sqlite3 "/root/Buttonsbebe Agent/webhook/data/webhook.db" \
        "select status, count(*) from jobs group by status;"

# --- Patch-specific checks ---
python3 "/root/Buttonsbebe Agent/processor/classifier.py"       # → CLASSIFIER SELF-TEST OK
"/root/Buttonsbebe Agent/processor/heartbeat.sh"; echo "exit=$?"  # → exit=0, healthy/down line logged
systemctl list-timers | grep buttonsbebe-heartbeat              # heartbeat timer scheduled

# --- KB still answers (unchanged by these patches, but good smoke test) ---
cd "/root/Buttonsbebe Agent/KB" && ./search.sh "do you ship to canada"
```

**What "good" looks like:** three MCP tools connected; all services `active (running)`; recent
processor log lines with no new tracebacks; the job queue showing jobs completing (not piling up in
`failed`); the classifier self-test printing OK; and the heartbeat exiting 0. If the heartbeat says
"PROCESSOR DOWN" while the processor is genuinely fine, raise `PROCESSOR_STALE_MINUTES` (§2.3).

---

### After deploy

Watch `journalctl -u buttonsbebe-processor -f` for the next few tickets. Confirm you see clean
drafts posted as internal notes, `NO_DRAFT` logged for empty/thanks-only messages, and sensitive
tickets flagged (not auto-drafted). Roll back per §4.3 (env) or by restoring the previous
`processor/classifier.py` / `hermes_runner.py` from your backup if anything looks wrong — and
remember, none of this ever sends a customer message on its own.
