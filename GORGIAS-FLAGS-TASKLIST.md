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
- [ ] 5. Assignee display + filter (mine/unassigned) — same template-gated path as status.
- [ ] 6. Tags display + filter — bound count/length, no inference.
- [ ] 7. Priority display — separate `gorgiasPriority` from AI `priority`; never overwrite AI draft priority.
- [ ] 8. Snoozed/spam/trashed handling — decide hide vs badge, never silently drop.
- [ ] 9. Full filter bar QA in browser + `verify_release.sh` gate before push.

## Notes

- Current projection hardcodes `status: unknown`, `assignee: None`, `statusEvents: []` (`export_projection.build`). Views collapse to single `Observed history` (`inbox.js:73-74`) because status/assignment are unknown.
- `parsed_messages.channel` already exists and is selected in export SQL but dropped in `build()` — hence channel is Task 1.
