import test from 'node:test';
import assert from 'node:assert/strict';
import { createRailOrgan } from '../js/tissues/rail.js';
import { createMailbox } from '../js/mailbox.js';

test('observed ticket renders Shopify snapshot without live provider calls', () => {
  const rail = createRailOrgan({shop:{observedHistory:true}, mailbox:createMailbox()});
  rail.loadSnapshot({customer:{displayName:'Test <script>',defaultEmailAddress:{emailAddress:'qa@example.com'},numberOfOrders:'2'},
    order:{id:'o1',name:'#10319148',lineItems:{nodes:[]}},
    returns:{returns:{nodes:[{id:'r1',status:'OPEN'}]}}, history:[],fetchedAt:'2026-09-08T00:00:00Z',stale:true},'t1');
  const html = rail.render();
  assert.match(html, /#10319148/);
  assert.match(html, /Test &lt;script&gt;/);
  assert.match(html, /Return on file/);
  assert.match(html, /Refresh delayed/);
  assert.match(html, /Open · 1 return/);
  assert.doesNotMatch(html, /Open · 1 item|No gift cards|No invoice|No warranty|No ETA/);
  assert.ok(html.indexOf('data-tissue="returns"') < html.indexOf('data-tissue="order"'));
  assert.equal((html.match(/Test &lt;script&gt;/g) || []).length, 1);
  assert.equal(rail.snapshot().open.returns, true);
  rail.loadSnapshot({customer:{displayName:'Second customer'},history:[]},'t2');
  const next = rail.render();
  assert.doesNotMatch(next, /#10319148|Return on file|Test &lt;/);
  assert.match(next, /matching order to see its returns/);
  assert.equal(rail.snapshot().open.returns, false);
});
