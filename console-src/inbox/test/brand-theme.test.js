import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { layoutFixture } from './layout-fixture.js';

const inbox = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const consolePage = readFileSync(new URL('../../index.html', import.meta.url), 'utf8');
const brandBlock = html => html.match(/<style id="buttonsbebe-brand">([\s\S]*?)<\/style>/)?.[1];

function contrastRatio(foreground, background) {
  const luminance = rgb => rgb.match(/[\d.]+/g).slice(0, 3).map(Number)
    .map(value => value / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
    .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  const first = luminance(foreground), second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

test('inbox embeds the console brand after the legacy theme, with offline Jost fonts', () => {
  const brand = brandBlock(inbox);
  assert.ok(brand, 'inbox must include the shared brand embed');
  assert.ok(brand.startsWith(brandBlock(consolePage)), 'console and inbox share one generated font and brand source');
  assert.ok(inbox.indexOf('id="buttonsbebe-brand"') > inbox.indexOf('id="support-theme"'));
  assert.equal((brand.match(/@font-face\{font-family:Jost/g) || []).length, 3);
  assert.doesNotMatch(inbox, /<link[^>]+fonts\.(?:googleapis|gstatic)\.com/);
});

test('optional Playwright imports skip only a genuinely absent top-level package', async () => {
  for (const filename of ['brand-theme.test.js', 'pane-height-layout.test.js']) {
    const source = readFileSync(new URL(filename, import.meta.url), 'utf8');
    const loader = source.match(/^let chromium;[\s\S]*?(?=\n\n)/m)[0];
    const attempt = importError => runInNewContext(`(async () => {
      ${loader.replace("import('playwright')", 'loadPlaywright()')}
      return chromium;
    })()`, { loadPlaywright: async () => { throw importError; } });
    const absent = Object.assign(new Error("Cannot find package 'playwright' imported from test.js"), { code: 'ERR_MODULE_NOT_FOUND' });
    assert.equal(await attempt(absent), undefined, `${filename}: absent package may skip`);
    for (const error of [
      new Error('Playwright initialization failed'),
      Object.assign(new Error("Cannot find package 'playwright-core' imported from playwright/index.mjs"), { code: 'ERR_MODULE_NOT_FOUND' }),
      Object.assign(new Error("Cannot find module '/node_modules/playwright/missing.js' imported from playwright/index.mjs"), { code: 'ERR_MODULE_NOT_FOUND' }),
    ]) {
      await assert.rejects(() => attempt(error), caught => caught === error, `${filename}: broken installed package must fail`);
    }
  }
});

const chrome = [process.env.INBOX_TEST_BROWSER,
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome',
].filter(Boolean).find(existsSync);
let chromium;
try { ({ chromium } = await import('playwright')); } catch (error) {
  if (error.code !== 'ERR_MODULE_NOT_FOUND' || !error.message.startsWith("Cannot find package 'playwright' imported from ")) throw error;
}

test('CSP subprocess selects the gate Python without assuming a local venv', () => {
  const source = readFileSync(new URL('brand-theme.test.js', import.meta.url), 'utf8');
  const declaration = source.match(/^  const python = [^;]+;/m)[0];
  for (const [env, expected] of [
    [{ INBOX_PYTHON: '/runtime/inbox-python', PYTHON: '/runtime/python' }, '/runtime/inbox-python'],
    [{ PYTHON: '/runtime/python' }, '/runtime/python'],
    [{}, 'python3'],
  ]) {
    assert.equal(runInNewContext(`${declaration} python;`, { process: { env }, root: '/checkout/' }), expected);
  }
});

test('Jost loads under the real inbox response CSP without allowing inline scripts', async t => {
  if (!chrome || !chromium) {
    t.skip('local Chrome and playwright required for response CSP checks');
    return;
  }
  const root = fileURLToPath(new URL('../../../', import.meta.url));
  const python = process.env.INBOX_PYTHON || process.env.PYTHON || 'python3';
  // Exercise the ASGI response without starting a server or loading real data.
  const result = spawnSync(python, ['-c', `
import json, os, sys, tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as temp:
    os.environ['HELPDESK_DB_FILE'] = str(Path(temp) / 'test.sqlite3')
    sys.path.insert(0, 'console-src/inbox')
    from review_server import app
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        response = client.get('/')
        print(json.dumps({'status': response.status_code, 'headers': dict(response.headers), 'body': response.text}))
`], { cwd: root, encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 });
  assert.equal(result.status, 0, result.stderr || String(result.error));
  const response = JSON.parse(result.stdout);
  assert.equal(response.status, 200);
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.route('**/*', route => route.request().url() === 'http://inbox.test/'
    ? route.fulfill({ status: response.status, headers: response.headers, body: response.body })
    : route.abort());
  await page.goto('http://inbox.test/');
  const fonts = await page.evaluate(async () => {
    await document.fonts.ready;
    return [...document.fonts].filter(font => font.family === 'Jost').map(font => font.status);
  });
  assert.ok(fonts.includes('loaded'), `Jost did not load under response CSP: ${fonts}`);
  assert.match(response.headers['content-security-policy'], /(?:^|; )script-src 'self';/);
});

test('standalone sand navigation keeps every label at AA text contrast', async t => {
  if (!chrome || !chromium) {
    t.skip('local Chrome and playwright required for computed brand checks');
    return;
  }
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1192, height: 887 } });
  await page.route('**/*', route => route.abort());
  const nav = inbox.match(/<nav class="support-nav"[\s\S]*?<\/nav>/)[0];
  await page.setContent((await layoutFixture()).replace(' data-embedded="1"', '')
    .replace('<div id="inbox-root">', `${nav}<div id="inbox-root">`));
  const labels = await page.evaluate(() => {
    const background = getComputedStyle(document.querySelector('.support-nav')).backgroundColor;
    return [...document.querySelectorAll('.support-nav a,.support-nav .lock-label')].map(element => ({
      label: element.textContent, color: getComputedStyle(element).color, background,
    }));
  });
  assert.equal(labels.length, 4, 'covers brand, Console, active Inbox, and Send-access label');
  const failures = labels.map(label => ({ ...label, ratio: contrastRatio(label.color, label.background) }))
    .filter(label => label.ratio < 4.5);
  assert.deepEqual(failures, [], 'all navigation text must contrast at least 4.5:1 with sand');
});

