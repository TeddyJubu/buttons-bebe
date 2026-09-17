import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync, statSync} from 'node:fs';
import {dirname, join, posix, sep} from 'node:path';
import {fileURLToPath} from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const inbox = join(here, '..');
const manifest = new Set(JSON.parse(readFileSync(join(inbox, 'static-manifest.json'), 'utf8')));

// ponytail: js reachable from production boot must be served; the server 404s
// anything absent from static-manifest.json, so this registry is the allowlist.
const TEST_ONLY = new Set([
  'js/fixtures/demo-inbox.js',
  'js/review-blocks.js',
  'js/shop/fixture-shop.js',
  'js/shop/helpdesk-shop.js',
  'js/shop/live-catalog.js',
  'js/tissues/view.js',
]);

function allJs(dir, base) {
  const out = [];
  for (const entry of readdirSync(dir).sort()) {
    const full = join(dir, entry);
    const rel = base ? `${base}/${entry}` : entry;
    if (statSync(full).isDirectory()) out.push(...allJs(full, rel));
    else if (entry.endsWith('.js')) out.push(rel.split(sep).join(posix.sep));
  }
  return out;
}

test('every non-manifest js file is registered test-only, with no stale entries', () => {
  const found = allJs(join(inbox, 'js'), 'js').filter((file) => !manifest.has(file));
  assert.deepEqual(new Set(found), TEST_ONLY);
});

test('no manifest js file imports anything outside the manifest', () => {
  const specifier = /(?:from\s+|import\s*\(\s*|import\s+)['"]([^'"]+)['"]/g;
  for (const file of [...manifest].filter((entry) => entry.endsWith('.js')).sort()) {
    const text = readFileSync(join(inbox, file), 'utf8');
    let match;
    while ((match = specifier.exec(text)) !== null) {
      const target = match[1];
      if (!target.startsWith('.')) continue; // bare, absolute, or URL import
      const resolved = posix.normalize(posix.join(posix.dirname(file), target));
      assert.ok(manifest.has(resolved), `${file} imports ${target} (resolves to ${resolved}), which static-manifest.json does not serve`);
    }
  }
});
