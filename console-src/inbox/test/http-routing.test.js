import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const source = readFileSync(new URL('../js/shop/helpdesk-client.js', import.meta.url), 'utf8');
const toolsUrl = new URL('../js/shop/helpdesk-tools.js', import.meta.url).href;

test('browser reads stay on the inbox mount rather than the dashboard API', async () => {
  for (const [moduleUrl, endpoint] of [
    ['https://support.example/inbox/js/shop/helpdesk-client.js', '/inbox/console/api/helpdesk'],
    ['http://localhost:8766/js/shop/helpdesk-client.js', '/console/api/helpdesk'],
    ['file:///test/js/shop/helpdesk-client.js', '/console/api/helpdesk'],
  ]) {
    // Execute the real client under each deployed module URL without a server.
    const code = source.replace('"./helpdesk-tools.js"', JSON.stringify(toolsUrl))
      .replaceAll('import.meta.url', JSON.stringify(moduleUrl));
    const {createHelpdeskClient} = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
    const calls = [];
    const client = createHelpdeskClient({fetch: async (url, options) => {
      calls.push({url, method: options.method, body: JSON.parse(options.body)});
      return {status: 200, redirected: false, json: async () => ({ok: true, source: 'inbox', tickets: []})};
    }});
    await client.listTickets({view: 'all'});
    assert.deepEqual(calls, [{url: endpoint, method: 'POST', body: {tool: 'helpdesk.list_tickets', arguments: {view: 'all'}}}], moduleUrl);
  }
});
