# Gorgias flags + filters — safe rollout

Last updated: 2026-09-15. Safety: read-only enrichment only. Send stays locked (`Activate the send access.`), Shopify stays read-only, `GORGIAS_BRIDGE_ENABLED=0` / `HELPDESK_OUTBOUND_ENABLED=0` unless explicitly activated. Inbox service stays credential-free; only the root exporter touches the canonical webhook DB read-only. No Gorgias REST calls from the inbox.

## Pipeline per flag

Each flag goes webhook-template (if needed) -> parse -> DB -> export_projection -> projection -> UI display -> UI filter, with tests at each layer. One flag at a time. Run `bash tools/verify_release.sh` before any push to `main` (auto-deploys).

## Tasks

- [x] 0. Contract inventory — webhook template currently sends only id/subject/channel/customer/message basics; `parsed_messages.channel` already stored. Gorgias API also offers status/assignee/tags/priority via read-only MCP. Done 2026-09-15, no code change.
- [x] 1. Channel display — retain bounded `channel` (40 chars) in `export_projection.build`, badge in `list.js` row, `test_projection` assertion. Verified: 17 python + 143 JS tests pass, end-to-end export keeps `channel: chat` in detail + summary. Done 2026-09-15.
- [ ] 2. Channel filter — filter observed-history list by channel in UI, fail-closed on unknown values.
- [x] 2. Channel filter — client-side `channelId` in `inbox.js` + channel menu in `list.js` (`channel/selected` topic, `selectChannel()` API). Facets derive from loaded rows; control hides when no channel data; unknown values show an honest empty list. Verified: 148 JS + 17 python tests pass (5 new). Done 2026-09-15.
- [ ] 3. Status display — requires Gorgias HTTP-integration template to send `ticket.status`; parse, store, export, display. Template change is account-side and must be confirmed first.
- [x] 3. Status display — template pasted 2026-09-15. `parse_event` keeps bounded `ticket_status` (30 chars, charset-checked, else None); `parsed_messages.ticket_status` column + startup `init_db` migration (verified idempotent on old-schema DB, rows kept); export uses latest event with `unknown` fallback; list rows badge observed status; notice now reads “Status is as last observed; assignment is unknown.” Verified: 149 JS + 18 inbox-python + retained-content incl. new bounds test + 11 webhook db/intake tests pass. Done 2026-09-15.
- [ ] 4. Status filter — open/closed views in observed-history mode once status is trusted.
- [x] 4. Status filter — client-side `statusId` in `inbox.js` + status menu in `list.js` (`status/selected` topic, `selectStatus()` API), mirroring the channel filter. Facets exclude `unknown`/blank; control hides when nothing known; unknown selections fail closed to an honest empty list; composes with the channel filter; view switches reset both. Verified: 155 JS + 18 python tests pass (6 new). Done 2026-09-15.
- [ ] 5. Assignee display + filter (mine/unassigned) — same template-gated path as status.
- [x] 5. Assignee display + filter — template already sent `assignee`; parser keeps bounded identity (prefers email, then name; accepts `assignee_user` alias; overlong/malformed fail closed to unassigned). `parsed_messages.ticket_assignee` + startup migration; export latest-wins with empty fallback; row badge; assignee menu (`Everyone` + per-person + `Unassigned`, hidden when nothing named); `assignee/selected` topic + `selectAssignee()`; composes with channel/status; literal `"unassigned"` values filter safely. No operator-identity concept, so no “mine” view (dead env var removal stands). Verified: 161 JS + 18 inbox-python + 5 parse + 11 webhook db/intake tests pass (7 new JS). Done 2026-09-15.
- [ ] 6. Tags display + filter — bound count/length, no inference.
- [x] 6. Tags display + filter — parser keeps ≤12 deduped tags (≤40 chars, strict charset; accepts JSON string, plain list, or {name} objects; malformed fails closed to no tags; never inferred from text). `parsed_messages.ticket_tags` (JSON) + same startup migration (verified idempotent on old-schema DB). Export revalidates and emits `tags[]`; rows show ≤3 tag badges + `+N` overflow; tag menu with per-tag counts, `tag/selected` topic + `selectTag()`, composes with channel/status/assignee; unknown tag fails closed; control hides with no tags. Verified: 168 JS + 18 inbox-python + 6 parse + 11 webhook db/intake tests pass (7 new JS, 1 new parse). Done 2026-09-15.
- [x] 7. Priority display — parser keeps bounded `ticket_priority` (≤20 chars, charset alnum/_/-; malformed fails closed to None). `parsed_messages.ticket_priority` + same startup migration. Export emits `gorgiasPriority` separately from AI `priority` in `ticket_results`; they never overwrite each other. List rows badge observed Gorgias priority (`title="Gorgias priority"`); no badge when absent; no invented value. Display only — no priority filter. Verified: 170 JS + 9 projection + 18 webhook (parse/db/intake) tests pass (2 new JS, 1 new parse). Done 2026-09-15. Proper pass 2026-09-15: export revalidates charset (fail closed to None); thread header shows Gorgias priority too (+1 JS thread test).
- [x] 8. Snoozed/spam/trashed handling - badge, never hide, no separate filter. Parser keeps 1/0 ticket_spam (bool-coerced spam), ticket_trashed (valid trashed_datetime or boolean trashed alias), ticket_snoozed (valid snooze_datetime or boolean snoozed alias); malformed fails closed to 0 and the ticket itself is always kept. parsed_messages gains ticket_spam/trashed/snoozed INTEGER plus the same startup migration. Export emits gorgiasSpam/gorgiasTrashed/gorgiasSnoozed latest-wins booleans. List rows plus thread header badge Spam/Trashed/Snoozed; flagged tickets stay visible, including under a matching channel filter. Verified: 174 JS (4 new) plus 18 inbox-python (latest-wins flag asserts) plus 8 parse (1 new flags test) pass; full verify_release.sh gate EXIT 0. Done 2026-09-15.
- [x] 9. Full filter bar QA in browser + `verify_release.sh` gate before push. Verified: 6 new JS tests (bar renders all four controls, all four compose to one ticket, impossible combo fails closed, clearing restores rows, view switch resets all four, flags stay visible under status/assignee/tag); 180 JS inbox tests pass, review-server smoke HTTP 200 with send locked, full verify_release.sh gate EXIT 0. Done 2026-09-15.

