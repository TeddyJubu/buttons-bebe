const assert = require('node:assert/strict');
const { existsSync, readFileSync } = require('node:fs');
const test = require('node:test');

const source = readFileSync(new URL('../index.html', `file://${__filename}`), 'utf8');
const chrome = [process.env.INBOX_TEST_BROWSER,
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome',
].filter(Boolean).find(existsSync);
const title = 'Intent 1 — First-time customer asks for help choosing between two sizes for a winter coat';

async function openConsole(t, width = 1340) {
  let chromium;
  try { ({ chromium } = require('playwright')); } catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND' || !error.message.startsWith("Cannot find module 'playwright'")) throw error;
  }
  if (!chrome || !chromium) { t.skip('local Chrome and playwright required'); return; }
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width, height: 844 }, reducedMotion: 'reduce' });
  const writes = [];
  await page.route('**/*', route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    if (req.method() !== 'GET') { writes.push(path); return route.abort(); }
    const data = {
      '/console/api/stats': { drafted: 80, sensitive_draft: 10, no_kb_match: 2, owner_alerts_need_attention: 51 },
      '/console/api/tickets': [], '/console/api/learning': {}, '/console/api/ops': { status: 'missing' },
      '/console/kbapi/health': { ok: true, folders: {}, products: {} },
      '/console/waapi/status': { state: 'connected', owner: '15555550100@s.whatsapp.net', notify: { mode: 'linked', number: '' } },
      '/console/api/notifications': { unread_count: 100, notifications: Array.from({ length: 100 }, (_, i) => ({ id: `review-${i}`, ticket_id: i + 1, kind: 'review', title: `Review ticket ${i + 1}`, read: false, occurred_at: '2026-09-24T10:00:00Z' })) },
      '/console/kbapi/list': { folders: [{ folder: 'intents', files: [{ path: 'intents/sizing.md', title }] }] },
      '/console/kbapi/file': { content: '# Synthetic sizing guidance' },
    };
    if (path === '/console/api/owner-alerts') {
      const offset = Number(new URL(req.url()).searchParams.get('offset') || 0);
      return route.fulfill({ json: { total: 51, limit: 50, offset, attempts: offset ? [
        { job_id: 1, ticket_id: null, message_id: null, status: 'attempting', attempted_at: '2026-09-23T10:00:00Z', finished_at: null, ticket_subject: null, reason: null },
      ] : Array.from({ length: 50 }, (_, i) => ({ job_id: 51 - i, ticket_id: 42, message_id: 420 + i, status: 'uncertain', attempted_at: '2026-09-24T10:00:00Z', finished_at: null, ticket_subject: '<img src=x onerror=alert(1)>', reason: 'Synthetic alert context' })) } });
    }
    if (path === '/inbox/') return route.fulfill({ contentType: 'text/html', body: '<p>Synthetic inbox</p>' });
    if (path === '/console/') return route.fulfill({ contentType: 'text/html', body: source });
    if (Object.hasOwn(data, path)) return route.fulfill({ json: data[path] });
    return route.abort();
  });
  await page.goto('http://console.test/console/');
  await page.locator('.kpi').first().waitFor();
  return { page, writes };
}

async function selectTab(page, tab) {
  if (await page.getByRole('button', { name: 'Open navigation', exact: true }).isVisible()) {
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  }
  await page.locator(`.nav [data-tab="${tab}"]`).click();
}

test('UI-05: cumulative draft output is not described as pending review', async t => {
  const fixture = await openConsole(t); if (!fixture) return;
  const text = await fixture.page.locator('.kpi').filter({ hasText: 'AI drafts written' }).innerText();
  assert.match(text.replace(/\s/g, ''), /92/);
  assert.match(text, /all.time/i);
  assert.doesNotMatch(text, /ready for review/i);
});

test('UI-08: owner-attempt inspection paginates and opens tickets without acknowledging or resending', async t => {
  const fixture = await openConsole(t); if (!fixture) return;
  const { page, writes } = fixture;
  assert.match(await page.locator('.owner-alert-warning').innerText(), /attempts/);
  await page.getByRole('button', { name: 'Inspect owner-alert attempts', exact: true }).click();
  await page.locator('[data-owner-attempt]').first().waitFor();
  assert.equal(await page.locator('[data-owner-attempt]').count(), 50);
  assert.match(await page.locator('#owner-alert-inspection').innerText(), /not distinct tickets or unread notifications/i);
  assert.equal(await page.locator('#owner-alert-inspection img').count(), 0, 'context is escaped');
  await page.getByRole('button', { name: 'Next attempts', exact: true }).click();
  await page.getByText('Ticket context unavailable', { exact: true }).waitFor();
  assert.equal(await page.locator('[data-owner-attempt]').count(), 1);
  assert.equal(await page.locator('[data-owner-attempt] [data-go-ticket]').count(), 0);
  await page.getByRole('button', { name: 'Previous attempts', exact: true }).click();
  await page.locator('[data-owner-attempt] [data-go-ticket]').first().click();
  await page.waitForURL('**/inbox/**');
  assert.equal(new URL(page.url()).searchParams.get('ticket'), 'gorgias:42');
  assert.deepEqual(writes, []);
});

