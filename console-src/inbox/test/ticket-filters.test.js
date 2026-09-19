import test from "node:test";
import assert from "node:assert/strict";
import { createInboxOrgan } from "../js/inbox.js";
import { createMailbox } from "../js/mailbox.js";
import { forbiddenControlHits } from "../js/util.js";
import { MAILBOX_TOPICS } from "../js/contracts.js";

// Issue #36: a Gorgias-style filter builder over the fields the observed
// snapshot actually holds. Conditions live in the organ, render as chips
// with Clear all, carry into the URL, and save as first-party views.
// Filtering is a client-side read over the loaded rows — it never writes
// to Gorgias and never mutates the snapshot.
const OPERATOR = "operator@example.test";

function projected(id, state) {
  return {id: `gorgias:${id}`, projectionSource: true, historyIncomplete: true,
    subject: `Subject ${id}`, customerName: `customer${id}@example.test`,
    fromEmail: `customer${id}@example.test`,
    snippet: "Observed", updatedAt: `2026-09-0${id}T00:00:00Z`,
    status: "open", assignee: null, assigneeEmail: null, messages: [], statusEvents: [], ...state};
}

function filterRows() {
  return [
    projected(1, {tags: ["vip"], channel: "email", assignee: "amy@example.com", gorgiasPriority: "high"}),
    projected(2, {tags: ["urgent"], channel: "chat", assignee: "bo@example.com", gorgiasPriority: "low", status: "closed"}),
    projected(3, {tags: ["vip", "urgent"], channel: "email", assignee: null, gorgiasPriority: "high"}),
  ];
}

function filterShop() {
  const rows = filterRows();
  return {
    observedHistory: true, operatorEmail: OPERATOR, projection: {generatedAt: "gen-1", stale: false},
    getCapabilities: async () => ({}),
    listTickets: async () => rows,
    getTicket: async ({ticketId}) => rows.find((row) => row.id === ticketId) || null,
  };
}

function freshStorage() {
  const backing = new Map();
  return {
    getItem: (k) => (backing.has(k) ? backing.get(k) : null),
    setItem: (k, v) => backing.set(k, String(v)),
    removeItem: (k) => backing.delete(k),
    dump: () => backing,
  };
}

function fakeHistory() {
  const entries = [];
  return {
    entries,
    replace(entry) { entries.push({kind: "replace", ...entry}); },
    push(entry) { entries.push({kind: "push", ...entry}); },
  };
}

function makeOrgan(overrides = {}) {
  const {shop: shopOverrides, ...rest} = overrides;
  return createInboxOrgan({
    shop: {...filterShop(), ...shopOverrides},
    storage: freshStorage(),
    ...rest,
  });
}

test("the filter builder offers condition rows over the fields we hold", async () => {
  const organ = makeOrgan();
  await organ.ready();
  const snap = await organ.setFilterConditions([{field: "status", op: "is", values: ["open"]}]);
  assert.match(snap.html, /data-add-condition/, "the builder has an Add condition control");
  assert.match(snap.html, /data-filter-match/, "the builder offers match all/any");
  assert.match(snap.html, /data-filter-field/, "a condition row carries its field select");
  assert.match(snap.html, /data-filter-op/, "a condition row carries its operator select");
  for (const label of ["Status", "Assignee", "Tag", "Channel", "Priority", "Customer", "Updated"]) {
    assert.match(snap.html, new RegExp(`value="${label === "Customer" ? "customer" : label.toLowerCase()}"[^>]*>${label}<`), `the field select offers ${label}`);
  }
  assert.match(snap.html, /aria-label="Remove condition"/, "rows are removable with a label");
  assert.match(snap.html, /aria-label="Filter field"/, "the field select is labeled");
  assert.deepEqual(forbiddenControlHits(snap.html), []);
});

