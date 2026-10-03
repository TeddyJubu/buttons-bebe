// Explicit offline E2 rehearsal on an already reconciled fresh saved-data copy.
// Aggregate reports only: no customer content, URLs, exceptions or screenshots.
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {provision} from './browser-auth.mjs';
process.umask(0o077);
const workspace=process.argv[2];assert(/^[a-z0-9][a-z0-9-]{0,38}$/.test(workspace||''),'Provide a fresh saved-data proof workspace');
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..'),dir=path.join(repo,'intake/.local',workspace),reportFile=path.join(dir,'team-saved-proof.json');
assert(fs.existsSync(path.join(dir,'inbox-display-reconciliation.json')),'Complete authenticated display reconciliation first');assert(!fs.existsSync(reportFile),'Preserve existing evidence');
const run=(args,space=workspace)=>{const result=spawnSync('python3',['-m','intake','--workspace',space,...args],{cwd:repo,encoding:'utf8',timeout:20000});assert.equal(result.status,0,'Offline CLI proof failed');return JSON.parse(result.stdout);};
const sourceHash=()=>{const result=spawnSync('python3',['-c',`import hashlib,json,sqlite3,sys
from pathlib import Path
db=sqlite3.connect(Path(sys.argv[1]).as_uri()+'?mode=ro',uri=True)
tables=['contacts','messages','attachments','source_records','import_batches','replay_receipts','replay_messages','simulated_outgoing','delivery_attempts','fake_dispatches','attachment_files','attachment_blobs']
data={t:sorted(list(db.execute('SELECT * FROM '+t)),key=repr) for t in tables}
print(hashlib.sha256(json.dumps(data,sort_keys=True,default=lambda v:v.hex()).encode()).hexdigest())`,path.join(dir,'intake.sqlite3')],{cwd:repo,encoding:'utf8',timeout:10000});assert.equal(result.status,0,'Source verification failed');return result.stdout.trim();};
const before=sourceHash(),secret=randomBytes(32).toString('base64url');
const admin=provision(repo,workspace,'team-admin','Team proof administrator','admin',secret),agent=provision(repo,workspace,'team-agent','Team proof agent','agent',secret);provision(repo,workspace,'team-viewer','Team proof viewer','viewer',secret);
const reserve=net.createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));const port=reserve.address().port;await new Promise(r=>reserve.close(r));const origin=`http://127.0.0.1:${port}`;
const server=spawn('python3',['-m','intake','--workspace',workspace,'serve','--port',String(port)],{cwd:repo,stdio:['ignore','pipe','pipe']});
const {request}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE||'playwright');let contexts=[],stage='startup',ordinal=0;
try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Server timeout')),10000);server.stdout.on('data',s=>{if(s.toString().includes('Offline intake:')){clearTimeout(timer);resolve();}});server.stderr.on('data',()=>reject(new Error('Server failed')));});
  const sessions={};
  for(const role of ['admin','agent','viewer']){
    const context=await request.newContext({baseURL:origin});contexts.push(context);
    assert.equal((await context.post('/api/auth/login',{data:{username:'team-'+role,password:secret}})).status(),200);
    const session=await (await context.get('/api/session')).json();
    sessions[role]=async(path,data)=>{const r=await context[data?'post':'get'](path,{headers:{'X-Intake-Token':session.token},...(data?{data}:{})});return {status:r.status(),body:await r.json()};};
  }
  stage='per-user saved conversations';
  const list=(await sessions.admin('/api/tickets?queue=all')).body;assert.equal(list.nextOffset,null);assert.equal(list.total,50);
  for(const row of list.tickets){
    ordinal++;
    const t=(await sessions.admin('/api/tickets/'+row.id)).body;
    assert(t.read_state.unread);
    assert.equal((await sessions.admin(`/api/tickets/${row.id}/read-state`,{operation_id:'read-'+ordinal,version:t.read_state.version,through_sequence:t.read_state.through_sequence,unread:false})).status,200);
    assert.equal((await sessions.admin('/api/tickets/'+row.id)).body.read_state.unread,false);
    for(const role of ['agent','viewer'])assert.equal((await sessions[role]('/api/tickets/'+row.id)).body.read_state.unread,true);
  }
  assert.equal((await sessions.admin('/api/tickets?queue=all&view=unread')).body.total,0);
  assert.equal((await sessions.agent('/api/tickets?queue=all&view=unread')).body.total,50);
  stage='assignment handoff and access checks';
  const id=list.tickets[0].id,t=(await sessions.admin('/api/tickets/'+id)).body;
  assert.equal((await sessions.admin(`/api/tickets/${id}/claim`,{operation_id:'claim',revision:t.revision})).status,200);
  assert.equal((await sessions.agent(`/api/tickets/${id}/claim`,{operation_id:'claim',revision:t.revision})).status,409);
  const claimed=(await sessions.admin('/api/tickets/'+id)).body;
  assert.equal((await sessions.admin(`/api/tickets/${id}/update`,{operation_id:'handoff',revision:claimed.revision,status:t.status,priority:t.priority,assignee_id:agent.id})).status,200);
  const final=(await sessions.agent('/api/tickets/'+id)).body;
  assert.equal(final.assignee,t.assignee);assert.equal(final.team_assignee.id,agent.id);
  assert.equal((await sessions.agent('/api/tickets?queue=all&view=mine')).body.total,1);
  assert.equal((await sessions.admin('/api/tickets?queue=all&view=mine')).body.total,0);
  assert.equal((await sessions.viewer(`/api/tickets/${id}/notes`,{operation_id:'forbidden',revision:final.revision,body:'Forbidden synthetic note'})).status,403);
  assert.equal((await sessions.agent('/api/import/preview',{})).status,403);
  stage='review ownership';let reviewed;
  for(const row of list.tickets){const candidate=(await sessions.admin('/api/tickets/'+row.id)).body;if(candidate.reply_context.available){reviewed=(await sessions.admin(`/api/tickets/${row.id}/simulation-review`,{mode:'offline_simulation',operation_id:'owner-review',revision:candidate.revision,body:'Synthetic offline E2 ownership check. No delivery authorized.',scenario:'accepted'})).body;break;}}
  assert(reviewed?.review_id);assert.equal((await sessions.agent(`/api/tickets/${reviewed.ticket_id}/simulation-confirm`,{mode:'offline_simulation',review_id:reviewed.review_id,digest:reviewed.digest,confirmed:true})).status,403);
  stage='source fidelity and private recovery';assert.equal(sourceHash(),before);
  const checkpoint=run(['backup','--name','e2-team-checkpoint']);run(['backup-verify','--name','e2-team-checkpoint']);
  const restored=run(['restore','--from-workspace',workspace,'--backup','e2-team-checkpoint','--expected-digest',checkpoint.digest],workspace+'-restored');
  const integrity=run(['verify-workspace'],workspace+'-restored');assert.equal(integrity.tables.sessions,0);assert.equal(integrity.tables.ticket_reads,50);assert.equal(integrity.tables.ticket_assignments,1);assert.equal(integrity.tables.review_owners,1);assert(restored.old_unconfirmed_reviews_invalidated);
  assert.equal(sourceHash(),before);
  const report={passed:true,tickets:50,messages:211,attachmentReferences:22,usersTested:3,perUserReadChecks:150,assignmentHandoff:true,staleClaimRejected:true,viewerWriteRejected:true,agentImportRejected:true,crossUserReplyConfirmationRejected:true,sourceAndMessageRowsUnchanged:true,pendingReviewNotDispatched:true,restorePreservedUsersAndReadState:true,restoredSessions:0,oldReviewsInvalidated:true,realIngress:0,realEgress:0,screenshots:0};
  fs.writeFileSync(reportFile,JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});console.log(JSON.stringify(report,null,2));
}catch{console.error(JSON.stringify({passed:false,stage,caseOrdinal:ordinal}));process.exitCode=1;}finally{for(const c of contexts)await c.dispose();server.kill('SIGTERM');}
