# Porting Fable → main — feature map and tasklist

> **STATUS as of 2026-07-29 — the port has been implemented in this repo.**
>
> | Task | State |
> |---|---|
> | 1 · 48-scenario test harness | ✅ ported — `testing/`, wired into the release gate |
> | 2 · Heartbeat / dead-man's switch | ✅ in repo — `processor/heartbeat.sh` + systemd units; **needs installing on the VPS** (`deploy/HEARTBEAT-INSTALL.md`) |
> | 3 · Draft cleaner | ✅ ported and wired — `processor/draft_cleaner.py` |
> | 4 · Risk-classifier rules | ✅ merged (rules only) into `processor/classifier.py` |
> | 5 · Hermes tool allow-list | ✅ in repo — `build_hermes_command()`; **run `tools/verify_hermes_toolset.sh` on the VPS before deploying** |
> | 6 · One `.env` + rotate secrets | 📄 runbook only — `deploy/ENV-CONSOLIDATION-RUNBOOK.md`. VPS work, not done |
> | 7 · Planning docs | ✅ ported — this file, `IMPROVEMENT-PLAN.md`, `DESIGN-CRITIQUE.md`, `TESTING-READINESS.md`, the competitive brief |
>
> Each task landed on its own branch (`task/1-test-harness` … `task/7-planning-docs`),
> to be reviewed and merged in that order. The original plan follows unchanged
> below — including the "do not bring over" list, which was honoured in full.

---

Created: 2026-07-29
Compares: `main` (the live system on the VPS) vs `Fable_buttonsbebe` (the R&D branch)
Merge base: `d23ea25` · Fable is 6 commits ahead, main is 37 commits ahead

---

## Read this first (plain-language summary)

The Fable branch contains **two completely different kinds of work**, and mixing them up
is the main risk here.

1. **A pile of small, self-contained safety and quality improvements** for the live system
   you run today. These are genuinely missing from `main` and each one can ship on its own.
   **This document plans those.**

2. **An entire second help-desk application** (the `fable/` folder — ~30,000 lines: its own
   server, console, chat widget, four fake API servers, 351 tests). Your own handover docs
   deliberately **quarantined** this (`HANDOVER/07-fable-rebuild.md`,
   `HANDOVER/10-fable-branch-quarantine.md`). **This document does not plan that.** It stays
   on its branch until you make a separate business decision about it.

There is also a third thing worth knowing: **a few files on Fable are actually older and
worse than what's on `main` now.** Copying them across would be a step backwards. They're
listed in the "Do not bring over" section at the bottom — please don't skip it.

---

## Part 1 — The map: what Fable has that main does not

### 1A · Worth bringing over (this is the tasklist below)

| # | Feature | What it actually is | On main today |
|---|---|---|---|
| F1 | **Draft cleaner** | Code that strips the AI's self-talk ("The response above was complete…"), collapses drafts the model accidentally wrote twice, and refuses to draft anything at all for empty / "thanks" / emoji-only messages | ❌ Nothing. The AI's raw output goes straight to a human |
| F2 | **Heartbeat / dead-man's switch** | A tiny script + timer that pings you on WhatsApp if the ticket processor dies or goes silent for 10 minutes | ❌ Nothing. If the processor dies, nobody finds out until tickets pile up |
| F3 | **48-scenario test harness** | 48 realistic customer messages + an A–E scoring rubric + a saved baseline of results, so you can prove a change didn't make the AI worse | ❌ Nothing. Main's only testing is human-graded live runs |
| F4 | **Extra risk-classifier rules** | ~80 additional trigger phrases main's classifier misses, plus a whole "customer is demanding a manager" category, plus structural anger detection (ALL CAPS, `!!!`) | ⚠️ Partial. Main has a real classifier, but with narrower coverage |
| F5 | **Hermes tool lockdown** | Written instructions for replacing the blanket `--yolo` flag with an explicit list of the 3 tools Hermes is allowed to call | ❌ Not done. `--yolo` is still live and flagged in `archive/DEV-ISSUES.md` #8 |
| F6 | **One `.env` instead of two** | Written instructions for consolidating credentials into a single file, deleting dead variables, and rotating leaked secrets | ❌ Not done. Still listed under "Known limitations" in `CLAUDE.md` |
| F7 | **Planning + analysis docs** | `IMPROVEMENT-PLAN.md`, `DESIGN-CRITIQUE.md`, `SPRINT-2-PLAN.md`, `TESTING-READINESS.md`, `Buttons-Bebe-Competitive-Brief.html` | ❌ Not on main |

