# Dashboard UX improvements

Last updated: 2026-07-13

## Now — frontend-safe improvements

- [x] Add a prominent **Needs your attention** summary for escalations, failed jobs, and queued work.
- [x] Rename **Automation rate** to **Draft coverage** so the UI does not imply replies are auto-sent.
- [x] Make overview metrics, risk rows, and recent activity drill into the relevant Ticket Feed view.
- [x] Separate the passive **Agent live** status from an explicit refresh action.
- [x] Show when data was last refreshed.
- [x] Show recoverable loading/API errors instead of silently converting failures into zeros.
- [x] Add a clear **View all activity** action.
- [x] Put action-required information before performance metrics on mobile.
- [x] Improve keyboard behavior, labels, focus handling, and live announcements.
- [x] Add restrained transitions.dev motion for panel entry, metric values, and notification badges.

## Next — requires backend/API support

- [ ] Add a global `Today / 7 days / 30 days / All time` range with one consistent scope across every metric.
- [ ] Return comparison-period data for meaningful trend labels.
- [ ] Return explicit unresolved/escalated counts instead of inferring attention items from the latest ticket page.
- [ ] Return a first-class health payload for webhook, queue, Gorgias, knowledge base, and WhatsApp services.
- [ ] Add pagination/cursors so dashboard drill-downs do not depend on the latest 60 tickets.

## Later — validate with usage

- [ ] Test whether frequent mobile operators benefit more from a sticky Ticket Feed shortcut or bottom navigation.
- [ ] Observe whether users understand **Draft coverage** without explanation.
- [ ] Review which overview metrics support daily decisions and remove vanity metrics that are not used.
- [ ] Run a focused WCAG contrast and screen-reader audit against real production data.

## Acceptance criteria

- The dashboard answers “what needs me now?” before reporting aggregate performance.
- Every prominent actionable number has a clear drill-down.
- Loading, stale, partial, and failed states are distinguishable from genuine zero values.
- Motion never blocks interaction and is removed when `prefers-reduced-motion` is enabled.
- Existing ticket-send confirmation and draft-review safeguards remain unchanged.
