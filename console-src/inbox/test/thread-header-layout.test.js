import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import test from "node:test";

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
          <span class="status-badge">Status unknown</span>
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
