import test from 'node:test';
import assert from 'node:assert/strict';
import {readObservedTickets} from '../js/inbox.js';
const page = prefix => Array.from({length:100},(_,i)=>({id:`${prefix}:${i}`}));
test('generation swap restarts at zero without mixing pages',async()=>{
 const offsets=[];let n=0;
 const shop={listTickets:async ({offset})=>{offsets.push(offset);n++;shop.projection={generatedAt:n===1?'old':'new'};return n===1?page('old'):n===2?page('discard'):n===3?page('new'):[{id:'new:last'}];}};
 const rows=await readObservedTickets(shop,200);
 assert.deepEqual(offsets,[0,100,0,100]);assert.equal(rows.length,101);
 assert.ok(rows.every(row=>row.id.startsWith('new:')));
});
test('repeated generation swaps are bounded to one restart',async()=>{
 let n=0;const shop={listTickets:async()=>{shop.projection={generatedAt:String(++n)};return page('x');}};
 await assert.rejects(readObservedTickets(shop,200),/repeatedly/);assert.equal(n,4);
});
test('API failures are not retried or turned into partial success',async()=>{
 let n=0;const shop={listTickets:async()=>{if(++n===2)throw new Error('API unavailable');shop.projection={generatedAt:'same'};return page('x');}};
 await assert.rejects(readObservedTickets(shop,200),/API unavailable/);assert.equal(n,2);
});

test('initial page is 100 and an expanded prefix can exceed 500',async()=>{
 const offsets=[];const shop={projection:{generatedAt:'same',ticketCount:625},listTickets:async({offset})=>{offsets.push(offset);return Array.from({length:Math.min(100,625-offset)},(_,i)=>({id:String(offset+i)}));}};
 assert.equal((await readObservedTickets(shop)).length,100);
 assert.deepEqual(offsets,[0]);offsets.length=0;
 const rows=await readObservedTickets(shop,700);
 assert.equal(rows.length,625);assert.equal(new Set(rows.map(r=>r.id)).size,625);
 assert.deepEqual(offsets,[0,100,200,300,400,500,600]);
});

test('load more preserves selection and reply, retries failures and reaches the end',async()=>{
 const {createInboxOrgan}=await import('../js/inbox.js');
 const tickets=Array.from({length:625},(_,i)=>({id:`gorgias:${i}`,subject:`Ticket ${i}`,customerName:'Test',messages:[],status:'unknown',projectionSource:true}));
 let fail=false;
 const shop={observedHistory:true,projection:{generatedAt:'same',ticketCount:625},getCapabilities:async()=>({}),
 listTickets:async({offset,limit})=>{if(fail)throw Error('Unavailable');return tickets.slice(offset,offset+limit);},getTicket:async({ticketId})=>tickets.find(t=>t.id===ticketId)};
 const organ=createInboxOrgan({shop,viewId:'all'});
 const first=await organ.ready(); assert.match(first.html,/Showing 100 of 625/);
 organ.setBody('Keep this unsent reply');
 fail=true;const failed=await organ.loadMore();assert.match(failed.html,/Could not load more/);assert.match(failed.html,/Showing 100|data-ticket="gorgias:99"/);
 fail=false;
 for(let i=0;i<6;i++)await organ.loadMore();
 const final=organ.snapshot();assert.equal(final.selectedId,first.selectedId);
 assert.match(final.html,/Keep this unsent reply/);assert.match(final.html,/Showing 625 of 625/);
 assert.match(final.html,/All available tickets loaded/);assert.doesNotMatch(final.html,/data-load-more/);
});