- [x] 10. Follow-up from live QA: hide the views funnel when only one view exists. Observed-history mode rendered a duplicate filter icon (views funnel + channel funnel) even though the views menu offers a single scope. The funnel now hides when views.length <= 1, matching the other fail-closed controls; the Inbox scope button stays the single source of truth. Verified: 181 JS inbox tests pass (1 new: funnel hides, scope button + view menu remain), full verify_release.sh gate EXIT 0. Done 2026-09-15.

## Notes

## Deploy record 2026-09-15

- Task 9 (filter-bar QA, 61aab62): 6 new JS composition/reset tests, 180 JS inbox tests green locally, review-server smoke HTTP 200 with send locked, full verify_release.sh gate EXIT 0, pushed; CI verify success; deploy success. Live verified: queue drained (8579 done, 0 failed), webhook + inbox /ready ok (sendAccessEnabled false), Send lock intact, projection fresh (3353 tickets), new QA tests present in deployed bundle.


- Task 5 (assignee, `f444a7d`): gate passed locally, pushed; CI verify
  succeeded after one cancelled hung attempt (rerun clean); deploy success.
  Live verified: `ticket_assignee` + `ticket_status` columns present, queue
  drained (8579 done, 0 failed), webhook + inbox `/ready` ok, Send lock exact,
  projection fresh (3353 tickets), assignee badge/filter/menu code live.

- Task 6 (tags, `70e8380`): gate passed locally, pushed; CI verify success
  (one slow attempt, completed on its own); deploy success. Live verified:
  all three flag columns present, queue drained, `/ready` ok, Send lock exact,
  projection fresh (3353 tickets), tag badge/menu code live.

 - Tasks 7+8 (priority proper + spam/trashed/snoozed, `7fa087c`): gate passed
   locally (EXIT 0, prepared venv interpreters), pushed; CI verify success;
   deploy success. Live verified: `ticket_spam` + `ticket_trashed` +
   `ticket_snoozed` columns present, queue drained (8579 done, 0 failed),
   webhook + inbox `/ready` ok (sendAccessEnabled false), Send lock exact,
   projection fresh (3353 tickets, generated 16:11 UTC), spam/trashed/snoozed
   badges + thread Gorgias-priority code live.

- Gate passed locally (EXIT 0), committed `d8f7f5a`, pushed; CI verify success.
- CD initially refused (exit 78): `deploy/systemd` hash drifted since approval.
  Review found only: helpdesk-inbox `+SHOP_RAIL_PATH` / `-INBOX_OPERATOR_GORGIAS_EMAIL`
  (dead var, zero code refs), shop-rail timer 1min→5min. `.d` secret placeholders
  untouched. Installed 2 units, daemon-reload, verified readys + Send lock.
- Approval pins updated (`deploy/systemd d080ab41…`); caddy already in sync.
- Deploy rerun: success. Live verified: `ticket_status` column present, queue
  drained (8579 done, 0 failed), inbox `/ready` ok, Send lock exact, projection
  fresh (3353 tickets), new badge/filter code live.
- Pre-existing nit (not touched): `systemd-analyze verify` warns the live
  shop-rail *service* file has an invalid env assignment (`Agent/.env` path
  with a space). Harmless (ignored) but worth a cleanup pass later.

- Current projection hardcodes `status: unknown`, `assignee: None`, `statusEvents: []` (`export_projection.build`). Views collapse to single `Observed history` (`inbox.js:73-74`) because status/assignment are unknown.
- `parsed_messages.channel` already exists and is selected in export SQL but dropped in `build()` — hence channel is Task 1.