test("conditions filter rows with is, is not, and multiple values", async () => {
  const organ = makeOrgan();
  await organ.ready();
  // Multi-value: tag is vip or urgent matches tickets 1, 2 and 3.
  const either = await organ.setFilterConditions([{field: "tag", op: "is", values: ["vip", "urgent"]}]);
  for (const id of [1, 2, 3]) assert.match(either.html, new RegExp(`data-ticket="gorgias:${id}"`));
  // Is not: tag is not vip leaves only ticket 2.
  const notVip = await organ.setFilterConditions([{field: "tag", op: "isNot", values: ["vip"]}]);
  assert.match(notVip.html, /data-ticket="gorgias:2"/);
  assert.doesNotMatch(notVip.html, /data-ticket="gorgias:1"/);
  assert.doesNotMatch(notVip.html, /data-ticket="gorgias:3"/);
  // A fresh, valueless row is a no-op — adding a row never blanks the list.
  const noop = await organ.setFilterConditions([{field: "status", op: "is", values: []}]);
  for (const id of [1, 2, 3]) assert.match(noop.html, new RegExp(`data-ticket="gorgias:${id}"`));
});

test("match all composes conditions with AND, match any with OR", async () => {
  const organ = makeOrgan();
  await organ.ready();
  const conditions = [
    {field: "tag", op: "is", values: ["vip"]},
    {field: "status", op: "is", values: ["closed"]},
  ];
  const all = await organ.setFilterConditions(conditions, {match: "all"});
  assert.doesNotMatch(all.html, /data-ticket="gorgias:/, "no row is both vip and closed");
  const any = await organ.setFilterConditions(conditions, {match: "any"});
  assert.match(any.html, /data-ticket="gorgias:1"/, "vip rows match under any");
  assert.match(any.html, /data-ticket="gorgias:2"/, "the closed row matches under any");
  assert.match(any.html, /data-ticket="gorgias:3"/, "ticket 3 carries vip, so it matches under any too");
  // A row that satisfies neither condition stays out even under any.
  const none = await organ.setFilterConditions([
    {field: "channel", op: "is", values: ["sms"]},
    {field: "status", op: "is", values: ["snoozed"]},
  ], {match: "any"});
  assert.doesNotMatch(none.html, /data-ticket="gorgias:/, "no row satisfies either condition");
});

test("priority, customer, and updated conditions match the snapshot fields", async () => {
  const organ = makeOrgan();
  await organ.ready();
  const high = await organ.setFilterConditions([{field: "priority", op: "is", values: ["high"]}]);
  assert.match(high.html, /data-ticket="gorgias:1"/);
  assert.match(high.html, /data-ticket="gorgias:3"/);
  assert.doesNotMatch(high.html, /data-ticket="gorgias:2"/);
  const customer = await organ.setFilterConditions([{field: "customer", op: "contains", values: ["customer2"]}]);
  assert.match(customer.html, /data-ticket="gorgias:2"/);
  assert.doesNotMatch(customer.html, /data-ticket="gorgias:1"/);
  const before = await organ.setFilterConditions([{field: "updated", op: "before", values: ["2026-09-02"]}]);
  assert.match(before.html, /data-ticket="gorgias:1"/, "only ticket 1 predates the edge");
  assert.doesNotMatch(before.html, /data-ticket="gorgias:2"/);
  const after = await organ.setFilterConditions([{field: "updated", op: "after", values: ["2026-09-02"]}]);
  assert.match(after.html, /data-ticket="gorgias:2"/);
  assert.match(after.html, /data-ticket="gorgias:3"/);
  assert.doesNotMatch(after.html, /data-ticket="gorgias:1"/);
});

test("the assignee condition covers named people and unassigned", async () => {
  const organ = makeOrgan();
  await organ.ready();
  const named = await organ.setFilterConditions([{field: "assignee", op: "is", values: ["amy@example.com"]}]);
  assert.match(named.html, /data-ticket="gorgias:1"/);
  assert.doesNotMatch(named.html, /data-ticket="gorgias:2"/);
  const unassigned = await organ.setFilterConditions([{field: "assignee", op: "is", values: ["unassigned"]}]);
  assert.match(unassigned.html, /data-ticket="gorgias:3"/);
  assert.doesNotMatch(unassigned.html, /data-ticket="gorgias:1"/);
});

test("chips summarize the active conditions with per-chip remove and Clear all", async () => {
  const organ = makeOrgan();
  await organ.ready();
  const snap = await organ.setFilterConditions([
    {field: "status", op: "is", values: ["open"]},
    {field: "tag", op: "isNot", values: ["urgent"]},
  ]);
  assert.match(snap.html, /data-filter-chip/, "each condition renders a chip");
  assert.match(snap.html, /Status/, "the chip names its field");
  assert.match(snap.html, /data-filter-remove/, "each chip carries a remove control");
  assert.match(snap.html, /data-filter-clear/, "Clear all renders");
  assert.match(snap.html, /aria-label="Remove Status filter"/, "chip removal is labeled");
  // Removing one condition leaves the other filtering.
  const one = await organ.setFilterConditions([{field: "status", op: "is", values: ["open"]}]);
  assert.match(one.html, /data-filter-chip/);
  assert.doesNotMatch(one.html, /Tag is not urgent/, "the removed condition's chip is gone");
  const cleared = await organ.clearFilters();
  assert.doesNotMatch(cleared.html, /data-filter-chip/, "no chips survive Clear all");
  for (const id of [1, 2, 3]) assert.match(cleared.html, new RegExp(`data-ticket="gorgias:${id}"`));
});

test("filter state syncs into the URL and replays on popstate", async () => {
  const history = fakeHistory();
  const organ = makeOrgan({history});
  await organ.ready();
  await organ.setFilterConditions([{field: "tag", op: "is", values: ["vip"]}], {match: "any"});
  const current = history.entries.at(-1);
  assert.ok(current.f, "a committed filter carries into the URL entry");
  const parsed = JSON.parse(current.f);
  assert.equal(parsed.m, "any", "the match mode travels with the conditions");
  assert.deepEqual(parsed.c, [{field: "tag", op: "is", values: ["vip"]}]);
  // A replay of the entry restores the conditions; without f they clear.
  const restored = await organ.replayEntry({ticket: null, view: "all", f: current.f});
  assert.equal(restored.filterConditions.length, 1, "the replay restores the conditions");
  assert.equal(restored.filterMatch, "any", "the replay restores the match mode");
  assert.doesNotMatch(restored.html, /data-ticket="gorgias:2"/);
  const cleared = await organ.replayEntry({ticket: null, view: "all"});
  assert.equal(cleared.filterConditions.length, 0, "a replay without f clears the filters");
  assert.match(cleared.html, /data-ticket="gorgias:2"/);
});

test("a deep link with f restores the filters on boot", async () => {
  const f = JSON.stringify({m: "all", c: [{field: "channel", op: "is", values: ["email"]}]});
  const organ = makeOrgan({filters: f});
  const snap = await organ.ready();
  assert.equal(snap.filterConditions.length, 1, "boot's f seeds the organ filters");
  assert.match(snap.html, /data-ticket="gorgias:1"/, "the restored filter narrows rows");
  assert.doesNotMatch(snap.html, /data-ticket="gorgias:2"/);
  assert.match(snap.html, /data-filter-chip/, "the restored filter shows its chips");
});

test("overlong or malformed filter state is bounded, never fatal", async () => {
  const organ = makeOrgan({filters: "not-json{"});
  const snap = await organ.ready();
  assert.equal(snap.filterConditions.length, 0, "malformed seed state yields no filters");
  const many = await organ.setFilterConditions(
    Array.from({length: 20}, () => ({field: "status", op: "is", values: ["open"]})),
  );
  assert.ok(many.filterConditions.length <= 8, "the organ bounds the condition count");
  const hostile = await organ.setFilterConditions([{field: "customer", op: "contains", values: ["x".repeat(5000)]}]);
  assert.ok(hostile.filterConditions[0].values[0].length <= 120, "values are clamped");
  assert.doesNotMatch(hostile.html, /data-ticket="gorgias:/, "a clamped hostile value misses, it never crashes");
});

test("facet picks remain and now write conditions", async () => {
  const organ = makeOrgan();
  await organ.ready();
  const pick = await organ.selectChannel("chat");
  assert.equal(pick.channelId, "chat", "the derived facet id still publishes");
  assert.equal(pick.filterConditions.length, 1, "a facet pick writes a condition");
  assert.deepEqual(pick.filterConditions, [{field: "channel", op: "is", values: ["chat"]}]);
  assert.match(pick.html, /data-filter-chip/, "the facet pick renders its chip");
  const cleared = await organ.selectChannel("");
  assert.equal(cleared.filterConditions.length, 0, "All channels clears the field's conditions");
  // A facet pick on a field the builder already filtered replaces that
  // field's conditions — the quick pick is authoritative.
  await organ.setFilterConditions([{field: "tag", op: "isNot", values: ["urgent"]}]);
  const replaced = await organ.selectTag("vip");
  assert.deepEqual(replaced.filterConditions, [{field: "tag", op: "is", values: ["vip"]}],
    "the facet pick replaces the field's builder conditions");
});

test("filters clear on a view switch like search does", async () => {
  const organ = makeOrgan();
  await organ.ready();
  await organ.setFilterConditions([{field: "tag", op: "is", values: ["vip"]}]);
  const switched = await organ.selectView("open");
  assert.equal(switched.filterConditions.length, 0, "a new view clears the conditions");
  assert.match(switched.html, /data-ticket="gorgias:1"/, "the view renders unfiltered");
});

test("search and conditions compose", async () => {
  const organ = makeOrgan();
  await organ.ready();
  await organ.selectSearch("Subject 1");
  const both = await organ.setFilterConditions([{field: "priority", op: "is", values: ["high"]}]);
  assert.match(both.html, /data-ticket="gorgias:1"/, "the row satisfies both the query and the condition");
  const clash = await organ.setFilterConditions([{field: "priority", op: "is", values: ["low"]}]);
  assert.doesNotMatch(clash.html, /data-ticket="gorgias:/, "no row satisfies both when they clash");
});

test("saved views persist first-party and list under the views menu", async () => {
  const storage = freshStorage();
  const organ = makeOrgan({storage});
  await organ.ready();
  await organ.setFilterConditions([{field: "tag", op: "is", values: ["vip"]}], {match: "any"});
  const saved = await organ.saveFilterView({name: "Brand team", shared: true});
  assert.equal(saved.savedViews.length, 1, "the snapshot lists the saved view");
  assert.equal(saved.savedViews[0].name, "Brand team");
  assert.equal(saved.savedViews[0].shared, true, "the share state travels");
  const stored = JSON.parse(storage.getItem("bb-inbox-saved-views-v1"));
  assert.equal(stored.length, 1, "the view is in the operator's browser store only");
  assert.match(saved.html, /data-saved-view/, "the views menu lists the saved view");
  assert.match(saved.html, /Brand team/, "the menu shows the name");
  assert.match(saved.html, /Saved views/, "the menu labels the group");
  // An unnamed view is refused — a view needs a name.
  const unnamed = await organ.saveFilterView({name: "   "});
  assert.equal(unnamed.savedViews.length, 1, "a blank name saves nothing");
});

test("applying a saved view restores its conditions and match mode", async () => {
  const organ = makeOrgan();
  await organ.ready();
  await organ.setFilterConditions([{field: "tag", op: "is", values: ["vip"]}], {match: "any"});
  const saved = await organ.saveFilterView({name: "Vip"});
  const id = saved.savedViews[0].id;
  await organ.clearFilters();
  assert.equal(organ.snapshot().filterConditions.length, 0);
  const applied = await organ.applyFilterView(id);
  assert.deepEqual(applied.filterConditions, [{field: "tag", op: "is", values: ["vip"]}],
    "applying the view restores its conditions");
  assert.equal(applied.filterMatch, "any", "applying the view restores the match mode");
  assert.match(applied.html, /data-ticket="gorgias:1"/);
  assert.doesNotMatch(applied.html, /data-ticket="gorgias:2"/);
});

test("deleting a saved view removes the entry but never tickets", async () => {
  const organ = makeOrgan();
  await organ.ready();
  await organ.setFilterConditions([{field: "channel", op: "is", values: ["email"]}]);
  const saved = await organ.saveFilterView({name: "Email only"});
  const id = saved.savedViews[0].id;
  const after = await organ.deleteFilterView(id);
  assert.equal(after.savedViews.length, 0, "the saved view is gone");
  assert.doesNotMatch(after.html, /Email only/, "the menu entry is gone");
  // Deleting a view never deletes tickets: every row the surviving filter
  // admits still renders, and clearing the filter restores every row.
  assert.match(after.html, /data-ticket="gorgias:1"/);
  assert.match(after.html, /data-ticket="gorgias:3"/);
  assert.deepEqual(organ.snapshot().filterConditions, [{field: "channel", op: "is", values: ["email"]}],
    "the applied conditions survive deleting the view that inspired them");
  const cleared = await organ.clearFilters();
  for (const id2 of [1, 2, 3]) {
    assert.match(cleared.html, new RegExp(`data-ticket="gorgias:${id2}"`), "deleting a view never deletes tickets");
  }
});

test("the list tissue publishes filter changes from its controls", async () => {
  const mailbox = createMailbox();
  const seen = [];
  for (const topic of ["list/filter-changed", "list/filter-view-save", "list/filter-view-apply", "list/filter-view-delete"]) {
    mailbox.subscribe(topic, (msg) => seen.push({topic, msg}));
  }
  const {createListTissue} = await import("../js/tissues/list.js");
  const tissue = createListTissue({ mailbox });
  const live = {};
  const host = {
    innerHTML: "",
    set onchange(h) { this._change = h; },
    get onchange() { return this._change; },
    set onclick(h) { this._click = h; },
    get onclick() { return this._click; },
    set oninput(h) { this._input = h; },
    get oninput() { return this._input; },
    querySelector() { return null; },
  };
  const base = {
    tickets: [], views: [], counts: {}, selectedViewId: "all", searchQuery: "",
    filterConditions: [{field: "status", op: "is", values: ["open"]}],
    filterMatch: "all",
    filterFields: [{id: "status", label: "Status", ops: ["is", "isNot"], values: [{id: "open", label: "open", count: 1}]}],
    savedViews: [{id: "v1", name: "Vip", shared: false}],
  };
  tissue.mount(host);
  tissue.update(base);
  // The organ repaints the list tissue after every committed filter edit;
  // this harness mimics that by feeding each published edit back, so
  // consecutive edits build on each other like they do in the real pane.
  const repaint = () => tissue.update({...base,
    filterConditions: seen.at(-1).msg.conditions,
    filterMatch: seen.at(-1).msg.match,
  });
  // Match mode change.
  host.onchange?.({target: {closest: (sel) => sel === "[data-filter-match]" ? {value: "any"} : null}});
  assert.deepEqual(seen.at(-1), {topic: "list/filter-changed", msg: {conditions: [{field: "status", op: "is", values: ["open"]}], match: "any"}});
  repaint();
  // Field switch resets the row's values and operator.
  host.onchange?.({target: {closest: (sel) => sel === "[data-filter-field]" ? {value: "channel", dataset: {filterField: "0"}} : null}});
  assert.deepEqual(seen.at(-1).msg.conditions, [{field: "channel", op: "is", values: []}], "a field switch resets the row");
  repaint();
  // A value pick appends to the row.
  host.onchange?.({target: {closest: (sel) => sel === "[data-filter-value]" ? {value: "open", dataset: {filterValue: "0"}} : null}});
  assert.deepEqual(seen.at(-1).msg.conditions, [{field: "channel", op: "is", values: ["open"]}], "a value pick appends");
  repaint();
  // A date condition carries its edge.
  host.onchange?.({target: {closest: (sel) => sel === "[data-filter-date]" ? {value: "2026-09-02", dataset: {filterDate: "0"}} : null}});
  assert.deepEqual(seen.at(-1).msg.conditions, [{field: "channel", op: "is", values: ["2026-09-02"]}], "the date input writes the row's value");
  repaint();
  host.onclick?.({target: {closest: (sel) => sel === "[data-add-condition]" ? {} : null}});
  assert.equal(seen.at(-1).msg.conditions.length, 2, "Add condition appends a row");
  repaint();
  host.onclick?.({target: {closest: (sel) => sel === "[data-filter-remove]" ? {dataset: {filterRemove: "0"}} : null}});
  assert.equal(seen.at(-1).msg.conditions.length, 1, "a row removal drops that condition");
  repaint();
  host.onclick?.({target: {closest: (sel) => sel === "[data-filter-clear]" ? {} : null}});
  assert.deepEqual(seen.at(-1).msg.conditions, [], "Clear all empties the conditions");
  host.onclick?.({target: {closest: (sel) => sel === "[data-view-save]" ? {} : null}});
  host.oninput?.({target: {closest: (sel) => sel === "[data-view-name-input]" ? {value: "Lane"} : null}});
  host.onclick?.({target: {closest: (sel) => sel === "[data-view-save-confirm]" ? {} : null}});
  assert.equal(seen.at(-1).topic, "list/filter-view-save", "Confirm in the name editor publishes the save");
  host.onclick?.({target: {closest: (sel) => sel === "[data-saved-view]" ? {dataset: {savedView: "v1"}} : null}});
  assert.deepEqual(seen.at(-1).msg, {id: "v1"}, "applying a saved view publishes its id");
  host.onclick?.({target: {closest: (sel) => sel === "[data-view-delete]" ? {dataset: {viewDelete: "v1"}} : null}});
  assert.deepEqual(seen.at(-1).msg, {id: "v1"}, "deleting a saved view publishes its id");
});

test("a builder edit from the empty default row persists", async () => {
  // The browser caught this: with zero committed conditions the builder
  // renders one fresh default row; the change handler mapped over the empty
  // committed list, so the operator's first edit published [] and vanished.
  const mailbox = createMailbox();
  const seen = [];
  mailbox.subscribe("list/filter-changed", (msg) => seen.push(msg));
  const {createListTissue} = await import("../js/tissues/list.js");
  const tissue = createListTissue({ mailbox });
  const host = {
    innerHTML: "",
    set onchange(h) { this._change = h; },
    get onchange() { return this._change; },
    set onclick(h) { this._click = h; },
    get onclick() { return this._click; },
    querySelector() { return null; },
  };
  const fields = [{id: "status", label: "Status", ops: ["is", "isNot"], values: [{id: "open", label: "open"}]},
    {id: "channel", label: "Channel", ops: ["is", "isNot"], values: [{id: "chat", label: "chat"}]}];
  tissue.mount(host);
  tissue.update({tickets: [], views: [], counts: {}, selectedViewId: "all", searchQuery: "",
    filterConditions: [], filterMatch: "all", filterFields: fields, savedViews: []});
  // The builder shows one defaulted status row; the operator switches it to channel.
  host.onchange?.({target: {closest: (sel) => sel === "[data-filter-field]" ? {value: "channel", dataset: {filterField: "0"}} : null}});
  assert.deepEqual(seen.at(-1).conditions, [{field: "channel", op: "is", values: []}],
    "the first edit from the default row survives");
  // The organ repaints; the operator picks a value on the persisted row.
  tissue.update({tickets: [], views: [], counts: {}, selectedViewId: "all", searchQuery: "",
    filterConditions: seen.at(-1).conditions, filterMatch: "all", filterFields: fields, savedViews: []});
  host.onchange?.({target: {closest: (sel) => sel === "[data-filter-value]" ? {value: "chat", dataset: {filterValue: "0"}} : null}});
  assert.deepEqual(seen.at(-1).conditions, [{field: "channel", op: "is", values: ["chat"]}],
    "the value pick lands on the persisted row");
});

test("saving a view from the menu names it inline before publishing", async () => {
  // The browser caught this: the Save button published FILTER_VIEW_SAVE with
  // no name and nothing collected one, so the button could never save.
  const mailbox = createMailbox();
  const seen = [];
  mailbox.subscribe("list/filter-view-save", (msg) => seen.push(msg));
  const {createListTissue} = await import("../js/tissues/list.js");
  const tissue = createListTissue({ mailbox });
  const host = {
    innerHTML: "",
    set onclick(h) { this._click = h; },
    get onclick() { return this._click; },
    querySelector() { return null; },
  };
  const base = {tickets: [], views: [], counts: {}, selectedViewId: "all", searchQuery: "",
    channels: [{id: "email", label: "email", count: 1}],
    filterConditions: [{field: "channel", op: "is", values: ["email"]}], filterMatch: "all",
    filterFields: [{id: "channel", label: "Channel", ops: ["is"], values: []}],
    savedViews: []};
  tissue.mount(host);
  tissue.update(base);
  // Click Save: the menu swaps in an inline name input, saving nothing yet.
  host.onclick?.({target: {closest: (sel) => sel === "[data-view-save]" ? {} : null}});
  tissue.update(base);
  assert.equal(seen.length, 0, "Save alone publishes nothing — the view needs a name first");
  assert.match(host.innerHTML, /data-view-name-input/, "a name input opens");
  // Typing drafts locally; Confirm publishes the named save.
  host.oninput?.({target: {closest: (sel) => sel === "[data-view-name-input]" ? {value: "Email lane"} : null}});
  host.onclick?.({target: {closest: (sel) => sel === "[data-view-save-confirm]" ? {} : null}});
  assert.deepEqual(seen.at(-1), {name: "Email lane", shared: false},
    "Confirm publishes the typed name and the private default");
  // Cancel closes the editor without saving.
  host.onclick?.({target: {closest: (sel) => sel === "[data-view-save-cancel]" ? {} : null}});
  tissue.update(base);
  assert.doesNotMatch(host.innerHTML, /data-view-name-input/, "Cancel closes the name editor");
});

test("the views-menu click applies a saved view through the mailbox", async () => {
  // The browser caught this: the FILTER_VIEW_APPLY subscription called
  // applyFilterView(), which exists only as a method on the returned organ —
  // inside mount() it was an unbound name, so the menu click never applied.
  const organ = makeOrgan();
  await organ.ready();
  await organ.setFilterConditions([{field: "channel", op: "is", values: ["email"]}]);
  const saved = await organ.saveFilterView({name: "Email only"});
  const id = saved.savedViews[0].id;
  await organ.clearFilters();
  assert.equal(organ.snapshot().filterConditions.length, 0);
  // Mount like boot does — the subscriptions the menu click relies on are
  // wired in mount(), not in the constructor.
  const root = {innerHTML: "", querySelector() { return {innerHTML: "", querySelector() { return null; }}; }};
  await organ.mount(root);
  // The tissue click publishes through the mailbox; the organ must apply.
  organ.mailbox.publish(MAILBOX_TOPICS.FILTER_VIEW_APPLY, {id});
  assert.deepEqual(organ.snapshot().filterConditions, [{field: "channel", op: "is", values: ["email"]}],
    "publishing apply through the mailbox restores the view's conditions");
});
