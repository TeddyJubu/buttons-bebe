# RETIRED — do not implement from these paths

Live system: `AGENTS.md` (sole root source of truth; `CLAUDE.md` was merged
into it and removed). The paths below are retired or
superseded. They stay in the repo for history only — no live code imports
them, the release gate does not syntax-check them, and the deploy manifest
does not ship them. Do not "fix" them back to life or copy patterns from
them without revisiting the safety model.

| Path | Status |
|---|---|
| `gorgias-webhook/` (~1.4 MB) | Retired pre-rebuild system (Supermemory/ChromaDB, own classifier/draft engine). Superseded by `processor/` + `webhook/`. |
| `teddy/` | Retired prototype tree. Background only. |
| `fable/` (+ branch `Fable_buttonsbebe`) | Track B standalone prototype, quarantined. Not planned work. |
| `kb-editor/` (incl. `vendor/`) | Retired editor; live KB editing is `kb-admin/` (:8087). |
| `qa_v3/` | Retired QA fixtures/harness. Live gate is `testing/` (48 scenarios). |
| `dashboard/` | Older console snapshot without Notice Board. **Deleted 2026-09-17** (Wave 2); history in git. Live console source is `console-src/index.html`; isolated preview is `console-src/inbox/`. |
| `webhook/src/bb_webhook/review_console.html` + `dashboard.html` | In-package console snapshots served by nothing; shipped in the wheel. **Deleted 2026-09-17** (Wave 2); history in git. |
| `deploy/review_console.html` | Runbook-history copy of a file nothing served. **Deleted 2026-09-17** (Wave 2); history in git. |
| `HANDOVER/` | Onboarding docs dated 2026-07-13, before the Fable port. Claims like "webhook/processor source is not in the repo" are outdated. Trust order: `AGENTS.md` → `HANDOVER/` → dated plans. Superseded: `INCONSISTENCIES.md`, `DEV-ISSUES.md`. Stale layout: root `README.md`. |
| `processor/gorgias_writer.py` | Retired, fail-closed (import raises unless `BUTTONSBEBE_ALLOW_GORGIAS_WRITER=1`). Live Gorgias writes are human-gated via `webhook/src/bb_webhook/gorgias_client.py`. |
| `processor/feedback_collector.py` | Superseded poller; rollback only via `FEEDBACK_LEGACY_OPT_IN=1` for a bounded test. Live learning path is `webhook/.../learning.py`. |
| `processor/classifier_shim.py` | Renamed shim; canonical classifier is `processor/classifier/`. Exists for parity-history lookup only. |
| `kb/scripts/review_learned.py` + `feedback/review.py` + console `/dashboard/api/review/*` (+ `webhook/test_legacy_review_auth.py`) | Legacy v1 human gate on extinct `ticket-*.md` packets (live writes `lesson-*.md`, nightly auto-promotion). **Deleted 2026-09-17** (Wave 3.10, owner decision "retire all three"); history in git. |
