import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.INBOX_TEST_URL||'http://127.0.0.1:8878';
const artifacts=process.env.INBOX_TEST_ARTIFACT_DIR||path.join(os.tmpdir(),'buttonsbebe-inbox-send');
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.INBOX_TEST_BROWSER?{executablePath:process.env.INBOX_TEST_BROWSER}:{})});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
page.setDefaultTimeout(10000);
const errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));
const now=new Date().toISOString();
const ticket={id:'gorgias:123',subject:'Question about my order',customerName:'Example customer',fromEmail:'customer@example.com',channel:'email',status:'open',updatedAt:now,syncedAt:now,messages:[{id:'456',fromAgent:false,fromName:'Example customer',body:'When will my order arrive?',at:now}],shopifyRail:{status:'missing'}};
const context={inboxTicketId:ticket.id,ticketId:'123',sourceMessageId:'456',recipient:'customer@example.com',channel:'email',sourceMessageText:'When will my order arrive?',draftRevision:'a'.repeat(64),contextId:'b'.repeat(64),unresolvedActions:[]};
let accessFailure=false,sendMode='sent',statusMode='sent',reviewDelay=null,statusDelay=null,statusOperation='',expirySeconds=1800;
await page.route('**/inbox/api/helpdesk',route=>{
  const name=route.request().postDataJSON().tool;assert(['helpdesk.capabilities','helpdesk.list_tickets','helpdesk.get_ticket','helpdesk.get_messages'].includes(name));
  return route.fulfill({json:{ok:true,source:'gorgias_api',...(name==='helpdesk.get_ticket'?{ticket}:name==='helpdesk.list_tickets'?{tickets:[ticket],total:1,nextOffset:null,projection:{complete:true,generatedAt:now}}:{readOnly:true})}});
});
await page.route('**/console/api/**',async route=>{
  const request=route.request(),url=new URL(request.url());calls.push({path:url.pathname,method:request.method(),body:request.postDataJSON(),headers:request.headers()});
  if(url.pathname.endsWith('/send-access')){
    if(accessFailure)return route.fulfill({status:503,json:{error:'send_access_unavailable'}});
    const enabled=request.postDataJSON().enabled;
    return route.fulfill({json:{ok:true,enabled,...(enabled?{token:'test-grant',expiresAt:Math.ceil(Date.now()/1000)+expirySeconds}:{})}});
  }
  if(url.pathname.includes('/review-context/')){if(reviewDelay)await reviewDelay;return route.fulfill({json:{ok:true,context}});}
  if(url.pathname.endsWith('/send')){
    assert.equal(request.headers()['x-inbox-send-access'],'test-grant');
    assert.equal(request.postDataJSON().confirmed,true);
    if(sendMode==='abort')return route.abort();
    // Match preflight_refusal: echo identity only after ruling out a prior intent.
    if(sendMode==='stale')return route.fulfill({status:409,json:{ok:false,error:'new_customer_message_refresh_ticket',delivery_status:'not_attempted',operation_id:request.postDataJSON().operation_id}});
    if(sendMode==='chronology')return route.fulfill({status:409,json:{ok:false,error:'message_chronology_unavailable',delivery_status:'not_attempted',operation_id:request.postDataJSON().operation_id}});
    if(sendMode==='preflight-uncertain')return route.fulfill({status:503,json:{ok:false,error:'review_context_unavailable'}});
    if(sendMode==='mismatch')return route.fulfill({json:{ok:true,delivery_status:'sent',operation_id:'wrong-operation'}});
    if(sendMode==='error-mismatch')return route.fulfill({status:409,json:{error:'remote_delivery_failed',delivery_status:'failed',operation_id:'wrong-operation'}});
    return route.fulfill({status:sendMode==='sent'?200:202,json:{ok:sendMode==='sent',delivery_status:sendMode,operation_id:request.postDataJSON().operation_id}});
  }
  if(url.pathname.includes('/actions/')){if(statusDelay)await statusDelay;const operation=statusOperation||url.pathname.split('/').at(-1);return route.fulfill({status:statusMode==='failed'?409:200,json:{ok:statusMode==='sent',delivery_status:statusMode,operation_id:operation}});}
  throw new Error('Unexpected console request: '+url.pathname);
});
const toggle=page.getByRole('switch'),editor=page.locator('#reply'),send=page.locator('[data-action="review-send"]'),confirm=page.locator('[data-action="confirm-send"]');
async function flip(){await toggle.click();await page.waitForFunction(()=>!document.querySelector('[role="switch"]').disabled);}
const writes=()=>calls.filter(c=>c.path.endsWith('/send'));
await page.goto(`${base}/inbox/?ticket=gorgias%3A123`);await editor.waitFor();
assert.equal(await toggle.getAttribute('aria-checked'),'false');assert(await send.isHidden());
await editor.fill('Thanks for reaching out.');await page.locator('#details-tab').click();await page.locator('#conversation-tab').click();
assert.equal(calls.length,0,'Browsing and editing must not call send routes');
await flip();assert.equal(await toggle.getAttribute('aria-checked'),'true');assert(await send.isVisible());assert.equal(writes().length,0);
await send.click();await page.getByRole('dialog').waitFor();assert.equal(writes().length,0);
assert.match(await page.locator('#send-review-recipient').textContent(),/customer@example.com/);
assert.equal(await page.locator('#send-review-text').textContent(),'Thanks for reaching out.');
await page.keyboard.press('Escape');assert.equal(writes().length,0);assert.equal(await editor.inputValue(),'Thanks for reaching out.');
await send.click();await confirm.click();await page.getByText('Reply sent via Gorgias.',{exact:true}).first().waitFor();
assert.equal(writes().length,1);assert.equal(await editor.inputValue(),'');assert.equal(writes()[0].body.expected_recipient,context.recipient);
assert.equal(writes()[0].body.context_id,context.contextId);assert.equal(writes()[0].body.approve_learning,false);
await editor.fill('Keep this draft when toggling off.');await flip();assert(await send.isHidden());assert.equal(await editor.inputValue(),'Keep this draft when toggling off.');
await flip();await page.reload();await editor.waitFor();assert.equal(await toggle.getAttribute('aria-checked'),'false');assert(await send.isHidden());
assert(!(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).includes('test-grant'),'Grant must never be persisted');
accessFailure=true;await flip();assert.equal(await toggle.getAttribute('aria-checked'),'false');accessFailure=false;
await flip();sendMode='stale';await send.click();await confirm.click();await page.getByText('Reply not sent. Your draft is saved.').waitFor();
const chronologyDraft=await editor.inputValue();sendMode='chronology';await send.click();await confirm.click();await page.getByText('Message dates are incomplete. Staff must check this conversation before a reply can be sent.').waitFor();assert.equal(await editor.inputValue(),chronologyDraft);
assert.equal(await editor.inputValue(),'Keep this draft when toggling off.');assert(!(await send.isDisabled()));
sendMode='pending';await send.click();await confirm.click();await page.getByRole('button',{name:'Check status'}).waitFor();assert(await send.isDisabled());
await flip();await page.getByRole('button',{name:'Check status'}).click();await page.getByText('Reply sent via Gorgias.',{exact:true}).first().waitFor();
const before=writes().length;assert.equal(await toggle.getAttribute('aria-checked'),'false');assert.equal(writes().length,before,'Status checks never resend');assert.equal(await editor.inputValue(),'');
await flip();await editor.fill('A draft after a network problem.');sendMode='abort';await send.click();await confirm.click();await page.getByRole('button',{name:'Check status'}).waitFor();assert(await send.isDisabled());
await page.reload();await editor.waitFor();assert.equal(await toggle.getAttribute('aria-checked'),'false');assert.equal(await editor.inputValue(),'A draft after a network problem.');
await page.getByRole('button',{name:'Check status'}).click();await page.getByText('Reply sent via Gorgias.',{exact:true}).first().waitFor();
assert.equal(await editor.inputValue(),'');
await flip();await editor.fill('Keep this draft after failure.');sendMode='pending';await send.click();await confirm.click();await page.getByRole('button',{name:'Check status'}).waitFor();statusMode='failed';await page.getByRole('button',{name:'Check status'}).click();await page.getByText('Gorgias reports delivery failed. Inspect it in Gorgias.').waitFor();assert.equal(await editor.inputValue(),'Keep this draft after failure.');assert(!(await send.isDisabled()));statusMode='sent';

