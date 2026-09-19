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
    (push ? history.push : history.replace)?.({ticket: selectedId, view: viewId, q: searchQuery});
  }
  // #42: back/forward replays both URL fields without pushing — the organ
  // owns the view, so a stale view must never be written back over the
  // popped entry. A popped entry with no ticket falls back to the first
  // visible row (the thread pane must not hang empty) and the replace
  // restamps the URL with what actually renders, so the bar and the UI
  // agree.
  async function replayEntry({ticket, view, q} = {}) {
    const generation = ++replayGeneration;
    const stale = () => generation !== replayGeneration;
    const nextView = availableViews.some((candidate) => candidate.id === view) ? view : "all";
    const changedView = nextView !== viewId;
    viewId = nextView;
    // A replayed entry owns the whole URL, so dead facet filters must not
    // survive into the restored view — selectView clears them and so does
    // the back button, or the popped ticket could be filtered out of its
    // own restored rows.
    channelId = "";
    statusId = "";
    assigneeId = "";
    tagId = "";
    // A popped entry owns its query too: no q in the URL means no search,
    // exactly like a dead facet. A popped q restores the exact search. The
    // URL never encodes the escalation, so every replay de-escalates back
    // to the scoped search.
    setSearchQuery(q);
    searchAllViews = false;
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
  let capabilities = { ...(shop.capabilities || {}) };
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
  let channelId = "";
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
  let statusId = "";
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
  let assigneeId = "";
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
  let tagId = "";
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
  let listCollapsed = false;
  let railCollapsed = false;
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
  let listError = "";
  let projectionNotice = "";
  let loadingMore = false;
  let moreError = "";
  let paintListOnly = null;
  let listRows = pinnedCatalog ? pinnedCatalog.filter((ticket) => ticketInView(ticket, viewId)) : [];
  // #37: the unfiltered loaded snapshot — the search-every-view escalation
  // matches against this, not the view-partitioned listRows.
  let allRows = pinnedCatalog || [];
  let selected = pinnedCatalog?.find((ticket) => ticket.id === selectedId) || null;
  let counts = pinnedCatalog ? viewCounts(pinnedCatalog) : viewCounts(fixtureTickets);
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
      (!channelId || normalizeChannel(ticket?.channel) === channelId) &&
      (!statusId || normalizeStatus(ticket?.status) === statusId) &&
      assigneeMatches(ticket, assigneeId) &&
      (!tagId || ticketTags(ticket).includes(tagId)) &&
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
    // #39: view/filter changes clear the multi-select — ids may no longer be
    // visible, so a stale selection would act on hidden rows.
    clearBulk();
  }

  async function refreshList() {
    listError = "";
    if (pinnedCatalog) {
      listRows = pinnedCatalog.filter((ticket) => ticketInView(ticket, viewId));
      reconcileBulk();
      counts = viewCounts(pinnedCatalog);
      return;
    }
    if (typeof shop.listTickets === "function") {
      try {
        if (shop.observedHistory) {
          const rows = (await readObservedTickets(shop)).map(withOperatorAssignee);
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
          counts.all = (shop.projection?.ticketCount ?? rows.length) - flaggedInSnapshot;
          projectionNotice = shop.projection?.stale
            ? "Observed history is stale; refresh is delayed."
            : "Observed history · last 90 days. Status and assignment are shown when the latest observed webhook carried them; otherwise unknown.";
          return;
        }
        const [rows, ...viewRows] = await Promise.all([
          shop.listTickets({ view: viewId, limit: 50 }),
          ...availableViews.map((view) => shop.listTickets({ view: view.id, limit: 100 })),
        ]);
        if (Array.isArray(rows)) {
          listRows = rows;
          // #37: the union of the loaded per-view pages is the escalation's
          // snapshot on non-observed shops — there is no single unfiltered
          // list to hold.
          const seen = new Map();
          for (const batch of [rows, ...viewRows]) {
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
        return;
      } catch {
        listError = "Could not load tickets. Refresh to try again.";
        if (shop.observedHistory) {
          if (listRows.length) projectionNotice = "Showing previously loaded history. Refresh failed; these tickets may be stale. Refresh to try again.";
          return;
        }
      }
    }
    listRows = fixtureTickets.filter((ticket) => ticketInView(ticket, viewId));
    allRows = fixtureTickets;
    reconcileBulk();
    counts = viewCounts(fixtureTickets);
  }

  async function refreshThread() {
    const id = selectedId;
    if (!id) {
      selected = null;
      return;
    }
    if (pinnedCatalog) {
      selected = pinnedCatalog.find((ticket) => ticket.id === id) || null;
      return;
    }
    if (typeof shop.getTicket === "function") {
      try {
        const ticket = await shop.getTicket({ ticketId: id });
        // #42: a back/forward replay may have moved on while this fetch was
        // in flight — a stale thread must never overwrite the newer replay's.
        if (selectedId !== id) return;
        if (ticket) {
          selected = shop.observedHistory ? withOperatorAssignee(ticket) : ticket;
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

  function railCollapsedHtml() {
    return `<div class="pane-inner">
      <button type="button" class="rail-expand-btn" data-rail-expand aria-label="Expand customer rail" title="Show customer rail">
        ${RAIL_EXPAND_ICON}
        <span class="rail-expand-label">Customer</span>
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
          try { selected = withOperatorAssignee(await shop.getTicket({ticketId:selectedId})); } catch { if (selected) selected = {...selected,historyUnavailable:true}; }
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

  function composerInput(ticket) {
    return {
      capabilities,
      ticket: withRecipient(ticket, toEmail),
      draft: discarded ? "" : strip,
      summarize: summarizeText,
      macros,
      body,
      strip: discarded ? "" : strip,
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
      listRows = rows;
      allRows = rows;
      reconcileBulk();
      const observed = viewCounts(rows);
      const flaggedInSnapshot = (shop.projection?.spamCount ?? observed.spam)
        + (shop.projection?.trashCount ?? observed.trash)
        - (shop.projection?.flaggedOverlap ?? Math.min(observed.spam, observed.trash));
      counts = {
        ...observed,
        all: (shop.projection?.ticketCount ?? rows.length) - flaggedInSnapshot,
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
      notice: projectionNotice,
      pagination,
      selectedTicketId: selectedId,
      views: availableViews,
      counts,
      selectedViewId: viewId,
      channels: channelFacets(),
      selectedChannelId: channelId,
      statuses: statusFacets(),
      selectedStatusId: statusId,
      assignees: assigneeFacets(),
      selectedAssigneeId: assigneeId,
      tags: tagFacets(),
      selectedTagId: tagId,
      collapsed: listCollapsed,
      unreadIds: [...unreadIds],
      sortId,
      bulkSelection: bulkSelectionInput(),
      searchQuery,
      searchAllViews,
      searchBounded,
      searchResults,
    };
  }

  function shopifyRailSnapshot(ticket) {
    const snapshot = ticket?.shopifyRail;
    if (!snapshot || typeof snapshot !== "object") return null;
    return snapshot.customer || snapshot.order ? snapshot : null;
  }

  function showsCustomerRail(ticket) {
    if (!ticket) return false;
    if (shopifyRailSnapshot(ticket)) return true;
    if (ticket.projectionSource || capabilities.customerDetails === false) return false;
    return true;
  }

  function snapshot() {
    ensureSelection();
    const ticket = selectedTicket();
    const listModel = listTissue.update(listInput());
    const threadModel = threadTissue.update({ ticket, capabilities, title: derivedTitle(ticket), missingTicketId: missingTicketId() });
    const composerModel = composerTissue.update(composerInput(ticket));
    const railHtml = !showsCustomerRail(ticket) ? emptyRailHtml() : railCollapsed ? railCollapsedHtml() : rail.render();
    const html = `<div class="inbox" data-organ="inbox">
      <a class="skip-link" href="#inbox-thread">Skip to thread.</a>
      <section class="pane pane-list${listCollapsed ? " is-collapsed" : ""}" data-pane="list">${listTissue.render(listModel)}</section>
      <section class="pane pane-thread" id="inbox-thread" data-pane="thread" tabindex="-1">${threadTissue.render(threadModel)}${composerTissue.render(composerModel)}</section>
      <aside class="pane pane-rail${railCollapsed ? " is-collapsed" : ""}" data-pane="rail">${railHtml}</aside>
    </div>${gateSheetHtml()}`;
    return {
      html,
      panes: { views: false, list: true, thread: true, rail: true },
      listCollapsed,
      railCollapsed,
      viewId,
      channelId,
      statusId,
      assigneeId,
      tagId,
      searchQuery,
      searchAllViews,
      selectedId,
      unreadIds: [...unreadIds],
      titles,
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

  function emptyRailHtml() {
    const ticket = selectedTicket();
    const context = ticket?.customerContext;
    if (ticket?.projectionSource) {
      const identity = context?.source === "canonical_webhook" && !context.conflict && context.status === "observed" ? context.identity : null;
      const fields = [["Name", identity?.name], ["Email", identity?.email], ["Phone", identity?.phone], ["Gorgias customer ID", identity?.id]]
        .filter(([, value]) => typeof value === "string" && value.trim())
        .map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("");
      return `<div class="empty-pane observed-customer"><strong>Customer details</strong>
        ${fields ? `<dl>${fields}</dl>` : `<p>${context?.conflict ? "Conflicting customer details were observed; identity needs review." : "Customer identity was not included in the observed history."}</p>`}
        <p class="customer-source">Source: observed Gorgias webhook${context?.observedAt ? ` · ${esc(formatWhen(context.observedAt))}` : ""}. ${ticket.projection?.stale ? "Snapshot is stale." : "This is a snapshot, not a live customer lookup."}</p>
        <strong>Orders and returns</strong><p>${ticket.shopifyRail?.status === "missing" ? "No matching Shopify customer or order was found." : ticket.shopifyRail?.status === "error" ? "Shopify details could not be refreshed. We will retry automatically." : "Shopify details are awaiting refresh. They will appear here when available."}</p>
      </div>`;
    }
    return `<div class="empty-pane"><strong>Customer details</strong><p>${capabilities.customerDetails === false ? "Customer and order lookup is not connected to this inbox." : "Select a conversation to see customer and order details."}</p></div>`;
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
      panes.list?.classList?.toggle?.("is-collapsed", listCollapsed);
      panes.rail?.classList?.toggle?.("is-collapsed", railCollapsed);
      safeMount(listTissue, panes.list, listInput());
      listTissue.afterPaint?.();
      const threadResult = safeMount(threadTissue, panes.thread, { ticket, capabilities, title: derivedTitle(ticket), missingTicketId: missingTicketId() });
      safeMount(composerTissue, panes.composer, composerInput(ticket));
      try {
        if (!showsCustomerRail(ticket)) {
          panes.rail.innerHTML = emptyRailHtml();
        } else if (railCollapsed) {
          panes.rail.innerHTML = railCollapsedHtml();
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
      if (host) host.innerHTML = gateSheetHtml();
    };

    const showActionError = error => {
      sendError = String(error?.message || "Action failed. No change was confirmed.");
      paint();
    };
    mailbox.subscribe(MAILBOX_TOPICS.LIST_COLLAPSED, ({ collapsed }) => {
      listCollapsed = Boolean(collapsed);
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.RAIL_COLLAPSED, ({ collapsed }) => {
      railCollapsed = Boolean(collapsed);
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.VIEW_SELECTED, ({ viewId: next }) => {
      viewId = next;
      channelId = "";
      statusId = "";
      assigneeId = "";
      tagId = "";
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
    mailbox.subscribe(MAILBOX_TOPICS.CHANNEL_SELECTED, ({ channelId: next }) => {
      channelId = normalizeChannel(next);
      resetUiState();
      ensureSelection();
      refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(paint);
    });
    mailbox.subscribe(MAILBOX_TOPICS.STATUS_SELECTED, ({ statusId: next }) => {
      statusId = normalizeStatus(next);
      resetUiState();
      ensureSelection();
      refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(paint);
    });
    mailbox.subscribe(MAILBOX_TOPICS.ASSIGNEE_SELECTED, ({ assigneeId: next }) => {
      assigneeId = normalizeAssignee(next);
      resetUiState();
      ensureSelection();
      refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(paint);
    });
    mailbox.subscribe(MAILBOX_TOPICS.TAG_SELECTED, ({ tagId: next }) => {
      tagId = normalizeTag(next);
      resetUiState();
      ensureSelection();
      refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(paint);
    });
    mailbox.subscribe(MAILBOX_TOPICS.LIST_SELECTED, ({ ticketId }) => {
      resetUiState(ticketId);
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
    mailbox.subscribe(MAILBOX_TOPICS.COMPOSER_REGENERATE, () => {
      discarded = false;
      const ticket = selectedTicket();
      const requestTicketId = ticket?.id || null;
      loadDraft(ticket).then((text) => {
        if (selectedId !== requestTicketId) return;
        strip = text;
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
    mailbox.subscribe(MAILBOX_TOPICS.WRITE_GATE_OPEN, () => {
      closeAllGates();
      writeGateOpen = true;
      paint();
    });
    mailbox.subscribe(MAILBOX_TOPICS.WRITE_GATE_CLOSE, () => {
      writeGateOpen = false;
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
      channelId = "";
      statusId = "";
      assigneeId = "";
      tagId = "";
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
    selectChannel(next) {
      channelId = normalizeChannel(next);
      resetUiState();
      ensureSelection();
      return refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(afterUi);
    },
    selectStatus(next) {
      statusId = normalizeStatus(next);
      resetUiState();
      ensureSelection();
      return refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(afterUi);
    },
    selectAssignee(next) {
      assigneeId = normalizeAssignee(next);
      resetUiState();
      ensureSelection();
      return refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(afterUi);
    },
    selectTag(next) {
      tagId = normalizeTag(next);
      resetUiState();
      ensureSelection();
      return refreshThread().then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(afterUi);
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
    collapseList(collapsed = true) {
      listCollapsed = Boolean(collapsed);
      return afterUi();
    },
    collapseRail(collapsed = true) {
      railCollapsed = Boolean(collapsed);
      return afterUi();
    },
    toggleRail(key) {
      return rail.toggle(key);
    },
    setBody(text) {
      body = text;
      composerTissue.update(composerInput(selectedTicket()));
      afterUi();
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
      const text = discarded ? "" : strip;
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
      threadTissue.update({ ticket: selectedTicket(), capabilities, missingTicketId: missingTicketId() });
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
