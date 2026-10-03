import {provision,signIn} from './browser-auth.mjs';
import assert from 'node:assert/strict';
import {verifyDelivery} from './browser-delivery.mjs';
import {verifyRecovery} from './browser-recovery.mjs';
import {spawn,spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

process.umask(0o077);
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const workspace='browser-'+Date.now();
const artifacts=path.join(repo,'intake/.local',workspace);
const reserve=net.createServer();
await new Promise(resolve=>reserve.listen(0,'127.0.0.1',resolve));
const port=reserve.address().port;
await new Promise(resolve=>reserve.close(resolve));
const origin=`http://127.0.0.1:${port}`;
const server=spawn('python3',['-m','intake','--workspace',workspace,'serve','--port',String(port)],{cwd:repo,stdio:['ignore','pipe','pipe']});
let browser;
try{
  await new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>reject(new Error('Sandbox server did not start')),10000);
    server.once('exit',code=>{clearTimeout(timeout);reject(new Error('Sandbox exited: '+code));});
    server.stdout.on('data',chunk=>{if(chunk.toString().includes('Offline intake:')){clearTimeout(timeout);resolve();}});
    server.stderr.on('data',chunk=>{clearTimeout(timeout);reject(new Error(chunk.toString()));});
  });
  const admin=provision(repo,workspace,'admin','Test operator');
  browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-background-networking','--disable-component-update','--disable-sync']});
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
  const page=await context.newPage();page.setDefaultTimeout(10000);
  const external=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',route=>{if(!route.request().url().startsWith(origin+'/')){external.push(route.request().url());return route.abort();}return route.continue();});
  await signIn(page,origin,'admin','/');
  await page.getByText('No tickets in this view.',{exact:true}).waitFor();
  assert.equal(await page.locator('#count').textContent(),'0');
  assert(await page.getByText('Offline sandbox',{exact:true}).isVisible());

  await page.locator('#open-create').click();
  await page.locator('#customer-name').fill('Browser Test');
  await page.locator('#customer-email').fill('browser@example.test');
  await page.locator('#subject').fill('Manual intake rehearsal');
  await page.locator('#description').fill('Record a phone conversation without sending anything.');
  await page.getByRole('button',{name:'Create test ticket',exact:true}).click();
  await page.getByRole('heading',{name:'Manual intake rehearsal',exact:true}).waitFor();
  await page.locator('#note').fill('A private follow-up note.');
  await page.getByRole('button',{name:'Save note',exact:true}).click();
  await page.getByText('A private follow-up note.',{exact:true}).waitFor();
  await page.locator('#status').selectOption('waiting_team');
  await page.locator('#assignee').selectOption(admin.id);
  await page.getByRole('button',{name:'Save details',exact:true}).click();
  await page.locator('.activity-item').filter({hasText:'Ticket details updated'}).waitFor();
  await page.reload();
  await page.getByRole('heading',{name:'Manual intake rehearsal',exact:true}).waitFor();
  assert.equal(await page.locator('#assignee').inputValue(),admin.id);
  assert.equal(await page.locator('#status').inputValue(),'waiting_team');
  assert.equal(await page.locator('.message').count(),2);

  await page.locator('#open-import').click();
  await page.locator('#account').fill('synthetic-demo');
  await page.locator('#export-file').setInputFiles(path.join(repo,'intake/fixtures/gorgias-synthetic.json'));
  await page.getByRole('button',{name:'Preview import',exact:true}).click();
  await page.getByText('Ready for review',{exact:true}).waitFor();
  assert.equal(await page.locator('#count').textContent(),'1','Preview must not import');
  await page.locator('#commit-import').click();
  await page.getByText('Import saved in this sandbox.',{exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelector('#count').textContent==='3');
  await page.getByRole('button',{name:'Preview import',exact:true}).click();
  await page.getByText('Ready for review',{exact:true}).waitFor();
  await page.locator('#commit-import').click();
  await page.getByText('Already imported — no duplicate records.',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Close import',exact:true}).click();

  await page.getByRole('button',{name:/Photo of a damaged button/}).click();
  await page.getByRole('heading',{name:'Photo of a damaged button',exact:true}).waitFor();
  assert.equal(await page.locator('img').count(),0);
  assert(await page.getByText('button-photo.jpg · Metadata only · File not downloaded',{exact:true}).isVisible());
  assert((await page.locator('.message-body').textContent()).includes('the button arrived damaged'));

  // A second client writes first: the open page must reject its stale edit.
  await page.evaluate(async()=>{
    const {token}=await (await fetch('/api/session')).json();
    const headers={'X-Intake-Token':token,'Content-Type':'application/json'};
    const list=await (await fetch('/api/tickets?query=Photo',{headers})).json();
    const t=list.tickets[0];
    await fetch(`/api/tickets/${t.id}/update`,{method:'POST',headers,body:JSON.stringify({operation_id:'other-operator',revision:t.revision,status:'closed',priority:'high',assignee_id:''})});
  });
  await page.locator('#note').fill('Keep this unsaved note through a conflict.');
  await page.getByRole('button',{name:'Save details',exact:true}).click();
  await page.getByText('This ticket changed. Refresh it before saving your change.',{exact:true}).waitFor();
  assert.equal(await page.locator('#note').inputValue(),'Keep this unsaved note through a conflict.');
  await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.querySelector('#status').value==='closed');
  assert.equal(await page.locator('#note').inputValue(),'Keep this unsaved note through a conflict.');

  await page.locator('#open-history').click();
  await page.locator('.history-entry').waitFor();
  assert.equal(await page.locator('.history-entry').count(),1);
  await page.keyboard.press('Escape');
  const denied=await page.evaluate(async()=>{
    const {token}=await (await fetch('/api/session')).json();
    return (await fetch('/api/send',{method:'POST',headers:{'X-Intake-Token':token,'Content-Type':'application/json'},body:'{}'})).status;
  });
  assert.equal(denied,403);

  // Replay is an explicit offline CLI action; the browser has no ingress route.
  const replayFile=path.join(repo,'intake/fixtures/replay-synthetic.json');
  const cli=(args,targetWorkspace=workspace)=>{
    const result=spawnSync('python3',['-m','intake','--workspace',targetWorkspace,...args],{cwd:repo,encoding:'utf8',timeout:10000});
    assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);
  };
  const replayPreview=cli(['replay-preview',replayFile]);
  assert.equal(await page.locator('#count').textContent(),'3');
  const replayResult=cli(['replay',replayFile,'--expected-digest',replayPreview.digest]);
  assert.deepEqual(replayResult.outcomes,{created:5,appended:1,ignored:1});
  assert.deepEqual(cli(['replay',replayFile,'--expected-digest',replayPreview.digest]).outcomes,{duplicate_event:7});
  await page.locator('#refresh').click();
  await page.waitForFunction(()=>document.querySelector('#count').textContent==='6');
  await page.locator('#queue').selectOption('spam');
  await page.waitForFunction(()=>document.querySelector('#count').textContent==='1');
  await page.getByRole('button',{name:/Synthetic spam/}).click();
  await page.getByText('Marked as spam',{exact:true}).waitFor();
  await page.getByText('Simulated incoming',{exact:true}).waitFor();
  await page.locator('#queue').selectOption('automatic');
  await page.getByRole('button',{name:/Synthetic automatic response/}).waitFor();
  assert.equal(await page.locator('#count').textContent(),'1');
  await page.locator('#queue').selectOption('review');
  await page.getByRole('button',{name:/Synthetic missing header/}).click();
  await page.getByText('Missing message ID',{exact:true}).waitFor();
  await page.locator('#queue').selectOption('all');
  await page.waitForFunction(()=>document.querySelector('#count').textContent==='8');
  await verifyDelivery(page);
  await verifyRecovery(page,{cli,artifacts,workspace,repo});
  await page.screenshot({path:path.join(artifacts,'desktop.png'),fullPage:true});
  for(const width of [1440,1024,800,768,390,320]){
    await page.setViewportSize({width,height:900});
    const sizes=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));
    assert(sizes.page<=sizes.viewport,`Horizontal overflow at ${width}: ${JSON.stringify(sizes)}`);
  }
  await page.setViewportSize({width:390,height:900});
  await page.screenshot({path:path.join(artifacts,'mobile.png'),fullPage:true});
  await page.locator('#search').fill('no matching ticket');
  await page.getByText('No tickets in this view.',{exact:true}).waitFor();
  await page.locator('#search').fill('');
  await page.waitForFunction(()=>document.querySelector('#count').textContent==='8');
  assert.deepEqual(external,[],'Browser attempted an external request');
  assert.deepEqual(errors,[],'Browser JavaScript errors');
  fs.writeFileSync(path.join(artifacts,'browser-result.json'),JSON.stringify({passed:true,externalRequests:external.length,workspace,checks:['empty boot','manual creation','private notes','reload persistence','details edit','preview without writes','import and repeated import','HTML and attachment isolation','stale edit','draft preservation','import history','send refusal','offline CLI replay and repeat','review/spam/automatic queues','simulation labels','fake reply review and confirmation','stale review refusal','uncertain receipt and explicit retry','lost browser response recovery','private attachment import and inert display','CLI backup restore and byte recovery','desktop/mobile','search']},null,2));
  for(const name of ['desktop.png','mobile.png','browser-result.json']){
    assert.equal(fs.statSync(path.join(artifacts,name)).mode&0o777,0o600,'Private artifact permissions');
  }
  console.log(`Browser checks passed. Synthetic screenshots: ${artifacts}`);
}finally{
  if(browser)await browser.close();
  server.kill('SIGTERM');
}
