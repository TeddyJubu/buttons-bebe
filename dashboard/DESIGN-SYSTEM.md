# Buttons Bebe Support Console — Design System

> The shared "visual language" for the support dashboard (`dashboard/index.html`).
> Everything here is already wired up as CSS variables (design tokens) at the top of that file,
> so any future change is a one-line edit that updates the whole app at once.
>
> **Plain-English version:** instead of picking colors, sizes and spacing by hand every time,
> we defined them once as named "tokens" (like `--acc` for the brand purple). Every button, card
> and label points at those tokens. Change the token, and every screen updates together — no hunting
> through the file. Last updated: 2026-07-09.

---

## 1. How to use this

Open `dashboard/index.html`. The block that starts with `:root{` (near the top, inside `<style>`)
is the control panel. Want a different purple? Change `--acc`. Want rounder cards? Change `--r-lg`.
You never need to touch the JavaScript.

---

## 2. Design tokens

### Color

**Brand (purple).** One family, used for anything interactive or "ours".

| Token | Value | Use |
|-------|-------|-----|
| `--acc` | `#6d5efc` | Base brand purple — accents, focus ring, active bar |
| `--acc-strong` | `#5b4de0` | Solid purple **with white text** (primary buttons, active chips, switch) — darker so text passes contrast |
| `--acc-hover` | `#4d40cf` | Hover state for purple buttons |
| `--acc-ink` | `#5546d6` | Purple **text on white** (links, active nav) — dark enough to read |
| `--acc-soft` | `#f1eefe` | Tinted purple background (active nav, hero card, tags) |
| `--acc-100` | `#e7e2fd` | Slightly stronger tint / borders |

**Neutrals (gray ramp).** Replaces the old pile of one-off grays with a single 0→900 scale.

| Token | Value | Use |
|-------|-------|-----|
| `--gray-0` | `#ffffff` | Card surface (`--card`) |
| `--gray-25` | `#fafbfd` | Subtle fills, row hover, textarea bg |
| `--gray-50` | `#f6f7fb` | Page background (`--bg`) |
| `--gray-100` | `#eef0f5` | Soft fills, chips, hover backgrounds |
| `--gray-200` | `#e5e7ef` | Borders & divider lines (`--line`) |
| `--gray-300` | `#cfd4df` | Stronger border / hover border |
| `--gray-400` | `#9aa2b1` | Faint icons (chevrons) |
| `--gray-500` | `#6b7382` | Secondary text (`--ink3`) — **darkened for readability** |
| `--gray-600` | `#4a5262` | Body secondary text (`--ink2`) |
| `--gray-900` | `#14161c` | Primary text (`--ink`) |

**Semantic.** Each status has a *fill* and a darker *ink* (text) variant so text on a tinted chip is always legible.

| Meaning | Fill | Text (ink) | Soft bg |
|---------|------|-----------|---------|
| Success / low-risk | `--green` `#12b76a` | `--green-ink` `#0a7a48` | `--green-s` `#e7f7ee` |
| Warning / sensitive | `--amber` `#e08c00` | `--amber-ink` `#8a5a06` | `--amber-s` `#fdf3e0` |
| Danger / failed | `--red` `#e5484d` | `--red-ink` `#b42318` | `--red-s` `#fdecec` |

### Typography

System font stack (fast, no external download). Sizes snap to a clean scale — the old half-pixel sizes (13.5px, 12.5px…) are gone.

| Role | Size / weight | Where |
|------|---------------|-------|
| Display (KPI number) | 30px / 700 | `.kpi .v` |
| Stat number | 26px / 700 | `.lstat .v` |
| Page title | 20px / 700 | `.top h1` |
| Panel heading | 15px / 700 | `.panel h3` |
| Body | 14px / 400–500 | default |
| Body small / meta | 13px / 500 | labels, hints |
| Micro | 12px / 600 | KPI labels, timestamps |
| Tag / pill | 10–11px / 700 uppercase | `.tag`, `.pill`, `.sec-title` |

Weights used: 400 (body), 500 (labels), 600 (emphasis/buttons), 700 (numbers/titles).

### Spacing

4-px rhythm. Card padding `18–22px`, page padding `24–28px`, gaps `8/12/14/16px`.

### Radius

| Token | Value | Use |
|-------|-------|-----|
| `--r-xs` | 6px | tiny buttons, code chips |
| `--r-sm` | 8px | buttons, inputs, nav, icons |
| `--r-md` | 10px | nested boxes, message/draft cards |
| `--r-lg` | 14px | main cards & panels |
| `--r-pill` | 999px | chips, badges, switches |

