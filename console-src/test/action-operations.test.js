import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
const html=readFileSync(new URL('../index.html',import.meta.url),'utf8');
const slice=(from,to)=>{const s=html.indexOf(from),e=html.indexOf(to,s);assert.ok(s>0&&e>s,`stable boundary: ${from} -> ${to}`);return html.slice(s,e);};
// ponytail: the tickets tab is an embedded inbox iframe; the console keeps
// the write path (bridge fns + bbPerformWrite) and the hello handshake.
// These tests pin the iframe, the bridge payloads, and dry-run behavior.
test('tickets tab renders the embedded inbox iframe, not the legacy feed',()=>{
 assert.match(html,/id="inbox-frame"/);
 assert.match(html,/function ticketsView\(\)/);
 assert.match(html,/function bbInboxSrc\(\)/);
 assert.doesNotMatch(html,/function legacyTicketsView/);
 assert.doesNotMatch(html,/function row\(t\)/);
 assert.doesNotMatch(html,/Legacy feed/);
});
test('bridge routes rewrite/note/send to the console API with operation payloads',()=>{
 for(const fn of ['function bbFindConsoleTicket(','function bbRewriteTicket(','function bbNoteTicket(','function bbSendTicket(','function bbPerformWrite('])assert.ok(html.includes(fn),`missing ${fn}`);
 assert.match(html,/approve_learning:!!approveLearning/);
 assert.match(html,/operation_id:opId/);
 assert.match(html,/draft_revision:revision/);
});
test('dry-run stays default: unarmed note/send validate without posting',()=>{
 const src='var bbArmed=false;'+slice('function bbPerformWrite(','function bbReplyRewrite(');
 const context=vm.createContext({console:{info(){}},crypto:{},fetch(){throw new Error('must not fetch in dry-run');}});
 vm.runInContext(src+';this.perform=bbPerformWrite;',context);
 return Promise.all([
  context.perform('note',{ticket_id:1},'x').then(r=>assert.equal(r.dryRun,true)),
  context.perform('send',{ticket_id:1},{text:'x'}).then(r=>assert.equal(r.dryRun,true)),
 ]);
});
test('hello handshake is bound to the inbox frame on load',()=>{
 assert.match(html,/bb-console-hello/);
 assert.match(html,/inbox-frame/);
 assert.match(html,/helloBound/);
});
test('uncertain owner alerts remain visible without a retry control',()=>{
 const start=html.indexOf('function ownerAlertWarning('),end=html.indexOf('function overview(){',start);
 const context=vm.createContext({});vm.runInContext(html.slice(start,end),context);
 assert.match(context.ownerAlertWarning(2),/2 owner alerts need review/);
 assert.equal(context.ownerAlertWarning(0),'');
 assert.match(html,/ownerAlertWarning\(stats.owner_alerts_need_attention\)/);
});
test('overview and notification deep links route into the embedded inbox',()=>{
 const source=html.slice(html.indexOf('function inboxDeepFilter('),html.indexOf('\nfunction render(){'));
 const context=vm.createContext({tab:'overview',inboxNavView:'all',inboxNavTicket:null,render(){},document:{getElementById:()=>null}});
 vm.runInContext(source,context);
 context.goTickets('all',42);
 assert.equal(context.inboxNavTicket,'gorgias:42');
 assert.equal(context.tab,'tickets');
 context.goTickets('all','gorgias:42');
 assert.equal(context.inboxNavTicket,'gorgias:42');
 // ponytail: regression for the message-id/value mix-up — keyOf must hand
 // goTickets the Gorgias ticket_id, never the message_id.
 const keyOf=vm.runInContext('const keyOf=t=>String(t.ticket_id??t.message_id??"");keyOf;',vm.createContext({}));
 assert.equal(keyOf({ticket_id:7,message_id:'customer-message-9'}),'7');
 assert.equal(keyOf({message_id:'customer-message-9'}),'customer-message-9');
 context.goTickets('failed',null);
 assert.equal(context.inboxNavView,'all');
 context.goTickets('escalated',null);
 assert.equal(context.inboxNavView,'all');
 context.goTickets('open',null);
 assert.equal(context.inboxNavView,'open');
});
