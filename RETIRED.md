# RETIRED — do not implement from these paths

Live system: `AGENTS.md` (sole root source of truth; `CLAUDE.md` was merged
into it and removed). The paths below are retired or
superseded. Those still on disk stay in the repo for history only — no live
code imports them, the release gate does not syntax-check them, and the
deploy manifest does not ship them. Rows marked **Deleted** no longer exist
in the tree at all; their history is in git only. Do not "fix" any of them
back to life or copy patterns from them without revisiting the safety
model.

| Path | Status |
|---|---|
| `archive/gorgias-webhook/` (~1.4 MB) | Retired pre-rebuild system (Supermemory/ChromaDB, own classifier/draft engine). Superseded by `processor/` + `webhook/`. **Moved to `archive/` 2026-09** (no hard-delete). |
| `archive/teddy/` | Retired prototype tree. Background only. **Moved to `archive/` 2026-09**. |
| `fable/` (+ branch `Fable_buttonsbebe`) | Track B standalone prototype, quarantined. Not planned work. (Already absent from tree; branch only.) |
| `archive/kb-editor/` (incl. `vendor/`) | Retired editor; live KB editing is `kb-admin/` (:8087). **Moved to `archive/` 2026-09**. |
| `archive/qa_v3/` | Retired QA fixtures/harness. Live gate is `testing/` (48 scenarios). **Moved to `archive/` 2026-09**. |
| `archive/qa-run/` | One-off Hermes QA artifact (2026-07). **Moved to `archive/` 2026-09**. |
| `dashboard/` | Older console snapshot without Notice Board. **Deleted 2026-09-17** (Wave 2); history in git. Live console source is `console-src/index.html`; isolated preview is `console-src/inbox/`. |
| `webhook/src/bb_webhook/review_console.html` + `dashboard.html` | In-package console snapshots served by nothing; shipped in the wheel. **Deleted 2026-09-17** (Wave 2); history in git. |
| `deploy/review_console.html` | Runbook-history copy of a file nothing served. **Deleted 2026-09-17** (Wave 2); history in git. |
| `HANDOVER/` | Onboarding docs dated 2026-07-13, before the Fable port. Claims like "webhook/processor source is not in the repo" are outdated. Trust order: `AGENTS.md` → `HANDOVER/` → dated plans. Superseded audits: `archive/INCONSISTENCIES.md`, `archive/DEV-ISSUES.md`. Stale former root README: `archive/README-stale.md`. |
| `processor/gorgias_writer.py` | Retired write-back stub (import raised unless `BUTTONSBEBE_ALLOW_GORGIAS_WRITER=1`). **Deleted 2026-09-17** (Wave 4, owner decision); history in git. Live Gorgias writes are human-gated via `webhook/src/bb_webhook/gorgias_client.py`. |
| `processor/feedback_collector.py` | Superseded poller stub (fail-closed unless `FEEDBACK_LEGACY_OPT_IN=1`). **Deleted 2026-09-17** (Wave 4, owner decision); history in git. Live learning path is `webhook/.../learning.py`; the retained `feedback/collector.py` remains for bounded rollback tests. |
| `processor/classifier_shim.py` + `tools/compare_classifier.py` | T-FIX-3 parity apparatus: 13-line re-export shim + 10,000-sample one-time-proof harness. **Deleted 2026-09-17** (Wave 4, owner decision, report 04-5) after 231 green deploys; history in git (pre-split classifier at parent of `ba138d5`). Canonical classifier is `processor/classifier/`. |
| `kb/scripts/review_learned.py` + `feedback/review.py` + console `/dashboard/api/review/*` (+ `webhook/test_legacy_review_auth.py`) | Legacy v1 human gate on extinct `ticket-*.md` packets (live writes `lesson-*.md`, nightly auto-promotion). **Deleted 2026-09-17** (Wave 3.10, owner decision "retire all three"); history in git. |
| `archive/deploy/CD-UNBLOCK-2026-09-15.md` | Dated incident record. Live CD-approval procedure: `deploy/PRODUCTION-OPERATOR-RUNBOOK.md`. **Moved under `archive/deploy/` 2026-09**. |
| `archive/deploy/PRODUCTION-READINESS-2026-09-07.md` | Dated point-in-time review. Live procedure: `deploy/PRODUCTION-OPERATOR-RUNBOOK.md`. **Moved under `archive/deploy/` 2026-09**. |
| `archive/deploy/RECOVERED-LIVE-SNAPSHOT.md` | Dated 2026-07-13 recovery evidence. Live procedures: `deploy/PRODUCTION-OPERATOR-RUNBOOK.md`. **Moved under `archive/deploy/` 2026-09**. |

See also `archive/README.md` for the full clutter batch (superseded root docs,
sprint/tasklist markdown, proposal/build-plan, pptx).