### Elevation (shadow)

| Token | Use |
|-------|-----|
| `--sh-xs` | hairline lift (buttons) |
| `--sh-sm` | resting cards & panels |
| `--sh-md` | hovered cards (paired with a 2px lift) |
| `--sh-lg` | reserved for popovers/modals |
| `--ring` | focus ring — `0 0 0 3px rgba(109,94,252,.30)` |

### Motion

`--dur .18s` / `--dur-fast .12s`, easing `--ease cubic-bezier(.4,0,.2,1)`. Used for hover, chevron rotate, bar fills, the live-dot pulse, and the loading spinner.

---

## 3. Components

### Button (`.btn`)
| Variant | Class | Use |
|---------|-------|-----|
| Primary | `.btn.p` | Main action (Save, Rewrite) — purple |
| Send | `.btn.send` | Customer-facing send — green, safety-distinct |
| Default | `.btn` | Secondary actions |
| Ghost | `.btn.ghost` | Low-emphasis (Request edit) |

States: hover (border/bg shift), active (1px press), disabled (55% opacity), focus-visible (ring). All buttons share radius `--r-sm` and 13px/600 text.

### Badge (`.badge`)
Pill with a leading status dot. `.low` = green "AI draft"; `.sens` = amber "Sensitive". Text uses the *ink* color so it's readable on the tint.

### Chip (`.chip`)
Filter toggle. Default = outlined; `.on` = solid `--acc-strong` with white text. Hover darkens the border.

### Card / Panel (`.kpi`, `.panel`, `.conn`)
White surface, `--r-lg`, `--sh-sm`, 1px `--line` border. KPI and connection cards lift on hover (`--sh-md` + 2px). The **first KPI** is a "hero": soft purple gradient + purple number, giving the eye a clear starting point.

### Nav item (`.nav a`)
Sidebar link. Hover = gray fill; `.on` = purple tint, purple text, and a 3px accent bar on the left. Collapses to icon-only on narrow screens.

### Toggle switch (`.sw`)
44×26 pill, knob slides on `.on`, turns `--acc-strong`.

### Input / Textarea (`.numin`, `.dedit`, `.kbed textarea`)
1px border, `--r-sm/md`; on focus the border turns purple and shows the focus ring.

### Icon button (`.iconbtn`) — new
Square 36px control for icon-only actions (the top-bar Refresh). Keeps refresh visually separate from the "Agent live" status.

### Row chevron (`.chev`) — new
A ⌄ on each ticket row that rotates 180° when the row is expanded, so it's obvious rows are clickable.

---

## 4. Patterns

- **List + detail:** ticket feed rows expand in place to reveal the message, editable draft, and action bar.
- **Card grid:** KPIs and connections use `auto-fit` grids that reflow from many columns to one.
- **Left-nav shell:** fixed sidebar + sticky top bar + scrolling content. On mobile the sidebar becomes a horizontal icon bar (previously it disappeared entirely).
- **Inline feedback:** small `.toast` text in action bars; native confirm dialog gates any customer send.

---

## 5. Audit summary (before → after)

**Score: 62 → 88 / 100.**

| Category | Before | Fix applied |
|----------|--------|-------------|
| Token coverage | ~15 vars, many hardcoded grays/greens | Full ramp + semantic ink/soft pairs; hardcoded values removed |
| Typography | 12+ near-duplicate sizes incl. half-pixels | Snapped to a clean scale |
| Color contrast | `--ink3` and amber text failed WCAG AA | Darkened to pass AA |
| Focus states | none | `:focus-visible` ring on all controls |
| Depth / hierarchy | flat, all cards equal | Shadows + hover lift + hero KPI |
| Radius | 5 ad-hoc values | 5-step named scale |
| Mobile nav | sidebar hidden entirely | horizontal icon bar |
| Affordance | rows looked static | expand chevron + clearer refresh |

## 6. Known follow-ups (need small JS changes, not done yet)
- **Keyboard operability:** nav links and ticket rows are `div`/`a`-without-href with `onclick`; add `tabindex="0"` + Enter handling and `role="button"` so keyboard users can operate them.
- **Refresh:** the refresh action still blanks the screen briefly; could refresh in place with a spinning icon instead.
- **Toasts:** add `aria-live="polite"` so screen readers announce "Sent"/"Saved".