await editor.fill('Submitted revision.');sendMode='pending';await send.click();await confirm.click();await page.getByRole('button',{name:'Check status'}).waitFor();await editor.fill('A newer revision.');await page.getByRole('button',{name:'Check status'}).click();await page.getByText('Reply sent via Gorgias.',{exact:true}).first().waitFor();assert.equal(await editor.inputValue(),'A newer revision.');
await editor.fill('Identical text.');await send.click();await confirm.click();await page.getByRole('button',{name:'Check status'}).waitFor();await editor.fill('Temporary edit.');await editor.fill('Identical text.');await page.getByRole('button',{name:'Check status'}).click();assert.equal(await editor.inputValue(),'Identical text.');

sendMode='sent';await editor.fill('Reviewed before preparation.');let release;reviewDelay=new Promise(resolve=>release=resolve);await send.click();await page.waitForFunction(()=>document.querySelector('[data-action="review-send"]').textContent==='Preparing…');await editor.fill('Typed while preparation was pending.');release();reviewDelay=null;await page.getByRole('dialog').waitFor();assert.equal(await page.locator('#send-review-text').textContent(),'Reviewed before preparation.');await confirm.click();await page.getByText('Reply sent via Gorgias.',{exact:true}).first().waitFor();assert.equal(await editor.inputValue(),'Typed while preparation was pending.');

