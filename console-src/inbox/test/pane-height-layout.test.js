import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import test from 'node:test';
import { layoutFixture } from './layout-fixture.js';

const chrome = [process.env.INBOX_TEST_BROWSER,
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome',
].filter(Boolean).find(existsSync);
let chromium;
try { ({ chromium } = await import('playwright')); } catch (error) {
  if (error.code !== 'ERR_MODULE_NOT_FOUND' || !error.message.startsWith("Cannot find package 'playwright' imported from ")) throw error;
}

async function openFixture(t, width, height, { legacyViewport = false } = {}) {
  if (!chrome || !chromium) {
    t.skip('local Chrome and playwright required for layout checks');
    return null;
  }
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width, height } });
  await page.route('**/*', route => route.abort());
  const html = await layoutFixture();
  // Older engines discard unsupported declarations, not the preceding vh fallback.
  await page.setContent(legacyViewport ? html.replace(/max-height:\s*35dvh;/g, '') : html);
  return page;
}

// Dimensions are the embedded iframe, after the console sidebar/header.
for (const [width, height] of [[1092, 643], [1192, 887], [374, 696]]) {
  test(`long drafts leave readable conversation space at ${width}x${height}`, async t => {
    const page = await openFixture(t, width, height);
    if (!page) return;
    const layout = await page.evaluate(() => {
      const rect = selector => document.querySelector(selector).getBoundingClientRect();
      const thread = rect('.thread-scroll'), head = rect('.thread-head');
      const composer = rect('[data-slot="composer"]'), pane = rect('.pane-thread');
      return { threadHeight: thread.height, headBottom: head.bottom,
        threadBottom: thread.bottom, composerTop: composer.top,
        composerBottom: composer.bottom, paneBottom: pane.bottom };
    });
    assert.ok(layout.threadHeight >= 100, `conversation has only ${layout.threadHeight}px`);
    assert.ok(layout.composerTop >= layout.headBottom - 1, 'composer overlaps ticket header');
    assert.ok(layout.composerTop >= layout.threadBottom - 1, 'composer overlaps conversation');
    assert.ok(layout.composerBottom <= layout.paneBottom + 1, 'composer escapes pane');
    const summary = page.locator('.summarize-row');
    assert.ok(await summary.evaluate(el => el.getBoundingClientRect().bottom <= document.querySelector('[data-slot="composer"]').getBoundingClientRect().top + 1), 'summary control overlaps composer');
    await summary.locator('button').scrollIntoViewIfNeeded();
    assert.ok(await summary.locator('button').evaluate(el => {
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return hit === el || el.contains(hit);
    }), 'summary control must remain reachable');
    await page.locator('[data-send]').scrollIntoViewIfNeeded();
    assert.ok(await page.locator('[data-send]').evaluate(el => {
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return hit === el || el.contains(hit);
    }), 'send control must remain reachable without dismissing the draft');
  });
}

test('mobile ticket list retains a scroll budget when dvh is unsupported', async t => {
  const page = await openFixture(t, 374, 696, { legacyViewport: true });
  if (!page) return;
  const layout = await page.locator('.ticket-list').evaluate(list => ({
    height: list.clientHeight, scrollHeight: list.scrollHeight,
  }));
  assert.ok(layout.height >= 160, `ticket rows have only ${layout.height}px`);
  assert.ok(layout.height <= Math.ceil(696 * 0.35), `ticket list lost its viewport budget: ${layout.height}px`);
  assert.ok(layout.scrollHeight > layout.height, 'tickets must scroll inside the bounded list');
});

for (const width of [374, 359]) {
  test(`mobile history notice and pagination do not consume ticket rows at ${width}px`, async t => {
    const page = await openFixture(t, width, 696);
    if (!page) return;
    const layout = await page.evaluate(() => {
      const list = document.querySelector('.ticket-list');
      const pane = document.querySelector('.pane-list').getBoundingClientRect();
      const footer = document.querySelector('.list-pagination').getBoundingClientRect();
      return { height: list.clientHeight, scrollHeight: list.scrollHeight,
        footerBottom: footer.bottom, paneBottom: pane.bottom };
    });
    assert.ok(layout.height >= 160, `ticket rows have only ${layout.height}px`);
    assert.ok(layout.scrollHeight > layout.height, 'tickets should scroll inside the list');
    assert.ok(layout.footerBottom <= layout.paneBottom + 1, 'pagination is clipped');
    await page.locator('[data-load-more]').scrollIntoViewIfNeeded();
    await page.locator('[data-load-more]').click({ trial: true });
  });
}
