import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import test from "node:test";
import { createThreadTissue } from "../js/tissues/thread.js";
import { createMailbox } from "../js/mailbox.js";

const browsers = [
  process.env.INBOX_TEST_BROWSER,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/chromium-browser",
  "/usr/bin/chromium",
  "/usr/bin/google-chrome",
].filter(Boolean);
let chrome = browsers.find((candidate) => existsSync(candidate));
const inbox = fileURLToPath(new URL("..", import.meta.url));
const css = readFileSync(join(inbox, "styles.css"), "utf8");

let chromium = null;
if (chrome) {
  // Dev dependency of the review checkout, not the shipped inbox manifest.
  try {
    ({ chromium } = await import("playwright"));
  } catch {
    chrome = null;
  }
}

// The header's lines, selects and buttons share one 28px control height:
// the id/status lines stretch to it, the detail selects fix to it, and the
// hairline/quiet buttons drop their vertical padding so min-height rules.
// (Runs on the stylesheet text, so it holds without a local Chrome.)
test("thread header controls share one height", () => {
  assert.match(css, /\.thread-head-actions > \.ticket-id-badge,\s*\.thread-head-actions > \.status-line\s*\{[^}]*min-height:\s*28px/, "the id/status lines stretch to the shared height");
  assert.match(css, /\.thread-head-actions \.detail-field select\s*\{[^}]*height:\s*28px/, "the detail selects fix to the shared height");
  assert.match(css, /\.thread-head-actions > \.btn-hairline,\s*\.thread-head-actions \.btn-quiet\s*\{[^}]*padding-bottom:\s*0/, "the header buttons drop vertical padding so min-height rules");
});
test("the thread pane docks with a pinned composer on narrow screens", () => {
  // Task 6: under 780px the thread sticks to the scroll container top at
  // full height — thread-scroll scrolls inside, composer pinned at bottom.
  assert.match(css, /@media \(max-width: 780px\)[\s\S]*?\.pane-thread\s*\{[^}]*position:\s*sticky[^}]*height:\s*100%/);
});
test("thread title and selected row carry the hierarchy", () => {
  // Task 5: 22px title over 14px body clears the 1.5x level step; the open
  // ticket names itself in semibold on a deeper wash so unread rows don't
  // out-shout it.
  assert.match(css, /\.thread-head h2\s*\{[^}]*font-size:\s*22px/, "the thread title clears the level step");
  assert.match(css, /\.ticket-row\.is-selected\s*\{[^}]*14%/, "the selected wash reads at a glance");
  assert.match(css, /\.ticket-row\.is-selected \.ticket-name\s*\{[^}]*font-weight:\s*600/, "the open ticket names itself");
});
test("status readouts are dot + word, never pills", () => {
  assert.doesNotMatch(css, /\.status-badge\s*\{/, "no pill badge class remains in the stylesheet");
  assert.match(css, /\.status-line\s*\{[^}]*gap:\s*5px/, "the readout line carries its dot");
  assert.match(css, /\.status-dot\.is-open\s*\{[^}]*background:\s*var\(--accent\)/, "open reads as attention");
  assert.match(css, /\.status-dot\.is-urgent\s*\{[^}]*background:\s*var\(--red\)/, "urgent reads as red");
  assert.doesNotMatch(css, /\.ticket-id-badge\s*\{[^}]*border:/, "the ticket id is plain text, never a pill");
});
// The real header's action set: id badge, copy link, status, the #61 detail
// controls, prev/next. Rendered in a container narrower than its intrinsic
// width, as the three-pane shell always is — the case that collapsed the
// title to one character per line site-wide.
test("thread actions wrap instead of collapsing the ticket title", async (t) => {
  if (!chrome || !chromium) {
    t.skip("no local Chrome/Chromium with playwright found; set INBOX_TEST_BROWSER to run this layout test");
    return;
  }
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1400, height: 800 } });

  await page.setContent(`
    <style>${css}</style>
    <main style="width: 800px">
      <header class="thread-head">
        <div><h2>Mindykop2 1032007</h2><p class="thread-subject">Size exchange</p></div>
        <div class="thread-head-actions">
          <span class="ticket-id-badge">gorgias:61003</span>
          <button class="btn-hairline">Copy link</button>
          <span class="status-line"><span class="status-dot is-unknown" aria-hidden="true"></span>Status unknown</span>
          <div class="thread-detail-controls">
            ${["Status", "Priority", "Assignee"].map((label) => `<label class="detail-field"><span class="detail-label">${label}</span><select><option>Observed</option><option>Open</option></select></label>`).join("")}
          </div>
          <span class="detail-nav"><button class="btn-quiet">Previous</button><button class="btn-quiet">Next</button></span>
        </div>
      </header>
    </main>
  `);
  await page.waitForFunction(() => document.styleSheets.length > 0);

  const layout = await page.evaluate(() => {
    const header = document.querySelector(".thread-head");
    const title = header.firstElementChild;
    const actions = header.lastElementChild;
    return {
      headerWidth: header.getBoundingClientRect().width,
      headerScrollWidth: header.scrollWidth,
      titleWidth: title.getBoundingClientRect().width,
      titleLines: (() => {
        const range = document.createRange();
        range.selectNodeContents(title.querySelector("h2"));
        return range.getClientRects().length;
      })(),
      titleBottom: title.getBoundingClientRect().bottom,
      actionsTop: actions.getBoundingClientRect().top,
      actionsRight: actions.getBoundingClientRect().right,
    };
  });

  assert.equal(layout.titleLines, 1, "ticket title wrapped under action pressure");
  assert.ok(layout.actionsTop >= layout.titleBottom, "actions did not wrap below the title");
  assert.ok(layout.actionsRight <= layout.headerWidth + 1, "actions overflowed the thread header");
  assert.equal(layout.headerScrollWidth, layout.headerWidth, "header overflowed horizontally");
});

// The thread tissue renders inside [data-slot="thread"], which the shell
// grows with flex:1; .thread-inner fills it via height:100% and
// .thread-scroll takes the leftover. A production ticket whose messages
// never arrived therefore shows the notice plus the empty state in a
// full-height region, never a sliver.
test("the thread slot child fills the pane so an empty timeline never collapses", () => {
  assert.match(css, /\.pane-thread > \[data-slot="thread"\][\s\S]*?\{[^}]*flex:\s*1/, "the slot grows into the freed pane space");
  assert.match(css, /\.thread-empty\s*\{[^}]*text-align:\s*center/, "the empty timeline reads as an intentional state");
});
test("an empty timeline says so instead of rendering a sliver", () => {
  const tissue = createThreadTissue({ mailbox: createMailbox() });
  const html = tissue.render({
    ticket: {
      id: "gorgias:281635661",
      subject: "Re: Dress size 4",
      customerName: "Avigail Zagelbaum",
      status: "open",
      messages: [],
      statusEvents: [],
      projectionSource: true,
      updatedAt: "2026-09-15T10:24:15Z",
    },
    capabilities: {},
    title: "Re: Dress size 4",
    nav: { position: 0, total: 1, hasPrev: false, hasNext: false },
    operatorEmail: "",
    ticketState: null,
  });
  assert.match(html, /Partial webhook history/, "the incompleteness note stays");
  assert.match(html, /<p class="thread-empty" role="status">No messages in this snapshot yet\.<\/p>/, "the empty timeline names itself");
  assert.doesNotMatch(html, /<article class="bubble/, "no message bubbles are invented");
});
