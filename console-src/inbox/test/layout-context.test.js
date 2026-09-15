import test from 'node:test';
import assert from 'node:assert/strict';
import {createInboxOrgan} from '../js/inbox.js';

const ticket = {id:'gorgias:123',projectionSource:true,status:'unknown',customerName:'averylongsyntheticcustomer@example.test',subject:'A synthetic subject',updatedAt:'2026-09-07T00:00:00Z',messages:[],statusEvents:[]};
async function render(context) {
 const item={...ticket,customerContext:context};
 const shop={observedHistory:true, getCapabilities:async()=>({}), listTickets:async()=>[item],getTicket:async()=>item};
 // Explicit input avoids provider/network; the production projection DTO is the contract under test.
 return (await createInboxOrgan({shop,tickets:[item],viewId:'all'}).ready()).html;
}
test('observed identity is escaped, labelled and never presented as order lookup',async()=>{
 const html=await render({source:'canonical_webhook',status:'observed',conflict:false,observedAt:'2026-09-07T00:00:00Z',identity:{name:'<img src=x onerror=alert(1)>',email:'owner@example.test',phone:null,id:'12'}});
 assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);
 assert.doesNotMatch(html,/<img src=x/);
 assert.match(html,/Source: observed Gorgias webhook/);
 assert.match(html,/Shopify details are awaiting refresh/);
 assert.match(html,/class="ticket-name">averylong/);
 assert.doesNotMatch(html,/class="ticket-status">Unknown/);
 assert.match(html,/Activate the send access\./);
});
test('conflicted and missing identities never fabricate a customer',async()=>{
 const html=await render({source:'canonical_webhook',status:'conflict',conflict:true,identity:{name:'Do not render'}});
 assert.match(html,/Conflicting customer details/);
 assert.doesNotMatch(html,/Do not render/);
 assert.match(await render(null),/Customer identity was not included/);
});
