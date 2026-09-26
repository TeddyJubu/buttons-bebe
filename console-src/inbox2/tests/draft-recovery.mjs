import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.INBOX_TEST_URL||'http://127.0.0.1:8878';
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
try {
const page=await browser.newPage({viewport:{width:1440,height:1000}});
page.setDefaultTimeout(10000);
const errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));
const now=new Date().toISOString();
const ticket={id:'gorgias:123',subject:'Product measurements',customerName:'Example customer',
  fromEmail:'customer@example.invalid',channel:'email',status:'open',updatedAt:now,syncedAt:now,
  readonlyDraft:'',draftSourceMessageId:'456',draftProcessedAt:now,draftRevision:'a'.repeat(64),
  draftGenerationState:'failed',draftAttemptCount:3,draftAction:'drafted',
  messages:[{id:'456',fromAgent:false,fromName:'Example customer',body:'What is the sleeve length for the named dress?',at:now}],shopifyRail:{status:'missing'}};
await page.route('**/inbox/api/helpdesk',route=>{
 const {tool}=route.request().postDataJSON();
 assert(['helpdesk.capabilities','helpdesk.list_tickets','helpdesk.get_ticket','helpdesk.get_messages'].includes(tool));
 return route.fulfill({json:{ok:true,source:'gorgias_api',...(tool==='helpdesk.get_ticket'?{ticket}:tool==='helpdesk.list_tickets'?{tickets:[ticket],total:1,nextOffset:null,projection:{complete:true,generatedAt:now}}:{readOnly:true})}});
});
let networkError=true;
await page.route('**/console/api/**',route=>{
 const req=route.request();calls.push({path:new URL(req.url()).pathname,body:req.postDataJSON(),headers:req.headers()});
 assert.equal(calls.at(-1).path,'/console/api/ticket/123/retry-draft');
 assert.equal(req.method(),'POST');assert(!req.headers()['x-inbox-send-access']);
 if(networkError)return route.abort();
 return route.fulfill({status:202,json:{ok:true,queued:true,operation_id:calls.at(-1).body.operation_id,job_id:1}});
});
const composer=page.locator('#reply'),retry=page.getByRole('button',{name:'Retry AI draft'});
await page.goto(`${base}/inbox/?ticket=gorgias%3A123`);await retry.waitFor();
await composer.fill('Preserve my unfinished manual response.');
await retry.click();await page.waitForFunction(()=>!document.querySelector('[data-action="retry-draft"]').disabled);
assert.equal(await composer.inputValue(),'Preserve my unfinished manual response.');
networkError=false;await retry.click();await page.getByRole('heading',{name:'AI retry queued'}).waitFor();
assert.equal(calls.length,2);assert.equal(calls[0].body.operation_id,calls[1].body.operation_id);
assert.equal(calls[1].body.source_message_id,'456');assert.equal(calls[1].body.draft_revision,'a'.repeat(64));
assert(await retry.isDisabled());assert.equal(await composer.inputValue(),'Preserve my unfinished manual response.');
assert.equal(await page.getByRole('switch').getAttribute('aria-checked'),'false');
// A completed retry remains a suggestion, including across page reload.
ticket.readonlyDraft='The verified sleeve length is 18 cm.';ticket.draftGenerationState='ready';ticket.draftProcessedAt=new Date(Date.now()+1000).toISOString();
await page.reload();await page.getByRole('button',{name:'Use draft',exact:true}).waitFor();
assert.equal(await composer.inputValue(),'Preserve my unfinished manual response.');
// Staff-only knowledge cannot be copied into a customer response via Use draft.
ticket.draftGenerationState='needs_review';ticket.draftReviewRequired=true;
ticket.draftStaffNextStep='Measure the sleeve of the named dress and complete the response.';
await page.reload();await page.getByText('Needs staff input',{exact:true}).waitFor();
assert(await page.getByRole('button',{name:'Use draft',exact:true}).isDisabled());
assert.equal(await composer.inputValue(),'Preserve my unfinished manual response.');
await composer.fill('Staff verified: the sleeve measures 18 cm.');
fs.mkdirSync('/tmp/bb-ai-reliability-browser',{recursive:true});
for(const state of ['needs_review','failed','retry_wait']){
 ticket.draftGenerationState=state;ticket.draftNextRetryAt=state==='retry_wait'?new Date(Date.now()+30000).toISOString():null;
 for(const width of [1440,390,320]){
  await page.setViewportSize({width,height:1000});await page.reload();await composer.waitFor();
  assert.equal(await composer.inputValue(),'Staff verified: the sleeve measures 18 cm.');
  assert(await composer.isEditable());
  const size=await page.evaluate(()=>({width:document.body.scrollWidth,viewport:innerWidth}));
  assert(size.width<=size.viewport,`${state} overflow at ${width}`);
  if(width!==320)await page.screenshot({path:`/tmp/bb-ai-reliability-browser/${state}-${width}.png`});
 }
}
ticket.draftGenerationState='no_reply';ticket.draftReviewRequired=false;ticket.readonlyDraft='';
await page.reload();await page.getByText('No new question to answer.',{exact:false}).waitFor();
assert.equal(await page.getByRole('button',{name:'Use draft',exact:true}).count(),0);
assert.equal(calls.length,2,'Rendering and refreshing never enqueue or send');
assert.equal(errors.length,0,errors.join('\n'));
console.log('Passed: unavailable/retry/staff-input states; duplicate operation reuse; read-only boundary; manual text preservation; desktop/mobile layouts.');
} finally {await browser.close();}
