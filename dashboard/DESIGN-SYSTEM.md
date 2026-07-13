# Buttons Bebe Dashboard Design System

This document is the implementation contract for every internal Buttons Bebe dashboard and helpdesk surface, on every branch.

The visual source of truth is the approved Variant dashboard: warm editorial beige, translucent white cards, near-black type, chartreuse and coral accent panels, generous rounded geometry, and quiet purposeful motion.

## Core principles

1. **Warm and calm.** The application background is warm beige, not cool gray or white.
2. **Editorial hierarchy.** Page titles are large and light; card labels are small, compact, and legible.
3. **Glass without spectacle.** Cards use translucent white surfaces and a subtle inset highlight. Avoid heavy shadows, gradients, and decorative blur.
4. **Color has a job.** Yellow and orange establish priority and rhythm. Green, amber, and red remain semantic safety colors.
5. **Motion explains change.** Transitions clarify navigation, updated values, and new badges. They never delay work.
6. **Review stays in control.** Helpdesk actions keep confirmations, undo, sensitive-ticket warnings, and explicit send states.

## Tokens

```css
:root {
  --ink: #1c1c1c;
  --ink-muted: #5c5952;
  --ink-subtle: #817d73;

  --canvas: #e8e5d8;
  --glass: rgba(255, 255, 255, 0.42);
  --glass-strong: rgba(255, 255, 255, 0.56);
  --line: rgba(28, 28, 28, 0.09);

  --yellow: #e7f65e;
  --orange: #f9a16c;

  --green: #237a4b;
  --green-soft: #dff1aa;
  --amber: #b56a2f;
  --amber-soft: #f9d4ad;
  --red: #b74339;
  --red-soft: #f4c8be;

  --radius-control: 12px;
  --radius-card: 20px;
  --radius-panel: 28px;
  --radius-pill: 999px;

  --ease-smooth: cubic-bezier(.2, .8, .2, 1);
  --duration-fast: 160ms;
  --duration-normal: 280ms;
  --duration-panel: 420ms;
}
```

Do not introduce a branch-specific primary hue. If a feature needs another color, document its semantic meaning first.

## Layout

### Desktop

- Sidebar: 260px, flat against the canvas, 40px top and 24px side padding.
- Main content: 40px horizontal gutters.
- Page header: approximately 120px tall, with the status and refresh controls aligned to the title baseline.
- KPI row: dense and balanced; the overview dashboard uses six cards where space permits.
- Supporting panels: 7/5 split for primary analysis and recent activity.
- Panel gap: 14px.
- Long operational views may scroll within the main column. Navigation remains available.

### Mobile

- At 820px and below, the sidebar becomes an off-canvas drawer.
- The drawer must have a visible menu trigger, dismissible scrim, and full text labels.
- KPI cards become two columns, then one column below 520px.
- Touch targets are at least 40px.
- No horizontal page scrolling is allowed.

## Components

### Navigation

- Active navigation uses a translucent white surface, not a brand-colored fill.
- Notification counts use orange.
- The current item exposes `aria-current="page"`.
- Hover may move an item by 2px at most.

### Page header

- Page titles use 30–40px type with a tight line height and modest weight.
- Health and freshness are separate from the refresh button.
- Refresh is a circular glass control with a text alternative.

### Cards and panels

- Use `--glass`, a subtle white border, and a 20–28px radius.
- Prefer an inset white highlight over a drop shadow.
- Hover elevation is limited to a 2px upward shift.
- The first priority KPI uses yellow; a supporting or outcome KPI uses orange.
- Decorative circles or hatch marks must remain low contrast and non-interactive.

### Controls

- Primary action: near-black background and white text.
- Send action: green.
- Sensitive warning: amber.
- Destructive action: red.
- Filter chips use a dark selected state; channels may retain muted channel-specific colors.
- Focus rings must be visible on every interactive element.

### Empty, loading, and error states

Every data surface needs:

- a skeleton or progress state;
- a useful empty-state explanation;
- an error message with an inline retry action where retry is possible;
- a freshness indicator when stale data could change a decision.

## Motion

Motion patterns follow the behavior library at [transitions.dev](https://transitions.dev/).

- Panel entry: fade plus 10px upward movement over 360–420ms.
- Number update: brief vertical slide without hiding the final value.
- New badge: one short scale pulse.
- Drawer: 360ms horizontal slide; scrim: 260ms fade.
- Hover: 160ms.
- Never animate layout continuously.
- Under `prefers-reduced-motion: reduce`, remove transforms and reduce animations/transitions to effectively zero duration.

## Accessibility

- Meet WCAG AA contrast for text and controls.
- Do not use color as the only status signal.
- Preserve semantic buttons, headings, labels, and keyboard operation.
- Mark decorative SVGs and marks as hidden from assistive technology.
- Dialogs and drawers must have clear labels and predictable dismissal.
- Dynamic status messages use an appropriate live region when the update matters.

## Surface mapping

| Surface | Required implementation |
| --- | --- |
| `dashboard/index.html` | Shared owner dashboard: exact tokens, layout, metrics, attention panel, activity, learning, and motion patterns. |
| `console-src/index.html` | Live dashboard source: same system, with feature-specific sections preserved. |
| `fable/console/style.css` | Operational helpdesk: same tokens and shell; ticket safety semantics and workflow affordances take precedence over decorative uniformity. |

## Review checklist

Before merging any dashboard change:

- [ ] No legacy purple primary tokens remain in the rendered interface.
- [ ] Desktop and 390px mobile layouts are usable.
- [ ] Sidebar, header, cards, controls, and KPI accents match this contract.
- [ ] Send, sensitive, and destructive states retain semantic colors and text labels.
- [ ] Keyboard focus and `aria-current` are present.
- [ ] Motion respects reduced-motion preferences.
- [ ] Loading, empty, error, and stale states remain understandable.
- [ ] Relevant browser smoke tests and the full project test suite pass.
