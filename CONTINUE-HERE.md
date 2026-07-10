STATUS: COMPLETE — verified 2026-07-10 (scheduled run, session 2)

# Sprint 2 implementation — handoff log

If a fresh session is reading this: Sprint 2 (per SPRINT-2-PLAN.md) is DONE on branch
`Fable_buttonsbebe`. All tests below were run personally in the sandbox and are green.
Nothing on the live VPS, real Gorgias, or real Shopify was touched. The AI still never
auto-sends — all safety invariants hold and are now also proven against the real-brain path.

## Stream status

| Stream | What | Status |
|---|---|---|
| Baseline | Run existing `./fable/scripts/test.sh` in sandbox | ✅ DONE (305 pre-existing tests green) |
| V | `fable/server/app/draft_cleaner.py` + tests + `deploy/vps-patches/` (classifier port, heartbeat script, README with hermes_runner patch notes + env consolidation + secret-hygiene checklist) | ✅ DONE |
| B | Real brain adapter `fable/server/app/brains/anthropic.py` (offline-testable via injected httpx transport; falls back to MockBrain without a key) + `kb_search.py` wired into pipeline/context | ✅ DONE |
| C | Console fixes per DESIGN-CRITIQUE.md: P0 (captureDraft on re-render, keyboard on customer cards, stale-ticket banner) + P1 (collapse older messages + sticky draft, undo-send 5s, inline tag input + removal, edited flag, SVG channel icons, type-scale snap — no 13.5px remains) | ✅ DONE |
| R | `fable/emulators/gorgias/` emulator + `fable/server/app/migration.py` importer (dry-run) + email adapter interface (`channels_email.py`) | ✅ DONE |
| T | New tests: `test_draft_cleaner.py` (QA leaks #01/#04/#10/#19), `test_risk_parity.py` (fable risk.py vs VPS classifier port, 31 tests — fixed one real drift: bare "lost" now flags in the port), `test_golden_set.py` (48 scenarios offline, `-k golden`), `test_safety_invariants_anthropic.py` (real brain via MockTransport: no auto-send, sensitive stays flagged, zero network, keyless fallback), console JS check | ✅ DONE |
| FINAL | Full suite green, run personally in sandbox | ✅ DONE |

## Verified results (2026-07-10, session 2 — all run personally in the sandbox)
```
./fable/scripts/test.sh                          → 351 passed  (was 305 at sprint start)
FABLE_BRAIN=anthropic ./fable/scripts/test.sh    → 351 passed  (GATE 2 command; offline, keyless fallback)
node --check fable/console/app.js                → OK
pytest fable/tests -k golden                     → 6 passed
py_compile + bash -n on deploy/vps-patches/*     → OK
```
One pre-existing test was fixed to be brain-agnostic: `test_route_coverage.py::
test_health_reports_brain_and_queue` hard-coded `brain == "mock"`; it now asserts the
health endpoint reports the *configured* brain, so the suite is green in both modes.

## What still needs a human / the VPS (out of sandbox scope, by design)
- **GATE 1 apply:** nothing in `deploy/vps-patches/` is on the VPS yet. Follow
  `deploy/vps-patches/README.md` step by step (it includes hermes_runner wiring,
  `--yolo` → tool allow-list, env consolidation, heartbeat systemd units, verification).
- **T1 live 48-run** (glm-5.2 on the VPS) and **T6 heartbeat live test** — VPS-only.
- **T3 Playwright browser smoke** — needs a browser runtime; static console checks +
  `node --check` covered in-sandbox (`test_console_static.py`, `test_frontend.py`).
- **Secret rotation (V7):** `_VPS-FULL-BACKUP-20260706/` contains plaintext live secrets
  (OpenRouter, Shopify, Redo, Telegram, webhook, Postgres). Rotate them — flagged
  prominently in the patch README.
- Tony's decision #1 (order-change rule) + Chaim's policy confirmations — content lane.

## Log
- 2026-07-10 (session 3) — Independent re-verification, all run personally: `test.sh` → **351 passed**; `FABLE_BRAIN=anthropic test.sh` → **351 passed**; golden → **6 passed**; **E2E live stack (`FABLE_E2E=1`, 4 real services) → 4 passed** (not run in session 2); `node --check` OK; VPS classifier self-test 34 checks OK; console source spot-checks confirmed (19× captureDraft, 0× 13.5px, keyboard-accessible customer cards, undo-send, stale-ticket banner, collapsed threads). Work committed on `Fable_buttonsbebe`. STATUS stays COMPLETE.
- 2026-07-10 04:45 — Session 1: plans written (IMPROVEMENT-PLAN, DESIGN-CRITIQUE, SPRINT-2-PLAN, TESTING-READINESS). Scheduled continuation task `continue-buttonsbebe-sprint2` at 06:27. Launching baseline + agent waves now.
- 2026-07-10 (session 2, scheduled) — Found streams V/B/C/R largely built but unlogged; suite at 305 green after installing sandbox deps. Completed the gaps: risk-parity tests (fixed "lost" drift in the VPS classifier port), offline golden-set harness, anthropic-path safety invariants, `deploy/vps-patches/README.md` operator guide + file headers. Fixed the brain-hardcoded health test. Personally verified all commands above green → STATUS: COMPLETE.
