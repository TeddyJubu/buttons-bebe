import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.INBOX_TEST_URL || 'http://127.0.0.1:8878';
const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
const page = await browser.newPage({viewport:{width:1280,height:800}});
page.setDefaultTimeout(10000);
const errors = [], calls = [], writes = [];
const stamp = '2026-10-05T12:00:00Z';
const rows = Array.from({length:56}, (_, i) => ({
  id:`gorgias:${700+i}`, customerName:`Example ${i}`, fromEmail:`example-${i}@example.invalid`,
  status:'open', channel:'email', gorgiasPriority:'normal', updatedAt:stamp,
  snippet:'A fictional question', messages:[], shopifyRail:{status:'unavailable'},
}));
let releaseOpen, openStarted;
const heldOpen = new Promise(resolve => {releaseOpen=resolve;});
const started = new Promise(resolve => {openStarted=resolve;});
let openCompleted = false;
page.on('pageerror', error => errors.push(error.message));
await page.route('**/console/api/**', async route => {
  writes.push(route.request().url());
  await route.fulfill({status:403,json:{error:'inbox_read_only'}});
});
await page.route('**/inbox/api/helpdesk', async route => {
  const payload = route.request().postDataJSON();
  calls.push(payload);
  const tool = payload.tool.replace('helpdesk.','');
  assert(['capabilities','list_tickets','get_ticket','get_messages'].includes(tool));
  const args = payload.arguments || {};
  if (tool==='list_tickets' && args.view==='open') {
    openStarted();
    await heldOpen;
  }
  const selected = args.view==='closed' ? [{...rows[0],id:'gorgias:999',status:'closed'}] : rows;
  const offset = args.offset || 0;
  const limit = args.limit || 25;
  await route.fulfill({json:{ok:true,source:'gorgias_api',operatorEmail:'support@example.invalid',
    categoryAvailability:{assigned:true,snoozed:true,spam:true,trash:true},
    ...(tool==='list_tickets' ? {tickets:selected.slice(offset,offset+limit),total:selected.length,
      nextOffset:offset+limit<selected.length?offset+limit:null,projection:{complete:true,generatedAt:stamp}}
      : tool==='get_ticket' ? {ticket:rows.find(t=>t.id===args.ticketId)||rows[0]} : {}),
  }});
  if (tool==='list_tickets' && args.view==='open') openCompleted=true;
});
const visibleIds = () => page.locator('.ticket-row').evaluateAll(nodes=>nodes.map(node=>node.dataset.ticket));
try {
  await page.goto(`${base}/inbox/?ticket=gorgias%3A700`);
  await page.waitForFunction(()=>document.querySelectorAll('.ticket-row').length===25);
  assert.deepEqual(await visibleIds(),['gorgias:700','gorgias:701','gorgias:702','gorgias:703','gorgias:704','gorgias:705','gorgias:706','gorgias:707','gorgias:708','gorgias:709','gorgias:710','gorgias:711','gorgias:712','gorgias:713','gorgias:714','gorgias:715','gorgias:716','gorgias:717','gorgias:718','gorgias:719','gorgias:720','gorgias:721','gorgias:722','gorgias:723','gorgias:724']);
  assert.equal(await page.locator('#count').textContent(),'Page 1 · 25 of 25 loaded Gorgias shown · 56 total');
  await page.locator('#select-page').check();
  await page.locator('[data-action="page-next"]').click();
  await page.waitForFunction(()=>document.querySelector('.ticket-row')?.dataset.ticket==='gorgias:725');
  assert.deepEqual(await visibleIds(),['gorgias:725','gorgias:726','gorgias:727','gorgias:728','gorgias:729','gorgias:730','gorgias:731','gorgias:732','gorgias:733','gorgias:734','gorgias:735','gorgias:736','gorgias:737','gorgias:738','gorgias:739','gorgias:740','gorgias:741','gorgias:742','gorgias:743','gorgias:744','gorgias:745','gorgias:746','gorgias:747','gorgias:748','gorgias:749']);
  assert.equal(await page.locator('#selection-count').textContent(),'0 selected on this page');
  await page.locator('[data-action="page-next"]').click();
  await page.waitForFunction(()=>document.querySelector('.ticket-row')?.dataset.ticket==='gorgias:750');
  assert.deepEqual(await visibleIds(),['gorgias:750','gorgias:751','gorgias:752','gorgias:753','gorgias:754','gorgias:755']);
  assert.equal(await page.locator('#count').textContent(),'Page 3 · 6 of 6 loaded Gorgias shown · 56 total');
  assert(await page.locator('[data-action="page-next"]').isDisabled());
  await page.locator('[data-action="page-prev"]').click();
  await page.waitForFunction(()=>document.querySelector('.ticket-row')?.dataset.ticket==='gorgias:725');
  assert.deepEqual(await visibleIds(),['gorgias:725','gorgias:726','gorgias:727','gorgias:728','gorgias:729','gorgias:730','gorgias:731','gorgias:732','gorgias:733','gorgias:734','gorgias:735','gorgias:736','gorgias:737','gorgias:738','gorgias:739','gorgias:740','gorgias:741','gorgias:742','gorgias:743','gorgias:744','gorgias:745','gorgias:746','gorgias:747','gorgias:748','gorgias:749']);
  await page.locator('.view-menu>summary').click();await page.locator('[data-view="open"]').click();
  let startTimer;
  try {await Promise.race([started,new Promise((_,reject)=>{startTimer=setTimeout(()=>reject(new Error('Expected open-view list_tickets request did not start within 10 seconds.')),10000);})]);}
  finally {clearTimeout(startTimer);}
  await page.locator('.view-menu>summary').click();await page.locator('[data-view="closed"]').click();
  await page.waitForFunction(()=>document.querySelector('.ticket-row')?.dataset.ticket==='gorgias:999');
  releaseOpen();
  const deadline = Date.now()+10000;
  while (!openCompleted && Date.now()<deadline) await new Promise(resolve=>setTimeout(resolve,10));
  assert(openCompleted,'held response must finish before checking the final view');
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.deepEqual(await visibleIds(),['gorgias:999'],'older delayed view must not replace the latest view');
  assert.equal(await page.locator('[data-view="closed"]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#count').textContent(),'Page 1 · 1 of 1 loaded Gorgias shown · 1 total');
  assert.equal(await page.locator('#selection-count').textContent(),'0 selected on this page');
  assert.deepEqual(errors,[]);
  assert.deepEqual(writes,[]);
  const evidence = process.env.INBOX_EVIDENCE_DIR || fs.mkdtempSync(path.join(os.tmpdir(),'bb-ticket-views-'));
  fs.mkdirSync(evidence,{recursive:true});
  await page.screenshot({path:path.join(evidence,'ticket-views-latest-response.png')});
  fs.writeFileSync(path.join(evidence,'ticket-views-receipt.json'),JSON.stringify({
    pagination:'PASS',delayedOlderView:'PASS',finalIds:await visibleIds(),calls,consoleWrites:writes,pageErrors:errors,
    scope:'Real Inbox frontend with synthetic response timing; provider query correctness is checked separately.',
  },null,2));
  console.log('PASS: literal three-page IDs and counts; delayed older view cannot overwrite the latest view; no console writes.');
} finally {
  releaseOpen();
  await browser.close();
}
