import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],...(process.env.INBOX_TEST_BROWSER?{executablePath:process.env.INBOX_TEST_BROWSER}:{})});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
page.setDefaultTimeout(10000);
const errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));
const now=new Date().toISOString();
const ticket={id:'gorgias:123',subject:'Question about my order',customerName:'Example customer',fromEmail:'customer@example.com',channel:'email',status:'open',updatedAt:now,syncedAt:now,messages:[{id:'456',fromAgent:false,fromName:'Example customer',body:'When will my order arrive?',at:now}],shopifyRail:{status:'missing'}};
const context={inboxTicketId:ticket.id,ticketId:'123',sourceMessageId:'456',recipient:'customer@example.com',channel:'email',sourceMessageText:'When will my order arrive?',draftRevision:'a'.repeat(64),contextId:'b'.repeat(64),unresolvedActions:[]};
let accessFailure=false,sendMode='sent',statusMode='sent',reviewDelay=null,expirySeconds=1800;
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
    if(sendMode==='stale')return route.fulfill({status:409,json:{error:'new_customer_message_refresh_ticket',delivery_status:'not_attempted'}});
    return route.fulfill({status:sendMode==='sent'?200:202,json:{ok:sendMode==='sent',delivery_status:sendMode,operation_id:request.postDataJSON().operation_id}});
  }
  if(url.pathname.includes('/actions/'))return route.fulfill({status:statusMode==='failed'?409:200,json:{ok:statusMode==='sent',delivery_status:statusMode,operation_id:url.pathname.split('/').at(-1)}});
  throw new Error('Unexpected console request: '+url.pathname);
});
const toggle=page.getByRole('switch'),editor=page.locator('#reply'),send=page.locator('[data-action="review-send"]'),confirm=page.locator('[data-action="confirm-send"]');
async function flip(){await toggle.click();await page.waitForFunction(()=>!document.querySelector('[role="switch"]').disabled);}
const writes=()=>calls.filter(c=>c.path.endsWith('/send'));
await page.goto('http://127.0.0.1:8878/inbox/?ticket=gorgias%3A123');await editor.waitFor();
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
assert.equal(await editor.inputValue(),'Keep this draft when toggling off.');assert(!(await send.isDisabled()));
sendMode='pending';await send.click();await confirm.click();await page.getByRole('button',{name:'Check status'}).waitFor();assert(await send.isDisabled());
await flip();await page.getByRole('button',{name:'Check status'}).click();await page.getByText('Reply sent via Gorgias.',{exact:true}).first().waitFor();
const before=writes().length;assert.equal(await toggle.getAttribute('aria-checked'),'false');assert.equal(writes().length,before,'Status checks never resend');
await flip();await editor.fill('A draft after a network problem.');sendMode='abort';await send.click();await confirm.click();await page.getByRole('button',{name:'Check status'}).waitFor();assert(await send.isDisabled());
await page.reload();await editor.waitFor();assert.equal(await toggle.getAttribute('aria-checked'),'false');assert.equal(await editor.inputValue(),'A draft after a network problem.');
await page.getByRole('button',{name:'Check status'}).click();await page.getByText('Reply sent via Gorgias.',{exact:true}).first().waitFor();
// Provider-reported failure from a status check must leave the pending state.
await flip();sendMode='pending';await send.click();await confirm.click();await page.getByRole('button',{name:'Check status'}).waitFor();statusMode='failed';await page.getByRole('button',{name:'Check status'}).click();await page.getByText('Gorgias reports delivery failed. Inspect it in Gorgias.').waitFor();assert(!(await send.isDisabled()));statusMode='sent';await flip();
// A delayed context response cannot open a send dialog after switching off.
await flip();let release;reviewDelay=new Promise(resolve=>release=resolve);await send.click();await flip();release();await page.waitForTimeout(100);reviewDelay=null;
assert.equal(await page.getByRole('dialog').count(),0);assert.equal(await toggle.getAttribute('aria-checked'),'false');
// Expiry also removes authority and closes an already prepared confirmation.
expirySeconds=1;await flip();await send.click();await page.getByRole('dialog').waitFor();await page.waitForTimeout(2100);assert.equal(await toggle.getAttribute('aria-checked'),'false');assert.equal(await page.getByRole('dialog').count(),0);
expirySeconds=1800;await flip();sendMode='sent';
fs.mkdirSync('/tmp/buttonsbebe-inbox-send',{recursive:true});
for(const width of [1440,1040,650,390,320]){
  await page.setViewportSize({width,height:900});
  const size=await page.evaluate(()=>({w:document.body.scrollWidth,iw:innerWidth}));assert(size.w<=size.iw,`overflow at ${width}`);
  assert(await toggle.isVisible());assert(await page.locator('.access-switch').isVisible());
  await page.locator('#toast').evaluate(el=>el.hidden=true);
  await page.locator('#conversation').evaluate(el=>el.scrollTop=el.scrollHeight);
  if(width===1440||width===390)await page.screenshot({path:`/tmp/buttonsbebe-inbox-send/toggle-${width}.png`});
  await send.click();await page.getByRole('dialog').waitFor();
  const box=await page.getByRole('dialog').boundingBox();assert(box.x>=0&&box.x+box.width<=width,`dialog overflow at ${width}`);
  await page.locator('#cancel-send').click();
}
assert.equal(errors.length,0,errors.join('\n'));
console.log('Passed: off by default, explicit grant, confirmation/cancel, payload identity, delivery states, no duplicate clicks, off/reload/expiry, failed enable, draft retention, late review, responsive switch/dialog, no browser errors.');
await browser.close();
