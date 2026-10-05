import assert from 'node:assert/strict';
import fs from 'node:fs';
const source=fs.readFileSync(new URL('../local_state.js',import.meta.url),'utf8');
const {stateRecords,readRecords,readState,readMarker,localTicket,matchesLocal,rememberObserved,observedRows,syncObservedOverride,enrichMessageEvidence,MAX_OBSERVED_SUMMARIES}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
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
const numericMessage={id:'gorgias:5',lastMessageId:123456,updatedAt:'2026-10-05T01:00:00Z'};
const numericRead={'gorgias:5':readMarker(numericMessage)};
assert(readState({...numericMessage,updatedAt:'2026-10-05T02:00:00Z'},numericRead),'mutable metadata does not unread an unchanged known message');
assert.equal(readState({...numericMessage,lastMessageId:123457},numericRead),false,'a newer numeric message identity invalidates read');
const deficient={id:'gorgias:601',status:'closed',gorgiasPriority:'high',assigneeEmail:'row@example.invalid',subject:'Row subject',snippet:'Row snippet',updatedAt:'2026-10-05T01:00:00Z'};
const accepted={...deficient,status:'open',gorgiasPriority:'normal',assigneeEmail:'detail@example.invalid',messages:[{id:'m1',at:'2026-10-05T00:00:00Z'}]};
const enriched=enrichMessageEvidence(deficient,accepted);
assert.deepEqual(enriched,{...deficient,lastMessageAt:'2026-10-05T00:00:00Z',lastMessageId:'m1'},'only genuine message evidence enriches a matching provider row');
assert.equal(Object.hasOwn(deficient,'lastMessageAt'),false,'enrichment leaves input unchanged');
assert.equal(readState(enriched,{[deficient.id]:readMarker(accepted)}),true);
assert.equal(readState({...deficient,lastMessageId:'m1'},{[deficient.id]:readMarker(accepted)}),true,'same known ID already reads without timestamp enrichment');
assert.deepEqual(enrichMessageEvidence(deficient,{...deficient,messages:[{id:123456}]}),{...deficient,lastMessageId:'123456'},'ID-only actual numeric message does not manufacture activity');
for(const incoming of [
  {...deficient}, {...deficient,updatedAt:'2026-10-05T02:00:00Z'},
  {...accepted,updatedAt:'2026-10-05T00:00:00Z'},
  {...accepted,updatedAt:''}, {...accepted,updatedAt:'2026-10-05T02:00:00'},
  ...['not-a-time','2026-02-30T00:00:00Z','2026-10-05T00:00:00'].map(at=>({...deficient,messages:[{at}]})),
  {...accepted,id:'gorgias:602'}, {...accepted,localOnly:true},
])assert.deepEqual(enrichMessageEvidence(deficient,incoming),deficient,'missing/invalid message evidence or regressed/unknown metadata never invents a watermark');
const newer={...deficient,lastMessageAt:'2026-10-05T03:00:00Z',lastMessageId:'m3',updatedAt:'2026-10-05T04:00:00Z'};
for(const incoming of [
  accepted, {...accepted,updatedAt:'2026-10-05T05:00:00Z'},
  {...newer,lastMessageId:'different'}, {...newer,lastMessageAt:'',lastMessageId:'different'},
  {...newer,lastMessageAt:'2026-10-05T05:00:00Z',updatedAt:'2026-10-05T03:00:00Z',lastMessageId:'different'},
])assert.deepEqual(enrichMessageEvidence(newer,incoming),newer,'independent clocks and equal/unknown differing IDs preserve existing message evidence');
assert.deepEqual(enrichMessageEvidence({...deficient,lastMessageId:'m-old'},accepted),{...deficient,lastMessageId:'m-old'},'a differing known ID cannot be replaced when previous activity ordering is unknown');
assert.deepEqual(enrichMessageEvidence({...deficient,id:'local:uuid'},accepted),{...deficient,id:'local:uuid'});
assert.deepEqual(enrichMessageEvidence(newer,{...newer,lastMessageAt:'2026-10-05T05:00:00Z',lastMessageId:'m4'}),{...newer,lastMessageAt:'2026-10-05T05:00:00Z',lastMessageId:'m4'},'fully nonregressing genuine new activity may advance');
const idAbsent={...deficient,updatedAt:'2026-10-05T02:00:00Z',lastMessageAt:'2026-10-05T02:00:00Z',messages:[{id:'',at:'2026-10-05T02:00:00Z'}]};
const oldMessage={id:'m1',at:'2026-10-05T00:00:00Z',body:'Keep the old message content',attachments:[{name:'Keep attachment'}]};
for(const aliases of [
  {lastMessageId:'m1'}, {latestMessageId:'m1'}, {messages:[oldMessage]},
  {lastMessageId:'m1',latestMessageId:'m1',messages:[{id:'history',body:'Keep earlier content'},oldMessage]},
]){
  const prior={...deficient,lastMessageAt:'2026-10-05T00:00:00Z',...aliases},snapshot=structuredClone(prior),mark={[prior.id]:readMarker(prior)};
  const advanced=enrichMessageEvidence(prior,idAbsent);
  assert.equal(advanced.lastMessageAt,'2026-10-05T02:00:00Z');
  assert.equal(readState(advanced,mark),false,'newer actual activity without an ID cannot retain any stale message identity alias');
  const expected={...prior,lastMessageAt:idAbsent.lastMessageAt};delete expected.lastMessageId;delete expected.latestMessageId;
  if(expected.messages)expected.messages=[...expected.messages.slice(0,-1),{...expected.messages.at(-1),id:''}];
  assert.deepEqual(advanced,expected,'only stale identity aliases and actual activity change; message content and metadata remain intact');
  assert.deepEqual(prior,snapshot,'clearing fallback identity does not mutate the original message or row');
  const unchanged=enrichMessageEvidence(prior,{...idAbsent,lastMessageAt:prior.lastMessageAt,messages:[{id:'',at:prior.lastMessageAt}]});
  assert.deepEqual(unchanged,prior,'unchanged time without incoming ID preserves known identity');assert.equal(readState(unchanged,mark),true);
  for(const rejected of [
    {...idAbsent,updatedAt:'2026-10-05T00:00:00Z'}, {...idAbsent,updatedAt:''},
    {...idAbsent,lastMessageAt:'2026-10-04T00:00:00Z'},
    {...idAbsent,lastMessageAt:'2026-10-05T02:00:00'}, {...idAbsent,lastMessageAt:'not-a-time'},
  ])assert.deepEqual(enrichMessageEvidence(prior,rejected),prior,'rejected independent clock observations retain all old identity evidence');
}
const unknownPrior={...deficient,lastMessageId:'m1'};
assert.equal(enrichMessageEvidence(unknownPrior,idAbsent).lastMessageId,'m1','unknown previous activity is not license to invalidate a held identity');
assert.equal(readState(enrichMessageEvidence(unknownPrior,idAbsent),{[unknownPrior.id]:readMarker(unknownPrior)}),true,'unknown-order retained identity remains an explicit existing read-policy limit');
const filtered={...ticket,customerName:'Foo Bar',assignee:'Owner@Example.invalid',channel:'Email',tags:['Returns']};
assert(matchesLocal(filtered,'assigned',{},effective,'owner@example.invalid'));
assert(matchesLocal(filtered,'all',{assignee:'owner@example.invalid',tag:'returns',channel:'email',query:'  FOO   BAR  '},effective,''));
assert(matchesLocal(filtered,'all',{query:'   '},effective,''));
assert.equal(matchesLocal(filtered,'all',{priority:'high'},effective,''),false);
assert(matchesLocal({...filtered,status:'closed'},'closed',{},effective,''));
assert.equal(matchesLocal({...filtered,status:'closed'},'open',{},effective,''),false);
assert(matchesLocal({...filtered,snooze:'2026-10-06T00:00:00Z'},'snoozed',{},effective,'',Date.parse('2026-10-05T00:00:00Z')));
assert(matchesLocal({...filtered,fromEmail:'Customer@Example.invalid'},'all',{query:'customer@example.invalid'},effective,''));
assert(matchesLocal({...filtered,snippet:'x'.repeat(200)},'all',{query:'x'.repeat(200)},effective,''));
assert.equal(matchesLocal({...filtered,snippet:'x'.repeat(201)},'all',{query:'x'.repeat(201)},effective,''),false,'deep-linked query must respect the UI normalized search bound');
const cleared={'gorgias:8':{status:null,observed:{id:'gorgias:8'},notes:{value:'Private note'}}};
syncObservedOverride(cleared,{id:'gorgias:8'});
assert.deepEqual(cleared['gorgias:8'],{notes:{value:'Private note'}},'last override removal discards only disposable observation/empty overrides');
cleared['gorgias:9']={status:null,observed:{id:'gorgias:9'}};
syncObservedOverride(cleared,{id:'gorgias:9'});assert.equal(cleared['gorgias:9'],undefined);
cleared['gorgias:10']={status:null,title:{value:'Private title'},notes:{value:'Keep this'}};
syncObservedOverride(cleared,{id:'gorgias:10',subject:'Observed subject'});
assert.equal(cleared['gorgias:10'].title.value,'Private title');assert.equal(cleared['gorgias:10'].notes.value,'Keep this');assert(cleared['gorgias:10'].observed);
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
assert.equal(ordered['gorgias:3'].observed.lastMessageAt,'','metadata timestamp must not fabricate message activity');
rememberObserved(ordered,{id:'gorgias:6',status:'closed',lastMessageAt:'2026-10-05T00:00:00Z',updatedAt:'2026-10-05T01:00:00Z'},2);
rememberObserved(ordered,{id:'gorgias:6',status:'open',updatedAt:'2026-10-05T02:00:00Z'},3);
assert.equal(ordered['gorgias:6'].observed.status,'closed','newer metadata with missing actual message time cannot replace known activity');
assert.equal(ordered['gorgias:6'].observed.lastMessageAt,'2026-10-05T00:00:00Z');
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
rememberObserved(ordered,{...current,status:'closed',lastMessageAt:'2026-10-07T00:00:00Z',updatedAt:'2026-10-07T01:00:00Z'},6);
assert.equal(ordered['gorgias:4'].observed.status,'closed','a fully nonregressing observation advances after mixed-clock observations were held');
for(let i=0;i<MAX_OBSERVED_SUMMARIES;i++)rememberObserved(records,{id:`gorgias:${i+10}`,customerName:'Small summary'},i+10);
assert.equal(observedRows(records).length,MAX_OBSERVED_SUMMARIES);assert.equal(records[ticket.id].observed,undefined);
assert.equal(records[ticket.id].title.value,'User title','only disposable saved observations may be evicted');
console.log('Passed local record versions, read message watermarks, observed categories, assignment/snooze membership and validated private ticket shape.');