sendMode='mismatch';await editor.fill('Keep after mismatched success.');await send.click();await confirm.click();await page.getByText('Delivery is unconfirmed. Check status before sending again.',{exact:true}).waitFor();assert.equal(await editor.inputValue(),'Keep after mismatched success.');
await page.evaluate(()=>{const actions=JSON.parse(localStorage.getItem('bb-inbox-send-actions-v1'));delete actions['gorgias:123'];localStorage.setItem('bb-inbox-send-actions-v1',JSON.stringify(actions));});
await page.reload();await editor.waitFor();await flip();sendMode='error-mismatch';await send.click();await confirm.click();await page.getByText('Delivery is unconfirmed. Check status before sending again.',{exact:true}).waitFor();assert.equal(await editor.inputValue(),'Keep after mismatched success.');

await page.evaluate(()=>localStorage.setItem('bb-inbox-send-actions-v1','{}'));
await page.reload();await editor.waitFor();await flip();sendMode='preflight-uncertain';await send.click();await confirm.click();await page.getByText('Delivery is unconfirmed. Check status before sending again.',{exact:true}).waitFor();
assert(await send.isDisabled());assert.equal(await editor.inputValue(),'Keep after mismatched success.');
assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('bb-inbox-send-actions-v1'))['gorgias:123'].status),'unknown','Unreadable or existing intents must not be treated as definitely refused');

await page.evaluate(()=>{localStorage.setItem('bb-inbox2-composer-v1',JSON.stringify({'gorgias:123':{body:'Legacy draft without revision',at:1}}));localStorage.setItem('bb-inbox-send-actions-v1',JSON.stringify({'gorgias:123':{operationId:'legacy-operation',status:'pending'}}));});
sendMode='sent';statusMode='sent';await page.reload();await editor.waitFor();await page.getByRole('button',{name:'Check status'}).click();await page.getByText('Reply sent via Gorgias.',{exact:true}).first().waitFor();assert.equal(await editor.inputValue(),'Legacy draft without revision');

await page.evaluate(()=>{localStorage.setItem('bb-inbox2-composer-v1',JSON.stringify({'gorgias:123':{body:'Draft for operation B',at:2,version:'revision-b'}}));localStorage.setItem('bb-inbox-send-actions-v1',JSON.stringify({'gorgias:123':{operationId:'operation-a',status:'pending',submittedDraftVersion:'revision-b'}}));});
await page.reload();await editor.waitFor();statusDelay=new Promise(resolve=>release=resolve);await page.getByRole('button',{name:'Check status'}).click();await page.evaluate(()=>{localStorage.setItem('bb-inbox-send-actions-v1',JSON.stringify({'gorgias:123':{operationId:'operation-b',status:'pending',submittedDraftVersion:'revision-b'}}));});release();statusDelay=null;await page.waitForTimeout(100);
assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('bb-inbox-send-actions-v1'))['gorgias:123']),{operationId:'operation-b',status:'pending',submittedDraftVersion:'revision-b'});assert.equal(await editor.inputValue(),'Draft for operation B');
await page.evaluate(()=>localStorage.setItem('bb-inbox-send-actions-v1','{}'));await page.reload();await editor.waitFor();

await flip();reviewDelay=new Promise(resolve=>release=resolve);await send.click();await flip();release();await page.waitForTimeout(100);reviewDelay=null;
assert.equal(await page.getByRole('dialog').count(),0);assert.equal(await toggle.getAttribute('aria-checked'),'false');
expirySeconds=1;await flip();await send.click();await page.getByRole('dialog').waitFor();await page.waitForTimeout(2100);assert.equal(await toggle.getAttribute('aria-checked'),'false');assert.equal(await page.getByRole('dialog').count(),0);
expirySeconds=1800;await flip();sendMode='sent';
fs.mkdirSync(artifacts,{recursive:true});
for(const width of [1440,1040,650,390,320]){
  await page.setViewportSize({width,height:900});
  const size=await page.evaluate(()=>({w:document.body.scrollWidth,iw:innerWidth}));assert(size.w<=size.iw,`overflow at ${width}`);
  assert(await toggle.isVisible());assert(await page.locator('.access-switch').isVisible());
  await page.locator('#toast').evaluate(el=>el.hidden=true);
  await page.locator('#conversation').evaluate(el=>el.scrollTop=el.scrollHeight);
  if(width===1440||width===390)await page.screenshot({path:path.join(artifacts,`toggle-${width}.png`)});
  await send.click();await page.getByRole('dialog').waitFor();
  const box=await page.getByRole('dialog').boundingBox();assert(box.x>=0&&box.x+box.width<=width,`dialog overflow at ${width}`);
  await page.locator('#cancel-send').click();
}
assert.equal(errors.length,0,errors.join('\n'));
console.log('Passed: grant and confirmation safety, immediate/delayed/reloaded/error delivery, revision identity, review-time edits, operation matching, legacy retention, no resend, expiry, and responsive controls.');
await browser.close();
