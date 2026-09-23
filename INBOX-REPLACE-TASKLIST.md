# Inbox replaces Ticket feed — task list

## A. Ticket-feed removal (console-src/index.html)
- [x] A1. Remove feed-source state: ticketFeedSource init block, setTicketFeedSource, qpFeed/localStorage (L385-391)
- [x] A2. Remove bridge fns: bbFindConsoleTicket, bbRewriteTicket, bbHashText, bbNoteTicket, bbRefuseWrite/bbSendErrorText/bbSendTicket, bbPerformWrite, bbReplyRewrite/Note/Send, __bbInboxBridge message listener (L395-512)
- [x] A3. Remove feedToggleHtml + inboxPreviewHtml (L513-522); ticketsView() returns embedded inbox iframe directly
- [x] A4. Rename pages.tickets label+sub away from "Ticket feed" (L621-622); keep tab key `tickets` (deep-link compat)
- [x] A5. Remove legacyTicketsView(), row(), cleanDraft(), cleanMessage() (L707-771)
- [x] A6. Remove action state+ops: openTicket, curTk, actionStorageKey, hashActionText, actionOperation, rememberAction, actionMessage, submitAction, sendReply, noteReply, checkActionStatus, rewriteDraft, captureAct, act* lets (L772-849)
- [x] A7. Remove bind() wirings for legacy ticket UI: data-f chips, data-feed, inbox-preview-frame hello, row[data-tk], act-send/note/status/edit/rewrite/canceledit/raw (L1129-1140)
- [x] A8. Update user-facing copy mentioning legacy feed: bbNoteTicket errors, bbSendErrorText strings, dry-run hints, "reopen in the Legacy feed"
- [x] A9. Remove dead state vars: tickets[] if unused elsewhere, openTk, filter; check overview()/goTickets()/notifications still need tickets
- [x] A10. Wrap embedded iframe in a component that always posts bb-console-hello + routes bb-inbox-{rewrite,note,send} to real API endpoints

## B. Inbox wiring: learning (already sends approveLearning; verify)
- [x] B1. Verify composer send-confirm approve checkbox -> parentSend -> console /send approve_learning (exists; test it)
- [x] B2. Add Overview "Learning over time" panel data source: does inbox need learning ledger? Decide: console Overview keeps learning; inbox embeds tickets only — learning stays on console Overview tab (no inbox change). Confirm with tests.

## C. Inbox wiring: knowledge base + notices
- [x] C1. Decide surface: KB editor + Notice Board stay console tabs (server-backed, no inbox equivalent) — inbox ticket view embeds alongside them in nav. No code change; document.
- [x] C2. Ensure console nav keeps: Overview, Tickets(=Inbox), Connections, Knowledge base, Notice Board, Notifications, Settings — only Tickets content changes.

## D. Ticket-feature parity: legacy console features that must survive in embedded inbox
Audit each legacy feature -> inbox equivalent or port:
- [x] D1. Filters: all/draft/escalated/failed/queue + risk:* -> inbox views/facets/builder (map each; failed+queue have NO inbox equivalent — decide: job_status only exists in dashboard DB. Port as?? or drop with justification)
- [x] D2. Quoted-history strip + Show full email toggle (cleanMessage/actShowRaw) — inbox thread: check strip behavior
- [x] D3. Sensitive banner + [SENSITIVE] marker handling (cleanDraft/isEsc warn) — inbox composer is-sensitive kicker exists; verify escalate path
- [x] D4. Owner-alert uncertain banner — inbox: MISSING? verify
- [x] D5. Draft-as-note + Send + rewrite + learning checkbox + confirm dialogs — inbox parent bridge exists; verify each path incl. dry-run
- [x] D6. Check-last-action-status (delivery_status query) — inbox: check
- [x] D7. KPI/attention deep-links (data-go-filter/data-go-ticket) — must route into embedded inbox view/ticket selection via postMessage or URL
- [x] D8. Recent activity + risk breakdown + overview KPIs — stay on console Overview (uses dashboard DB, not inbox). Keep.
- [x] D9. Notifications tab (mark read, WhatsApp link/test/unlink, destination) — stays console tab. Keep.
- [x] D10. New-ticket local-only creation — inbox has it (#38). Keep.

## E. Tests
- [x] E1. console-src/test/*.test.js: fix/extend — render-xss slices function row(; action-operations slices actionStorageKey(; both will break when functions removed — rewrite to test new ticketsView/iframe + bridge routing
- [x] E2. Add tests: embedded inbox iframe present, hello handshake posted, rewrite/note/send route to API with correct payloads (incl. approve_learning), dry-run behavior
- [x] E3. No inbox JS changed (all ports console-side) — no new inbox tests needed; gate ran existing inbox suites green. Inbox JS tests: add coverage for any ported feature (D-items that need inbox changes)
- [x] E4. Full gate: tools/verify_release.sh green

## F. Ship
- [x] F1. Commit on branch inbox-replaces-ticket-feed
- [ ] F2. Push + open PR
- [ ] F3. CI-fix loop until green
