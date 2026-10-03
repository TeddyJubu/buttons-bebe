import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {provision,signIn,api} from './browser-auth.mjs';
process.umask(0o077);
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..'),workspace='assist-browser-'+Date.now(),dir=path.join(repo,'intake/.local',workspace);
const seeded=spawnSync('python3',['-m','intake.assistance_demo','--workspace',workspace],{cwd:repo,encoding:'utf8',timeout:20000});assert.equal(seeded.status,0,seeded.stdout);
const cases=Object.fromEntries(JSON.parse(fs.readFileSync(path.join(dir,'assistance-demo-cases.json'),'utf8')).map(r=>[r.case,r.ticket_id]));
provision(repo,workspace);provision(repo,workspace,'viewer','Fixture Viewer','viewer');
const reserve=net.createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));const origin=`http://127.0.0.1:${port}`;
const server=spawn('python3',['-m','intake','--workspace',workspace,'serve','--port',String(port)],{cwd:repo,stdio:['ignore','pipe','pipe']});
const refreshFixture=(id)=>{const result=spawnSync('python3',['-c',`from intake import assistance
from intake.assistance_demo import make_fixture
from intake.policy import install_offline_guard,workspace_directory
from intake.records import canonical
from intake.store import Store
import sys
install_offline_guard()
s=Store(workspace_directory(sys.argv[1]));value=make_fixture(assistance.read_input(s,sys.argv[2])['input'],'ready')
value['response']['body'] += '\\nLiteral test markup: <script>fetch("https://never-run.example.test")</script>'
value['context']['sections']['orders']['records'][0]['text'] += '\\n<img src="https://never-load.example.test/pixel">'
payload=canonical(value).encode();p=assistance.import_fixture(s,payload,preview=True);assistance.import_fixture(s,payload,p['digest'])`,workspace,id],{cwd:repo,encoding:'utf8',timeout:10000});assert.equal(result.status,0,result.stderr);};
let browser;const external=[],errors=[];
try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Server timeout')),10000);server.stdout.on('data',s=>{if(s.toString().includes('Offline intake:')){clearTimeout(timer);resolve();}});server.stderr.on('data',()=>reject(new Error('Server failed')));});
  browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-background-networking','--disable-component-update','--disable-sync']});
  async function openPage(){const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==origin){external.push('blocked');return route.abort();}return route.continue();});const p=await context.newPage();p.setDefaultTimeout(10000);p.on('pageerror',e=>errors.push(e.message));p.on('dialog',d=>d.accept());return p;}
  const page=await openPage();await signIn(page,origin);
  async function choose(key){await page.goto(origin+'/inbox/?ticket='+cases[key]);await page.getByRole('heading',{name:'Assistance demo: '+key,exact:true}).waitFor();await page.locator('#assistance-card').waitFor();}
  await choose('ready');assert.equal((await api(page,'/api/tickets/'+cases.ready)).body.assistance.draft,null);
  await page.getByRole('button',{name:'Run fixture',exact:true}).click();await page.getByRole('heading',{name:'Suggested reply · Test fixture',exact:true}).waitFor();
  const draft=(await api(page,'/api/tickets/'+cases.ready)).body.assistance.draft;
  assert(await page.getByRole('button',{name:'Use test suggestion',exact:true}).isEnabled());assert.equal(await page.locator('#assistance-card .draft-body').textContent(),draft.body);
  assert(await page.getByText('Test order TEST-1001',{exact:true}).isVisible());
  const input=(await api(page,`/api/tickets/${cases.ready}/assistance-input`)).body.input;assert.equal(input.ticket.id,cases.ready);assert(input.content_is_untrusted);assert.equal(input.source_message_id,input.messages[0].id);
  await page.reload();await page.getByRole('heading',{name:'Suggested reply · Test fixture',exact:true}).waitFor();assert.equal((await api(page,'/api/tickets/'+cases.ready)).body.assistance.draft.id,draft.id);
  // An existing human reply is never silently replaced by a fixture.
  await page.locator('#open-reply').click();await page.locator('#reply-body').fill('Keep my manual draft');await page.getByRole('button',{name:'Close reply simulation',exact:true}).click();
  await page.getByRole('button',{name:'Use test suggestion',exact:true}).click();await page.getByText('Your existing reply is still in the editor. Clear it before using a different suggestion.',{exact:true}).waitFor();
  await page.locator('#open-reply').click();assert.equal(await page.locator('#reply-body').inputValue(),'Keep my manual draft');await page.locator('#reply-clear').click();await page.getByRole('button',{name:'Close reply simulation',exact:true}).click();
  await page.getByRole('button',{name:'Use test suggestion',exact:true}).click();await page.locator('#reply-dialog[open]').waitFor();assert.equal(await page.locator('#reply-body').inputValue(),draft.body);
  await page.locator('#reply-preview').click();await page.locator('#reply-confirm').waitFor();
  // New context without a new message still invalidates a frozen review.
  refreshFixture(cases.ready);await page.locator('#reply-confirm').click();await page.locator('#reply-error').filter({hasText:'A newer test fixture replaced this suggestion'}).waitFor();
  await page.getByRole('button',{name:'Close reply simulation',exact:true}).click();await page.locator('#refresh').click();await page.getByRole('heading',{name:'Suggestion out of date',exact:true}).waitFor();assert(await page.getByRole('button',{name:'Use test suggestion',exact:true}).isDisabled());
  await page.locator('#open-reply').click();await page.locator('#reply-clear').click();await page.getByRole('button',{name:'Close reply simulation',exact:true}).click();
  await page.getByRole('button',{name:'Run fixture again',exact:true}).click();await page.getByRole('heading',{name:'Suggested reply · Test fixture',exact:true}).waitFor();
  assert.equal(await page.locator('img,iframe,object').count(),0);assert((await page.locator('#assistance-card .draft-body').textContent()).includes('<script>'));
  for(const width of [1440,768,390,320]){await page.setViewportSize({width,height:width===320?568:1000});await page.locator('#assistance-card').scrollIntoViewIfNeeded();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(dir,`assistance-${width}.png`)});}
  await page.setViewportSize({width:1440,height:1000});
  // Accepted timeout follows the existing fake receipt workflow, with no retry.
  await page.getByRole('button',{name:'Use test suggestion',exact:true}).click();await page.locator('#reply-scenario').selectOption('accepted_timeout');await page.locator('#reply-preview').click();await page.locator('#reply-confirm').click();
  await page.getByText('Outcome uncertain',{exact:true}).waitFor();await page.getByRole('button',{name:'Check fake receipt',exact:true}).click();await page.getByText('Simulated delivery recorded',{exact:true}).waitFor();
  await page.getByRole('heading',{name:'Suggestion out of date',exact:true}).waitFor();
  await choose('needs_staff');await page.getByRole('button',{name:'Run fixture',exact:true}).click();await page.getByRole('heading',{name:'Needs staff input',exact:true}).waitFor();assert(await page.getByRole('button',{name:'Use test suggestion',exact:true}).isDisabled());assert.equal(await page.locator('#assistance-card .draft-body').count(),0);assert(await page.locator('#open-reply').isEnabled());
  await choose('no_reply');await page.getByRole('button',{name:'Run fixture',exact:true}).click();await page.getByRole('heading',{name:'No reply needed · Test fixture',exact:true}).waitFor();assert.equal((await api(page,'/api/tickets/'+cases.no_reply)).body.status,'open');
  await choose('timeout');await page.route('**/assistance-run',async route=>{await route.fetch();await route.abort();});await page.getByRole('button',{name:'Run fixture',exact:true}).click();await page.getByText('The result was not received. Refresh to inspect the record; do not assume it failed.',{exact:true}).waitFor();await page.unroute('**/assistance-run');await page.locator('#refresh').click();await page.getByRole('heading',{name:'Suggestion unavailable',exact:true}).waitFor();
  const failed=(await api(page,'/api/tickets/'+cases.timeout)).body.assistance.draft.id;
  await page.getByRole('button',{name:'Run fixture again',exact:true}).click();await page.getByText('Offline fixture run recorded. No model was called.',{exact:true}).waitFor();assert.equal((await api(page,'/api/tickets/'+cases.timeout)).body.assistance.draft.id,failed);
  await choose('malformed');await page.getByRole('button',{name:'Run fixture',exact:true}).click();await page.getByText('The offline fixture returned invalid output. No usable suggestion was produced.',{exact:true}).waitFor();
  await choose('identity_conflict');await page.getByRole('button',{name:'Run fixture',exact:true}).click();await page.getByRole('heading',{name:'Needs staff input',exact:true}).waitFor();assert.equal(await page.locator('.fixture-evidence').count(),0);
  const viewer=await openPage();await signIn(viewer,origin,'viewer','/inbox/?ticket='+cases.needs_staff);assert.equal(await viewer.locator('#assistance-card button').count(),0);assert.equal((await api(viewer,`/api/tickets/${cases.needs_staff}/assistance-use`,{operation_id:'forged',run_id:'wrong'})).status,403);
  // The lab reads the same result, and dismissal survives a new page load.
  await page.goto(origin+'/');await page.locator(`[data-ticket="${cases.no_reply}"]`).click();await page.getByRole('heading',{name:'No reply needed · Test fixture',exact:true}).waitFor();await page.getByRole('button',{name:'Dismiss suggestion',exact:true}).click();await page.getByRole('heading',{name:'Suggestion dismissed',exact:true}).waitFor();await choose('no_reply');await page.getByRole('heading',{name:'Suggestion dismissed',exact:true}).waitFor();
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(dir,'assistance-browser-proof.json'),JSON.stringify({passed:true,cases:6,nativeIds:true,draftPreservation:true,staleReviewRejected:true,fakeTimeoutReconciled:true,lostResponseIdempotent:true,viewerDenied:true,dismissalPersistent:true,markupInert:true,externalRequests:0,modelCalls:0,realMessagesSent:0},null,2),{mode:0o600});
  console.log('Offline assistance browser proof passed: '+dir);
}catch(error){console.error(error);process.exitCode=1;}finally{if(browser)await browser.close();server.kill('SIGTERM');}
