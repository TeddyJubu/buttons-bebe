import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=fileURLToPath(new URL('../../../',import.meta.url));
const python=process.env.INBOX_TEST_PYTHON||process.env.QA_PYTHON||'python3';
const evidence=process.env.INBOX_EVIDENCE_DIR||fs.mkdtempSync(path.join(os.tmpdir(),'bb-inbox-fixture-runtime-'));
fs.mkdirSync(evidence,{recursive:true});
const reservation=net.createServer();
await new Promise((resolve,reject)=>{reservation.once('error',reject);reservation.listen(0,'127.0.0.1',resolve);});
const port=reservation.address().port;
await new Promise(resolve=>reservation.close(resolve));
const base=`http://127.0.0.1:${port}`,ticketId='gorgias:841000000000001';
const child=spawn(python,['-u',path.join(root,'testing/serve_inbox_fixture_stack.py'),'--repo',root,'--port',String(port)],{cwd:root,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
let logs='',exitResult=null,spawnError=null,browser=null,context=null,videoPath=null,receipt=null;
const append=data=>{logs=(logs+data.toString()).slice(-20000);};
child.stdout.on('data',append);child.stderr.on('data',append);
child.once('error',error=>{spawnError=error;});
const exited=new Promise(resolve=>child.once('exit',(code,signal)=>{exitResult={code,signal};resolve(exitResult);}));
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let deadline;
const bounded=new Promise((_,reject)=>{deadline=setTimeout(()=>{child.kill('SIGINT');void browser?.close().catch(()=>{});reject(new Error(`Fixture browser exceeded 110 seconds. ${logs}`));},110000);});
async function json(url,options={}){const response=await fetch(url,{...options,signal:AbortSignal.timeout(1500)});assert(response.ok,`HTTP ${response.status} from ${url}`);return response.json();}
async function actualTicket(){return json(`${base}/inbox/api/helpdesk`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool:'helpdesk.get_ticket',arguments:{ticketId}})});}
async function ready(){const end=Date.now()+30000;let lastError;while(Date.now()<end){if(spawnError)throw spawnError;if(exitResult)throw new Error(`Fixture process exited ${JSON.stringify(exitResult)}. ${logs}`);try{const health=await json(`${base}/health`),diagnostics=await json(`${base}/__fixture__/diagnostics`);if(health.ok&&diagnostics.fixtureDataReady&&diagnostics.syncComplete){const response=await actualTicket();if(response.ok&&response.ticket?.id===ticketId&&response.ticket.messages?.length===6)return {diagnostics,ticket:response.ticket};}}catch(error){lastError=error.message;}await wait(100);}throw new Error(`Fixture readiness failed. ${lastError}. ${logs}`);}
async function run(){
  const initial=await ready();
  assert.equal(initial.diagnostics.synthetic,true);assert.equal(initial.diagnostics.readOnly,true);
  assert.equal(initial.ticket.messages.length,6);
  assert.equal(initial.ticket.shopifyRail.order.name,'#10312345');
  assert.equal(initial.ticket.redoDetails.status,'observed');
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  context=await browser.newContext({viewport:{width:1280,height:800},recordVideo:{dir:evidence,size:{width:1280,height:800}}});
  const page=await context.newPage();
  page.setDefaultTimeout(10000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  videoPath=await page.video().path();
  await page.goto(`${base}/inbox/?ticket=${encodeURIComponent(ticketId)}`);
  await page.locator('.ticket-title').waitFor();
  assert.equal(await page.locator('.ticket-title').textContent(),'Order 10312345');
  await page.locator('[data-action="refresh"]').click();
  await page.waitForFunction(()=>document.querySelector('[aria-current="true"] .row-subject')?.textContent==='Order 10312345');
  assert.equal(await page.locator('.message').count(),6);
  const clean=(await page.locator('.message-body').allTextContents()).join('\n');
  for(const text of ['নমস্কার Amina','ধন্যবাদ','🙏','#10312345','Before I decide'])assert(clean.includes(text),`Missing meaningful cleaned text ${text}`);
  for(const clutter of ['Your monthly style update is ready','New season arrivals are here','maildecor.woff2','clutter.woff2','@font-face','<html>'])assert.equal(clean.includes(clutter),false,`Email clutter escaped intake cleanup ${clutter}`);
  assert.equal(await page.locator('.message script,.message style,.message iframe').count(),0);
  const shipping='https://shop.example.invalid/pages/shipping?country=BD&source=fixture';
  assert(await page.locator(`.message-body a[href="${shipping}"]`).count()>0);
  assert((await page.locator('.original-evidence pre').allTextContents()).join('\n').includes('maildecor.woff2'));
  async function rectangles(){return page.locator('#reply,[data-action="copy-reply"]').evaluateAll(elements=>elements.map(el=>{const r=el.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:innerHeight,width:innerWidth};}));}
  async function anchored(){const result=await rectangles();assert.equal(result.length,2);for(const r of result)assert(r.top>=0&&r.left>=0&&r.bottom<=r.height&&r.right<=r.width,`Editor/action outside viewport ${JSON.stringify(r)}`);return result;}
  const visibility=[];
  visibility.push({viewport:'1280x800',rectangles:await anchored()});
  await page.locator('#reply').fill('My private fixture reply. No customer is contacted.');
  await page.screenshot({path:path.join(evidence,'fixture-runtime-desktop.png')});await wait(3000);
  const lastMessage=page.locator('.message').last();
  await lastMessage.locator('.original-evidence>summary').click();
  await lastMessage.locator('.original-evidence pre').scrollIntoViewIfNeeded();
  assert(await lastMessage.locator('.original-evidence pre').isVisible());await anchored();await wait(3000);
  await lastMessage.locator('.original-evidence>summary').click();
  await lastMessage.locator('.message-body').first().scrollIntoViewIfNeeded();await anchored();
  await page.locator('.redo-section>summary').click();
  assert((await page.locator('.redo-section').textContent()).includes('size exchange'));
  assert((await page.locator('#customer-rail').textContent()).includes('Shopify returns'));
  assert.equal(await page.locator('.order-item').count(),2);
  await page.screenshot({path:path.join(evidence,'fixture-runtime-shopify-redo.png')});await wait(3000);
  await page.locator('[data-view="closed"]').click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-ticket]').length===1&&document.querySelector('[data-ticket]')?.dataset.ticket==='gorgias:841000000000003');
  await wait(1500);await page.locator('[data-view="all"]').click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-ticket]').length===4);await wait(1500);
  await page.locator('[data-action="list-collapse"]').click();assert(await page.locator('.list-reopen').isVisible());await anchored();await wait(1500);
  await page.locator('.list-reopen .icon-button').click();assert(await page.locator('.ticket-sidebar').isVisible());
  await page.locator('#customer-rail [data-action="rail-close"]').click();assert(await page.locator('.rail-reopen').isVisible());await anchored();await wait(1500);
  await page.locator('.rail-reopen .icon-button').click();assert(await page.locator('#customer-rail').isVisible());
  await page.locator('.ticket-actions-menu>summary').click();
  await page.locator('[data-field="priority"]').selectOption('high');
  assert((await page.locator('.local-observed').textContent()).includes(`observed: ${initial.ticket.gorgiasPriority}`));
  await page.locator('.ticket-actions-menu>summary').click();
  await page.locator('#select-page').check();assert.equal(await page.locator('#selection-count').textContent(),'4 selected on this page');
  await page.locator('#bulk-action').selectOption('unread');await page.locator('[data-action="bulk-apply"]').click();
  assert.equal(await page.locator('#selection-count').textContent(),'0 selected on this page');
  for(const state of await page.locator('.row-state').allTextContents())assert(state.includes('Unread'));
  assert.equal((await actualTicket()).ticket.gorgiasPriority,initial.ticket.gorgiasPriority);await wait(3000);
  for(const [width,height] of [[390,844],[640,450]]){
    await page.setViewportSize({width,height});
    visibility.push({viewport:`${width}x${height}`,rectangles:await anchored()});
    await page.locator('[data-action="expand-draft"]').click();await anchored();await page.locator('[data-action="expand-draft"]').click();
    await page.screenshot({path:path.join(evidence,`fixture-runtime-${width}.png`)});await wait(3000);
  }
  await page.setViewportSize({width:1280,height:800});
  await page.locator('[data-action="new"]').click();
  await page.locator('#local-new [name="name"]').fill('Private runtime example');
  await page.locator('#local-new [name="subject"]').fill('Private runtime ticket');
  await page.locator('#local-new [name="body"]').fill('This synthetic private ticket contacts nobody.');
  await page.locator('#local-new button[type="submit"]').click();
  await page.waitForFunction(()=>location.search.includes('local%3A'));
  assert((await page.locator('.ticket-subtitle').textContent()).includes('Local ticket'));
  assert.equal(await page.locator('[data-action="review-send"]').isVisible(),false);
  await page.locator('#reply').fill('A saved private runtime draft.');await anchored();await wait(3000);
  await page.reload();await page.locator('.ticket-title').waitFor();
  assert.equal(await page.locator('#reply').inputValue(),'A saved private runtime draft.');
  assert((await page.locator('.message-body').textContent()).includes('contacts nobody'));
  assert((await page.locator('#send-mode-note').textContent()).includes('Customer sending is unavailable'));
  await page.screenshot({path:path.join(evidence,'fixture-runtime-local-ticket.png')});await wait(3000);
  const diagnostics=await json(`${base}/__fixture__/diagnostics`);
  assert.equal(diagnostics.fixtureDataReady,true);assert.equal(diagnostics.syncComplete,true);
  assert.deepEqual(diagnostics.mutationAttempts,[]);assert.deepEqual(diagnostics.consoleRequests,[]);assert.deepEqual(diagnostics.blockedNetworkAttempts,[]);
  const allowed={synthetic_gorgias_mcp:['list_inbox_tickets','get_ticket','get_ticket_messages'],synthetic_console_projection:['helpdesk.get_ticket'],synthetic_shopify_snapshot:['read'],synthetic_redo_snapshot:['read']};
  for(const call of diagnostics.providerCalls)assert(allowed[call.provider]?.includes(call.operation),`Unexpected provider operation ${JSON.stringify(call)}`);
  const observations=diagnostics.providerOperationCounts;
  assert(Array.isArray(observations),'Missing cumulative provider operation counts');
  assert(observations.length<=32,'Cumulative provider operation counts exceeded their fixed bound');
  for(const observation of observations){
    assert(allowed[observation.provider]?.includes(observation.operation),`Unexpected cumulative provider operation ${JSON.stringify(observation)}`);
    assert(Number.isSafeInteger(observation.count)&&observation.count>0,`Invalid cumulative provider operation count ${JSON.stringify(observation)}`);
  }
  assert.equal(observations.reduce((sum,observation)=>sum+observation.count,0),diagnostics.providerCallCount,'Cumulative counts must cover the full bounded provider trace');
  for(const [provider,operations] of Object.entries(allowed))for(const operation of operations)
    assert(observations.some(observation=>observation.provider===provider&&observation.operation===operation),`Missing cumulative adapter observation ${provider}/${operation}`);
  assert.equal(diagnostics.stores.canonicalProjection.ready,true,'The observed projection must be the loaded canonical snapshot');
  assert.equal(errors.length,0,errors.join('\n'));
  receipt={synthetic:true,actualAdapters:true,ticketId,port,messages:6,visibility,providerCallCount:diagnostics.providerCallCount,mutationAttempts:0,blockedNetworkAttempts:0,video:'fixture-runtime-review.webm',longStaffInput:'No backend fixture-control endpoint is provided; covered separately by draft-recovery.mjs.',diagnostics};
  fs.writeFileSync(path.join(evidence,'fixture-runtime-receipt.json'),JSON.stringify(receipt,null,2));
}
let failure;
try {await Promise.race([run(),bounded]);}catch(error){failure=error;}
finally {
  clearTimeout(deadline);
  if(context)await Promise.race([context.close().catch(()=>{}),wait(4000)]);
  if(browser)await Promise.race([browser.close().catch(()=>{}),wait(1000)]);
  if(!exitResult&&!spawnError){child.kill('SIGINT');await Promise.race([exited,wait(3000)]);}
  if(!exitResult&&!spawnError){child.kill('SIGKILL');await Promise.race([exited,wait(1000)]);}
  if(!exitResult&&!spawnError)failure ||= new Error('Owned fixture child did not stop.');
  fs.writeFileSync(path.join(evidence,'fixture-runtime-server.log'),logs);
  if(videoPath&&fs.existsSync(videoPath))fs.copyFileSync(videoPath,path.join(evidence,'fixture-runtime-review.webm'));
}
if(failure)throw failure;
console.log(`PASS actual fixture adapters, six cleaned messages, Bengali/link/original preservation, separate Shopify/Redo reads, anchored desktop/mobile/200% zoom, local bulk/read/private-ticket persistence, zero provider writes. Receipt ${path.join(evidence,'fixture-runtime-receipt.json')}. Synthetic walkthrough ${path.join(evidence,'fixture-runtime-review.webm')}. Child stopped ${JSON.stringify(exitResult)}.`);
