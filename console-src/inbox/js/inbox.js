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
    return listRows.filter((ticket) =>
      // #33: on the observed path the view partition holds for every render,
      // including rows appended by loadMore, so flagged rows never leak into
      // working views. Server-filtered list rows carry no assignee field and
      // are already view-filtered, so they must not be re-filtered here.
      (!shop.observedHistory || ticketInView(ticket, viewId)) &&
      (!channelId || normalizeChannel(ticket?.channel) === channelId) &&
      (!statusId || normalizeStatus(ticket?.status) === statusId) &&
      assigneeMatches(ticket, assigneeId) &&
      (!tagId || ticketTags(ticket).includes(tagId)));
  }

  function selectedTicket() {
    return selected || listRows.find((ticket) => ticket.id === selectedId) || null;
  }

  function ensureSelection() {
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
        if (ticket) {
          selected = shop.observedHistory ? withOperatorAssignee(ticket) : ticket;
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
    // rows occupy part of the prefix.
    const pagination = shop.observedHistory ? {
      total: counts[viewId] ?? listRows.length,
      loaded: listRows.filter((ticket) => ticketInView(ticket, viewId)).length,
      loading: loadingMore,
      error: moreError,
      loadMore,
    } : null;
    return {
      tickets: sortedVisibleTickets(),
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
    const threadModel = threadTissue.update({ ticket, capabilities });
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
      selectedId,
      unreadIds: [...unreadIds],
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
      const scroll = panes.list?.querySelector?.(".ticket-list");
      if (scroll) scroll.scrollTop = scrollTop;
      if (focused) panes.list?.querySelector?.("[data-load-more]")?.focus({preventScroll:true});
    };
    const paint = () => {
      const ticket = selectedTicket();
      panes.list?.classList?.toggle?.("is-collapsed", listCollapsed);
      panes.rail?.classList?.toggle?.("is-collapsed", railCollapsed);
      safeMount(listTissue, panes.list, listInput());
      const threadResult = safeMount(threadTissue, panes.thread, { ticket, capabilities });
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
      resetUiState();
      refreshList().then(() => {
        ensureSelection();
        return refreshThread();
      }).then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(paint);
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
      resetUiState();
      return refreshList().then(() => {
        ensureSelection();
        return refreshThread();
      }).then(refreshRail).then(refreshComposer).then(() => refreshMacros(macroQuery)).then(afterUi);
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
    selectTicket(id) {
      resetUiState(id);
      markRead(id);
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
      threadTissue.update({ ticket: selectedTicket(), capabilities });
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
      return snapshot();
    },
    async ingestEmail(args) {
      if (typeof shop.ingestEmail !== "function") return null;
      const result = await shop.ingestEmail(args);
      await refreshList();
      if (result?.id) {
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
