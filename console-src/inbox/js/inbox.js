import { MAILBOX_TOPICS, MARKETING_LOCKED_COPY, PAYMENTS_LOCKED_COPY, PRIVACY_LOCKED_COPY, CUSTOMER_JOIN_LOCKED_COPY, ORDER_LINK_LOCKED_COPY } from "./contracts.js";
import { ACTIVATE_SEND_MESSAGE } from "./send-access.js";
import { ticketInView, viewCounts, views } from "./view-model.js";
// A refresh can atomically publish a new snapshot between pages. Retry once,
// starting from zero; never combine generations or retry unrelated API errors.
export async function readObservedTickets(shop, count = 100) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const rows = [];
    let generation;
    let changed = false;
    for (let offset = 0; offset < count; offset += 100) {
      const page = await shop.listTickets({view: "all", limit: 100, offset});
      const nextGeneration = shop.projection?.generatedAt;
      if (offset && generation !== nextGeneration) { changed = true; break; }
      generation = nextGeneration;
      rows.push(...page);
      if (page.length < 100 || rows.length >= shop.projection?.ticketCount) break;
    }
    if (!changed) return rows;
  }
  throw new Error("Projection refreshed repeatedly during pagination.");
}
const SHOP = "";
const fixtureTickets = [];
const fixtureMacros = [];
import { createMailbox } from "./mailbox.js";
import { createHelpdeskShop } from "./shop/production-shop.js";
import { createComposerTissue } from "./tissues/composer.js";
import { createListTissue } from "./tissues/list.js";
import { createRailOrgan } from "./tissues/rail.js";
import { projectReturns } from "./tissues/returns.js";
import { createThreadTissue } from "./tissues/thread.js";
import { esc, formatWhen, forbiddenControlHits, GATE_CONFIRM_LABEL } from "./util.js";

const RAIL_EXPAND_ICON = `<svg class="list-expand-icon" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" focusable="false">
  <path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" d="M9 2.5 4.5 7 9 11.5"/>
</svg>`;

function withRecipient(ticket, email) {
  if (!ticket) return null;
  return {
    ...ticket,
    toEmail: email || "",
  };
}

function safeMount(tissue, el, input) {
  try {
    if (input) tissue.update?.(input);
    tissue.mount(el);
    return { ok: true };
  } catch (err) {
    el.innerHTML = `<div class="tissue-error" data-tissue-error="${tissue.id}">${tissue.id} unavailable</div>`;
    return { ok: false, error: String(err?.message || err) };
  }
}

/**
 * Inbox organ: list + thread + rail + composer.
 * Views live in the list filter menu (no separate views pane).
 * One tissue error stays in its pane.
 */
