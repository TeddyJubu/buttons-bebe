import test from 'node:test';
import assert from 'node:assert/strict';
import {createInboxOrgan} from '../js/inbox.js';
import {ticketInView, viewCounts, views} from '../js/view-model.js';

const OPERATOR = 'operator@example.test';

function projected(id, state) {
  return {id:`gorgias:${id}`,projectionSource:true,historyIncomplete:true,subject:`Subject ${id}`,
    customerName:`customer${id}@example.test`,snippet:'Observed',updatedAt:`2026-09-${String(id).padStart(2,'0')}T00:00:00Z`,
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
  for (const viewId of ['mine','unassigned','open','snoozed','closed','all']) {
    assert.equal(ticketInView(trashed,viewId), false, viewId);
    assert.equal(ticketInView(spam,viewId), false, viewId);
  }
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
  // All excludes the spam/trash rows (#33): 8 rows partition into 6 + 1 + 1.
  assert.equal(snap.html.match(/class="ticket-row/g).length, ROWS.length - 2);
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
  const expected = {all:6, open:3, mine:1, unassigned:1, snoozed:1, closed:1, trash:1, spam:1};
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

// #34: read/unread must work on the observed inbox. First-seen rows start
// unread with a dot + screen-reader label; opening marks read; the read set
// persists to localStorage so a reload keeps the distinction instead of
// regressing to "everything read" (or "everything unread").
function freshStorage() {
  const backing = new Map();
  return {
    getItem: (k) => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => backing.set(k, String(v)),
    removeItem: (k) => backing.delete(k),
  };
}

test('first-seen observed rows render unread with a dot and screen-reader label', async () => {
  const rows = [projected(21,{status:'open'}), projected(22,{status:'open'})];
  const organ = createInboxOrgan({shop:observedShop(rows), storage:freshStorage()});
  const snap = await organ.ready();
  // The auto-opened first row is already read (its thread is displayed); the
  // second row was never opened, so it starts unread with a dot.
  assert.match(snap.html, /class="ticket-row is-unread"[^>]*data-ticket="gorgias:22"/);
  assert.match(snap.html, /data-ticket="gorgias:22"[\s\S]*?data-unread-dot[^>]*role="img"[^>]*aria-label="Unread"/);
  assert.equal(snap.unreadIds.includes('gorgias:22'), true);
  // Opening the unread row marks it read and drops the dot.
  const after = await organ.selectTicket('gorgias:22');
  assert.doesNotMatch(after.html, /class="ticket-row[^"]*is-unread[^"]*"[^>]*data-ticket="gorgias:22"/);
  assert.doesNotMatch(after.html, /data-unread-dot/);
  assert.equal(after.unreadIds.includes('gorgias:22'), false);
});

test('read state survives reload via the persisted store', async () => {
  const rows = [projected(21,{status:'open'}), projected(22,{status:'open'}), projected(23,{status:'open'})];
  const storage = freshStorage();
  const first = createInboxOrgan({shop:observedShop(rows), storage});
  await first.ready();
  // Row 21 is auto-opened by ready(); row 22 is opened explicitly, so the
  // reload below only passes if BOTH the auto-open and the explicit open
  // persisted. Row 23 stays unread.
  await first.selectTicket('gorgias:22');
  const reloaded = await createInboxOrgan({shop:observedShop(rows), storage}).ready();
  assert.doesNotMatch(reloaded.html, /class="ticket-row[^"]*is-unread[^"]*"[^>]*data-ticket="gorgias:2[12]"/);
  assert.match(reloaded.html, /class="ticket-row is-unread"[^>]*data-ticket="gorgias:23"/);
  assert.deepEqual(reloaded.unreadIds, ['gorgias:23']);
});

test('persisted read ids are honored on first paint and never re-mark unread', async () => {
  const rows = [projected(21,{status:'open'}), projected(22,{status:'open'})];
  const storage = freshStorage();
  storage.setItem('bb-inbox-read-v1', JSON.stringify(['gorgias:21','gorgias:22']));
  const snap = await createInboxOrgan({shop:observedShop(rows), storage}).ready();
  assert.deepEqual(snap.unreadIds, []);
  assert.doesNotMatch(snap.html, /is-unread/);
});

test('a blocked localStorage keeps the observed inbox session-local instead of throwing', async () => {
  Object.defineProperty(globalThis, 'localStorage', {get() { throw new Error('blocked'); }, configurable: true});
  try {
    const rows = [projected(24,{status:'open'})];
    const snap = await createInboxOrgan({shop:observedShop(rows)}).ready();
    assert.match(snap.html, /data-ticket="gorgias:24"/);
    assert.deepEqual(snap.unreadIds, []);
    const after = await createInboxOrgan({shop:observedShop(rows)}).selectTicket('gorgias:24');
    assert.equal(after.unreadIds.includes('gorgias:24'), false);
  } finally {
    delete globalThis.localStorage;
  }
});

test('two tabs merge read sets instead of the stale tab clobbering the fresh one', async () => {
  const rows = [projected(25,{status:'open'}), projected(26,{status:'open'}), projected(27,{status:'open'})];
  const storage = freshStorage();
  const tabA = createInboxOrgan({shop:observedShop(rows), storage});
  await tabA.ready();  // auto-opens 25; tabA's in-memory read set is now stale
  // Another tab persists 25 and 26 behind tabA's back.
  storage.setItem('bb-inbox-read-v1', JSON.stringify(['gorgias:25','gorgias:26']));
  await tabA.selectTicket('gorgias:27');
  const stored = JSON.parse(storage.getItem('bb-inbox-read-v1'));
  assert.deepEqual([...stored].sort(), ['gorgias:25','gorgias:26','gorgias:27']);
});