for (const embedded of [true, false]) {
  test(`${embedded ? 'embedded' : 'standalone'} inbox matches console branding without changing workbench density`, async t => {
    if (!chrome || !chromium) {
      t.skip('local Chrome and playwright required for computed brand checks');
      return;
    }
    const browser = await chromium.launch({ executablePath: chrome, headless: true });
    t.after(() => browser.close());
    const page = await browser.newPage({ viewport: { width: 1192, height: 887 }, reducedMotion: 'reduce' });
    await page.route('**/*', route => route.abort());
    const nav = inbox.match(/<nav class="support-nav"[\s\S]*?<\/nav>/)[0];
    let html = (await layoutFixture()).replace('<div id="inbox-root">', `${nav}<div id="inbox-root">`);
    if (!embedded) html = html.replace(' data-embedded="1"', '');
    // The observed-history fixture locks Send; a separate enabled control covers the primary treatment.
    html = html.replace('</body>', '<button class="btn-ink" id="brand-primary">Primary</button></body>');
    await page.setContent(html);
    await page.evaluate(() => document.fonts.ready);
    const styles = await page.evaluate(() => {
      const css = selector => getComputedStyle(document.querySelector(selector));
      const root = css(':root');
      return {
        font: css('body').fontFamily, fontLoaded: document.fonts.check('14px Jost'),
        primary: css('.btn-ink:not(:disabled):not(.is-disabled)').backgroundColor,
        buttonRadius: css('.btn-hairline').borderRadius,
        border: css('.pane').borderTopColor, paneRadius: css('.pane').borderRadius,
        danger: css('.draft-strip.is-sensitive').borderTopColor,
        warning: root.getPropertyValue('--warn').trim(), success: root.getPropertyValue('--ok').trim(),
        disabled: css('.btn-send').backgroundColor,
        bodySize: css('body').fontSize, padding: css('.inbox').padding, gap: css('.inbox').gap,
        nav: css('.support-nav').display, rootHeight: document.querySelector('#inbox-root').clientHeight,
      };
    });
    assert.match(styles.font, /Jost/);
    assert.equal(styles.fontLoaded, true);
    assert.equal(styles.bodySize, '14px');
    assert.equal(styles.padding, '8px');
    assert.equal(styles.gap, '8px');
    assert.equal(styles.nav, embedded ? 'none' : 'flex');
    assert.equal(styles.rootHeight, embedded ? 887 : 835);

    await page.locator('#brand-primary').hover();
    const primaryHover = await page.locator('#brand-primary').evaluate(el => getComputedStyle(el).backgroundColor);

    // Compare resolved values rather than duplicating the console palette in tests.
    await page.setContent(`<style>${brandBlock(consolePage)} .danger-token{color:var(--red)}</style><body class="bb-console"><div class="panel"></div><button class="btn p">Primary</button><button class="btn">Secondary</button><span class="danger-token"></span></body>`);
    const consoleStyles = await page.evaluate(() => {
      const css = selector => getComputedStyle(document.querySelector(selector));
      const root = css(':root');
      return {
        primary: css('.btn.p').backgroundColor, buttonRadius: css('.btn').borderRadius,
        border: css('.panel').borderTopColor, paneRadius: css('.panel').borderRadius,
        danger: css('.danger-token').color, warning: root.getPropertyValue('--amber').trim(),
        success: root.getPropertyValue('--green').trim(), ground: css('body').backgroundColor,
      };
    });
    for (const key of ['primary', 'buttonRadius', 'border', 'paneRadius', 'danger', 'warning', 'success']) {
      assert.equal(styles[key], consoleStyles[key], `${key} must match the console`);
    }
    assert.equal(styles.disabled, consoleStyles.ground, 'locked Send must remain visually disabled');
    await page.locator('.btn.p').hover();
    assert.equal(primaryHover, await page.locator('.btn.p').evaluate(el => getComputedStyle(el).backgroundColor), 'primary hover must match the console');
  });
}
