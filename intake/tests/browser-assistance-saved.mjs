// Native-ID and unavailable-context proof on saved data; never screenshot or log PII.
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {provision,signIn,api} from './browser-auth.mjs';
process.umask(0o077);
const workspace=process.argv[2];assert(/^[a-z0-9][a-z0-9-]{0,38}$/.test(workspace||''),'Provide a new assistance proof workspace');
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..'),dir=path.join(repo,'intake/.local',workspace),reportFile=path.join(dir,'assistance-saved-proof.json');
assert(!fs.existsSync(reportFile),'Preserve prior proof');
const prepared=JSON.parse(fs.readFileSync(path.join(dir,'assistance-preparation.json'),'utf8'));assert(prepared.prepared);
const secret=randomBytes(32).toString('base64url');provision(repo,workspace,'assistance-proof','Assistance proof operator','agent',secret);
const reserve=net.createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));const origin=`http://127.0.0.1:${port}`;
const server=spawn('python3',['-m','intake','--workspace',workspace,'serve','--port',String(port)],{cwd:repo,stdio:['ignore','pipe','pipe']});
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');let browser,stage='startup',ordinal=0,external=0,errors=0;
const cli=(args,space=workspace)=>{const r=spawnSync('python3',['-m','intake','--workspace',space,...args],{cwd:repo,encoding:'utf8',timeout:20000});assert.equal(r.status,0,'Private CLI check failed');return JSON.parse(r.stdout);};
try{
  await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('Server timeout')),10000);server.stdout.on('data',chunk=>{if(chunk.toString().includes('Offline intake:')){clearTimeout(timeout);resolve();}});server.stderr.on('data',()=>reject(new Error('Server failed')));});
  browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-background-networking','--disable-component-update','--disable-sync']});
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
  await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==origin){external++;return route.abort();}return route.continue();});
  const page=await context.newPage();page.on('pageerror',()=>errors++);page.setDefaultTimeout(10000);
  await signIn(page,origin,'assistance-proof','/inbox/',secret);
  let messages=0,sourceChecks=0;
  for(const item of prepared.cases){
    ordinal++;stage='input and current fixture';
    const t=(await api(page,'/api/tickets/'+item.ticket_id)).body;
    const input=(await api(page,`/api/tickets/${item.ticket_id}/assistance-input`)).body;
    assert.equal(input.input.ticket.id,item.ticket_id);assert.equal(input.input_digest,item.input_digest);
    assert(JSON.stringify(input.input.messages.map(m=>[m.id,m.kind,m.body,m.author_name,m.author_email,m.created_at]))===JSON.stringify(t.messages.map(m=>[m.id,m.kind,m.body,m.author_name,m.author_email,m.created_at])));
    assert(JSON.stringify(input.input.source_references)===JSON.stringify([...t.sources].sort((a,b)=>(a.account+a.external_id).localeCompare(b.account+b.external_id))));
    assert(!t.assistance.draft);assert(t.assistance.can_run);assert.equal(t.assistance.context.identity.contact_id,t.contact_id);
    stage='authenticated fixture UI';
    await page.goto(origin+'/inbox/?ticket='+item.ticket_id);await page.locator('#assistance-card').waitFor();
    await page.getByRole('button',{name:'Run fixture',exact:true}).click();await page.getByRole('heading',{name:'Needs staff input',exact:true}).waitFor();
    assert(await page.getByRole('button',{name:'Use test suggestion',exact:true}).isDisabled());assert.equal(await page.locator('#assistance-card .draft-body').count(),0);assert.equal(await page.locator('.fixture-evidence').count(),0);
    const finished=(await api(page,'/api/tickets/'+item.ticket_id)).body;
    assert.equal(finished.assistance.draft.state,'needs_staff');assert.equal(finished.assistance.draft.body,'');assert.equal(finished.assistance.draft.priority,t.priority);assert.equal(finished.status,t.status);assert.equal(finished.revision,t.revision);assert.equal(finished.messages.length,t.messages.length);
    assert.equal((await api(page,`/api/tickets/${item.ticket_id}/assistance-use`,{operation_id:'blocked-use-'+ordinal,run_id:finished.assistance.draft.id})).status,409);
    const rerun={mode:'offline_fixture',operation_id:'repeat-proof-'+ordinal,revision:t.revision,fixture_digest:t.assistance.fixture.digest};
    const once=await api(page,`/api/tickets/${item.ticket_id}/assistance-run`,rerun),twice=await api(page,`/api/tickets/${item.ticket_id}/assistance-run`,rerun);assert.equal(once.status,200);assert.equal(twice.body.run_id,once.body.run_id);
    messages+=t.messages.length;sourceChecks+=input.input.messages.length+input.input.source_references.length;
  }
  stage='business fidelity';
  const checked=spawnSync('python3',['-c',`from intake.assistance_rehearse import business_digest
from intake.policy import install_offline_guard,workspace_directory
from intake.store import Store
import sys
install_offline_guard();print(business_digest(Store(workspace_directory(sys.argv[1]))))`,workspace],{cwd:repo,encoding:'utf8',timeout:10000});assert.equal(checked.status,0);assert.equal(checked.stdout.trim(),prepared.business_digest);
  const check=cli(['verify-workspace']);assert.equal(check.tables.messages,messages);assert.equal(check.tables.assistance_runs,prepared.tickets*2);assert.equal(check.tables.delivery_attempts,0);assert.equal(check.tables.simulated_outgoing,0);
  stage='recovery';const backup=cli(['backup','--name','e3-assistance-checkpoint']);cli(['restore','--from-workspace',workspace,'--backup','e3-assistance-checkpoint','--expected-digest',backup.digest],workspace+'-restored');
  const restored=cli(['verify-workspace'],workspace+'-restored');assert.equal(restored.tables.sessions,0);assert.equal(restored.tables.assistance_runs,prepared.tickets*2);
  assert.equal(external,0);assert.equal(errors,0);
  const report={passed:true,tickets:prepared.tickets,messages,sourceAndMessageBindings:sourceChecks,explicitRuns:prepared.tickets*2,repeatedRunsDeduplicated:prepared.tickets,staffOnlyUseBlocked:prepared.tickets,businessRowsUnchanged:true,contextEvidenceInvented:0,restoredRuns:restored.tables.assistance_runs,restoredSessions:0,externalRequests:0,modelCalls:0,realIngress:0,realEgress:0,screenshots:0};
  fs.writeFileSync(reportFile,JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});console.log(JSON.stringify(report,null,2));
}catch{fs.writeFileSync(path.join(dir,'assistance-proof-failure-'+Date.now()+'.json'),JSON.stringify({passed:false,stage,caseOrdinal:ordinal}),{mode:0o600,flag:'wx'});console.error(JSON.stringify({passed:false,stage,caseOrdinal:ordinal}));process.exitCode=1;}
finally{if(browser)await browser.close();server.kill('SIGTERM');}