### 1B · Deliberately out of scope (the `fable/` prototype)

Listed here only so nothing looks "forgotten". None of this is in the tasklist.

- Standalone FastAPI help desk with its own SQLite inbox (`fable/server/`)
- Pluggable AI "brain" layer — mock / real Anthropic adapter / Hermes stub
- Four wire-accurate emulators: Shopify, Redo, mailbox, Gorgias (+ ~16,000 lines of seed data)
- Its own console UI, embeddable chat widget, and demo store page
- Gorgias-compatible API layer + a Gorgias-export → Fable migration importer
- IMAP/SMTP email adapter skeleton, local keyword KB search
- A 351-test automated suite and a demo seeder

**Recommendation:** leave it on `Fable_buttonsbebe`. If you ever want it, that's its own
project with its own decision, not a merge.

---

## Part 2 — The tasklist

Each task is designed to be done and shipped **completely on its own**, in this order.
Nothing here touches ticket-handling logic until Task 1 and Task 2 give you a safety net.

Every task follows the same shape: **What · Why · Files · Risk · Test · Done means.**

---

### ☐ Task 1 — Bring in the 48-scenario test harness

> **Do this first.** You cannot safely change the live AI until you can measure whether a
> change made it better or worse. This gives you the measuring stick.

- **What:** copy the `testing/` folder from Fable onto `main`.
- **Why:** it holds 48 realistic customer messages (including every sensitive category and
  deliberately tricky ones), an A–E scoring rubric, and a saved "before" scorecard. Your own
  `TESTING-READINESS.md` makes a clean run of this a hard gate before any live change.
- **Files to copy:** `testing/TEST-PLAN.md`, `testing/HOW-TO-RUN.md`, `testing/scenarios.json`,
  `testing/run_live_tests.py`, `testing/results-sim.json`, `testing/results-live.json`,
  `testing/AI-REPLY-JUDGMENT.md`, `testing/FULL-RUN-JUDGMENT.md`, `testing/LIVE-RUN-JUDGMENT.md`,
  `testing/RUN-LIVE-ON-SERVER.md`, `testing/_harness/AGENT-BRIEF.md`
- **Risk:** **none.** These are documents and a standalone script. Nothing in the live pipeline
  imports them.
- **Test:** `python3 -m py_compile testing/run_live_tests.py` and confirm
  `python3 -c "import json;print(len(json.load(open('testing/scenarios.json'))))"` returns 48.
- **Done means:** you can run all 48 scenarios against the live `glm-5.2` model and produce a
  fresh `results.json` that diffs cleanly against `results-sim.json`. **Record that run as your
  "before" baseline — every later task is compared against it.**

---

### ☐ Task 2 — Add the heartbeat alert

> Ship this second. It is pure addition, it cannot break ticket handling, and it means that
> if any *later* task goes wrong you find out in 10 minutes instead of 10 hours.

- **What:** install `heartbeat.sh` plus a systemd service and timer that check the processor
  is alive and not silently hung, and WhatsApp you once if it isn't (and once again when it
  recovers).
- **Why:** right now nothing watches the processor. `IMPROVEMENT-PLAN.md` reliability item #5.
- **Files:** copy `deploy/vps-patches/heartbeat.sh` → `processor/heartbeat.sh`; add
  `deploy/systemd/buttonsbebe-heartbeat.service` and `.timer` (unit contents are in
  `deploy/vps-patches/README.md` §2.3).
- **Adaptation needed:** the Fable script builds its own WhatsApp URL. Main already has
  `processor/whatsapp_notifier.py`, which reads `WHATSAPP_SEND_URL` and sends `WA_SEND_SECRET`
  as a header. **Rewire the script to use main's existing auth path** rather than the Fable
  URL-with-token-in-the-path pattern — main's is stricter and already hardened.