export function createInboxOrgan(opts = {}) {
  const mailbox = opts.mailbox || createMailbox();
  const shop = opts.shop || createHelpdeskShop({ fail: opts.fail });
  // #42: the URL is the operator's address bar, so boot owns history and
  // injects it like the #39 downloads adapter. The organ calls syncUrl on
  // every selection/view change; back/forward arrives as selectTicket with
  // {fromHistory} so it replaces instead of pushing.
  const history = opts.history || null;
  // #42: the SPA mount path — /inbox/ in production, whatever the review
  // server serves under. Boot reports it; the copy link derives from it so
  // the copied deep link resolves wherever the inbox is mounted.
  const ticketPath = opts.ticketPath || "/inbox/";
  let urlSuspended = false;
  function syncUrl({push = true} = {}) {
    if (!history || urlSuspended) return;
    (push ? history.push : history.replace)?.({ticket: selectedId, view: viewId, q: searchQuery, f: encodeFilters()});
  }
  // #42: back/forward replays both URL fields without pushing — the organ
  // owns the view, so a stale view must never be written back over the
  // popped entry. A popped entry with no ticket falls back to the first
  // visible row (the thread pane must not hang empty) and the replace
  // restamps the URL with what actually renders, so the bar and the UI
  // agree.
  async function replayEntry({ticket, view, q, f} = {}) {
    const generation = ++replayGeneration;
    const stale = () => generation !== replayGeneration;
    const nextView = availableViews.some((candidate) => candidate.id === view) ? view : "all";
    const changedView = nextView !== viewId;
    viewId = nextView;
    // A replayed entry owns the whole URL, so dead facet filters must not
    // survive into the restored view — selectView clears them and so does
    // the back button, or the popped ticket could be filtered out of its
    // own restored rows.
    filterConditions = [];
    filterMatch = "all";
    // A popped entry owns its query too: no q in the URL means no search,
    // exactly like a dead facet. A popped q restores the exact search. The
    // URL never encodes the escalation, so every replay de-escalates back
    // to the scoped search.
    setSearchQuery(q);
    searchAllViews = false;
    // A popped entry owns its filters like its query: no f means none, a
    // carried f restores the exact rows and match mode.
    const filterState = parseFilterSeed(f);
    filterConditions = normalizeFilterConditions(filterState.c);
    filterMatch = filterState.m;
    selectedId = ticket || null;
    selected = null;
    resetUiState(ticket || null);
    // The popped entry's ticket is protected exactly like a boot deep
    // link: an id outside the rows stays put, whatever renders resolves.
    protectedTicketId = ticket || null;
    if (changedView) await refreshList();
    if (stale()) return afterUi();
    ensureSelection();
    syncUrl({push: false});
    await refreshThread();
    if (stale()) return afterUi();
    await refreshRail();
    if (stale()) return afterUi();
    await refreshComposer();
    return afterUi();
  }
  // #42: the deep link the badge/Copy-link control hands out: relative,
  // same-origin, carrying the active view so the bookmark restores the
  // partition as well as the ticket.
  function ticketLink(ticketId) {
    if (!ticketId) return "";
    return `${ticketPath}?view=${encodeURIComponent(viewId)}&ticket=${encodeURIComponent(ticketId)}`;
  }
  // #34: read state is the operator's browser state, persisted so a reload
  // keeps the distinction. The observed path's projection server is
  // read-only, so the read set lives here (localStorage in production, an
  // injectable shim for tests) — never in the webhook snapshot.
  const READ_KEY = "bb-inbox-read-v1";
  // Merely referencing localStorage throws in browsers that block it, so
  // resolve it inside try/catch; the inbox degrades to session-local.
  const storage = opts.storage || (() => {
    try {
      return typeof localStorage !== "undefined" ? localStorage : null;
    } catch {
      return null;
    }
  })();
  function loadReadIds() {
    try {
      const raw = JSON.parse(storage?.getItem?.(READ_KEY) || "null");
      return new Set(Array.isArray(raw) ? raw.filter((id) => typeof id === "string") : []);
    } catch {
      return new Set();
    }
  }
  let readIds = loadReadIds();
  function persistRead(removed = []) {
    try {
      // Merge with the stored set first: a second tab may have marked other
      // tickets read since this organ loaded, and its reads must survive.
      // Removals (mark unread) win over the merge so they stick.
      const stored = loadReadIds();
      const removedSet = new Set(removed);
      for (const id of stored) if (!removedSet.has(id)) readIds.add(id);
      storage?.setItem?.(READ_KEY, JSON.stringify([...readIds]));
    } catch {
      /* private-mode storage quota is not an inbox error */
    }
  }
  // #41: the operator's own ticket titles are first-party state like the
  // read set — a browser store, never a Gorgias write. A rename here never
  // leaves the operator's browser.
  const TITLE_KEY = "bb-inbox-titles-v1";
  function loadTitles() {
    try {
      const raw = JSON.parse(storage?.getItem?.(TITLE_KEY) || "null");
      return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    } catch {
      return {};
    }
  }
  let titles = loadTitles();
  // #41: ids this organ has renamed or cleared since load. Key presence in
  // `titles` is not local ownership (it also holds load-time copies), so
  // persistence is anchored to this set instead. Untouched keys follow
  // storage, so a stale tab's persist never clobbers another tab's rename
  // and never resurrects another tab's clear.
  const localTitleIds = new Set();
  // Fallback titles get the same guard as typed renames: collapsed
  // whitespace, trimmed, capped at 120 — the derived line stays one row.
  function screenTitle(raw) {
    return String(raw ?? "").replace(/\s+/g, " ").trim().slice(0, 120);
  }
  function derivedTitle(ticket) {
    if (!ticket) return "New ticket";
    const record = titles[ticket.id];
    // #41: records carry {title, by, at}; a bare string is a pre-#41 entry.
    const stored = typeof record === "string" ? record : record?.title;
    if (typeof stored === "string" && stored.trim()) return screenTitle(stored);
    const orderName = screenTitle(ticket.shopifyRail?.order?.name);
    if (orderName) return orderName;
    const subject = screenTitle(ticket.subject);
    if (subject && subject.toLowerCase() !== "no subject") return subject;
    return "New ticket";
  }
  function persistTitles() {
    try {
      // Two-tab merge: adopt storage for every key this organ has not
      // touched, then apply this organ's own renames (present in `titles`)
      // and clears (touched but absent) on top.
      const merged = {...loadTitles()};
      for (const id of localTitleIds) {
        if (id in titles) merged[id] = titles[id];
        else delete merged[id];
      }
      titles = merged;
      storage?.setItem?.(TITLE_KEY, JSON.stringify(titles));
    } catch {
      /* private-mode storage quota is not an inbox error */
    }
  }
  // #41: who/when travel with the title — the store is auditable without a
  // server. The observed operator email is recorded, never invented.
  function writeTitle(ticketId, raw) {
    const title = screenTitle(raw);
    localTitleIds.add(ticketId);
    if (!title) {
      // Clearing the rename deletes the record; the derived fallback shows.
      delete titles[ticketId];
      persistTitles();
      return;
    }
    titles[ticketId] = {
      title,
      by: String(opts.operatorEmail ?? shop.operatorEmail ?? "operator").trim().toLowerCase() || "operator",
      at: Date.now(),
    };
    persistTitles();
  }
  // #44: the operator's ticket-detail overrides — status, priority, assignee
  // — are first-party state like titles and the read set: a browser store,
  // never a Gorgias write. The observed values stay rendered beside any
  // override; clearing an override falls back to what Gorgias reported.
  const STATE_KEY = "bb-inbox-ticket-state-v1";
  const STATE_FIELDS = ["status", "priority", "assignee"];
  function loadTicketState() {
    try {
      const raw = JSON.parse(storage?.getItem?.(STATE_KEY) || "null");
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
      // null records are deletions this tab owns; keep them so the merge
      // below preserves the deletion instead of resurrecting stale values.
      return raw;
    } catch {
      return {};
    }
  }
  let ticketState = loadTicketState();
  // #41-style ownership anchor, field-level: a tab owns only the fields it
  // changed, so two tabs editing different fields never erase each other.
  // A cleared field persists as null inside the record (a field tombstone)
  // so the deletion itself survives the merge.
  const ownedStateFields = new Map();
  function persistTicketState() {
    try {
      const merged = {...loadTicketState()};
      for (const [id, fields] of ownedStateFields) {
        const record = {...(merged[id] && typeof merged[id] === "object" ? merged[id] : {})};
        for (const field of fields) {
          const value = ticketState[id]?.[field];
          if (value) record[field] = value;
          else record[field] = null; // this tab's field deletion
        }
        merged[id] = Object.keys(record).length ? record : null;
      }
      ticketState = merged;
      storage?.setItem?.(STATE_KEY, JSON.stringify(ticketState));
    } catch {
      /* private-mode storage quota is not an inbox error */
    }
  }
  // Who/when travel with each change — the store is auditable without a
  // server. The observed operator email is recorded, never invented.
  function operatorStamp() {
    return String(opts.operatorEmail ?? shop.operatorEmail ?? "operator").trim().toLowerCase() || "operator";
  }
  function setTicketStateLocal(ticketId, changes = {}) {
    const fields = {};
    for (const field of STATE_FIELDS) {
      if (field in changes) fields[field] = changes[field];
    }
    if (!Object.keys(fields).length) return;
    const record = ticketState[ticketId] && ticketState[ticketId] !== null && typeof ticketState[ticketId] === "object" ? {...ticketState[ticketId]} : {};
    const stamp = {by: operatorStamp(), at: Date.now()};
    for (const [field, value] of Object.entries(fields)) {
      // Values come from the picker's own option lists or the operator's
      // address — trim, never truncate (a sliced address breaks the Me pick).
      const clean = typeof value === "string" ? value.trim() : null;
      if (clean) record[field] = {value: clean, ...stamp};
      else delete record[field];
      // Field-level ownership: this tab now owns the field, value or clear.
      if (!ownedStateFields.has(ticketId)) ownedStateFields.set(ticketId, new Set());
      ownedStateFields.get(ticketId).add(field);
    }
    ticketState[ticketId] = Object.keys(record).length ? record : null;
    persistTicketState();
  }
  // #44: the closure bodies behind the organ methods — subscriptions live
  // in mount() where organ methods are unreachable by bare name.
  function stepTicketSelection(delta) {
    const order = sortedVisibleTickets().map((ticket) => ticket.id);
    const index = order.indexOf(selectedId);
    if (index < 0 || !delta) return Promise.resolve();
    const next = order[Math.max(0, Math.min(order.length - 1, index + (delta > 0 ? 1 : -1)))];
    if (next === selectedId) return Promise.resolve();
    protectedTicketId = null;
    resetUiState(next);
    markRead(next);
    syncUrl({push: true});
    return refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery));
  }
  async function applyTicketState() {
    await refreshList();
    ensureSelection();
    await refreshThread();
    await refreshRail();
    await refreshComposer();
  }

  // #38: create-flow closure bodies — both the mailbox subscriptions and
  // the organ methods route through these (subscriptions cannot reach organ
  // methods by bare name).
  function openCreateSheetLocal() {
    createSheetOpen = true;
    createError = "";
    return afterUi();
  }
  function closeCreateSheetLocal() {
    createSheetOpen = false;
    createError = "";
    // Closing abandons the draft — the operator chose not to file it.
    createDraft = {customerName: "", fromEmail: "", subject: "", channel: "email", body: ""};
    return afterUi();
  }
  // The form submit handler hands the raw field values here; validation and
  // the capability gate both refuse with createError instead of throwing,
  // so the sheet stays open and the operator can fix the fields.
  async function createLocalTicketLocal(fields = {}) {
    if (capabilities.createTicket === false) {
      createError = "Ticket creation is not enabled.";
      return afterUi();
    }
    const error = validateCreateForm(fields);
    if (error) {
      createError = error;
      // The draft keeps the typed fields across the error repaint.
      createDraft = {...createDraft, ...fields};
      return afterUi();
    }
    const record = addLocalTicket(fields);
    createSheetOpen = false;
    createError = "";
    createDraft = {customerName: "", fromEmail: "", subject: "", channel: "email", body: ""};
    // The operator just created this ticket — it is a real row now.
    protectedTicketId = null;
    resetUiState(record.id);
    markRead(record.id);
    syncUrl({push: true});
    await refreshList();
    ensureSelection();
    await refreshThread();
    await refreshRail();
    await refreshComposer();
    await refreshMacros(macroQuery);
    return afterUi();
  }

  // The observed value for a field, before any local override. The
  // Gorgias priority lives under gorgiasPriority (ticket.priority is the
  // draft's); the assignee address may sit in either assignee field.
  function observedState(ticket, field) {
    if (field === "status") return ticket.observedStatus ?? ticket.status ?? null;
    if (field === "priority") return ticket.gorgiasPriority ?? null;
    return ticket.assigneeEmail ?? ticket.assignee ?? null;
  }
  // The effective value for a field: the local override, else the observed
  // one. View filters read through this so a locally-closed ticket is
  // honestly in the Closed view; the thread badge keeps the observed value.
  function effectiveState(ticket, field) {
    const override = ticketState[ticket.id]?.[field]?.value;
    if (typeof override === "string" && override.trim()) return override.trim();
    const observed = observedState(ticket, field);
    return typeof observed === "string" && observed.trim() ? observed.trim() : null;
  }

  // Local assignee picks speak the observed "me"/"other" dialect: the picker
  // stores the operator's address, but rows and views compare against "me".
  function normalizedAssignee(raw) {
    const mine = String(opts.operatorEmail ?? shop.operatorEmail ?? "").trim().toLowerCase();
    const value = String(raw ?? "").trim();
    // "unassigned" is the picker's reserved id — rows speak null, matching
    // the observed-assignee dialect the view predicates already use.
    if (!value || value === "unassigned") return null;
    if (value === mine && mine) return "me";
    return value;
  }
  // #38: the operator's own local-only tickets — model 1 in the issue,
  // recorded in AGENTS.md §2(7). The record mirrors agent-side intake's
  // message shape ({from, fromName, fromEmail, body, at}) so the thread
  // renders one customer bubble. First-party browser state like titles and
  // the read set: never a Gorgias write, never a customer notification.
  const LOCAL_TICKETS_KEY = "bb-inbox-local-tickets-v1";
  function screenCreateText(raw, cap = 2000) {
    return String(raw ?? "").replace(/\s+/g, " ").trim().slice(0, cap);
  }
  function loadLocalTickets() {
    try {
      const raw = JSON.parse(storage?.getItem?.(LOCAL_TICKETS_KEY) || "null");
      if (!Array.isArray(raw)) return [];
      return raw.filter((row) => row && typeof row.id === "string"
        && typeof row.customerName === "string");
    } catch {
      return [];
    }
  }
  let localTickets = loadLocalTickets();
  // ponytail: append-only union merge — two tabs each adding tickets keeps
  // both. There is no edit/delete surface for local tickets yet, so id
  // adoption is the whole merge story.
  function persistLocalTickets() {
    try {
      const merged = [...localTickets];
      const seen = new Set(merged.map((row) => row.id));
      for (const row of loadLocalTickets()) if (!seen.has(row.id)) merged.push(row);
      localTickets = merged;
      storage?.setItem?.(LOCAL_TICKETS_KEY, JSON.stringify(localTickets));
    } catch {
      /* private-mode storage quota is not an inbox error */
    }
  }
  // A local ticket as a list row: the intake shape, plus the row fields the
  // tissues already read (snippet/updatedAt/status/assignee/channel).
  function localTicketRow(record) {
    return {
      id: record.id,
      customerName: record.customerName,
      fromEmail: record.fromEmail,
      subject: record.subject,
      snippet: screenCreateText(record.body, 120),
      body: record.body,
      channel: record.channel,
      status: "open",
      assignee: null,
      updatedAt: record.createdAt,
      messages: [{
        from: "customer",
        fromName: record.customerName,
        fromEmail: record.fromEmail,
        body: record.body,
        at: record.createdAt,
      }],
      statusEvents: [],
      localOnly: true,
    };
  }
  // The store's records are the source of truth; rows derive from them.
  function localTicketRows() {
    return localTickets.map(localTicketRow);
  }
  function validateCreateForm({customerName, fromEmail, subject, body, channel} = {}) {
    if (!screenCreateText(customerName, 120)) return "Customer name is required.";
    if (!screenCreateText(body, 2000)) return "Message is required.";
    const email = String(fromEmail ?? "").trim();
    if (email && !/^[^\s@]+@[^\s@]+$/.test(email)) return "Enter a valid email address, or leave it blank.";
    if (channel && !["email", "chat", "phone", "whatsapp"].includes(String(channel).trim().toLowerCase())) {
      return "Pick a supported channel.";
    }
    return "";
  }
  function addLocalTicket({customerName, fromEmail, subject, body, channel} = {}) {
    const now = new Date().toISOString();
    const record = {
      id: `local:${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      customerName: screenCreateText(customerName, 120),
      fromEmail: String(fromEmail ?? "").trim().toLowerCase() || null,
      // Subject may auto-title from the body when blank, like intake's
      // chat path derives one — the field is optional.
      subject: screenCreateText(subject, 120) || screenCreateText(body, 60),
      body: String(body ?? "").trim().slice(0, 2000),
      channel: ["email", "chat", "phone", "whatsapp"].includes(String(channel).trim().toLowerCase())
        ? String(channel).trim().toLowerCase() : "email",
      createdAt: now,
      by: String(opts.operatorEmail ?? shop.operatorEmail ?? "operator").trim().toLowerCase() || "operator",
    };
    localTickets.push(record);
    persistLocalTickets();
    return record;
  }
  // #44: rows entering the organ carry the operator's local overrides on
  // top of the observed values, so every view/filter/count that reads
  // status or assignee sees the effective value without per-site patches.
  // The observed values ride along as observedStatus/observedAssignee so
  // the header badge can keep naming what Gorgias reported.
  function applyLocalState(ticket) {
    if (!ticket) return ticket;
    const status = effectiveState(ticket, "status");
    const priority = effectiveState(ticket, "priority");
    const assignee = ticketState[ticket.id]?.assignee
      ? normalizedAssignee(ticketState[ticket.id].assignee.value)
      : withOperatorAssignee(ticket).assignee;
    const local = stateOverrides(ticket);
    return {
      ...ticket,
      observedStatus: ticket.status ?? null,
      observedAssignee: ticket.assigneeEmail ?? ticket.assignee ?? null,
      status: status || ticket.status,
      priority: priority || ticket.priority,
      assignee,
      localOverrides: Object.keys(local).length ? local : undefined,
    };
  }
  function stateOverrides(ticket) {
    // Which fields the operator overrode locally — the header badges them.
    const fields = {};
    for (const field of STATE_FIELDS) {
      if (ticketState[ticket.id]?.[field]) fields[field] = true;
    }
    return fields;
  }
  let capabilities = { ...(shop.capabilities || {}) };
  let parentRewrite = null;
  let rewriteBusy = false;
  let rewriteError = "";
  let lastRewriteInstruction = "";
  let parentNote = null;
  let parentSend = null;
  let noteConfirm = false;
  let noteBusy = false;
  let noteError = "";
  let noteInfo = "";
  const shopHost = opts.shopHost || shop.shop || SHOP;
  const pinnedCatalog = opts.tickets || null;
  const listTissue = createListTissue({ mailbox });
  const threadTissue = createThreadTissue({ mailbox });
  const composerTissue = createComposerTissue({ mailbox });
  const rail = createRailOrgan({ shop, mailbox });

  let viewId = opts.viewId || (shop.observedHistory ? "all" : "mine");
  // #39: multi-select is operator-browser state. Selection lives in the
  // visible order so shift-click can span ranges; view/filter changes clear
  // it because ids may no longer be visible or in the same domain.
  const downloads = opts.downloads || null;
  let bulkIds = new Set();
  let bulkAnchorId = null;
  let bulkEscalated = [];
  let bulkError = "";
  // #39 review: sort lives in the organ so the rendered order (what the
  // operator shift-clicks across) and the selection range use one source of
  // truth. The tissue renders the rows in the order it receives them.
  let sortId = "default";
  function sortedVisibleTickets() {
    const rows = visibleTickets();
    if (sortId === "newest" || sortId === "oldest") {
      rows.sort((a, b) => {
        const left = Date.parse(a.updatedAt || 0) || 0;
        const right = Date.parse(b.updatedAt || 0) || 0;
        return sortId === "oldest" ? left - right : right - left;
      });
    }
    return rows;
  }
  function reconcileBulk() {
    // #39 review: a refresh can drop a selected row from the visible set; a
    // stale id must never survive into bulk actions.
    const visible = new Set(visibleIds());
    for (const id of [...bulkIds]) if (!visible.has(id)) bulkIds.delete(id);
    if (bulkAnchorId && !visible.has(bulkAnchorId)) bulkAnchorId = null;
  }
  function clearBulk() {
    bulkIds = new Set();
    bulkAnchorId = null;
    bulkEscalated = [];
    bulkError = "";
  }
  function visibleIds() {
    return sortedVisibleTickets().map((ticket) => ticket.id);
  }
  function toggleSelect(ticketId, {shiftKey = false} = {}) {
    if (!ticketId) return afterUi();
    const ids = visibleIds();
    if (!ids.includes(ticketId)) return afterUi();
    if (shiftKey && bulkAnchorId && ids.includes(bulkAnchorId)) {
      const from = ids.indexOf(bulkAnchorId);
      const to = ids.indexOf(ticketId);
      for (const id of ids.slice(Math.min(from, to), Math.max(from, to) + 1)) bulkIds.add(id);
    } else {
      if (bulkIds.has(ticketId)) bulkIds.delete(ticketId);
      else bulkIds.add(ticketId);
      bulkAnchorId = ticketId;
    }
    bulkError = "";
    return afterUi();
  }
  function clearSelection() {
    clearBulk();
    return afterUi();
  }
  function bulkMarkRead() {
    for (const id of bulkIds) markRead(id);
    return afterUi();
  }
  function bulkMarkUnread() {
    const removed = [...bulkIds];
    for (const id of removed) {
      unreadIds.add(id);
      readIds.delete(id);
    }
    persistRead(removed);
    return afterUi();
  }
  function csvCell(value) {
    let text = String(value ?? "").replace(/[\r\n]+/g, " ");
    // #39 review: formula-shaped cells get a leading apostrophe so
    // spreadsheets render them as text, never execute them.
    if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
    return /[",]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }
  function bulkExport() {
    if (!downloads || !bulkIds.size) return afterUi();
    const selected = listRows.filter((ticket) => bulkIds.has(ticket.id));
    const header = "id,subject,status,updatedAt,assignee,channel";
    const lines = selected.map((ticket) => [
      ticket.id,
      ticket.subject,
      ticket.status,
      ticket.updatedAt,
      // #39 review: export the observed address, not the synthetic "me".
      ticket.assigneeEmail ?? ticket.assignee ?? "",
      ticket.channel || "",
    ].map(csvCell).join(","));
    downloads.download(
      `inbox-tickets-${Date.now()}.csv`,
      [header, ...lines].join("\n"),
      "text/csv",
    );
    return afterUi();
  }
  function bulkEscalate() {
    if (!bulkIds.size) return afterUi();
    if (capabilities.escalateTicket === false || typeof shop.escalateTicket !== "function") {
      bulkError = "Bulk escalate is not available in this inbox.";
      return afterUi();
    }
    bulkEscalated = [];
    const targetIds = [...bulkIds];
    const unconfirmed = [];
    return (async () => {
      // #39 review: one ticket failing must not abort the batch or hide the
      // others' progress — attempt every ticket, report every unconfirmed id.
      for (const id of targetIds) {
        try {
          const result = await shop.escalateTicket({ticketId: id});
          if (result?.id === id && result.escalated) bulkEscalated.push(id);
          else unconfirmed.push(id);
        } catch {
          unconfirmed.push(id);
        }
      }
      if (unconfirmed.length) {
        bulkError = `Not escalated: ${unconfirmed.join(", ")}. Please try again.`;
      }
      return afterUi();
    })();
  }
  const availableViews = views;
  // #36: the facet ids are derived from the condition rows — one quick
  // pick per field maps to one `is` condition, so the legacy menu, the
  // chips and the builder can never disagree.
  function facetCondition(field) {
    return filterConditions.find((condition) => condition.field === field && condition.op === "is") || null;
  }
  const channelId = () => facetCondition("channel")?.values[0] || "";
  const statusId = () => facetCondition("status")?.values[0] || "";
  const assigneeId = () => facetCondition("assignee")?.values[0] || "";
  const tagId = () => facetCondition("tag")?.values[0] || "";
  function normalizeChannel(value) {
    return typeof value === "string" ? value.trim().slice(0, 40) : "";
  }
  function channelFacets() {
    const countsByChannel = new Map();
    for (const ticket of listRows) {
      const channel = normalizeChannel(ticket?.channel);
      if (!channel) continue;
      countsByChannel.set(channel, (countsByChannel.get(channel) || 0) + 1);
    }
    return [...countsByChannel.entries()]
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([id, count]) => ({ id, label: id, count }));
  }
  function normalizeStatus(value) {
    return typeof value === "string" ? value.trim().slice(0, 30) : "";
  }
  function statusFacets() {
    const countsByStatus = new Map();
    for (const ticket of listRows) {
      const status = normalizeStatus(ticket?.status);
      if (!status || status === "unknown") continue;
      countsByStatus.set(status, (countsByStatus.get(status) || 0) + 1);
    }
    return [...countsByStatus.entries()]
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([id, count]) => ({ id, label: id, count }));
  }
  function normalizeAssignee(value) {
    return typeof value === "string" ? value.trim().slice(0, 120) : "";
  }
  // A literal assignee of "unassigned" filters exactly like a blank one,
  // so the reserved id can never hide or misroute a real person.
  const UNASSIGNED_ID = "unassigned";
  function assigneeMatches(ticket, selected) {
    const assignee = normalizeAssignee(ticket?.assignee);
    if (!selected) return true;
    if (selected === UNASSIGNED_ID) return !assignee || assignee === UNASSIGNED_ID;
    return assignee === selected;
  }
  function assigneeFacets() {
    const countsByAssignee = new Map();
    let unassigned = 0;
    for (const ticket of listRows) {
      const assignee = normalizeAssignee(ticket?.assignee);
      if (!assignee || assignee === UNASSIGNED_ID) unassigned += 1;
      else countsByAssignee.set(assignee, (countsByAssignee.get(assignee) || 0) + 1);
    }
    const facets = [...countsByAssignee.entries()]
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([id, count]) => ({ id, label: id, count }));
    // Unassigned is only offered alongside named people; alone it would
    // duplicate Everyone and imply knowledge we do not have.
    if (unassigned && facets.length) facets.unshift({ id: UNASSIGNED_ID, label: "Unassigned", count: unassigned });
    return facets;
  }
  function normalizeTag(value) {
    return typeof value === "string" ? value.trim().slice(0, 40) : "";
  }
  function ticketTags(ticket) {
    return Array.isArray(ticket?.tags) ? ticket.tags.map(normalizeTag).filter(Boolean) : [];
  }
  function tagFacets() {
    const countsByTag = new Map();
    for (const ticket of listRows) {
      for (const tag of ticketTags(ticket)) {
        countsByTag.set(tag, (countsByTag.get(tag) || 0) + 1);
      }
    }
    return [...countsByTag.entries()]
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([id, count]) => ({ id, label: id, count }));
  }
  let selectedId = opts.ticketId || null;
  // #37: search stays first-party — the query is organ state, matched
  // client-side against the loaded snapshot rows (subject, customer name
  // and address, snippet). The bound is the URL's practical limit; an
  // over-limit paste never truncates into a false prefix match — it misses.
  let searchQuery = "";
  let searchOverLimit = false;
  let searchAllViews = false;
  function setSearchQuery(raw) {
    const text = typeof raw === "string" ? raw.trim() : "";
    // The bounded text stays in state (input, URL, count); the flag alone
    // decides matching, so an over-limit paste can never prefix-match.
    searchOverLimit = text.length > 200;
    searchQuery = text.slice(0, 200);
  }
  setSearchQuery(opts.searchQuery);
  function ticketMatchesSearch(ticket) {
    if (!searchQuery) return true;
    if (searchOverLimit) return false;
    const haystack = [ticket?.subject, ticket?.customerName, ticket?.fromEmail, ticket?.snippet]
      .map((part) => String(part ?? "").toLowerCase()).join(" \n");
    return haystack.includes(searchQuery.toLowerCase());
  }
  // #36: the Gorgias-style filter builder. Conditions are organ state over
  // the fields the observed summary rows actually carry, evaluated
  // client-side against the loaded snapshot — read-only, first-party, and
  // composable with the view partition and the search.
  const FILTER_FIELDS = Object.freeze({
    status: {
      label: "Status", ops: ["is", "isNot"],
      // "unknown" is the observed placeholder for a missing status, not a
      // filterable value — it never appears in the builder's offers.
      offers: () => statusFacets().map((entry) => ({id: entry.id, label: entry.label})),
      // cubic: offers are normalized, so the match normalizes too — a raw
      // row value with whitespace must match the facet-picked id.
      matches: (ticket, op, values) => filterValueSet(values, normalizeStatus(ticket?.status), op),
    },
    assignee: {
      label: "Assignee", ops: ["is", "isNot"],
      offers: () => assigneeFacets().map((entry) => ({id: entry.id, label: entry.label})),
      matches: (ticket, op, values) => {
        // The reserved unassigned id matches a blank assignee exactly like
        // the facet pick, so the builder and the quick menu agree.
        const raw = normalizeAssignee(ticket?.assignee);
        const hit = values.some((value) => value === "unassigned" ? !raw : value === raw);
        return op === "isNot" ? !hit : hit;
      },
    },
    tag: {
      label: "Tag", ops: ["is", "isNot"],
      offers: () => tagFacets().map((entry) => ({id: entry.id, label: entry.label})),
      matches: (ticket, op, values) => {
        const tags = ticketTags(ticket);
        const hit = values.some((value) => tags.includes(value));
        return op === "isNot" ? !hit : hit;
      },
    },
    channel: {
      label: "Channel", ops: ["is", "isNot"],
      offers: () => channelFacets().map((entry) => ({id: entry.id, label: entry.label})),
      matches: (ticket, op, values) => filterValueSet(values, normalizeChannel(ticket?.channel), op),
    },
    priority: {
      label: "Priority", ops: ["is", "isNot"],
      offers: () => {
        const counts = new Map();
        for (const ticket of listRows) {
          const priority = String(ticket?.gorgiasPriority ?? "").trim().slice(0, 20);
          if (priority) counts.set(priority, (counts.get(priority) || 0) + 1);
        }
        return [...counts.entries()].sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
          .map(([id, count]) => ({id, label: id, count}));
      },
    matches: (ticket, op, values) => filterValueSet(values, String(ticket?.gorgiasPriority ?? "").trim().slice(0, 20), op),
    },
    customer: {
      label: "Customer", ops: ["is", "isNot", "contains"],
      offers: () => [],
      matches: (ticket, op, values) => {
        // "is" is a whole-value match on the address or the observed name;
        // "contains" is the substring the builder offers for typing into.
        const name = String(ticket?.customerName ?? "").toLowerCase();
        const address = String(ticket?.fromEmail ?? "").toLowerCase();
        const hit = values.some((value) => {
          const needle = value.toLowerCase();
          if (op === "contains") return name.includes(needle) || address.includes(needle);
          return name === needle || address === needle;
        });
        return op === "isNot" ? !hit : hit;
      },
    },
    updated: {
      label: "Updated", ops: ["before", "after"],
      offers: () => [],
      matches: (ticket, op, values) => {
        const when = new Date(ticket?.updatedAt).getTime();
        if (!Number.isFinite(when)) return false;
        for (const value of values) {
          // The date input names a day, not an instant: "after" is that
          // day and later, "before" stops at its midnight.
          const edge = new Date(`${value}T00:00:00Z`).getTime();
          const dayEnd = edge + 86_400_000;
          if (!Number.isFinite(edge)) continue;
          if (op === "before" && when < edge) return true;
          if (op === "after" && when >= edge && when < dayEnd + 1) return true;
          if (op === "after" && when > dayEnd) return true;
        }
        return false;
      },
    },
  });
  function filterValueSet(values, raw, op) {
    const actual = String(raw ?? "");
    const hit = values.includes(actual);
    return op === "isNot" ? !hit : hit;
  }
  function normalizeFilterConditions(raw) {
    if (!Array.isArray(raw)) return [];
    const seen = new Set();
    return raw.slice(0, 8).flatMap((row) => {
      const field = typeof row?.field === "string" && row.field in FILTER_FIELDS ? row.field : null;
      if (!field) return [];
      // ponytail: values are clamped and deduped; an unknown op falls back
      // to the field's first op so a hand-edited URL never yields an
      // unmatchable condition ("is" on a date field matches nothing).
      const op = FILTER_FIELDS[field].ops.includes(row.op) ? row.op : FILTER_FIELDS[field].ops[0];
      const values = (Array.isArray(row.values) ? row.values : [row?.values])
        .filter((value) => typeof value === "string")
        .map((value) => value.trim().slice(0, 120))
        .filter(Boolean)
        .filter((value) => { const key = `${field}:${op}:${value}`; if (seen.has(key)) return false; seen.add(key); return true; });
      // A valueless row survives as a no-op — a half-built condition must
      // never blank the list, and the row the operator is typing into must
      // not vanish under them.
      return [{field, op, values}];
    });
  }
  function encodeFilters() {
    if (!filterConditions.length) return "";
    // ponytail: one compact JSON object in one URL param. Long enough for
    // real stacks, short enough for an address bar.
    return JSON.stringify({m: filterMatch, c: filterConditions});
  }
  function parseFilterSeed(raw) {
    try {
      const parsed = typeof raw === "string" && raw.trim() ? JSON.parse(raw) : null;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {m: "all", c: []};
      return {m: parsed.m === "any" ? "any" : "all", c: Array.isArray(parsed.c) ? parsed.c : []};
    } catch {
      return {m: "all", c: []};
    }
  }
  const filterSeed = parseFilterSeed(opts.filters);
  let filterMatch = filterSeed.m;
  let filterConditions = normalizeFilterConditions(filterSeed.c);
  function ticketMatchesFilters(ticket) {
    if (!filterConditions.length) return true;
    const results = filterConditions.map((condition) => {
      // A valueless row filters nothing — it is a row being built, not a
      // condition that excludes everything.
      if (!condition.values.length) return true;
      const field = FILTER_FIELDS[condition.field];
      return field ? field.matches(ticket, condition.op, condition.values) : true;
    });
    return filterMatch === "any" ? results.some(Boolean) : results.every(Boolean);
  }
  // A quick facet pick is authoritative for its field: it replaces every
  // condition the builder holds on that field, so the menu and the chips
  // can never show different filter states.
  function pickFacet(field, value) {
    filterConditions = filterConditions.filter((condition) => condition.field !== field);
    if (value) filterConditions.push({field, op: "is", values: [value]});
    if (!filterConditions.length) filterMatch = "all";
  }
  // cubic P1: an edit can filter out the selected row — the thread must
  // refresh, or the pane keeps painting the cached ticket the filter removed.
  function applyFilterEdit(conditions, match) {
    filterConditions = normalizeFilterConditions(conditions);
    if (match === "all" || match === "any") filterMatch = match;
    resetUiState();
    ensureSelection();
    syncUrl({push: false});
    return refreshThread().then(refreshRail).then(refreshComposer).then(afterUi);
  }
  function pickFacetAndRefresh(field, value) {
    pickFacet(field, value);
    resetUiState();
    ensureSelection();
    syncUrl({push: false});
    return refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(afterUi);
  }
  // #36: saved views are the operator's own browser state — like read
  // markers and titles, first-party only, never a Gorgias write.
  const SAVED_VIEWS_KEY = "bb-inbox-saved-views-v1";
  function loadSavedViews() {
    try {
      const raw = JSON.parse(storage?.getItem?.(SAVED_VIEWS_KEY) || "null");
      return Array.isArray(raw) ? raw.filter((entry) => entry && typeof entry.id === "string" && typeof entry.name === "string") : [];
    } catch {
      return [];
    }
  }
  let savedViews = loadSavedViews();
  function persistSavedViews() {
    try {
      // Two-tab merge like the title store: adopt unseen ids, keep this
      // organ's own adds and removals.
      const stored = new Map(loadSavedViews().map((entry) => [entry.id, entry]));
      for (const entry of savedViews) stored.set(entry.id, entry);
      const localIds = new Set(savedViews.map((entry) => entry.id));
      savedViews = [...stored.values()].filter((entry) => localIds.has(entry.id) || !removedViewIds.has(entry.id));
      storage?.setItem?.(SAVED_VIEWS_KEY, JSON.stringify(savedViews));
    } catch {
      /* private-mode storage quota is not an inbox error */
    }
  }
  const removedViewIds = new Set();
  function savedViewList() {
    return savedViews.map((entry) => ({id: entry.id, name: entry.name, shared: Boolean(entry.shared)}));
  }
  function saveView(rawName, shared) {
    const name = String(rawName ?? "").replace(/\s+/g, " ").trim().slice(0, 80);
    if (!name) return null;
    const entry = {id: `view-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      name, shared: Boolean(shared), match: filterMatch, conditions: filterConditions.map((condition) => ({...condition}))};
    savedViews = [...savedViews, entry];
    persistSavedViews();
    return entry;
  }
  function removeView(id) {
    removedViewIds.add(String(id));
    savedViews = savedViews.filter((entry) => entry.id !== id);
    persistSavedViews();
  }
  // #36: applying a saved view is a closure function, not just an organ
  // method — the FILTER_VIEW_APPLY subscription inside mount() calls this
  // name, and it must resolve there.
  function applySavedView(id) {
    const view = savedViews.find((entry) => entry.id === id);
    if (!view) return;
    filterConditions = normalizeFilterConditions(view.conditions);
    filterMatch = view.match === "any" ? "any" : "all";
    resetUiState();
    ensureSelection();
    syncUrl({push: false});
    return refreshThread().then(refreshRail).then(refreshComposer);
  }
  // #42: the id the operator landed on — from the boot deep link or a
  // back/forward replay — that no visible row matches. It stays selected
  // (thread shows not-found, or getTicket resolves it) instead of being
  // snapped to the first visible row. Every operator-driven selection
  // clears it: the operator picked a real ticket.
  let protectedTicketId = opts.ticketId || null;
  // #42: back/forward replays race — the newest popped entry must win. Each
  // replay takes a number; an async refresh landing under a stale number
  // (its entry was replaced by a newer replay) discards its work instead of
  // overwriting the newer entry's thread.
  let replayGeneration = 0;
  let body = "";
  let strip = "";
  let summarizeText = "";
  let discarded = false;
  let sent = [];
  let toEmail = "";
  let macros = fixtureMacros;
  let macroQuery = "";
  let selectedMacroId = "";
  let macrosOpen = false;
  // #40: collapse state persists across reloads — the layout the operator
  // chose is part of their setup, not a session accident.
  const COLLAPSE_KEY = "bb-inbox-collapsed-v1";
  function loadCollapseState() {
    try {
      const raw = JSON.parse(storage?.getItem?.(COLLAPSE_KEY) || "null");
      if (!raw || typeof raw !== "object") return {list: false, rail: false};
      return {list: Boolean(raw.list), rail: Boolean(raw.rail)};
    } catch {
      return {list: false, rail: false};
    }
  }
  // cubic: persist only the field this organ actually changed — writing the
  // whole record would overwrite another tab's fresh choice with this
  // organ's stale value for the untouched pane.
  function persistCollapseState(changed) {
    try {
      const current = loadCollapseState();
      current[changed] = changed === "list" ? listCollapsed : railCollapsed;
      storage?.setItem?.(COLLAPSE_KEY, JSON.stringify(current));
    } catch {
      /* private-mode storage quota is not an inbox error */
    }
  }
  const collapseSeed = loadCollapseState();
  let listCollapsed = collapseSeed.list;
  let railCollapsed = collapseSeed.rail;
  // Task 6: on phones the open ticket owns the viewport — selecting one
  // drops the list to its collapsed strip so the composer starts visible.
  // Session-local and never persisted: an explicit expand survives polls and
  // refreshes, and desktop never inherits the phone's choice. matchMedia is
  // guarded so non-DOM tests simply never take this path.
  function narrowViewport() {
    try {
      return typeof window !== "undefined" && typeof window.matchMedia === "function"
        && window.matchMedia("(max-width: 780px)").matches;
    } catch {
      return false;
    }
  }
  function collapseListForNarrow() {
    if (narrowViewport() && selectedId && !listCollapsed) {
      listCollapsed = true;
    }
  }
  // The rail's Ticket details card starts toggled off — the vague unknowns
  // stay one click away instead of leading the rail. Session-local like the
  // other rail toggles; reset on context switch in resetUiState.
  let ticketDetailsOpen = false;
  // #34: first-seen ids start unread unless the persisted read store already
  // marks them read. Fixtures seed unread exactly like before; the observed
  // path learns ids from the snapshot itself.
  const unreadIds = new Set(
    (pinnedCatalog || fixtureTickets).map((ticket) => ticket.id).filter(Boolean).filter((id) => !readIds.has(id)),
  );
  const knownTicketIds = new Set(unreadIds);
  let writeGate = {
    mutationsEnabled: false,
    refused: ["send", "refund", "cancel"],
    message: "Shopify writes are refused. SHOPIFY_MUTATIONS_ENABLED stays 0.",
  };
  let bridgeStatus = {
    gorgiasEnabled: false,
    gorgiasConfigured: false,
    outboundEnabled: false,
    emailConfigured: false,
    allowlistActive: false,
  };
  let sendError = "";
  let bridgePollTimer = null;
  let writeGateOpen = false;
  let customerJoinGateOpen = false;
  let orderLinkGateOpen = false;
  let privacyGateOpen = Boolean(opts.privacyGate);
  let marketingGateOpen = Boolean(opts.marketingGate);
  // #38: the New ticket sheet — first-party like the gate sheets. The form
  // draft is session-local organ state (like the rename editor's) so an
  // error repaint keeps what the operator typed; only the created ticket
  // persists.
  let createSheetOpen = false;
  let createError = "";
  let createDraft = {customerName: "", fromEmail: "", subject: "", channel: "email", body: ""};
  let listError = "";
  // Task 2: one freshness banner for the whole snapshot. Every pane renders
  // the same snapshot, so staleness is announced once (in the list, the
  // snapshot's home) instead of once per pane. Thread and rail keep their
  // own distinct notes (partial history, per-card source) with timestamps
  // but no extra "stale" copies.
  let projectionNotice = { text: "", showRefresh: false };
  // The projection stamps generatedAtEpoch (seconds) and generatedAt (ISO);
  // without either, no time claim is made.
  function projectionUpdatedAt() {
    const meta = shop.projection || {};
    const epoch = Number(meta.generatedAtEpoch);
    if (Number.isFinite(epoch) && epoch > 0) {
      try {
        return formatWhen(new Date(epoch * 1000).toISOString());
      } catch {
        return "";
      }
    }
    return typeof meta.generatedAt === "string" ? formatWhen(meta.generatedAt) : "";
  }
  function freshnessNotice(kind) {
    const when = projectionUpdatedAt();
    const stamp = when ? ` Last updated ${when}.` : "";
    if (kind === "failed") {
      return { text: `Showing previously loaded history — refresh failed.${stamp}`, showRefresh: true };
    }
    if ((shop.projection || {}).stale) {
      return { text: `Observed history is stale.${stamp}`, showRefresh: true };
    }
    return {
      text: `Observed history · last 90 days. Status and assignment are shown when the latest observed webhook carried them; otherwise unknown.${stamp}`,
      showRefresh: true,
    };
  }
  let loadingMore = false;
  let moreError = "";
  let paintListOnly = null;
  // #38: local-only tickets union into every list source — the same rows the
  // shop hands back, plus this browser's own. They flow through applyLocalState
  // like every other row so the #44 overrides still reach them.
  function unionLocalRows(rows = []) {
    const seen = new Set(rows.map((row) => row.id));
    return [...rows, ...localTicketRows().filter((row) => !seen.has(row.id)).map(applyLocalState)];
  }
  let listRows = pinnedCatalog
    ? unionLocalRows(pinnedCatalog.map(applyLocalState)).filter((ticket) => ticketInView(ticket, viewId))
    : [];
  // #37: the unfiltered loaded snapshot — the search-every-view escalation
  // matches against this, not the view-partitioned listRows.
  let allRows = pinnedCatalog ? unionLocalRows(pinnedCatalog) : localTicketRows();
  function localTicketById(id) {
    const record = localTickets.find((row) => row.id === id);
    return record ? applyLocalState(localTicketRow(record)) : null;
  }
  let selected = pinnedCatalog
    ? (applyLocalState(pinnedCatalog.find((ticket) => ticket.id === selectedId))
      || (selectedId?.startsWith?.("local:") ? localTicketById(selectedId) : null))
    : (selectedId?.startsWith?.("local:") ? localTicketById(selectedId) : null);
  let counts = pinnedCatalog ? viewCounts(unionLocalRows(pinnedCatalog)) : viewCounts(unionLocalRows(fixtureTickets));
  /** Set by mount(); programmatic organ APIs remount chrome when present. */
  let paintMounted = null;

  function disconnectSend() {
    sendError = ACTIVATE_SEND_MESSAGE;
    if (typeof paintMounted === "function") paintMounted();
  }

  function markRead(ticketId) {
    if (!ticketId) return;
    unreadIds.delete(ticketId);
    if (!readIds.has(ticketId)) {
      readIds.add(ticketId);
      persistRead();
    }
  }

  /**
   * "Assigned to me" is resolved here, not in the credential-free exporter:
   * the snapshot only carries the assignee address Gorgias observed. An
   * address that is not the configured operator's is "other", never null —
   * counting another agent's ticket as Unassigned would be an invented state.
   * With no configured operator address, nothing is ever mine.
   */
  function withOperatorAssignee(ticket) {
    if (!ticket) return ticket;
    const mine = String(opts.operatorEmail ?? shop.operatorEmail ?? "").trim().toLowerCase();
    // The snapshot carries the observed address under assigneeEmail; older
    // fixtures and snapshots put the same address in assignee. Either way the
    // raw address must survive for the assignee facet menu and row badge.
    const observed = String(ticket.assigneeEmail ?? ticket.assignee ?? "").trim().toLowerCase();
    if (!observed) return { ...ticket, assignee: null };
    return { ...ticket, assignee: mine && observed === mine ? "me" : (ticket.assignee ?? "other") };
  }

  function afterUi() {
    paintMounted?.();
    return snapshot();
  }

  function visibleTickets() {
    // #37: the escalation searches the whole loaded snapshot; a scoped search
    // rides the view partition. Either way flagged rows stay out.
    const source = searchAllViews ? allRows : listRows;
    return source.filter((ticket) =>
      // #33: on the observed path the view partition holds for every render,
      // including rows appended by loadMore, so flagged rows never leak into
      // working views. Server-filtered list rows carry no assignee field and
      // are already view-filtered, so they must not be re-filtered here.
      // #37: a scoped search rides the view partition; the "search every
      // view" escalation lifts only the partition, never the flag guard.
      (searchAllViews ? !(ticket.spam || ticket.trashed) : !shop.observedHistory || ticketInView(ticket, viewId)) &&
      ticketMatchesFilters(ticket) &&
      ticketMatchesSearch(ticket));
  }

  function selectedTicket() {
    return selected || listRows.find((ticket) => ticket.id === selectedId) || null;
  }

  // #42: the deep-linked id can name a ticket this snapshot does not hold.
  // The thread must say so, not silently show another row — but a ticket
  // getTicket resolved (it merely sits outside the active view's rows) is
  // not missing; its thread renders.
  function missingTicketId() {
    const requested = opts.ticketId;
    if (!requested || selectedId !== requested || selected || listRows.some((ticket) => ticket.id === requested)) return null;
    return requested;
  }

  function ensureSelection() {
    // #42: a deep-linked or replayed id that matches no visible row is not
    // snapped to the first row — the operator landed on it, so the thread
    // shows the not-found state (or getTicket resolves it) until they pick
    // a real ticket. Operator-driven selection clears the protection.
    if (protectedTicketId && !listRows.some((ticket) => ticket.id === protectedTicketId)
      && selectedId === protectedTicketId) return;
    const visible = visibleTickets();
    if (!visible.some((ticket) => ticket.id === selectedId)) {
      selectedId = visible[0]?.id || null;
    }
    if (selectedId) markRead(selectedId);
  }

  // ponytail: one definition of "switching context clears the selection and composer".
  function resetUiState(nextSelectedId = null) {
    selectedId = nextSelectedId;
    body = "";
    strip = "";
    summarizeText = "";
    discarded = false;
    selectedMacroId = "";
    macrosOpen = false;
    // A new context reverts to the default: ticket details toggled off.
    ticketDetailsOpen = false;
    // #39: view/filter changes clear the multi-select — ids may no longer be
    // visible, so a stale selection would act on hidden rows.
    clearBulk();
  }

  async function refreshList() {
    listError = "";
    if (pinnedCatalog) {
      const overlaid = unionLocalRows(pinnedCatalog.map(applyLocalState));
      listRows = overlaid.filter((ticket) => ticketInView(ticket, viewId));
      reconcileBulk();
      counts = viewCounts(overlaid);
      return;
    }
    if (typeof shop.listTickets === "function") {
      try {
        if (shop.observedHistory) {
          const rows = unionLocalRows((await readObservedTickets(shop)).map(withOperatorAssignee).map(applyLocalState));
          // #34: first-seen ids become unread here too — the observed inbox
          // is the production path and must not render everything as read.
          for (const row of rows) {
            if (!knownTicketIds.has(row.id) && !readIds.has(row.id)) unreadIds.add(row.id);
            knownTicketIds.add(row.id);
          }
          listRows = rows.filter((ticket) => ticketInView(ticket, viewId));
          // #37: the whole loaded snapshot stays searchable — the "search
          // every view" escalation must see rows the view partition keeps
          // out of listRows.
          allRows = rows;
          reconcileBulk();
          counts = viewCounts(rows);
          // #33: pagination totals stay in the active view's domain. For All,
          // the exact working total is the snapshot minus the flagged union
          // (a row can be both spam and trashed — subtract once). Metadata
          // carries exact counts; the loaded prefix only approximates when
          // metadata is missing.
          const flaggedInSnapshot = (shop.projection?.spamCount ?? counts.spam)
            + (shop.projection?.trashCount ?? counts.trash)
            - (shop.projection?.flaggedOverlap ?? Math.min(counts.spam, counts.trash));
          // #38: the projection's metadata counts observed tickets only, so
          // this browser's local tickets add on top of the All total.
          counts.all = (shop.projection?.ticketCount ?? rows.length - localTickets.length)
            - flaggedInSnapshot + localTickets.length;
          projectionNotice = freshnessNotice("ok");
          return;
        }
        const [rows, ...viewRows] = await Promise.all([
          shop.listTickets({ view: viewId, limit: 50 }),
          ...availableViews.map((view) => shop.listTickets({ view: view.id, limit: 100 })),
        ]);
        if (Array.isArray(rows)) {
          // #44: the overlay must reach every list source — a locally-closed
          // ticket must leave the Open view here too, not just in the
          // observed history path. #38: the server page is already view-
          // filtered and its rows may not even carry the status/assignee
          // fields ticketInView reads, so only the local additions are
          // filtered — a local ticket joins only the views it belongs to.
          const overlaid = rows.map(applyLocalState);
          const localInView = localTicketRows().map(applyLocalState)
            .filter((ticket) => ticketInView(ticket, viewId));
          listRows = [...overlaid, ...localInView];
          // #37: the union of the loaded per-view pages is the escalation's
          // snapshot on non-observed shops — there is no single unfiltered
          // list to hold. #38: the local rows union in here too, or the
          // search-every-view escalation loses them.
          const seen = new Map();
          // One batch (not a spread — each local row is not itself an array)
          // or the Array.isArray guard silently skips it.
          for (const batch of [overlaid, localTicketRows().map(applyLocalState), ...viewRows.map((batch) => Array.isArray(batch) ? batch.map(applyLocalState) : batch)]) {
            if (!Array.isArray(batch)) continue;
            for (const row of batch) if (row?.id && !seen.has(row.id)) seen.set(row.id, row);
          }
          allRows = [...seen.values()];
          for (const row of rows) {
            if (!knownTicketIds.has(row.id)) unreadIds.add(row.id);
            knownTicketIds.add(row.id);
          }
        }
        counts = Object.fromEntries(availableViews.map((view, index) => [
          view.id,
          Array.isArray(viewRows[index]) ? viewRows[index].length : 0,
        ]));
        // #38: the per-view server counts know nothing about this browser's
        // local tickets — add each one to every view it belongs to.
        for (const row of localTicketRows()) {
          for (const view of availableViews) {
            if (ticketInView(row, view.id)) counts[view.id] += 1;
          }
        }
        return;
      } catch {
        listError = "Could not load tickets. Refresh to try again.";
        if (shop.observedHistory) {
          if (listRows.length) projectionNotice = freshnessNotice("failed");
          return;
        }
      }
    }
    const fixtureRows = unionLocalRows(fixtureTickets.map(applyLocalState));
    listRows = fixtureRows.filter((ticket) => ticketInView(ticket, viewId));
    allRows = fixtureRows;
    reconcileBulk();
    counts = viewCounts(fixtureRows);
  }

  async function refreshThread() {
    const id = selectedId;
    if (!id) {
      selected = null;
      return;
    }
    // #38: a local-only ticket resolves from this browser's store, never
    // from the shop — the shop has never seen it.
    if (id.startsWith?.("local:")) {
      const record = localTickets.find((row) => row.id === id);
      selected = record ? applyLocalState(localTicketRow(record)) : null;
      return;
    }
    if (pinnedCatalog) {
      selected = applyLocalState(pinnedCatalog.find((ticket) => ticket.id === id)) || null;
      return;
    }
    if (typeof shop.getTicket === "function") {
      try {
        const ticket = await shop.getTicket({ ticketId: id });
        // #42: a back/forward replay may have moved on while this fetch was
        // in flight — a stale thread must never overwrite the newer replay's.
        if (selectedId !== id) return;
        if (ticket) {
          selected = applyLocalState(shop.observedHistory ? withOperatorAssignee(ticket) : ticket);
          // #41: the detail fetch carries the rail snapshot (order name), so
          // the selected row's title can match the thread's. Rows this organ
          // never opened keep the bare list summary — projection.py only
          // attaches the rail on get_ticket.
          const row = listRows.find((rowTicket) => rowTicket.id === id);
          if (row && ticket.shopifyRail) row.shopifyRail = ticket.shopifyRail;
          return;
        }
      } catch {
        if (shop.observedHistory) {
          selected = {...(listRows.find(ticket => ticket.id === id) || {id}),historyUnavailable:true};
          return;
        }
      }
    }
    selected = fixtureTickets.find((ticket) => ticket.id === id)
      || listRows.find((ticket) => ticket.id === id)
      || null;
  }

  function closeAllGates() {
    writeGateOpen = false;
    privacyGateOpen = false;
    marketingGateOpen = false;
    customerJoinGateOpen = false;
    orderLinkGateOpen = false;
  }

  // #38: the New ticket sheet. Local-only model (AGENTS.md §2(7)): the
  // created ticket lives in this browser's store; nothing here reaches
  // Gorgias or notifies the customer. The form itself is unlabeled <input>s
  // the tissue holds in the DOM — the organ only needs the shell, the
  // confirm button, and the error line.
  function createSheetHtml() {
    if (!createSheetOpen) return "";
    return `<div class="gate-sheet-backdrop" data-create-sheet-backdrop>
      <form class="gate-sheet create-sheet" role="dialog" aria-modal="true" aria-labelledby="create-sheet-copy" data-create-sheet>
        <p id="create-sheet-copy"><strong>New ticket</strong> — saved in this browser only. It never reaches Gorgias and never notifies the customer.</p>
        <label class="create-field">Customer name
          <input name="customerName" data-create-customer maxlength="120" autocomplete="off" value="${esc(createDraft.customerName)}" required>
        </label>
        <label class="create-field">Customer email (optional)
          <input name="fromEmail" data-create-email type="email" maxlength="200" autocomplete="off" value="${esc(createDraft.fromEmail)}">
        </label>
        <label class="create-field">Subject (optional — titled from the message when blank)
          <input name="subject" data-create-subject maxlength="120" autocomplete="off" value="${esc(createDraft.subject)}">
        </label>
        <label class="create-field">Channel
          <select name="channel" data-create-channel>
            ${["email", "chat", "phone", "whatsapp"].map((value) => `<option value="${value}"${createDraft.channel === value ? " selected" : ""}>${value === "whatsapp" ? "WhatsApp" : value.charAt(0).toUpperCase() + value.slice(1)}</option>`).join("")}
          </select>
        </label>
        <label class="create-field">Message
          <textarea name="body" data-create-body maxlength="2000" required>${esc(createDraft.body)}</textarea>
        </label>
        ${createError ? `<p class="bulk-error" role="alert">${esc(createError)}</p>` : ""}
        <div class="gate-sheet-actions">
          <button type="button" class="btn-ink" data-create-confirm title="Save this ticket in your browser only. No Gorgias write, no customer notification.">Create ticket</button>
          <button type="button" class="btn-hairline" data-create-sheet-dismiss title="Close this form">Close</button>
        </div>
      </form>
    </div>`;
  }

  function gateSheetHtml() {
    if (privacyGateOpen && capabilities.markPrivacyHandled !== false) {
      return `<div class="gate-sheet-backdrop" data-gate-sheet data-privacy-gate>
        <div class="gate-sheet" role="dialog" aria-modal="true" aria-labelledby="gate-sheet-copy">
          <p id="gate-sheet-copy">${PRIVACY_LOCKED_COPY}</p>
          <div class="gate-sheet-actions">
            <button type="button" class="btn-ink" data-privacy-handled title="Confirm on this ticket only. No live data erase or export.">${GATE_CONFIRM_LABEL}</button>
            <button type="button" class="btn-hairline" data-gate-dismiss title="Close this notice">Close</button>
          </div>
        </div>
      </div>`;
    }
    if (marketingGateOpen && capabilities.markUnsubscribed !== false) {
      return `<div class="gate-sheet-backdrop" data-gate-sheet data-marketing-gate>
        <div class="gate-sheet" role="dialog" aria-modal="true" aria-labelledby="gate-sheet-copy">
          <p id="gate-sheet-copy">${MARKETING_LOCKED_COPY}</p>
          <div class="gate-sheet-actions">
            <button type="button" class="btn-ink" data-unsubscribe-handled title="Confirm on this ticket only. No live unsubscribe.">${GATE_CONFIRM_LABEL}</button>
            <button type="button" class="btn-hairline" data-gate-dismiss title="Close this notice">Close</button>
          </div>
        </div>
      </div>`;
    }
    if (customerJoinGateOpen) {
      return `<div class="gate-sheet-backdrop" data-gate-sheet data-customer-join-gate>
        <div class="gate-sheet" role="dialog" aria-modal="true" aria-labelledby="gate-sheet-copy">
          <p id="gate-sheet-copy">${CUSTOMER_JOIN_LOCKED_COPY}</p>
          <div class="gate-sheet-actions">
            <button type="button" class="btn-hairline" data-gate-dismiss title="Close this notice">Close</button>
          </div>
        </div>
      </div>`;
    }
    if (orderLinkGateOpen) {
      return `<div class="gate-sheet-backdrop" data-gate-sheet data-order-link-gate>
        <div class="gate-sheet" role="dialog" aria-modal="true" aria-labelledby="gate-sheet-copy">
          <p id="gate-sheet-copy">${ORDER_LINK_LOCKED_COPY}</p>
          <div class="gate-sheet-actions">
            <button type="button" class="btn-hairline" data-gate-dismiss title="Close this notice">Close</button>
          </div>
        </div>
      </div>`;
    }
    if (!writeGateOpen) return "";
    return `<div class="gate-sheet-backdrop" data-gate-sheet>
      <div class="gate-sheet" role="dialog" aria-modal="true" aria-labelledby="gate-sheet-copy">
        <p id="gate-sheet-copy">${PAYMENTS_LOCKED_COPY}</p>
        <div class="gate-sheet-actions">
          <button type="button" class="btn-hairline" data-gate-dismiss title="Close this notice">Close</button>
        </div>
      </div>
    </div>`;
  }

  function sheetHtml() {
    return `${gateSheetHtml()}${createSheetHtml()}`;
  }

  // #40: the collapsed rail strip names what it hides — the customer pane
  // and the returns pane, with an open-return marker when one is in flight.
  // The marker follows the path: observed tickets read their own snapshot
  // (the rail's cached models go stale there when the next ticket carries
  // no snapshot); connected tickets read the rail's live-loaded models.
  function railCollapsedHtml() {
    const ticket = selectedTicket();
    const openReturn = ticket?.projectionSource
      ? Boolean(projectReturns(shopifyRailSnapshot(ticket)?.returns || null).inProgress)
      : Boolean(rail.snapshot().models?.returns?.inProgress);
    return `<div class="pane-inner">
      <button type="button" class="rail-expand-btn" data-rail-expand aria-label="Expand customer rail" title="Show customer rail">
        ${RAIL_EXPAND_ICON}
        <span class="rail-expand-label">Customer</span>
      </button>
      <button type="button" class="rail-expand-btn rail-expand-returns" data-rail-expand aria-label="Expand customer rail — Returns" title="Show the returns pane">
        ${RAIL_EXPAND_ICON}
        <span class="rail-expand-label">Returns</span>
        ${openReturn ? `<span class="rail-strip-return" data-strip-return="open" title="An open return is in flight" role="img" aria-label="Open return in flight">●</span>` : ""}
      </button>
    </div>`;
  }

  function shell() {
    return `<div class="inbox" data-organ="inbox">
      <a class="skip-link" href="#inbox-thread">Skip to thread.</a>
      <section class="pane pane-list${listCollapsed ? " is-collapsed" : ""}" data-pane="list"></section>
      <section class="pane pane-thread" id="inbox-thread" data-pane="thread" tabindex="-1">
        <div data-slot="thread"></div>
        <div data-slot="composer"></div>
      </section>
      <aside class="pane pane-rail${railCollapsed ? " is-collapsed" : ""}" data-pane="rail"></aside>
    </div>
    <div data-gate-host></div>`;
  }

  async function loadDraft(ticket) {
    if (capabilities.draftReply === false) return "";
    if (!ticket) return "";
    if (typeof shop.draftReply === "function") {
      try {
        const railSnap = rail.snapshot();
        const result = await shop.draftReply({
          ticketId: ticket.id,
          shop: shopHost,
          thread: ticket,
          customerId: ticket.customerId,
          orderId: ticket.orderId,
          customer: railSnap.models.customer?.record,
          order: railSnap.models.order?.record,
          returns: railSnap.models.returns?.record,
          pastOrders: railSnap.models.history?.rows,
        });
        if (result?.draft) return result.draft;
      } catch {
        // fixture fallback below
      }
    }
    return ticket.stubDraft || "";
  }

  async function loadSummary(ticket) {
    if (capabilities.summarizeThread === false) return "";
    if (!ticket) return "";
    if (typeof shop.summarizeThread === "function") {
      try {
        const result = await shop.summarizeThread({
          ticketId: ticket.id,
          shop: shopHost,
          thread: ticket,
        });
        if (result?.summary) return result.summary;
      } catch {
        // fixture fallback below
      }
    }
    return ticket.stubSummary || "";
  }

  async function refreshComposer() {
    const ticket = selectedTicket();
    const requestTicketId = ticket?.id || null;
    discarded = false;
    summarizeText = "";
    const text = ticket ? await loadDraft(ticket) : "";
    if (selectedId !== requestTicketId) return;
    strip = text;
  }

  async function refreshWriteGate() {
    if (typeof shop.writeGateStatus === "function") {
      try {
        const result = await shop.writeGateStatus();
        if (result && Array.isArray(result.refused)) writeGate = result;
      } catch {
        // keep default gated copy
      }
    }
  }

  async function refreshBridgeStatus() {
    if (typeof shop.bridgeStatus !== "function") return;
    try {
      const result = await shop.bridgeStatus();
      if (result && typeof result === "object") bridgeStatus = result;
    } catch {
      // keep defaults
    }
  }

  function startBridgePoll() {
    if (bridgePollTimer) {
      clearInterval(bridgePollTimer);
      bridgePollTimer = null;
    }
    if ((!bridgeStatus.gorgiasEnabled && !shop.observedHistory) || pinnedCatalog) return;
    bridgePollTimer = setInterval(() => {
      refreshList().then(async () => {
        const previousId = selectedId;
        ensureSelection();
        if (selectedId !== previousId) {
          selected = null;
          body = "";
          strip = "";
          summarizeText = "";
          discarded = false;
          selectedMacroId = "";
          macrosOpen = false;
        }
        if (!selectedId) {
          selected = null;
        } else if (shop.observedHistory && selectedId === previousId) {
          try { selected = applyLocalState(withOperatorAssignee(await shop.getTicket({ticketId:selectedId}))); } catch { if (selected) selected = {...selected,historyUnavailable:true}; }
        }
        if (selectedId !== previousId) {
          await refreshThread();
          await refreshRail();
          await refreshComposer();
        }
        paintMounted?.();
      }).catch(() => {});
    }, 30000);
    bridgePollTimer.unref?.();
  }

  async function persistAction(method, args, flag) {
    const ticket = selectedTicket();
    if (!ticket) return null;
    if (capabilities[method] === false || typeof shop[method] !== "function") {
      throw new Error("This action is not available in this inbox.");
    }
    const result = await shop[method]({ ticketId: ticket.id, ...args });
    if (!result || result.id !== ticket.id || !result[flag]) {
      throw new Error("The action was not confirmed. Please try again.");
    }
    // Never turn an error or missing response into a local success marker.
    if (selectedId === ticket.id) selected = result;
    return result;
  }

  async function escalateSelected(reason) {
    return persistAction("escalateTicket", { reason }, "escalated");
  }

  async function markPrivacyHandled() {
    const result = await persistAction("markPrivacyHandled", {}, "privacyHandled");
    privacyGateOpen = false;
    return result;
  }

  async function markUnsubscribed() {
    const result = await persistAction("markUnsubscribed", {}, "unsubscribeHandled");
    marketingGateOpen = false;
    return result;
  }

  async function markBugHandled() {
    return persistAction("markBugHandled", {}, "bugHandled");
  }

  async function refreshMacros(query = "") {
    if (capabilities.searchMacros === false) { macros = []; return; }
    macroQuery = query;
    if (typeof shop.searchMacros === "function") {
      try {
        const result = await shop.searchMacros({ query });
        if (Array.isArray(result?.macros)) {
          macros = result.macros;
          return;
        }
      } catch {
        // fixture fallback below
      }
    }
    const needle = String(query || "").trim().toLowerCase();
    macros = fixtureMacros.filter((macro) => {
      if (!needle) return true;
      return `${macro.id} ${macro.title} ${(macro.tags || []).join(" ")} ${macro.body}`.toLowerCase().includes(needle);
    });
  }

  // Task 1: the projection's read-only draft is a draft too. When the live
  // draft lane is empty (e.g. draftReply is off in this inbox) the ticket's
  // readonlyDraft feeds the composer strip instead of sitting in the thread
  // looking like a customer message. Dismiss still hides it until reselect
  // (resetUiState clears `discarded`), and a superseded draft stays hidden
  // because a newer customer message needs review first.
  function effectiveStrip(ticket) {
    const live = discarded ? "" : strip;
    if (live) return { text: live, readonly: false, note: "" };
    const fallback = !discarded && ticket?.readonlyDraft && !ticket?.draftSuperseded
      ? String(ticket.readonlyDraft)
      : "";
    if (!fallback) return { text: "", readonly: false, note: "" };
    // Task 3: the raw export stamp (microsecond ISO) stays out of the UI —
    // the operator gets the same short date the rest of the inbox uses.
    const at = ticket.draftSourceMessageAt ? formatWhen(ticket.draftSourceMessageAt) : "";
    const source = ticket.draftSourceMessageId && at
      ? `Source message: ${ticket.draftSourceMessageId} · ${at}`
      : ticket.draftSourceMessageId
        ? `Source message: ${ticket.draftSourceMessageId}`
        : "";
    const note = [source, ticket.draftReason || ""].filter(Boolean).join(" — ");
    return { text: fallback, readonly: true, note };
  }

  // Task 2: the banner's Refresh re-reads everything without touching the
  // operator's reply — no resetUiState, so body/strip/summarize survive.
  async function refreshHistory() {
    await refreshList();
    ensureSelection();
    syncUrl({ push: false });
    await refreshThread();
    await refreshRail();
    await refreshComposer();
    await refreshMacros(macroQuery);
    afterUi();
    return snapshot();
  }

  let sendInfo = "";
  let sendBusy = false;
  function composerInput(ticket) {
    return {
      capabilities: parentRewrite ? Object.assign({}, capabilities, { draftReply: true }) : capabilities,
      rewriteViaParent: parentRewrite !== null,
      rewriteInstruction: lastRewriteInstruction,
      rewriteBusy: rewriteBusy,
      rewriteError: rewriteError,
      noteViaParent: parentNote !== null,
      noteConfirm: noteConfirm,
      noteBusy: noteBusy,
      noteError: noteError,
      noteInfo: noteInfo,
      sendViaParent: parentSend !== null,
      sendBusy: sendBusy,
      sendInfo: sendInfo,
      ticket: withRecipient(ticket, toEmail),
      draft: effectiveStrip(ticket).text,
      summarize: summarizeText,
      macros,
      body,
      strip: effectiveStrip(ticket).text,
      stripReadonly: effectiveStrip(ticket).readonly,
      stripNote: effectiveStrip(ticket).note,
      query: macroQuery,
      selectedMacroId,
      searchOpen: macrosOpen,
      writeGate,
      bridgeStatus,
      sendError,
    };
  }

  async function loadMore() {
    if (!shop.observedHistory || loadingMore) return;
    loadingMore = true;
    moreError = "";
    paintListOnly?.();
    try {
      // Re-read the visible prefix so a newly published snapshot cannot cause
      // duplicates or skipped tickets at an offset boundary. listRows stays
      // the raw prefix; the ticketInView filter in visibleTickets keeps
      // flagged rows out of every render, and pagination totals stay in the
      // active view's domain via the same helpers refreshList uses.
      const rows = await readObservedTickets(shop, listRows.length + 100);
      // #34: newly paged-in ids are first-seen here too, so page 2 renders
      // its unread dots just like page 1.
      for (const row of rows) {
        if (!knownTicketIds.has(row.id) && !readIds.has(row.id)) unreadIds.add(row.id);
        knownTicketIds.add(row.id);
      }
      // #38: the re-read is observed rows only — the local union rides on
      // top, or Load more erases this browser's local tickets.
      const unioned = unionLocalRows(rows);
      listRows = unioned;
      allRows = unioned;
      reconcileBulk();
      const observed = viewCounts(rows);
      const flaggedInSnapshot = (shop.projection?.spamCount ?? observed.spam)
        + (shop.projection?.trashCount ?? observed.trash)
        - (shop.projection?.flaggedOverlap ?? Math.min(observed.spam, observed.trash));
      // #38: local tickets count on top of the observed totals, matching
      // refreshList's observed path.
      const localCount = unioned.length - rows.length;
      counts = {
        ...observed,
        all: (shop.projection?.ticketCount ?? rows.length) - flaggedInSnapshot + localCount,
      };
    } catch {
      moreError = "Could not load more tickets. Try again.";
    } finally {
      loadingMore = false;
      paintListOnly?.();
    }
    return snapshot();
  }

  // #39: the bulk bar renders inside the list; the tissue only needs ids,
  // the count and the action markers.
  function bulkSelectionInput() {
    return {
      ids: [...bulkIds],
      escalated: bulkEscalated,
      error: bulkError,
      canEscalate: capabilities.escalateTicket !== false && typeof shop.escalateTicket === "function",
      actions: {
        markRead: bulkMarkRead,
        markUnread: bulkMarkUnread,
        export: bulkExport,
        escalate: bulkEscalate,
        clear: clearSelection,
      },
    };
  }

  function listInput() {
    // #33: pagination totals live in the active view's domain — Spam shows
    // "3 of 3", All shows the working partition. `loaded` counts view
    // members in the raw prefix so Load more never stops early when flagged
    // rows occupy part of the prefix. #37: while a search is active the
    // pagination bar hides — the search count is the honest total for the
    // loaded snapshot, and Load more would restate the unfiltered count.
    const searching = Boolean(searchQuery);
    // An over-limit paste renders as a bounded miss, so the tissue must not
    // treat it as empty.
    const searchBounded = searchOverLimit;
    const pagination = shop.observedHistory && !searching ? {
      total: counts[viewId] ?? listRows.length,
      loaded: listRows.filter((ticket) => ticketInView(ticket, viewId)).length,
      loading: loadingMore,
      error: moreError,
      loadMore,
    } : null;
    // #37: the count is the rendered set — view (or flag) guard, facets and
    // the query all apply, so the number can never overstate the rows.
    const searchResults = searching ? visibleTickets().length : null;
    return {
      tickets: sortedVisibleTickets().map((ticket) => ({...ticket, derivedTitle: derivedTitle(ticket)})),
      error: listError,
      notice: projectionNotice.text,
      noticeRefresh: projectionNotice.showRefresh,
      pagination,
      selectedTicketId: selectedId,
      views: availableViews,
      counts,
      selectedViewId: viewId,
      channels: channelFacets(),
      selectedChannelId: channelId(),
      statuses: statusFacets(),
      selectedStatusId: statusId(),
      assignees: assigneeFacets(),
      selectedAssigneeId: assigneeId(),
      tags: tagFacets(),
      selectedTagId: tagId(),
      collapsed: listCollapsed,
      unreadIds: [...unreadIds],
      sortId,
      bulkSelection: bulkSelectionInput(),
      searchQuery,
      searchAllViews,
      searchBounded,
      searchResults,
      filterConditions: filterConditions.map((condition) => ({...condition})),
      filterMatch,
      // The builder renders its own offers from the facet counts, capped so
      // a wide snapshot cannot flood the popup.
      filterFields: Object.entries(FILTER_FIELDS).map(([id, field]) => {
        const offered = field.offers().slice(0, 12).map((offer) => ({id: offer.id, label: offer.label || offer.id}));
        // cubic: an active value past the 12-offer cap must still render as
        // chosen, or the row shows "Pick a value" and the filter looks lost.
        const active = filterConditions.filter((condition) => condition.field === id)
          .flatMap((condition) => condition.values)
          .filter((value) => !offered.some((offer) => offer.id === value))
          .map((value) => ({id: value, label: value}));
        return {id, label: field.label, ops: field.ops, values: [...offered, ...active]};
      }),
      savedViews: savedViewList(),
      // #38: the New ticket entry point — offered whenever the capability is
      // not explicitly off (the default-on matches the other capabilities).
      canCreateTicket: capabilities.createTicket !== false,
    };
  }

  function shopifyRailSnapshot(ticket) {
    const snapshot = ticket?.shopifyRail;
    if (!snapshot || typeof snapshot !== "object") return null;
    // #46: a snapshot with only returns still attaches — a ticket can have a
    // return with no resolved customer/order match. The current exporter only
    // emits returns alongside a matched order, so `snapshot.returns` alone is
    // forward-looking: it makes the payload contract correct for the day the
    // exporter learns returns-without-order (cubic review, PR 66).
    return snapshot.customer || snapshot.order || snapshot.returns ? snapshot : null;
  }

  function showsCustomerRail(ticket) {
    if (!ticket) return false;
    if (shopifyRailSnapshot(ticket)) return true;
    if (ticket.projectionSource || capabilities.customerDetails === false) return false;
    return true;
  }

  // #45: the ticket-details card — the rail's first card, Gorgias-style. Only
  // observed snapshot or first-party values render; anything the snapshot does
  // not carry is an explicit unknown, never invented (AGENTS.md §2). Tags are
  // read-only chips — the tag write question is the ticket-controls issue's.
  // The card starts toggled off (ticketDetailsOpen): the header peeks the
  // observed status and the vague rows stay one click away. The toggle is
  // organ-owned — the rail tissue only injects this HTML, so the rail's own
  // data-toggle handler must let ticket-details clicks bubble to the organ.
  function ticketDetailsHtml(ticket) {
    if (!ticket) return "";
    const open = ticketDetailsOpen;
    const known = (value) => typeof value === "string" && value.trim() ? value.trim() : null;
    const unknown = (label) => `<span class="ticket-detail-unknown" data-detail-unknown="${esc(label)}">Unknown</span>`;
    // cubic P1: escape at this boundary — observed statuses/priorities are
    // projection strings and must never render as markup. Callers pass raw.
    const row = (label, value) => `<dt>${esc(label)}</dt><dd>${value == null ? unknown(label) : esc(value)}</dd>`;
    // Contact reason: agent rows carry the intake classifier's requestType;
    // observed projection rows carry the processor's classified draftAction.
    // Neither is a Gorgias write — both are read-only observations.
    const reason = known(ticket.requestType) || known(ticket.draftAction);
    const channel = known(ticket.channel);
    // cubic: the card shows what Gorgias reported, like the thread badge —
    // never the operator's local override. observedState() reads the observed
    // status and the Gorgias priority (ticket.priority is the processor
    // draft's, not the ticket's). A projection row with no observed status
    // ("unknown" sentinel) renders the explicit unknown, never the sentinel.
    const rawStatus = observedState(ticket, "status");
    const status = rawStatus && rawStatus !== "unknown" ? rawStatus : null;
    const priority = observedState(ticket, "priority");
    // Product and resolution have no observed source in the projection yet —
    // they render as explicit unknowns rather than invented values. When the
    // order join carries them, known() picks them up unchanged.
    const product = known(ticket.product);
    const resolution = known(ticket.resolution);
    // Assignee: the observed address wins; the organ's "me" is first-party
    // knowledge, never an invented address.
    const assignee = known(ticket.assigneeEmail) || (ticket.assignee === "me" ? "me" : known(ticket.assignee) && ticket.assignee !== "other" ? ticket.assignee : null);
    const tags = Array.isArray(ticket.tags)
      ? ticket.tags.filter((tag) => typeof tag === "string" && tag.trim()).slice(0, 12)
      : [];
    // The collapsed peek names the observed status so the closed strip stays
    // useful; without one it falls back to the ticket id.
    const peek = status || known(ticket.id) || "Details";
    return `<section class="rail-card ticket-details" data-ticket-details data-tissue="ticket-details" data-open="${open ? "true" : "false"}">
      <button type="button" class="rail-toggle" data-toggle="ticket-details" aria-expanded="${open ? "true" : "false"}" title="Show or hide Ticket details">
        <h2>Ticket details</h2><span class="peek">${esc(peek)}</span>
      </button>
      <div class="rail-body"${open ? "" : " hidden"}>
        <dl class="ticket-detail-fields">
          ${row("Ticket ID", known(ticket.id))}
          ${row("Channel", channel)}
          ${row("Status", known(status))}
          ${row("Priority", known(priority))}
          ${row("Assignee", assignee)}
          ${row("Contact reason", reason)}
          ${row("Product", product)}
          ${row("Resolution", resolution)}
          ${row("Created", known(formatWhen(ticket.createdAt)))}
          ${row("Updated", known(formatWhen(ticket.updatedAt)))}
        </dl>
        <div class="ticket-detail-tags">
          <dt>Tags</dt>
          <dd>${tags.length ? tags.map((tag) => `<span class="ticket-detail-tag">${esc(tag)}</span>`).join("") : unknown("Tags")}</dd>
        </div>
      </div>
    </section>`;
  }

  // #44: the thread header's nav + first-party-state context. All thread
  // update sites share this so the controls never disagree with the list.
  function threadInput(ticket) {
    const order = sortedVisibleTickets().map((row) => row.id);
    const index = order.indexOf(ticket?.id);
    return {
      ticket, capabilities, title: derivedTitle(ticket), missingTicketId: missingTicketId(),
      nav: {
        position: index,
        total: order.length,
        hasPrev: index > 0,
        hasNext: index >= 0 && index < order.length - 1,
      },
      operatorEmail: String(opts.operatorEmail ?? shop.operatorEmail ?? "").trim().toLowerCase(),
      // #44: the raw override record, so the picker can re-select values the
      // normalized row model flattens away (an "Unassigned" pick reads null).
      ticketState: ticket ? ticketState[ticket.id] || null : null,
    };
  }

  function snapshot() {
    ensureSelection();
    const ticket = selectedTicket();
    const listModel = listTissue.update(listInput());
    const threadModel = threadTissue.update(threadInput(ticket));
    const composerModel = composerTissue.update(composerInput(ticket));
    // #45: the organ hands the rendered ticket-details card to the rail so it
    // leads the rail in every mode (live, snapshot, observed early-return).
    rail.setTicketDetails(ticketDetailsHtml(ticket));
    // #40: collapsed wins over empty — observed tickets never show a customer
    // rail, so the empty check first would make the collapse strip unreachable.
    const railHtml = railCollapsed ? railCollapsedHtml() : !showsCustomerRail(ticket) ? emptyRailHtml() : rail.render();
    const html = `<div class="inbox" data-organ="inbox">
      <a class="skip-link" href="#inbox-thread">Skip to thread.</a>
      <section class="pane pane-list${listCollapsed ? " is-collapsed" : ""}" data-pane="list">${listTissue.render(listModel)}</section>
      <section class="pane pane-thread" id="inbox-thread" data-pane="thread" tabindex="-1">${threadTissue.render(threadModel)}${composerTissue.render(composerModel)}</section>
      <aside class="pane pane-rail${railCollapsed ? " is-collapsed" : ""}" data-pane="rail">${railHtml}</aside>
    </div>${sheetHtml()}`;
    return {
      html,
      panes: { views: false, list: true, thread: true, rail: true },
      listCollapsed,
      railCollapsed,
      ticketDetailsOpen,
      viewId,
      channelId: channelId(),
      statusId: statusId(),
      assigneeId: assigneeId(),
      tagId: tagId(),
      filterConditions: filterConditions.map((condition) => ({...condition})),
      filterMatch,
      savedViews: savedViewList(),
      // #38: the create flow's state — the error names the last refusal, and
      // the selected ticket rides along for tests (selectedId alone cannot
      // tell a local row from a deep-linked id).
      createError,
      ticket: selectedTicket(),
      counts,
      searchQuery,
      searchAllViews,
      selectedId,
      unreadIds: [...unreadIds],
      titles,
      // #44: the selected ticket's effective first-party state — local
      // overrides over observed values — plus which fields are overridden.
      ticketState: selectedTicket() ? {
        status: ticketState[selectedTicket().id]?.status?.value ?? null,
        priority: ticketState[selectedTicket().id]?.priority?.value ?? null,
        assignee: ticketState[selectedTicket().id]?.assignee?.value ?? null,
        overridden: stateOverrides(selectedTicket()),
      } : null,
      bulkSelection: bulkSelectionInput(),
      selectedHasInkBar: Boolean(selectedId) && html.includes(`data-ticket="${selectedId}"`) && html.includes("is-selected"),
      sendDisabled: composerTissue.sendDisabled(composerModel),
      hideSendAndClose: composerTissue.hideSendAndClose(composerModel),
      forbidden: forbiddenControlHits(html),
      rail: rail.snapshot(),
      errors: mailbox.failures().concat(
        Object.entries(rail.snapshot().models)
          .filter(([, model]) => model.error)
          .map(([tissueId, model]) => ({ tissueId, message: model.error })),
      ),
      sent,
      sendError,
      body,
      strip: composerModel.strip,
      summarize: summarizeText,
      macros: composerModel.macros,
      macrosOpen,
      query: composerModel.query,
      selectedMacroId: composerModel.selectedMacroId,
      searchOpen: composerModel.searchOpen,
    };
  }

  // #40: the rail collapses in every mode — the observed path renders the
  // same collapse control the connected rail's toolbar carries.
  function railToolbarHtml() {
    return `<div class="rail-toolbar">
      <span class="rail-heading">Customer details</span>
      <button type="button" class="list-tool-btn" data-rail-collapse title="Collapse customer rail" aria-label="Collapse customer rail">
        <svg class="list-tool-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <path fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" d="M4.25 4.25l7.5 7.5M11.75 4.25l-7.5 7.5"/>
        </svg>
      </button>
    </div>`;
  }

  // #47: the observed-mode customer-details card. Every observed identity
  // field renders with its source and timestamp; a never-observed field is
  // an explicit unknown, never blank, never invented. Conflicts stay visible.
  // Notes and customer type are out of scope for this issue — no editor, no
  // field, only observed identity (AGENTS.md §2(6)).
  function observedCustomerCardHtml(ticket) {
    const context = ticket?.customerContext;
    const identity = context?.source === "canonical_webhook" && !context.conflict && context.status === "observed" ? (context.identity || {}) : {};
    // Task 3: three "Unknown" rows are noise. Observed values keep their
    // rows; the rest collapse into one explicit line — still named, never
    // blank, never invented (#47). A conflicted identity skips the line: the
    // conflict copy above already explains why nothing renders as fact.
    const fields = [["Name", identity.name], ["Email", identity.email], ["Phone", identity.phone], ["Gorgias customer ID", identity.id]];
    const isKnown = (value) => typeof value === "string" && value.trim();
    const rows = fields
      .filter(([, value]) => isKnown(value))
      .map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`)
      .join("");
    const missing = fields.filter(([, value]) => !isKnown(value)).map(([label]) => label);
    const missingLine = !context?.conflict && missing.length
      ? `<p class="customer-missing">Not observed: ${esc(missing.join(", "))}.</p>`
      : "";
    // The same-address history strip: other observed tickets from this
    // address, read-only. The whole loaded snapshot holds them (a closed
    // ticket still counts while the operator is in Open); nothing is
    // invented here. A different address — and a local-only row that was
    // never observed — never appears.
    const address = String(ticket?.fromEmail || "").trim().toLowerCase();
    const others = address ? allRows.filter((r) => r.projectionSource && r.id !== ticket?.id
      && !(r.spam || r.trashed)
      && String(r.fromEmail || "").trim().toLowerCase() === address) : [];
    const history = others.length
      ? `<dl class="customer-history" data-customer-history>
          <dt>Ticket history</dt><dd>
            <p class="customer-history-count">${others.length} ${others.length === 1 ? "ticket" : "tickets"} from this address</p>
            ${others.slice(0, 5).map((r) => `<p class="customer-history-row" data-history-ticket="${esc(r.id)}">${esc(r.subject || r.id)}</p>`).join("")}
          </dd>
        </dl>`
      : "";
    return `<section class="rail-card customer-details" data-customer-details>
      <h2>Customer details</h2>
      <dl class="ticket-detail-fields">
        ${rows}
      </dl>
      ${missingLine}
      ${context?.conflict
        ? `<p class="customer-conflict">Conflicting customer details were observed; identity needs review.</p>`
        : ""}
      <p class="customer-source">${context
        ? `Source: observed Gorgias webhook${context.observedAt ? ` · ${esc(formatWhen(context.observedAt))}` : ""}. This is a snapshot, not a live customer lookup.`
        : "Customer identity was not included in the observed history."}</p>
      ${history}
    </section>`;
  }

  function emptyRailHtml() {
    const ticket = selectedTicket();
    const context = ticket?.customerContext;
    if (ticket?.projectionSource) {
      return `${railToolbarHtml()}${ticketDetailsHtml(ticket)}${observedCustomerCardHtml(ticket)}
      <div class="empty-pane observed-customer">
        <strong>Orders and returns</strong><p>${ticket.shopifyRail?.status === "missing" ? "No matching Shopify customer or order was found." : ticket.shopifyRail?.status === "error" ? "Shopify details could not be refreshed. We will retry automatically." : "Shopify details are awaiting refresh. They will appear here when available."}</p>
      </div>`;
    }
    // cubic: everything below is the never-observed path (no selection,
    // local-only rows, disconnected inboxes) — there is no observed
    // identity to show, so the observed card does not render here.
    return `${railToolbarHtml()}${ticketDetailsHtml(ticket)}<div class="empty-pane"><strong>Customer details</strong><p>${capabilities.customerDetails === false ? "Customer and order lookup is not connected to this inbox." : "Select a conversation to see customer and order details."}</p></div>`;
  }


  async function refreshRail() {
    const ticket = selectedTicket();
    const snapshotRail = shopifyRailSnapshot(ticket);
    if (snapshotRail) {
      rail.loadSnapshot(snapshotRail, ticket.id);
      toEmail = rail.snapshot().models.customer?.record?.defaultEmailAddress?.emailAddress || ticket?.fromEmail || "";
      return;
    }
    if (!ticket || ticket.projectionSource || capabilities.customerDetails === false) {
      toEmail = ticket?.fromEmail || "";
      return;
    }
    await rail.load({
      shop: shopHost,
      customerId: ticket?.customerId,
      orderId: ticket?.orderId,
      ticketId: ticket?.id,
    });
    toEmail = rail.snapshot().models.customer?.record?.defaultEmailAddress?.emailAddress || "";
  }

  async function refreshCapabilities() {
    if (typeof shop.getCapabilities === "function") {
      try {
        const result = await shop.getCapabilities();
        // Only explicit booleans in the known capability vocabulary count.
        for (const key of Object.keys(capabilities)) capabilities[key] = result?.[key] === true;
      } catch { for (const key of Object.keys(capabilities)) capabilities[key] = false; }
    }
  }

  async function mount(root) {
    root.innerHTML = shell();
    const panes = {
      list: root.querySelector('[data-pane="list"]'),
      thread: root.querySelector("[data-slot=thread]"),
      composer: root.querySelector("[data-slot=composer]"),
      rail: root.querySelector('[data-pane="rail"]'),
    };
    await refreshCapabilities();
    await refreshList();
    ensureSelection();
    collapseListForNarrow();
    await refreshThread();
    await refreshRail();
    await refreshComposer();
    await refreshMacros("");
    await refreshWriteGate();
    await refreshBridgeStatus();
    startBridgePoll();

    paintListOnly = () => {
      const scrollTop = panes.list?.querySelector?.(".ticket-list")?.scrollTop || 0;
      const focused = panes.list?.querySelector?.("[data-load-more]") === panes.list?.ownerDocument?.activeElement;
      safeMount(listTissue, panes.list, listInput());
      listTissue.afterPaint?.();
      const scroll = panes.list?.querySelector?.(".ticket-list");
      if (scroll) scroll.scrollTop = scrollTop;
      if (focused) panes.list?.querySelector?.("[data-load-more]")?.focus({preventScroll:true});
    };
    const paint = () => {
      const ticket = selectedTicket();
      // #45: keep the rail's ticket-details card in sync with the selection.
      rail.setTicketDetails(ticketDetailsHtml(ticket));
      panes.list?.classList?.toggle?.("is-collapsed", listCollapsed);
      panes.rail?.classList?.toggle?.("is-collapsed", railCollapsed);
      safeMount(listTissue, panes.list, listInput());
      listTissue.afterPaint?.();
      const threadResult = safeMount(threadTissue, panes.thread, threadInput(ticket));
      safeMount(composerTissue, panes.composer, composerInput(ticket));
      try {
        if (railCollapsed) {
          panes.rail.innerHTML = railCollapsedHtml();
        } else if (!showsCustomerRail(ticket)) {
          panes.rail.innerHTML = emptyRailHtml();
        } else {
          rail.mount(panes.rail);
        }
      } catch (err) {
        panes.rail.innerHTML = `<div class="tissue-error" data-tissue-error="rail">rail unavailable</div>`;
        mailbox.publish(MAILBOX_TOPICS.TISSUE_ERROR, { tissueId: "rail", message: String(err?.message || err) });
      }
      if (!threadResult.ok) {
        mailbox.publish(MAILBOX_TOPICS.TISSUE_ERROR, { tissueId: "thread", message: threadResult.error });
      }
      const host = root.querySelector("[data-gate-host]");
      if (host) host.innerHTML = sheetHtml();
    };

    const showActionError = error => {
      sendError = String(error?.message || "Action failed. No change was confirmed.");
      paint();
    };
    mailbox.subscribe(MAILBOX_TOPICS.LIST_COLLAPSED, ({ collapsed }) => {
      listCollapsed = Boolean(collapsed);
      persistCollapseState("list");
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.RAIL_COLLAPSED, ({ collapsed }) => {
      railCollapsed = Boolean(collapsed);
      persistCollapseState("rail");
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.HISTORY_REFRESH, () => {
      refreshHistory();
    });
    mailbox.subscribe(MAILBOX_TOPICS.VIEW_SELECTED, ({ viewId: next }) => {
      viewId = next;
      filterConditions = [];
      filterMatch = "all";
      setSearchQuery("");
      searchAllViews = false;
      resetUiState();
      refreshList().then(() => {
        ensureSelection();
        syncUrl();
        return refreshThread();
      }).then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(paint);
    });
    // #37: the tissue publishes raw keystrokes; the organ owns the bound,
    // the match and the URL.
    mailbox.subscribe(MAILBOX_TOPICS.LIST_SEARCHED, ({ query, allViews } = {}) => {
      selectSearch(query, {allViews: Boolean(allViews)});
    });
    // #36: the quick menu's four picks all land in the condition rows.
    for (const [topic, field, normalize] of [
      [MAILBOX_TOPICS.CHANNEL_SELECTED, "channel", normalizeChannel],
      [MAILBOX_TOPICS.STATUS_SELECTED, "status", normalizeStatus],
      [MAILBOX_TOPICS.ASSIGNEE_SELECTED, "assignee", normalizeAssignee],
      [MAILBOX_TOPICS.TAG_SELECTED, "tag", normalizeTag],
    ]) {
      mailbox.subscribe(topic, (msg = {}) => {
        pickFacet(field, normalize(msg[`${field}Id`] ?? ""));
        resetUiState();
        ensureSelection();
        // cubic: the quick pick restamps the URL like every other filter
        // path, or the address bar's f drifts from the chips.
        syncUrl({push: false});
        refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(paint);
      });
    }
    // #36: the builder publishes committed edits; the organ applies the
    // field-by-field replace and restamps the URL.
    mailbox.subscribe(MAILBOX_TOPICS.FILTER_CHANGED, ({conditions, match} = {}) => {
      applyFilterEdit(conditions, match);
    });
    mailbox.subscribe(MAILBOX_TOPICS.FILTER_VIEW_SAVE, ({name, shared} = {}) => {
      saveView(name, shared);
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.FILTER_VIEW_APPLY, ({id} = {}) => {
      Promise.resolve(applySavedView(id)).then(paint);
    });
    mailbox.subscribe(MAILBOX_TOPICS.FILTER_VIEW_DELETE, ({id} = {}) => {
      removeView(id);
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.LIST_SELECTED, ({ ticketId }) => {
      resetUiState(ticketId);
      collapseListForNarrow();
      markRead(ticketId);
      syncUrl();
      refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(paint);
    });
    mailbox.subscribe(MAILBOX_TOPICS.BULK_TOGGLE, ({ ticketId, shiftKey }) => {
      // #39: checkbox toggle never opens the thread; toggleSelect repaints.
      toggleSelect(ticketId, {shiftKey});
    });
    mailbox.subscribe(MAILBOX_TOPICS.SORT_SELECTED, ({ sortId: next }) => {
      // #39 review: the tissue asks for a display order; the organ owns it.
      sortId = next === "newest" || next === "oldest" ? next : "default";
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.COMPOSER_BODY, ({ text }) => {
      body = text;
    });
    mailbox.subscribe(MAILBOX_TOPICS.COMPOSER_MACROS, ({ open }) => {
      macrosOpen = Boolean(open);
    });
    mailbox.subscribe(MAILBOX_TOPICS.COMPOSER_INSERT, (payload) => {
      if (payload?.macroId) {
        selectedMacroId = "";
        macrosOpen = false;
        macroQuery = "";
      }
      if (payload?.text) body = payload.text;
      strip = "";
      discarded = true;
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.COMPOSER_DISCARD, () => {
      strip = "";
      discarded = true;
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.COMPOSER_REGENERATE, (payload) => {
      discarded = false;
      const ticket = selectedTicket();
      const requestTicketId = ticket?.id || null;
      lastRewriteInstruction = String((payload && payload.instruction) || "");
      if (typeof parentRewrite !== "function") {
        loadDraft(ticket).then((text) => {
          if (selectedId !== requestTicketId) return;
          strip = text;
          paint();
        });
        return;
      }
      const instruction = lastRewriteInstruction.trim();
      if (!instruction) {
        rewriteBusy = false;
        rewriteError = "Type an instruction first.";
        composerTissue.update(composerInput(selectedTicket()));
        paint();
        return;
      }
      rewriteBusy = true;
      rewriteError = "";
      composerTissue.update(composerInput(selectedTicket()));
      paint();
      parentRewrite(requestTicketId, instruction).then((text) => {
        if (selectedId !== requestTicketId) return;
        strip = String(text || "");
        rewriteBusy = false;
        rewriteError = "";
        composerTissue.update(composerInput(selectedTicket()));
        paint();
      }).catch((err) => {
        if (selectedId !== requestTicketId) return;
        rewriteBusy = false;
        rewriteError = String((err && err.message) || err || "Rewrite failed.");
        composerTissue.update(composerInput(selectedTicket()));
        paint();
      });
    });
    mailbox.subscribe(MAILBOX_TOPICS.COMPOSER_NOTE, (payload) => {
      const ticket = selectedTicket();
      const requestTicketId = ticket?.id || null;
      const text = String((payload && payload.text) || "").trim();
      noteConfirm = false;
      if (typeof parentNote !== "function") {
        noteBusy = false;
        noteError = "Notes are not connected.";
        noteInfo = "";
        composerTissue.update(composerInput(selectedTicket()));
        paint();
        return;
      }
      if (!text) {
        noteBusy = false;
        noteError = "Nothing to post.";
        noteInfo = "";
        composerTissue.update(composerInput(selectedTicket()));
        paint();
        return;
      }
      noteBusy = true;
      noteError = "";
      noteInfo = "";
      sendInfo = "";
      sendError = "";
      composerTissue.update(composerInput(selectedTicket()));
      paint();
      parentNote(requestTicketId, text).then((result) => {
        if (selectedId !== requestTicketId) return;
        noteBusy = false;
        noteError = "";
        noteInfo = result && result.dryRun ? "Dry run — validated, nothing posted." : "Posted as an internal note in Gorgias.";
        composerTissue.update(composerInput(selectedTicket()));
        paint();
      }).catch((err) => {
        if (selectedId !== requestTicketId) return;
        noteBusy = false;
        noteError = String((err && err.message) || err || "Note failed.");
        noteInfo = "";
        composerTissue.update(composerInput(selectedTicket()));
        paint();
      });
    });
    mailbox.subscribe(MAILBOX_TOPICS.COMPOSER_SEND_CONFIRMED, (payload) => {
      const ticket = selectedTicket();
      const requestTicketId = ticket?.id || null;
      const text = String((payload && payload.text) || "").trim();
      const approveLearning = !!(payload && payload.approveLearning);
      const closeRequested = !!(payload && payload.close);
      if (typeof parentSend !== "function") {
        sendBusy = false;
        sendError = "Sending is not connected.";
        sendInfo = "";
        composerTissue.update(composerInput(selectedTicket()));
        paint();
        return;
      }
      if (!text) {
        sendBusy = false;
        sendError = "Write the reply first.";
        sendInfo = "";
        composerTissue.update(composerInput(selectedTicket()));
        paint();
        return;
      }
      sendBusy = true;
      sendError = "";
      sendInfo = "";
      noteInfo = "";
      noteError = "";
      composerTissue.update(composerInput(selectedTicket()));
      paint();
      parentSend(requestTicketId, text, approveLearning).then((result) => {
        if (selectedId !== requestTicketId) return;
        sendBusy = false;
        sendError = "";
        if (result && result.dryRun) {
          sendInfo = "Dry run — validated, nothing sent.";
          composerTissue.update(composerInput(selectedTicket()));
          paint();
          return;
        }
        sendInfo = "Sent to the customer.";
        composerTissue.update(composerInput(selectedTicket()));
        if (closeRequested && result && result.deliveryStatus === "sent") {
          setTicketStateLocal(requestTicketId, { status: "closed" });
          applyTicketState().then(afterUi);
          return;
        }
        paint();
      }).catch((err) => {
        if (selectedId !== requestTicketId) return;
        sendBusy = false;
        sendError = String((err && err.message) || err || "Send failed.");
        sendInfo = "";
        composerTissue.update(composerInput(selectedTicket()));
        paint();
      });
    });
    mailbox.subscribe(MAILBOX_TOPICS.COMPOSER_SUMMARIZE, ({ ticketId }) => {
      const ticket = selectedTicket() || listRows.find((item) => item.id === ticketId) || null;
      const requestTicketId = ticket?.id || selectedId || null;
      loadSummary(ticket).then((text) => {
        if (selectedId !== requestTicketId) return;
        summarizeText = text;
        paint();
      });
    });
    mailbox.subscribe(MAILBOX_TOPICS.THREAD_ESCALATE, ({ ticketId, reason }) => {
      if (ticketId && ticketId !== selectedId) selectedId = ticketId;
      escalateSelected(reason).then(() => refreshThread()).then(paint).catch(showActionError);
    });
    mailbox.subscribe(MAILBOX_TOPICS.THREAD_RENAME, ({ ticketId, title }) => {
      // #41: rename is first-party only — write the browser store, repaint.
      if (ticketId) writeTitle(ticketId, title);
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.THREAD_COPY_LINK, ({ ticketId }) => {
      // #42: boot injects the clipboard; the organ owns the URL shape because
      // it owns the active view. Same-origin relative link only.
      const link = ticketLink(ticketId);
      if (link) opts.clipboard?.writeText?.(link);
    });
    mailbox.subscribe(MAILBOX_TOPICS.THREAD_STEP, ({ delta }) => {
      // #44: prev/next walk the current filtered list. Display-only nav.
      stepTicketSelection(Number(delta) || 0).then(afterUi);
    });
    mailbox.subscribe(MAILBOX_TOPICS.THREAD_STATE, ({ ticketId, field, value }) => {
      // #44: first-party detail controls — browser store only, never Gorgias.
      if (!ticketId || !STATE_FIELDS.includes(field)) return;
      const changes = {};
      changes[field] = value === "" ? null : value;
      setTicketStateLocal(ticketId, changes);
      applyTicketState().then(afterUi);
    });
    mailbox.subscribe(MAILBOX_TOPICS.THREAD_MARK_UNREAD, ({ ticketId }) => {
      // #44: first-party read state, same store the list already uses.
      if (!ticketId) return;
      if (!unreadIds.has(ticketId)) {
        unreadIds.add(ticketId);
        readIds.delete(ticketId);
        persistRead();
      }
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.WRITE_GATE_OPEN, () => {
      closeAllGates();
      writeGateOpen = true;
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.WRITE_GATE_CLOSE, () => {
      writeGateOpen = false;
      paint();
    });
    // #38: the New ticket sheet's open/close — the toolbar button and the
    // sheet's own Close both publish; the organ owns the state.
    mailbox.subscribe(MAILBOX_TOPICS.CREATE_TICKET_OPEN, () => {
      openCreateSheetLocal();
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.CREATE_TICKET_CLOSE, () => {
      closeCreateSheetLocal();
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.CUSTOMER_JOIN_GATE_OPEN, () => {
      closeAllGates();
      customerJoinGateOpen = true;
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.CUSTOMER_JOIN_GATE_CLOSE, () => {
      customerJoinGateOpen = false;
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.ORDER_LINK_GATE_OPEN, () => {
      closeAllGates();
      orderLinkGateOpen = true;
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.ORDER_LINK_GATE_CLOSE, () => {
      orderLinkGateOpen = false;
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.PRIVACY_GATE_OPEN, () => {
      closeAllGates();
      privacyGateOpen = true;
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.PRIVACY_GATE_CLOSE, () => {
      privacyGateOpen = false;
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.PRIVACY_HANDLED, ({ ticketId }) => {
      if (ticketId && ticketId !== selectedId) selectedId = ticketId;
      markPrivacyHandled().then(() => refreshRail()).then(paint).catch(showActionError);
    });
    mailbox.subscribe(MAILBOX_TOPICS.MARKETING_GATE_OPEN, () => {
      closeAllGates();
      marketingGateOpen = true;
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.MARKETING_GATE_CLOSE, () => {
      marketingGateOpen = false;
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.MARKETING_HANDLED, ({ ticketId }) => {
      if (ticketId && ticketId !== selectedId) selectedId = ticketId;
      markUnsubscribed().then(() => refreshRail()).then(paint).catch(showActionError);
    });
    mailbox.subscribe(MAILBOX_TOPICS.BUG_HANDLED, ({ ticketId }) => {
      if (ticketId && ticketId !== selectedId) selectedId = ticketId;
      markBugHandled().then(() => refreshRail()).then(paint).catch(showActionError);
    });
    root.onclick = (event) => {
      if (event.target.closest("[data-rail-expand]")) {
        railCollapsed = false;
        persistCollapseState("rail");
        paint();
        return;
      }
      // #40: the observed rail never mounts the rail tissue, so the organ
      // carries its toolbar's collapse click itself.
      if (event.target.closest("[data-rail-collapse]")) {
        railCollapsed = true;
        persistCollapseState("rail");
        paint();
        return;
      }
      // The ticket-details toggle is organ-owned: the rail tissue only
      // injects the card's HTML (and skips the key in its own handler), so
      // this click flips the organ flag and repaints every rail path.
      if (event.target.closest('[data-toggle="ticket-details"]')) {
        ticketDetailsOpen = !ticketDetailsOpen;
        paint();
        return;
      }
      if (event.target.closest("[data-privacy-handled]")) {
        const ticket = selectedTicket();
        mailbox.publish(MAILBOX_TOPICS.PRIVACY_HANDLED, { ticketId: ticket?.id });
        return;
      }
      if (event.target.closest("[data-unsubscribe-handled]")) {
        const ticket = selectedTicket();
        mailbox.publish(MAILBOX_TOPICS.MARKETING_HANDLED, { ticketId: ticket?.id });
        return;
      }
      if (event.target.closest("[data-bug-handled]")) {
        const ticket = selectedTicket();
        mailbox.publish(MAILBOX_TOPICS.BUG_HANDLED, { ticketId: ticket?.id });
        return;
      }
      if (event.target.closest("[data-gate-dismiss]") || event.target.closest("[data-gate-sheet]") === event.target) {
        closeAllGates();
        paint();
      }
      // #38: the sheet's Close button and the backdrop click dismiss; the
      // form's confirm click reads the named inputs directly (click fires
      // before submit, and the organ's own markup means the form node is
      // reachable here without a second listener).
      if (event.target.closest("[data-create-sheet-dismiss]")
        || event.target.closest("[data-create-sheet-backdrop]") === event.target) {
        closeCreateSheetLocal();
        paint();
      }
      const createConfirm = event.target.closest("[data-create-confirm]");
      if (createConfirm) {
        const form = createConfirm.closest("[data-create-sheet]");
        const fields = {
          customerName: form?.elements?.customerName?.value ?? "",
          fromEmail: form?.elements?.fromEmail?.value ?? "",
          subject: form?.elements?.subject?.value ?? "",
          channel: form?.elements?.channel?.value ?? "email",
          body: form?.elements?.body?.value ?? "",
        };
        createLocalTicketLocal(fields).then(paint);
      }
    };
    mailbox.subscribe(MAILBOX_TOPICS.COMPOSER_SEND, () => {
      disconnectSend();
    });

    paintMounted = paint;
    paint();
    // #42: stamp the landing entry with the resolved selection so reload,
    // copy and bookmark all carry the deep link.
    syncUrl({push: false});
    return snapshot();
  }

  return {
    mailbox,
    shop,
    mount,
    snapshot,
    loadMore,
    toggleSelect,
    clearSelection,
    bulkMarkRead,
    bulkMarkUnread,
    bulkExport,
    bulkEscalate,
    // #41: first-party rename. Persists in the browser store only; the
    // observed Gorgias subject is never touched.
    async renameTicket(ticketId, raw) {
      writeTitle(ticketId, raw);
      return afterUi();
    },
    // #42: the console deep link for a ticket, carrying the active view.
    ticketLink,
    // #42: back/forward replay — restores both fields, never pushes.
    replayEntry,
    // #39 review: display-order control. Sorting does not touch the shop; the
    // selection stays intact because the ids still render, only reordered.
    selectSort(next) {
      sortId = next === "newest" || next === "oldest" ? next : "default";
      return afterUi();
    },
    selectView(next) {
      viewId = next;
      filterConditions = [];
      filterMatch = "all";
      // #37: a new view is a new partition — the search does not follow.
      setSearchQuery("");
      searchAllViews = false;
      resetUiState();
      return refreshList().then(() => {
        ensureSelection();
        syncUrl();
        return refreshThread();
      }).then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(afterUi);
    },
    // #37: search the loaded snapshot. Scoped by default; allViews is the
    // explicit "search every view" escalation.
    selectSearch(query, {allViews = false} = {}) {
      setSearchQuery(query);
      searchAllViews = Boolean(allViews) && Boolean(searchQuery);
      resetUiState();
      ensureSelection();
      // Live typing restamps the current entry; only committed navigation
      // pushes, so Back returns to the previous inbox state, not the last
      // keystroke prefix.
      syncUrl({push: false});
      return refreshThread().then(refreshRail).then(refreshComposer).then(afterUi);
    },
    selectChannel(next) { return pickFacetAndRefresh("channel", normalizeChannel(next)); },
    selectStatus(next) { return pickFacetAndRefresh("status", normalizeStatus(next)); },
    selectAssignee(next) { return pickFacetAndRefresh("assignee", normalizeAssignee(next)); },
    selectTag(next) { return pickFacetAndRefresh("tag", normalizeTag(next)); },
    // Task 2: the freshness banner's Refresh. Re-reads without clearing the
    // operator's reply, selection, or view.
    refreshHistory() { return refreshHistory(); },
    // #36: the builder's committed state. Rows for fields the operator
    // touched replace that field's conditions; untouched fields keep theirs.
    setFilterConditions(next, {match} = {}) {
      // The builder publishes the complete row list, so this is a replace —
      // merging by field would resurrect conditions the operator removed.
      return applyFilterEdit(next, match);
    },
    clearFilters() {
      filterConditions = [];
      filterMatch = "all";
      resetUiState();
      ensureSelection();
      syncUrl({push: false});
      return afterUi();
    },
    removeFilterCondition(index) {
      filterConditions = filterConditions.filter((_, at) => at !== Number(index));
      if (!filterConditions.length) filterMatch = "all";
      resetUiState();
      ensureSelection();
      syncUrl({push: false});
      return afterUi();
    },
    saveFilterView({name, shared = false} = {}) {
      saveView(name, shared);
      return afterUi();
    },
    applyFilterView(id) {
      applySavedView(id);
      return afterUi();
    },
    deleteFilterView(id) {
      removeView(id);
      return afterUi();
    },
    selectTicket(id, {fromHistory = false} = {}) {
      // The operator picked a real row — the deep-link/replay protection
      // must not hold a dead id over their choice.
      if (id !== protectedTicketId) protectedTicketId = null;
      resetUiState(id);
      markRead(id);
      // #42: operator-driven selection pushes a history entry; a popstate
      // replay replaces so back/forward does not grow the stack.
      syncUrl({push: !fromHistory});
      return refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(afterUi);
    },
    // #44: previous/next walk the current filtered list in its rendered
    // order and stop at the ends (explicit, no wrap). They route through
    // selectTicket so composer/draft state follows the new ticket.
    stepTicket(delta) {
      return stepTicketSelection(Number(delta) || 0).then(afterUi);
    },
    // #44: first-party ticket state. Only the named fields are writable;
    // each change persists in the browser store with who/when. A null value
    // clears the override and falls back to the observed value.
    async setTicketState(ticketId, changes) {
      setTicketStateLocal(ticketId, changes || {});
      return applyTicketState().then(afterUi);
    },
    collapseList(collapsed = true) {
      listCollapsed = Boolean(collapsed);
      persistCollapseState("list");
      return afterUi();
    },
    collapseRail(collapsed = true) {
      railCollapsed = Boolean(collapsed);
      persistCollapseState("rail");
      return afterUi();
    },
    toggleRail(key) {
      return rail.toggle(key);
    },
    // The ticket-details card starts toggled off; the operator opens it per
    // ticket. Session-local, reset on context switch — never persisted.
    toggleTicketDetails(open = null) {
      ticketDetailsOpen = open == null ? !ticketDetailsOpen : Boolean(open);
      return afterUi();
    },
    setBody(text) {
      body = text;
      composerTissue.update(composerInput(selectedTicket()));
      afterUi();
    },
    setParentBridge(hooks) {
      parentRewrite = (hooks && typeof hooks.rewrite === "function") ? hooks.rewrite : null;
      parentNote = (hooks && typeof hooks.note === "function") ? hooks.note : null;
      parentSend = (hooks && typeof hooks.send === "function") ? hooks.send : null;
      rewriteBusy = false;
      rewriteError = "";
      noteConfirm = false;
      noteBusy = false;
      noteError = "";
      noteInfo = "";
      sendBusy = false;
      sendError = "";
      sendInfo = "";
      composerTissue.update(composerInput(selectedTicket()));
      return afterUi();
    },
    attemptSend(close = false) {
      mailbox.publish(MAILBOX_TOPICS.COMPOSER_SEND, { text: body, close: Boolean(close) });
      disconnectSend();
      return snapshot();
    },
    discardStrip() {
      discarded = true;
      strip = "";
      composerTissue.update(composerInput(selectedTicket()));
      return afterUi();
    },
    insertDraft() {
      const text = effectiveStrip(selectedTicket()).text;
      if (text) body = body ? `${body}\n\n${text}` : text;
      strip = "";
      discarded = true;
      composerTissue.update(composerInput(selectedTicket()));
      return afterUi();
    },
    async regenerateDraft() {
      discarded = false;
      const ticket = selectedTicket();
      const requestTicketId = ticket?.id || null;
      const text = await loadDraft(ticket);
      if (selectedId !== requestTicketId) return afterUi();
      strip = text;
      composerTissue.update(composerInput(selectedTicket()));
      return afterUi();
    },
    openMacros() {
      macrosOpen = true;
      composerTissue.update(composerInput(selectedTicket()));
      return afterUi();
    },
    async searchMacros(query = "") {
      macrosOpen = true;
      await refreshMacros(query);
      composerTissue.update(composerInput(selectedTicket()));
      return afterUi();
    },
    async applyMacro(macroId, mode = "replace") {
      selectedMacroId = macroId;
      macrosOpen = false;
      macroQuery = "";
      let text = "";
      if (typeof shop.applyMacro === "function") {
        try {
          const result = await shop.applyMacro({
            macroId,
            mode,
            currentBody: body,
          });
          text = result?.text || "";
        } catch {
          text = "";
        }
      }
      if (!text) {
        const macro = macros.find((item) => item.id === macroId) || fixtureMacros.find((item) => item.id === macroId);
        if (macro) {
          text = mode === "append" && body.trim() ? `${body.trimEnd()}\n\n${macro.body}` : macro.body;
        }
      }
      if (text) body = text;
      composerTissue.update(composerInput(selectedTicket()));
      return afterUi();
    },
    async escalate(reason) {
      const ticket = await escalateSelected(reason);
      if (ticket && !pinnedCatalog) await refreshThread();
      composerTissue.update(composerInput(selectedTicket()));
      threadTissue.update(threadInput(selectedTicket()));
      return snapshot();
    },
    openWriteGate() {
      closeAllGates();
      writeGateOpen = true;
      return snapshot();
    },
    closeWriteGate() {
      writeGateOpen = false;
      return snapshot();
    },
    openCustomerJoinGate() {
      closeAllGates();
      customerJoinGateOpen = true;
      return snapshot();
    },
    closeCustomerJoinGate() {
      customerJoinGateOpen = false;
      return snapshot();
    },
    openOrderLinkGate() {
      closeAllGates();
      orderLinkGateOpen = true;
      return snapshot();
    },
    closeOrderLinkGate() {
      orderLinkGateOpen = false;
      return snapshot();
    },
    openPrivacyGate() {
      closeAllGates();
      privacyGateOpen = true;
      return snapshot();
    },
    closePrivacyGate() {
      privacyGateOpen = false;
      return snapshot();
    },
    openMarketingGate() {
      closeAllGates();
      marketingGateOpen = true;
      return snapshot();
    },
    closeMarketingGate() {
      marketingGateOpen = false;
      return snapshot();
    },
    // #38: the New ticket sheet + create. Local-only (AGENTS.md §2(7)):
    // browser-store persistence, never a Gorgias write, never a customer
    // notification. The capability gate refuses the flow when off.
    openCreateSheet() {
      return openCreateSheetLocal();
    },
    closeCreateSheet() {
      return closeCreateSheetLocal();
    },
    createLocalTicket(fields) {
      return createLocalTicketLocal(fields);
    },
    async markPrivacyHandled() {
      const ticket = await markPrivacyHandled();
      if (ticket && !pinnedCatalog) await refreshThread();
      await refreshRail();
      return snapshot();
    },
    async markUnsubscribed() {
      const ticket = await markUnsubscribed();
      if (ticket && !pinnedCatalog) await refreshThread();
      await refreshRail();
      return snapshot();
    },
    async markBugHandled() {
      const ticket = await markBugHandled();
      if (ticket && !pinnedCatalog) await refreshThread();
      await refreshRail();
      return snapshot();
    },
    async requestSummarize() {
      const ticket = selectedTicket();
      summarizeText = await loadSummary(ticket);
      composerTissue.update(composerInput(ticket));
      return afterUi();
    },
    async ready() {
      await refreshCapabilities();
      await refreshList();
      ensureSelection();
      collapseListForNarrow();
      await refreshThread();
      await refreshRail();
      await refreshComposer();
      await refreshMacros(macroQuery);
      await refreshWriteGate();
      // #42: stamp the landing entry with the resolved selection (replace,
      // not push — reload keeps one entry).
      syncUrl({push: false});
      return snapshot();
    },
    async ingestEmail(args) {
      if (typeof shop.ingestEmail !== "function") return null;
      const result = await shop.ingestEmail(args);
      await refreshList();
      if (result?.id) {
        // The operator just created/ingested this ticket — a real row now.
        protectedTicketId = null;
        selectedId = result.id;
        unreadIds.add(result.id);
      }
      await refreshThread();
      await refreshRail();
      await refreshComposer();
      return result;
    },
    async ingestChat(args) {
      if (typeof shop.ingestChat !== "function") return null;
      const result = await shop.ingestChat(args);
      await refreshList();
      if (result?.id) {
        protectedTicketId = null;
        selectedId = result.id;
        unreadIds.add(result.id);
      }
      await refreshThread();
      await refreshRail();
      await refreshComposer();
      return result;
    },
    async pullMailbox(args = {}) {
      if (typeof shop.pullMailbox !== "function") return null;
      const result = await shop.pullMailbox(args);
      await refreshList();
      const first = Array.isArray(result?.ingested) ? result.ingested[0] : null;
      if (first?.id) {
        protectedTicketId = null;
        selectedId = first.id;
        unreadIds.add(first.id);
      }
      await refreshThread();
      await refreshRail();
      await refreshComposer();
      return result;
    },
  };
}
