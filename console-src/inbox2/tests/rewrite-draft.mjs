import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.INBOX_TEST_URL||'http://127.0.0.1:8878';
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
page.setDefaultTimeout(10000);
const errors=[],calls=[];
page.on('pageerror',error=>errors.push(error.message));
const now=new Date().toISOString();
const original='Hi there,\n\nI understand you received the wrong color and would like a return label and refund. We will review your request.\n\nThanks,\nButtons Bebe';
const updated='Hi there,\n\nI’m sorry about the color mix-up. Could you let us know which color you received?\n\nThanks,\nButtons Bebe';
const ticket={id:'gorgias:123',subject:'Return request — wrong color',customerName:'Example customer',fromEmail:'customer@example.invalid',channel:'email',status:'open',updatedAt:now,syncedAt:now,readonlyDraft:original,draftSourceMessageId:'456',draftProcessedAt:now,draftAction:'sensitive_draft',draftReason:'The customer reports a color mix-up and requests a return label and refund.',messages:[{id:'456',fromAgent:false,fromName:'Example customer',body:'I received the wrong color. Could I get a return label and refund?',at:now}],shopifyRail:{status:'missing'}};
const other={...ticket,id:'gorgias:789',subject:'A different ticket',readonlyDraft:'The other ticket suggestion.',draftSourceMessageId:'790'};
let mode='ok',delay=null,responseDraft=updated;
await page.route('**/inbox/api/helpdesk',route=>{
  const {tool,arguments:args}=route.request().postDataJSON();
  assert(['helpdesk.capabilities','helpdesk.list_tickets','helpdesk.get_ticket','helpdesk.get_messages'].includes(tool));
  return route.fulfill({json:{ok:true,source:'gorgias_api',...(tool==='helpdesk.get_ticket'?{ticket:args.ticketId===other.id?other:ticket}:tool==='helpdesk.list_tickets'?{tickets:[ticket,other],total:2,nextOffset:null,projection:{complete:true,generatedAt:now}}:{readOnly:true})}});
});
await page.route('**/console/api/**',async route=>{
  const req=route.request();calls.push({path:new URL(req.url()).pathname,body:req.postDataJSON(),headers:req.headers()});
  assert.equal(calls.at(-1).path,'/console/api/ticket/123/rewrite','AI edits must only use the draft-only endpoint');
  assert.equal(req.method(),'POST');assert(!req.headers()['x-inbox-send-access']);
  if(delay)await delay;
  if(mode==='network')return route.abort();
  if(mode==='busy')return route.fulfill({status:503,json:{error:'rewrite_busy_try_later'}});
  if(mode==='timeout')return route.fulfill({status:504,json:{error:'rewrite_timed_out'}});
  if(mode==='auth')return route.fulfill({status:401,json:{error:'not_authenticated'}});
  if(mode==='source')return route.fulfill({status:404,json:{error:'source_message_not_in_console'}});
  return route.fulfill({json:{ok:true,draft:mode==='empty'?' ':responseDraft}}).catch(()=>{});
});
const edit=page.getByRole('button',{name:'Edit suggested reply with AI'}),dialog=page.locator('#draft-rewrite'),input=page.locator('#rewrite-instruction'),submit=page.locator('#rewrite-submit'),body=page.locator('.draft-card > .draft-body'),composer=page.locator('#reply');
async function ready(){await edit.waitFor();}
async function request(instruction='Make it shorter and warmer, and ask which color they received.'){
  await edit.click();await input.fill(instruction);await submit.click();
}
async function refresh(){await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));}
await page.goto(`${base}/inbox/?ticket=gorgias%3A123`);await ready();
assert.equal(await page.getByRole('switch').getAttribute('aria-checked'),'false');
await edit.click();assert(await dialog.isVisible());assert(await submit.isDisabled());
assert(await input.evaluate(el=>el===document.activeElement));
await input.fill('   ');assert(await submit.isDisabled());
await page.keyboard.press('Escape');assert(await dialog.isHidden());assert.equal(calls.length,0);
assert(await edit.evaluate(el=>el===document.activeElement));
await composer.fill('Keep my existing reply.');
let release;delay=new Promise(resolve=>release=resolve);
await request();await page.waitForFunction(()=>document.querySelector('#rewrite-status').textContent.includes('updating'));
assert(await submit.isDisabled());assert(await input.isDisabled());
assert.equal(await body.textContent(),original);
release();delay=null;await dialog.waitFor({state:'hidden'});
assert.equal(calls.length,1);assert.equal(calls[0].body.source_message_id,'456');assert.equal(calls[0].body.draft,original);
assert.equal(calls[0].body.instruction,'Make it shorter and warmer, and ask which color they received.');
assert.equal(await body.textContent(),updated);assert.equal(await composer.inputValue(),'Keep my existing reply.');
assert.match(await page.locator('.draft-heading .badge').textContent(),/Review required/);
await page.reload();await ready();assert.equal(await body.textContent(),updated);
await request('Make this friendlier.');await dialog.waitFor({state:'hidden'});assert.equal(calls.at(-1).body.draft,updated);
assert.equal(await composer.inputValue(),'Keep my existing reply.');
await composer.fill('');await page.getByRole('button',{name:'Use draft',exact:true}).click();assert.equal(await composer.inputValue(),updated);
// Errors keep both the suggestion and the instructions available for retry.
for(const [failure,message] of [['busy','another edit'],['timeout','too long'],['source','still syncing'],['empty','could not update'],['network','could not update'],['auth','session has expired']]){
  mode=failure;await request('Keep these instructions for retry.');
  await page.locator('#rewrite-error').waitFor();
  assert((await page.locator('#rewrite-error').textContent()).includes(message));
  assert.equal(await input.inputValue(),'Keep these instructions for retry.');assert(!(await submit.isDisabled()));
  assert.equal(await body.textContent(),updated);assert.equal(await composer.inputValue(),updated);
  await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
}
mode='ok';
// A newer projection cannot be overwritten by an older in-flight edit.
delay=new Promise(resolve=>release=resolve);await request();
ticket.readonlyDraft='A newer suggestion for the current message.';ticket.draftProcessedAt='2026-09-26T12:00:00Z';
await refresh();await body.filter({hasText:ticket.readonlyDraft}).waitFor();
release();delay=null;await page.locator('#rewrite-error').waitFor();
assert.match(await page.locator('#rewrite-error').textContent(),/changed/);
assert.equal(await body.textContent(),ticket.readonlyDraft);
await page.keyboard.press('Escape');
await page.reload();await ready();assert.equal(await body.textContent(),ticket.readonlyDraft);
// Cancelled work cannot apply later, even after another edit dialog is opened.
delay=new Promise(resolve=>release=resolve);await request();await page.keyboard.press('Escape');
await edit.click();await input.fill('A different set of instructions.');release();delay=null;
await page.waitForTimeout(100);assert.equal(await body.textContent(),ticket.readonlyDraft);
assert.equal(await input.inputValue(),'A different set of instructions.');await page.keyboard.press('Escape');
// Navigation closes a pending edit and isolates its result from both tickets.
delay=new Promise(resolve=>release=resolve);await request();
await page.evaluate(()=>{history.pushState({},'', '/inbox/?ticket=gorgias%3A789');dispatchEvent(new PopStateEvent('popstate'));});
await body.filter({hasText:other.readonlyDraft}).waitFor();assert(await dialog.isHidden());release();delay=null;
await page.waitForTimeout(100);assert.equal(await body.textContent(),other.readonlyDraft);
await page.goto(`${base}/inbox/?ticket=gorgias%3A123`);await ready();assert.equal(await body.textContent(),ticket.readonlyDraft);
// Source-less or superseded suggestions cannot be edited.
ticket.draftSourceMessageId='';await page.reload();await ready();assert(await edit.isDisabled());
ticket.draftSourceMessageId='456';ticket.draftSuperseded=true;await page.reload();await composer.waitFor();assert.equal(await edit.count(),0);
ticket.draftSuperseded=false;ticket.readonlyDraft=original;await page.reload();await ready();
fs.mkdirSync('/tmp/buttonsbebe-inbox-rewrite',{recursive:true});
for(const width of [1440,1040,650,390,320]){
  await page.setViewportSize({width,height:900});await edit.scrollIntoViewIfNeeded();
  assert(await edit.isVisible());
  const size=await page.evaluate(()=>({width:document.body.scrollWidth,viewport:innerWidth}));assert(size.width<=size.viewport,`Page overflows at ${width}`);
  if(width===1440)await page.locator('.draft-card').screenshot({path:'/tmp/buttonsbebe-inbox-rewrite/reply-card.png'});
  await edit.click();await input.fill('Make it shorter and warmer, and ask which color they received.');
  const box=await dialog.boundingBox();assert(box.x>=0&&box.x+box.width<=width,`Dialog overflows at ${width}`);
  assert(await submit.isVisible());
  if(width===1440||width===390)await page.screenshot({path:`/tmp/buttonsbebe-inbox-rewrite/editor-${width}.png`});
  await page.keyboard.press('Escape');
}
assert.equal(await page.getByRole('switch').getAttribute('aria-checked'),'false');
assert.equal(errors.length,0,errors.join('\n'));
console.log('Passed: AI edit payload, local revision persistence, follow-up edits, explicit Use draft, composer preservation, errors/retry, stale/cancelled/navigated responses, read-only boundary, and desktop/mobile dialog.');
await browser.close();
