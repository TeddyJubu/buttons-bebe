# Design Critique — the Fable Console (and old dashboard)

> Written 2026-07-10 after reading the actual code: `fable/console/` (app.js, style.css,
> index.html) and `dashboard/index.html` + `dashboard/DESIGN-SYSTEM.md`.
> Verdict up front: **strong foundation, a few real bugs, and one layout decision to rethink.**

---

## 1. What's already good (keep it)

- **The token system.** One purple family, a proper gray ramp, semantic green/amber/red with
  readable "ink" variants. Changing one line restyles the whole app. This is better than most
  commercial help desks.
- **Safety is visible in the design.** Send is green and visually distinct from every other
  button; sending always asks "Really send this to Emma?"; sensitive tickets get an amber
  banner in plain words ("This one mentions a refund — please read carefully"). The design
  enforces the safety model. Excellent.
- **Plain language everywhere.** "Waiting for you", "Needs a careful look", "Nothing waiting
  for you — enjoy the quiet." Friendly, human, on-brief.
- **Security hygiene.** All user text is escaped before rendering — no injection risk from a
  malicious customer message.
- **Accessibility basics.** Focus rings on everything, `aria-live="polite"` on toasts,
  nav and ticket cards keyboard-operable (Enter/Space). (The OLD dashboard lacks these —
  known follow-ups in DESIGN-SYSTEM.md §6 — one more reason to retire it.)

---

## 2. Real bugs found in the code (fix first — P0)

### B1 · Your draft edits can silently vanish
In `app.js`, the **Snooze**, **Mark as done**, **Reopen**, and **+ Tag** handlers re-render
the screen WITHOUT calling `captureDraft()` first. So: you spend two minutes editing a
reply, click "Snooze → until tomorrow", and your edited text is thrown away and replaced by
the AI's original. Same for adding a tag mid-edit.
**Fix:** call `captureDraft()` at the top of every handler that re-renders (one line each).
Add a regression test.

### B2 · Customer cards aren't keyboard-reachable
Ticket cards have `role="button" tabindex="0"` + Enter handling; customer cards
(`.custcard`) have none. Inconsistent and locks keyboard users out of the Customers page.
**Fix:** copy the exact pattern from `bindTicketCards()`.

### B3 · Stale ticket while you read it
The inbox polls every 5s, but the open ticket never refreshes. If the customer replies
"actually never mind, wrong order!" while you're polishing the draft, you'll send an answer
to a question they retracted.
**Fix:** poll the open ticket too (or check `last_message_at` and show a quiet banner:
"This customer just wrote again — refresh before sending"). The banner is safer than
auto-refreshing under the user's cursor.

---

## 3. The one layout decision to rethink (P1)

**The draft — the main thing — is at the bottom.** Ticket view stacks: header → whole
conversation → draft card. On a long thread the draft (and its Send button) start
off-screen. The user's #1 job on every ticket is "review the draft", and it's the thing
they have to scroll to find.

**Recommendation:** keep chronological order but (a) collapse older messages by default
("Show 6 earlier messages"), and (b) make the draft card sticky at the bottom of the
viewport once scrolled, like a compose box in email apps. On mobile, also collapse the
order sidebar into a one-line summary chip above the draft ("2 orders · 1 on its way")
that expands on tap — right now order context lands BELOW the draft on phones, which
means approving without seeing it.

---

## 4. Smaller improvements (P1)

- **No undo after Send.** Add a 5-second grace: "Sent — Undo" toast that actually delays
  dispatch until the toast expires. Cheap to build (the mailbox/outbox is ours), and it's
  the single biggest anxiety-reducer for a human clicking Send all day.
- **New-message awareness.** The poll updates counts silently. If you're staring at the
  inbox, a new sensitive ticket should be noticeable: pulse the Inbox badge and prepend the
  row with a soft highlight for a few seconds.
- **`window.prompt()` for tags** breaks the visual language (native gray popup in a purple
  app) — replace with a small inline input, and add tag **removal** (there's no × on tags,
  so a wrong tag is forever).
- **Edited-state honesty.** When a human edits the draft then saves as note/sends, mark it
  "edited" in the audit + UI. (The learning loop already tracks this on the VPS side;
  surface it.)
- **Emoji vs. SVG icons.** Channels use emoji (✉ 💬 🟢) while everything else is crisp
  SVG line icons. On Windows these emoji render in a different style entirely. Swap for
  three small SVGs with the existing channel colors — `--chan-wa-ink` green circle reads
  as "online status", not "WhatsApp", anyway.
- **Type-scale drift.** 13.5px snuck back in (`.seg`, `.warnbanner`) after DESIGN-SYSTEM.md
  proudly removed half-pixels. Snap to 13 or 14.

## 5. Nice-to-haves (P2)

- **Keyboard shortcuts** for the power loop: `j/k` next/prev ticket, `e` edit draft,
  `⌘Enter` send (still shows the confirm), `n` note. A support agent does this 100×/day.
- **Snooze options**: only "tomorrow / 3 days" exist; add "Monday" and a date picker.
- **Search result count** ("14 matches") and highlight of the matched term.
- **Dark mode** — the token system makes this a ~30-line `prefers-color-scheme` block.
- **Stats trends** — the KPI cards are point-in-time; a 14-day sparkline under each gives
  Chaim the "is this getting better?" answer at a glance.

---

## 6. Suggested order

| Priority | Items | Why |
|---|---|---|
| P0 — this week | B1, B2, B3 | B1 loses human work; B3 can cause a wrong send |
| P1 — Sprint 2 | draft-first layout, undo-send, new-message pulse, tag input + removal, edited flag, SVG channel icons, type snap | Biggest daily-use wins |
| P2 — later | shortcuts, snooze picker, search count, dark mode, sparklines | Polish |

All P0/P1 items are scheduled in `SPRINT-2-PLAN.md` (Stream C) with tests in
`TESTING-READINESS.md` (Playwright smoke covers B1–B3 regressions).