test('UI-08: ticket-feed failure leaves owner-alert inspection available', async t => {
  const fixture = await openConsole(t); if (!fixture) return;
  const { page, writes } = fixture;
  await page.route('**/console/api/tickets?*', route => route.fulfill({ status: 503, json: { error: 'unavailable' } }));
  await page.getByRole('button', { name: 'Refresh dashboard data', exact: true }).click();
  await page.getByRole('heading', { name: 'Overview metrics are unavailable', exact: true }).waitFor();
  assert.match(await page.locator('.owner-alert-warning').innerText({ timeout: 3000 }), /51 owner-alert attempts/);
  await page.getByRole('button', { name: 'Inspect owner-alert attempts', exact: true }).click();
  await page.locator('[data-owner-attempt]').first().waitFor();
  assert.equal(await page.locator('[data-owner-attempt]').count(), 50);

  // An open independent inspector remains available even if the next stats read fails.
  await page.route('**/console/api/stats', route => route.fulfill({ status: 503, json: { error: 'unavailable' } }));
  await page.getByRole('button', { name: 'Refresh dashboard data', exact: true }).click();
  await page.getByText('Could not load stats, tickets data.', { exact: true }).waitFor();
  assert.equal(await page.locator('.owner-alert-warning').count(), 0, 'do not present a stale count as current');
  assert.equal(await page.locator('[data-owner-attempt]').count(), 50);
  assert.deepEqual(writes, []);
});

test('UI-08: malformed attempt rows show a recoverable error rather than poisoning Overview', async t => {
  const fixture = await openConsole(t); if (!fixture) return;
  const { page, writes } = fixture;
  let fail = true;
  await page.route('**/owner-alerts?*', route => fail
    ? route.fulfill({ json: { total: 1, limit: 50, offset: 0, attempts: [null] } })
    : route.fallback());
  await page.getByRole('button', { name: 'Inspect owner-alert attempts', exact: true }).click();
  await page.getByRole('button', { name: 'Retry inspection', exact: true }).waitFor({ timeout: 3000 });
  assert.match(await page.locator('#owner-alert-inspection').innerText(), /Nothing was acknowledged or resent/);
  fail = false;
  await page.getByRole('button', { name: 'Retry inspection', exact: true }).click();
  await page.locator('[data-owner-attempt]').first().waitFor();
  assert.equal(await page.locator('[data-owner-attempt]').count(), 50);
  assert.deepEqual(writes, []);
});

for (const width of [1340, 390]) {
  test(`UI-06: delivery configuration precedes a long unread backlog at ${width}px`, async t => {
    const fixture = await openConsole(t, width); if (!fixture) return;
    const { page, writes } = fixture;
    await selectTab(page, 'notif');
    await page.locator('#wa-save').waitFor();
    const layout = await page.evaluate(() => ({
      setup: document.querySelector('#wa-save').getBoundingClientRect().top,
      activity: document.querySelector('[aria-labelledby="notification-title"]').getBoundingClientRect().top,
    }));
    assert.ok(layout.setup < layout.activity, 'WhatsApp setup is buried below activity');
    assert.equal(await page.locator('.notification-row').count(), 100);
    assert.deepEqual(writes, [], 'viewing delivery must not mark alerts read or save settings');
  });

  test(`UI-09/10: full document title is readable and editor is named at ${width}px`, async t => {
    const fixture = await openConsole(t, width); if (!fixture) return;
    const { page, writes } = fixture;
    await selectTab(page, 'kb');
    const item = page.locator('.kbitem');
    await item.waitFor();
    const layout = await item.evaluate(el => ({
      whiteSpace: getComputedStyle(el).whiteSpace,
      width: el.clientWidth, scrollWidth: el.scrollWidth,
      text: el.textContent,
    }));
    assert.notEqual(layout.whiteSpace, 'nowrap', 'friendly title is truncated to one line');
    assert.ok(layout.scrollWidth <= layout.width + 1, 'full title must wrap within list width');
    assert.ok(layout.text.includes(title));
    assert.ok(layout.text.includes('intents/sizing.md'), 'path stays available as secondary text');
    await item.click();
    await page.locator('[data-kb-mode=edit]').click();
    await page.locator('#kb-text').waitFor();
    assert.equal(await page.getByRole('textbox', { name: /document.*intents\/sizing\.md/i }).count(), 1);
    assert.deepEqual(writes, [], 'opening a document must not save or re-index it');
  });
}

test('Tickets navigation opens standalone Inbox without an embedded legacy page', async t => {
  const fixture = await openConsole(t); if (!fixture) return;
  const { page, writes } = fixture;
  await selectTab(page, 'tickets');
  await page.waitForURL('**/inbox/');
  assert.equal(new URL(page.url()).pathname, '/inbox/');
  assert.equal(await page.locator('iframe').count(), 0);
  assert.deepEqual(writes, []);
});
