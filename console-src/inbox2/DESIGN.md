# Buttons Bebe support console design system

**Status:** Visual reference for the current production console
**Reference:** [Support console](https://support.buttonsbebe.com/console/)
**Captured:** 25 September 2026
**Scope:** The console at `/console/`, its sign-in page, and the standalone Inbox at `/inbox/` where noted.

**Production version checked:** 25 September 2026; deployment manifest `6a7d1df18960bba67a0ed032534e71f1431ed8c3` (generation 221). Current console and sign-in files match their manifest checksums (`e9ee15c1c9d0` and `c31f8bd4c40f`). The public console route redirects unauthenticated requests to sign-in, whose served HTML was also checked.

The console page requires a signed-in session. This reference records the deployed HTML and CSS after restoration of the previous version; an unauthenticated request redirects to sign-in. Theme descriptions below reflect the final CSS cascade, including the console's bundled brand styles, rather than obsolete initial CSS declarations earlier in the file.

## Design direction

The console combines an earthy, sand-colored workspace with white operational cards, near-black text, burgundy brand details, and restrained green, amber, and red statuses. It balances dashboard density with clear section headings and familiar rounded cards. Inbox carries the same palette into a quieter, compact reading layout.

Keep the brand wordmark distinct from the working text. Use burgundy for page headings, selected navigation, and primary actions; use sand for navigation and green or amber for explicitly labeled states. Surfaces and separators should do most of the structural work. Use shadow sparingly for sign-in and temporary overlays.

## Color palette

These are the effective brand tokens in the current console styles. Prefer the token name where available; some existing components still use a direct value for a local variation.

| Token | Value | Use |
| --- | --- | --- |
| `--bg` / `--ground` | `#F7F4EF` | Main console background |
| `--card` / `--surface` | `#FFFFFF` | Cards, editors, buttons, and reading surfaces |
| `--ink` | `#222222` | Main text and important values |
| `--ink2` / `--muted` | `#625951` | Secondary text |
| `--ink3` | `#70665D` | Helper text, metadata, and timestamps |
| `--brand-burgundy` / `--acc` / `--accent` | `#7A3347` | Brand, page titles, selected items, primary buttons, links |
| `--acc-soft` | `#F2E7E9` | Selected navigation and soft burgundy accents |
| `--brand-sand` | `#C8B397` | Sidebar and brand mark |
| `--brand-forest` | `#103D3C` | Supporting brand color and icon accents |
| `--line` | `#DED6CC` | Card borders and dividers |
| `--green` | `#237A4B` | Positive or connected state |
| `--green-s` | `#E7F2E9` | Positive-state background |
| `--amber` | `#86500F` | Attention, review, or warning text |
| `--amber-s` | `#FFF1DB` | Warning background |
| `--red` | `#A42E36` | Destructive and error states |
| `--red-s` | `#FBEAEC` | Destructive-state background |
| `--brand-alert` | `#FFD8D1` | Owner-alert notice background |
| `--brand-alert-border` | `#D39292` | Owner-alert notice outline |
| `--brand-rose` | `#8E5250` | Secondary burgundy and alert icon |

Additional local colors include the sand navigation divider `#B39C80`, warm hover surface `#FAF7F3`, and darker burgundy button hover `#602638`. The legacy base stylesheet contains colors that are replaced by the later shared-theme and brand styles; those initial values are not the current brand palette.

Status is carried by readable labels and backgrounds, with icons or dots where useful. Do not infer a state from color alone. Keep error and destructive actions distinct from amber warnings.

## Typography

- **Interface and headings:** Jost, bundled locally in the console for weights 400, 500, and 600. The final brand layer applies Jost across the console, including inherited control text.
- **Brand wordmark:** Jost, using the separately bundled Buttons Bebe wordmark SVG in the console. The logo artwork itself uses forest green.
- **Code and technical text:** system monospace stack (for example, `ui-monospace`, Menlo, and SFMono-Regular); used for document identifiers and some editor content.
- **Inbox:** uses Inter for the interface and Jost for its wordmark. Its page loads Inter weights 400–700 and selected intermediate weights from Google Fonts.

Useful sizes found in the console: section navigation 13–14px; common supporting copy 12–14px; dashboard card headings 15–16px; page title 19px in the standard header and up to 26px in the compact or mobile header; KPI values 24–28px. The separate sign-in page uses a 28px Jost heading, 14px labels, and 16px introductory and input text. Use sentence case for interface labels.

The sign-in page’s earlier base rule names Inter, but its final bundled brand rule overrides the page and inherited controls to Jost. The page also reuses the console palette and Jost wordmark.

## Layout and responsive behavior

### Console shell

- A left navigation sidebar sits beside the main workspace. At desktop widths it is approximately 224–230px wide, with a sand background and a right divider.
- The sidebar groups a Buttons Bebe identity and product label, vertical dashboard navigation, and a store/account label at the bottom.
- The main column has a compact top header with a page title, description, freshness indicator, live/status label, refresh, notifications, account, and sign-out controls.
- Workspace content scrolls independently. Use responsive KPI and two-column card grids suited to each section.
- At widths of 900px and below, the sidebar becomes a slide-in drawer controlled by a menu button and backdrop.
- At widths of 600px and below, dashboard content and attention panels stack, section navigation remains reachable in the drawer, and the header actions wrap.
- The page title and section descriptions remain visible as the header compacts on scroll.

### Inbox

Inbox uses a full-height, three-column browse/work/context layout. At wide sizes, the ticket list is around 312–330px, the reading pane grows with a 380–440px minimum, and the customer-detail rail is about 344–365px. Between 651 and 1040px the detail rail opens as a drawer; at 650px and below the list, ticket, and details appear one main view at a time. The collapsed context rail preserves a clear way to reopen customer details.

### Sign-in

The sign-in card has an approximately 440px maximum width, a white surface, a sand top border, and a restrained shadow. Keep it centered with 24px page padding; fields and the primary action span the card’s content width.

## Components and shapes

- **Cards:** white background, fine warm border, generally 12px rounded corners in the console’s final brand styles. Dashboard section spacing is typically 16–24px.
- **Primary button:** burgundy fill, white text, 8px radius. Secondary buttons use white fill and a taupe outline. Standard controls are about 36–38px high; use generous hit areas on touch layouts.
- **Navigation:** muted labels; current section uses a burgundy fill with white text. Nested knowledge-base selections use a pale burgundy fill with a narrow inset marker.
- **Fields and editors:** white fill, thin taupe border, 6–9px radius. The knowledge editor uses a monospace face; ordinary writing uses Jost. Keep labels visible when a field is filled.
- **Status badges:** compact, rounded labels with readable status text and a tinted background. Positive is pale green; attention is pale amber.
- **Notices:** amber for facts requiring review; pale red for destructive confirmation; pale green for positive fulfilment states.
- **Temporary layers:** restrained shadows for menus, drawers, and dialogs. Persistent cards remain mostly flat.
- **Transitions:** short fill and border changes; the interface disables nonessential animation when reduced motion is requested.

## Icons and interaction

The console uses bundled inline SVG icons. The dashboard’s icon set uses filled Material-style SVG paths, while Inbox uses bundled Lucide SVGs on a 24px grid with a 2px stroke. Within each page, keep the icon family consistent. Decorative icons are hidden from assistive technology; give icon-only controls an accessible name.

Keyboard focus is a visible burgundy outline. Selected navigation exposes its current-page state, and the mobile navigation restores focus when it closes. Respect keyboard operation, readable labels, reduced motion, text enlargement, and wrapping at narrow widths. Links in message content should remain visible and descriptive.

## Practical reference tokens

```css
:root {
  --ink: #222222;
  --ink2: #625951;
  --ink3: #70665d;
  --acc: #7a3347;
  --acc-soft: #f2e7e9;
  --line: #ded6cc;
  --bg: #f7f4ef;
  --card: #ffffff;
  --brand-sand: #c8b397;
  --brand-rose: #8e5250;
  --brand-burgundy: #7a3347;
  --brand-forest: #103d3c;
  --green: #237a4b;
  --green-s: #e7f2e9;
  --amber: #86500f;
  --amber-s: #fff1db;
  --red: #a42e36;
  --red-s: #fbeaec;
  --brand-font: Jost, "Avenir Next", Arial, sans-serif;
  --mono: ui-monospace, Menlo, monospace;
  --radius: 12px;
}
```