- **Risk:** **low.** The script only reads systemd state and best-effort POSTs one message. It
  exits 0 on any missing dependency so it can never take a service down.
- **Test:** `bash -n processor/heartbeat.sh`, then on the VPS stop the processor and confirm a
  WhatsApp alert arrives within 10 minutes; start it and confirm one "back up" message.
- **Done means:** killing the processor pings your phone. Restarting it clears the alert. No
  repeat spam while it's down.

---

### ☐ Task 3 — Add the draft cleaner

> The single biggest quality win in the whole set.

- **What:** add one new stdlib-only module with two functions —
  `clean_draft(text)` (runs on the AI's draft) and `should_draft(message)` (runs on the
  customer's message) — and wire both into the processor.
- **Why:** it fixes four real, already-documented QA failures — the model appending
  "The response above was complete…" (#01, #10), writing the same reply twice, guessing when it
  shouldn't (#04), and fabricating a reply to a completely empty message (#19).
- **Files:** copy `fable/server/app/draft_cleaner.py` → `processor/draft_cleaner.py` (246 lines,
  uses only `re` and `dataclasses`, so nothing new to install). Copy
  `fable/tests/unit/test_draft_cleaner.py` → `processor/test_draft_cleaner.py` (304 lines).
- **Wiring:** in `processor/hermes_runner.py`, call `should_draft()` on the customer message
  before building the prompt, and run `clean_draft()` on the text that comes out of
  `_extract_draft()` before it reaches `draft_for_console()`. When either says "no draft",
  the pipeline must store **no draft** rather than a fallback one.
- **Risk:** **medium** — this is the first task that changes what a human sees. Both cleaning
  passes are written to be conservative (a normal reply passes through untouched, even if it
  contains the word "complete"), and the test file proves that, but review the wiring carefully.
- **Test:** `pytest processor/test_draft_cleaner.py -v` must be green, then re-run the Task 1
  48-scenario harness and diff against your baseline.
- **Done means:** QA cases #01 / #04 / #10 pass with the self-talk stripped, #19 produces no
  draft at all, and **no previously-good draft got worse** in the 48-run diff.

---

### ☐ Task 4 — Top up the risk classifier (merge, do **not** replace)

> ⚠️ **The most misunderstood task here. Read the warning before starting.**

- **What:** add the trigger phrases and the one extra category that Fable's classifier has and
  main's does not — into main's existing `processor/classifier.py`.
- **⚠️ WARNING — do NOT copy `deploy/vps-patches/classifier.py` over main's file.** That patch
  was written back when main's classifier was still a do-nothing stub. **Main's classifier is
  no longer a stub** — it's 273 real lines wired into main's config and logging. The two files
  are also incompatible: Fable's returns `"IMMEDIATE"/"HIGH"/"NORMAL"` in capitals, main's
  returns lowercase, and main's imports `get_settings` and `log_event` which Fable's does not
  have. **A straight file swap would break the live processor.** This is a rules merge only.
- **What main is genuinely missing (the useful part):**
  - **A "demanding a manager" category** — main has no rule for `speak to a manager`,
    `get me a manager`, `your supervisor`, `i demand`, `worst company`. These are angry-customer
    tickets that should escalate and currently may not.
  - **Structural anger detection** — three or more `!!!` in a row, and ALL-CAPS shouting.
    Main only counts angry *words*.
  - **Wrong-item phrasings with no "wrong" in them** — `not what i ordered`, `instead of the`,
    `but got a`, `different item`. Main's rule requires the literal word "wrong".
  - **Damage words main lacks** — `cracked`, `shattered`, `ripped`, `frayed`, `stained`,
    `fell apart`, `a hole`, `seam ripped`, `zipper is broken`.
  - **Missing/undelivered gaps** — bare `missing`, `didn't come with`, `not included`,
    `left out`; and `hasn't arrived`, `not delivered`, `marked delivered but`, bare `lost`.
  - **Fraud, dispute and legal variants** — `charged back`, `reverse the charge`, `disputing`,
    `never authorized`, `unauthorised` (British spelling), `scammed`, `ripped me off`,
    `stole my money`, `cease and desist`, `file a complaint`.
- **Also worth taking:** Fable's classifier ships a built-in self-test
  (`python3 classifier.py` prints "SELF-TEST OK (N checks passed)") and returns a `matched`
  audit trail showing which phrase fired. Both are cheap to add and make debugging obvious.
- **Also copy:** `fable/tests/unit/test_risk_parity.py` (31 tests) and
  `fable/tests/unit/test_risk.py` (90 lines) — adapt them to main's lowercase vocabulary.
- **Risk:** **medium.** More triggers means more escalations. That is the intended trade —
  a false escalation costs one human glance; a missed refund ticket is customer-facing. But
  watch the escalation rate for a week after.
- **Test:** the adapted parity tests green, plus a full 48-scenario re-run where **all 12
  sensitive cases still escalate** and nothing benign newly escalates.
- **Done means:** a ticket saying "I want to speak to a manager, this is UNACCEPTABLE!!!"
  escalates on the code path alone, even if the model misses it.

---

### ☐ Task 5 — Replace `--yolo` with an explicit tool allow-list

- **What:** stop launching Hermes with the blanket "auto-approve every tool call" flag and pass
  it an explicit `-t` list naming only the three read-only MCP tools it's allowed to use.
- **Why:** `--yolo` is safe *today* only because all three registered tools happen to be
  read-only. It is one future tool away from a real problem. Flagged in three places already:
  `archive/DEV-ISSUES.md` #8, `CLAUDE.md` "Known limitations", `HANDOVER/01-executive-summary.md`.
- **Files:** `processor/hermes_runner.py` (the `hermes --yolo -z "..."` invocation); guidance in
  `deploy/vps-patches/README.md`. Update the `--yolo` references in `CLAUDE.md`, `AGENTS.md`,
  `archive/DEV-ISSUES.md` and `HANDOVER/02-live-architecture.md` in the same commit so the docs stay true.
- **Risk:** **medium-high in one specific way** — if the allow-list is wrong or a tool name is
  misspelled, Hermes silently loses a tool and drafts get worse without any error. Verify with
  `hermes mcp list` first and copy the names exactly.
- **Test:** `hermes mcp list` shows exactly the three expected tools; process one real ticket
  end-to-end and confirm the draft still cites KB content and order data; then a 48-run diff.
- **Done means:** no `--yolo` anywhere in the processor, drafts unchanged in quality, and the
  docs no longer describe a flag that isn't used.

---

### ☐ Task 6 — Consolidate the two `.env` files and rotate secrets

- **What:** merge `/root/Buttonsbebe Agent/.env` and `/root/Buttonsbebe Agent/webhook/.env`
  into one source of truth, delete the dead `SHOPIFY_ADMIN_API_TOKEN`, `chmod 600` everything,
  and rotate any credential that has ever been pasted into a chat or a backup folder.
- **Why:** the split already cost a long debugging detour once (`IMPROVEMENT-PLAN.md` #6), and
  `_VPS-FULL-BACKUP-20260706/` contains plaintext live secrets (OpenRouter, Shopify, Redo,
  Telegram, webhook, Postgres) that `TESTING-READINESS.md`/`CONTINUE-HERE.md` flag as unrotated.
- **Files:** both `.env` files on the VPS, `env.example` / `.env.example` in the repo,
  `processor/config.py`, `webhook/` config loading, and the systemd `EnvironmentFile=` lines.
- **Risk:** **high if rushed** — a missing variable takes services down at restart. Do it in a
  quiet hour, keep a copy of both original files outside the repo first, and restart services
  one at a time.
- **Test:** `systemctl status` green for all seven units; `hermes mcp test buttonsbebe_kb`
  passes; one real ticket processes end-to-end; verify no secret appears in `git log -p`.
- **Done means:** one `.env`, every service reads it, no dead variables, all rotated keys live,
  and `CLAUDE.md` "Known limitations" loses its `.env` bullet.

---

### ☐ Task 7 — Bring the planning docs over

> Zero risk, do it whenever. Left last only because it changes no behaviour.

- **What:** copy `IMPROVEMENT-PLAN.md`, `DESIGN-CRITIQUE.md`, `TESTING-READINESS.md` and
  `Buttons-Bebe-Competitive-Brief.html` onto `main`.
- **Why:** they're the reasoning behind Tasks 1–6, and right now they only exist on a branch
  nobody checks out. `TESTING-READINESS.md` in particular is the gate definition Tasks 1, 3 and 4
  all refer to.
- **Skip:** `SPRINT-2-PLAN.md` and `CONTINUE-HERE.md` — both are finished-sprint logs specific to
  the Fable branch. Copying them onto `main` would read as current work when it isn't.
- **Adaptation:** add a one-line header to each explaining that Tracks A/B refer to the live
  system and the shelved Fable branch, so a new reader isn't confused.
- **Risk:** none.
- **Done means:** the docs are on `main` and cross-linked from `CLAUDE.md`.

---

## Part 3 — Do NOT bring these over

Checked individually. Every item here would make `main` worse.

| Item | Why not |
|---|---|
| `dashboard/index.html` | **Fable's copy is older and less safe.** Main's is from 2026-07-14, Fable's from 07-13. Fable's version re-introduces a **"Post drafts to Gorgias" on/off toggle**, which main deliberately replaced with a locked "Human review is always required" panel. It also reverts main's better wording about masked PII and restricted file permissions. `dashboard/DESIGN-SYSTEM.md` is already byte-identical on both branches, so there is nothing to gain. |
| `.gitignore` | **Fable's is a big regression.** Main ignores `*.db`, `*.log`, `*.jsonl`, `hermes/auth.json`, `hermes/config.yaml`, all the `.lock` files, `whatsapp-connect/auth/`, `*.pem`, `*.key`, `secrets.json`. Fable's drops nearly all of that. Copying it would risk committing credentials. |
| `dashboard/index.html.bak-a11y`, `.bak-before-redesign`, `.bak-icons`, `.bak-lucide` | ~2,281 lines of editor backup files. Git is the backup. |
| `fable/logs/server.log`, `fable/logs/server.pid` | A 91 KB binary log and a stale process ID from someone's local run. |
| `fable/.env.fable` | An environment file. Never commit these, even when they only contain variable names. |
| `deploy/vps-patches/classifier.py` (as a file) | See the Task 4 warning — it targets a stub that no longer exists and would break main's imports. Take the *rules*, not the file. |
| The whole `fable/` folder | Separate business decision. See Part 1B. |

---

## Part 4 — Suggested order and ground rules

```
Task 1 (test harness)  ──►  everything else is measured against its baseline
Task 2 (heartbeat)     ──►  ship before any behaviour change, so failures are visible
        │
        ├─► Task 3 (draft cleaner)      ── independent
        ├─► Task 4 (classifier rules)   ── independent
        ├─► Task 5 (tool allow-list)    ── independent
        └─► Task 6 (.env consolidation) ── do last of the live changes; highest blast radius

Task 7 (docs) ── any time, zero risk
```

**Ground rules for every task:**

1. **One task, one branch, one pull request.** Never combine two of these.
2. **Re-run the 48 scenarios after each one** and diff against the baseline from Task 1. Any case
   that got worse blocks the merge — fix it, don't note it for later.
3. **The safety invariant never moves:** the AI drafts, a human clicks send. Nothing in this
   tasklist changes that, and any change that would is out of scope by definition.
4. **Main's CI already runs a release gate** (`.github/workflows/ci.yml` → `tools/verify_release.sh`).
   Add each new test file to it so it can't silently rot.
5. **Deploy live changes in a quiet hour**, one service restart at a time, with the heartbeat
   from Task 2 already running.

---

## Appendix — how this map was produced

```bash
git log --oneline main..Fable_buttonsbebe      # 6 commits on Fable
git log --oneline Fable_buttonsbebe..main      # 37 commits on main
git diff --stat main...Fable_buttonsbebe       # 117 files, +35,768 / −36
git diff --name-status main...Fable_buttonsbebe | grep -v '^A'   # only 2 modified files
```

Only two files exist on both branches in different states (`.gitignore` and
`dashboard/index.html`) — and on both of them **main is the newer, safer version**. Everything
else on Fable is a pure addition, which is why each item above can be taken or left independently.
