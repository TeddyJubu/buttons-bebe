import assert from 'node:assert/strict';
import fs from 'node:fs';
const source=fs.readFileSync(new URL('../local_state.js',import.meta.url),'utf8');
const {stateRecords,readRecords,readState,readMarker,localTicket,matchesLocal,rememberObserved,observedRows,MAX_OBSERVED_SUMMARIES}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
assert.deepEqual(readRecords(['gorgias:1']),{});
assert.deepEqual(stateRecords({version:2,records:{unsafe:true}}),{});
assert.deepEqual(stateRecords({version:1,records:[]}),{});
const ticket={id:'gorgias:1',status:'open',gorgiasPriority:'normal',lastMessageAt:'2026-10-05T00:00:00Z',messages:[{id:'m1'}]};
const read={'gorgias:1':{read:true,message:'m1',activity:ticket.lastMessageAt}};
assert(readState(ticket,read));
assert(readState({id:ticket.id,lastMessageAt:ticket.lastMessageAt},read));
assert.equal(readState({...ticket,messages:[{id:'m2'}]},read),false);
assert.equal(readState({id:ticket.id,lastMessageAt:'2026-10-05T00:01:00Z'},read),false);
const effective=(t,f)=>f==='priority'?t.gorgiasPriority:t[f]||'';
assert.equal(matchesLocal({...ticket,spam:true},'all',{},effective,''),false);
assert(matchesLocal({...ticket,spam:true},'spam',{},effective,''));
assert(matchesLocal({...ticket,trashed:true},'trash',{},effective,''));
assert.equal(matchesLocal({...ticket,assigneeTeam:'Team'},'unassigned',{},effective,''),false);
assert.equal(matchesLocal(ticket,'assigned',{},effective,''),false);
assert(matchesLocal({...ticket,assignee:'owner@example.invalid'},'assigned',{},effective,'owner@example.invalid'));
assert.equal(matchesLocal({...ticket,snooze:'2026-10-06T00:00:00Z'},'snoozed',{},effective,'',Date.parse('2026-10-07T00:00:00Z')),false);
const local=localTicket({name:'Private customer',body:'A private message'},'uuid','2026-10-05T00:00:00Z');
assert.equal(local.id,'local:uuid');assert.equal(local.localOnly,true);assert.equal(local.fromEmail,'');assert.equal(local.messages[0].body,'A private message');
assert.throws(()=>localTicket({name:'',body:'Message'},'uuid'));
assert.throws(()=>localTicket({name:'Name',body:'x'.repeat(30001)},'uuid'));
const summary={id:'gorgias:2',updatedAt:'2026-10-05T00:01:00Z',lastMessageAt:'2026-10-05T00:00:00Z'};
const summaryRead={'gorgias:2':readMarker(summary)};
assert(readState(summary,summaryRead));
assert(readState({...summary,messages:[{id:'m-summary'}]},summaryRead),'activity marker preserves meaning in detail shape');
assert.equal(readState({...summary,lastMessageAt:'2026-10-05T00:02:00Z',messages:[{id:'new'}]},summaryRead),false);
const detailRead={'gorgias:2':readMarker({...summary,messages:[{id:'m-summary'}]})};
assert(readState(summary,detailRead));
assert.equal(readState({...summary,messages:[{id:'new'}]},detailRead),false);
assert.equal(readState(summary,{'gorgias:2':readMarker(summary,false)}),false);
const filtered={...ticket,customerName:'Foo Bar',assignee:'Owner@Example.invalid',channel:'Email',tags:['Returns']};
assert(matchesLocal(filtered,'assigned',{},effective,'owner@example.invalid'));
assert(matchesLocal(filtered,'all',{assignee:'owner@example.invalid',tag:'returns',channel:'email',query:'  FOO   BAR  '},effective,''));
assert(matchesLocal(filtered,'all',{query:'   '},effective,''));
assert.equal(matchesLocal(filtered,'all',{priority:'high'},effective,''),false);
assert(matchesLocal({...filtered,status:'closed'},'closed',{},effective,''));
assert.equal(matchesLocal({...filtered,status:'closed'},'open',{},effective,''),false);
assert(matchesLocal({...filtered,snooze:'2026-10-06T00:00:00Z'},'snoozed',{},effective,'',Date.parse('2026-10-05T00:00:00Z')));
const records={};
rememberObserved(records,{...filtered,fromEmail:'owner@example.invalid',subject:'x'.repeat(500),snippet:'s'.repeat(10000),messages:[{id:'m1',body:'private body'.repeat(1000)}],shopifyRail:{order:{name:'#12345',secret:'never store'}},secret:'never store'},1);
records[ticket.id].title={value:'User title'};
const saved=observedRows(records)[0];
assert.equal(saved.subject.length,200);assert.equal(saved.snippet.length,300);assert.equal(saved.orderName,'#12345');
assert.equal('messages' in saved,false);assert.equal('shopifyRail' in saved,false);assert.equal('secret' in saved,false);
rememberObserved(records,{...filtered,fromEmail:'other@example.invalid',lastMessageAt:'2026-10-06T00:00:00Z',updatedAt:'2026-10-06T00:00:00Z'},2);
assert.equal(observedRows(records)[0].orderName,undefined,'identity change must not keep another customer order label');
rememberObserved(records,{...filtered,lastMessageAt:'2026-10-05T00:00:00Z'},3);
assert.equal(observedRows(records)[0].lastMessageAt,'2026-10-06T00:00:00Z','older detail cannot roll back a saved newer observation');
const ordered={};
rememberObserved(ordered,{id:'gorgias:3',status:'closed',updatedAt:'2026-10-06T00:00:00Z'},1);
ordered['gorgias:3'].observed.lastMessageAt='';
rememberObserved(ordered,{id:'gorgias:3',status:'open',updatedAt:'2026-10-05T00:00:00Z',lastMessageAt:'2026-10-05T00:00:00Z'},2);
assert.equal(ordered['gorgias:3'].observed.status,'closed','known metadata time is protected even without a prior activity time');
const current={id:'gorgias:4',status:'closed',lastMessageAt:'2026-10-06T00:00:00Z',updatedAt:'2026-10-06T01:00:00Z',lastMessageId:123456};
rememberObserved(ordered,current,3);
assert.equal(ordered['gorgias:4'].observed.lastMessageId,'123456','numeric provider message IDs remain exact strings');
for(const incoming of [
  {...current,lastMessageAt:'2026-10-07T00:00:00Z',updatedAt:'2026-10-05T00:00:00Z'},
  {...current,lastMessageAt:'2026-10-05T00:00:00Z',updatedAt:'2026-10-07T00:00:00Z'},
  ...['2026-10-07T00:00:00','2026-02-30T00:00:00Z','not-a-date'].flatMap(time=>[
    {...current,lastMessageAt:time}, {...current,updatedAt:time},
  ]),
  {...current,lastMessageAt:'',updatedAt:''},
  {...current,updatedAt:''},
]) {
  rememberObserved(ordered,{...incoming,status:'open'},4);
  assert.equal(ordered['gorgias:4'].observed.status,'closed','neither older nor unverified metadata/activity can replace a known observation');
}
rememberObserved(ordered,{...current,status:'open',lastMessageAt:'2026-10-06T06:00:00+06:00',updatedAt:'2026-10-06T07:00:00+06:00'},5);
assert.equal(ordered['gorgias:4'].observed.status,'open','equivalent timezone-aware instants are accepted');
for(let i=0;i<MAX_OBSERVED_SUMMARIES;i++)rememberObserved(records,{id:`gorgias:${i+10}`,customerName:'Small summary'},i+10);
assert.equal(observedRows(records).length,MAX_OBSERVED_SUMMARIES);assert.equal(records[ticket.id].observed,undefined);
assert.equal(records[ticket.id].title.value,'User title','only disposable saved observations may be evicted');
console.log('Passed local record versions, read message watermarks, observed categories, assignment/snooze membership and validated private ticket shape.');
