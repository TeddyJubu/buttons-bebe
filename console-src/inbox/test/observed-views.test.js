import test from 'node:test';
import assert from 'node:assert/strict';
import {createInboxOrgan} from '../js/inbox.js';
import {ticketInView, viewCounts, views} from '../js/view-model.js';

const OPERATOR = 'operator@example.test';

function projected(id, state) {
  return {id:`gorgias:${id}`,projectionSource:true,historyIncomplete:true,subject:`Subject ${id}`,
    customerName:`customer${id}@example.test`,snippet:'Observed',updatedAt:`2026-09-0${id}T00:00:00Z`,
    status:'unknown',assignee:null,assigneeEmail:null,assigneeUserId:null,spam:false,trashed:false,
    messages:[],statusEvents:[],customerContext:null,...state};
}

// One shop per scenario mirrors the projection API, which has no view parameter:
// every view is a client-side filter over the same observed rows.
const ROWS = [
  projected(1,{status:'open',assigneeEmail:'Operator@Example.Test',assigneeUserId:'7'}),
  projected(2,{status:'open'}),
  projected(3,{status:'open',assigneeEmail:'colleague@example.test'}),
  projected(4,{status:'snoozed'}),
  projected(5,{status:'closed'}),
  projected(6,{}),
  projected(7,{trashed:true}),
  projected(8,{spam:true}),
];

function observedShop(rows = ROWS, operatorEmail = OPERATOR) {
  return {observedHistory:true, operatorEmail, projection:{generatedAt:'gen-1',stale:false},
    getCapabilities:async () => ({}),
    listTickets:async () => rows,
    getTicket:async ({ticketId}) => rows.find((row) => row.id === ticketId) || null};
}

test('trash and spam are flag buckets that stay out of the working views', () => {
  assert.deepEqual(views.map((view) => view.id), ['open','mine','unassigned','all','snoozed','closed','trash','spam']);
  const trashed = projected(9,{status:'open',assignee:'me',trashed:true});
  const spam = projected(10,{status:'closed',spam:true});
  assert.equal(ticketInView(trashed,'trash'), true);
  assert.equal(ticketInView(spam,'spam'), true);
  for (const viewId of ['mine','unassigned','open','snoozed','closed']) {
    assert.equal(ticketInView(trashed,viewId), false, viewId);
    assert.equal(ticketInView(spam,viewId), false, viewId);
  }
  assert.equal(ticketInView(trashed,'all'), true);
  assert.equal(ticketInView(spam,'all'), true);
});

test('a ticket whose status was never observed appears only in All', () => {
  const unknown = projected(11,{});
  assert.equal(ticketInView(unknown,'all'), true);
  for (const viewId of ['mine','unassigned','open','snoozed','closed','trash','spam']) {
    assert.equal(ticketInView(unknown,viewId), false, viewId);
  }
  assert.deepEqual(viewCounts([unknown]), {open:0,mine:0,unassigned:0,all:1,snoozed:0,closed:0,trash:0,spam:0});
});

test('observed history offers the default views with honest counts', async () => {
  const snap = await createInboxOrgan({shop:observedShop()}).ready();
  for (const view of views) assert.match(snap.html, new RegExp(`data-view="${view.id}"`), view.id);
  assert.doesNotMatch(snap.html, /Observed history<\/span>/);
  assert.match(snap.html, /Status and assignment are shown when the latest observed webhook carried them/);
  assert.equal(snap.viewId, 'all');
  assert.equal(snap.html.match(/class="ticket-row/g).length, ROWS.length);
});

test('Assigned to me matches only the configured operator address', async () => {
  const mine = await createInboxOrgan({shop:observedShop(), viewId:'mine'}).ready();
  assert.equal(mine.selectedId, 'gorgias:1');
  assert.match(mine.html, /data-ticket="gorgias:1"/);
  assert.equal(mine.html.match(/class="ticket-row/g).length, 1);
  // A colleague's ticket is assigned, so it is neither mine nor unassigned.
  const unassigned = await createInboxOrgan({shop:observedShop(), viewId:'unassigned'}).ready();
  assert.equal(unassigned.html.match(/class="ticket-row/g).length, 1);
  assert.match(unassigned.html, /data-ticket="gorgias:2"/);
  assert.doesNotMatch(unassigned.html, /data-ticket="gorgias:3"/);
});

test('an unset operator address leaves Assigned to me empty rather than guessing', async () => {
  const snap = await createInboxOrgan({shop:observedShop(ROWS,''), viewId:'mine'}).ready();
  assert.equal(snap.selectedId, null);
  assert.match(snap.html, /No tickets yet/);
  assert.doesNotMatch(snap.html, /data-ticket="gorgias:1"/);
});

test('each observed view filters the same snapshot without inventing rows', async () => {
  const organ = createInboxOrgan({shop:observedShop()});
  await organ.ready();
  const expected = {all:8, open:3, mine:1, unassigned:1, snoozed:1, closed:1, trash:1, spam:1};
  for (const [viewId, count] of Object.entries(expected)) {
    const snap = await organ.selectView(viewId);
    assert.equal(snap.html.match(/class="ticket-row/g)?.length ?? 0, count, viewId);
  }
  const closed = await organ.selectView('closed');
  assert.match(closed.html, /data-ticket="gorgias:5"[^>]*data-status="closed"/);
  assert.match(closed.html, /class="ticket-status">Closed</);
  const trash = await organ.selectView('trash');
  assert.match(trash.html, /data-ticket="gorgias:7"/);
  assert.doesNotMatch(trash.html, /class="ticket-status">Unknown</);
  assert.equal(organ.attemptSend().sendError, 'Activate the send access.');
});

test('a projection without observed state keeps every view except All empty', async () => {
  const rows = [projected(12,{}), projected(13,{})];
  const organ = createInboxOrgan({shop:observedShop(rows)});
  await organ.ready();
  for (const view of views) {
    const snap = await organ.selectView(view.id);
    const rendered = snap.html.match(/class="ticket-row/g)?.length ?? 0;
    assert.equal(rendered, view.id === 'all' ? rows.length : 0, view.id);
  }
});
