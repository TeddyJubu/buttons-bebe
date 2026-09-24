Buttons Bebe branding, inspected 2026-09-07 at https://buttonsbebe.com/.

- Jost Regular, Medium, SemiBold: original storefront files from
  https://cdn.shopify.com/s/files/1/0561/2742/2636/files/Jost-Regular.ttf
  (same directory for Jost-Medium.ttf and Jost-SemiBold.ttf).
- Jost license: OFL.txt, from https://github.com/google/fonts/tree/main/ofl/jost.
- logo.svg: original inline storefront logo; client brand asset, not covered by the font license.
- Sand #C8B397, rose #8E5250, burgundy #7A3347 are storefront colors.
  Logo green #103D3C comes from the original SVG. Background #F7F4EF and
  accessible status colors are console adaptations.

Edit brand.css, then run `python3 tools/sync_console_brand.py`.
The generator embeds the fonts, logo, and CSS in the console, login, inbox, and
WhatsApp pairing page. The KB editor and Notice Board are part of the console.
The inbox also appends inbox.css, a small brand adapter for workbench controls
and standalone navigation. Edit that adapter for inbox-only brand treatments;
keep pane sizing and density in `console-src/inbox/styles.css`. Both embedded
and standalone inbox modes use the same generated brand layer.
These small, self-contained pages keep working with the current login route
and deployment/rollback scripts, with no third-party font requests or new
public routes. The offline release gate checks generated copies for drift.
