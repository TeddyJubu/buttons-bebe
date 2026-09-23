import { MAILBOX_TOPICS } from "../contracts.js";
import { listCustomerName } from "../shop/clerk-ticket.js";
import { esc, formatWhen, requestTypeLabel, screenStatus, severityLabel } from "../util.js";

/**
 * List tissue. Client of helpdesk.list_tickets + view switcher.
 * In: `{ tickets, selectedTicketId, views, counts, selectedViewId, collapsed, unreadIds }`
 * Out: `{ ticketId }` on `list/selected`, `{ viewId }` on `view/selected`,
 *      `{ collapsed }` on `list/collapsed`
 * Selected row: pale accent wash + narrow accent edge. Uses first-party
 * customerName, snippet, and helpdesk status (open / closed / snoozed) —
 * never Return.status. Unread is session-local (bold name).
 * Chrome: Inbox (views) + one filter (channel/status/assignee/tag) / sort / collapse.
 */

const ICON_FILTER = `<svg class="list-tool-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
  <path fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" d="M2.5 4h11M2.5 8h11M2.5 12h11"/>
  <circle fill="currentColor" cx="5.5" cy="4" r="1.35"/>
  <circle fill="currentColor" cx="10.5" cy="8" r="1.35"/>
  <circle fill="currentColor" cx="7" cy="12" r="1.35"/>
</svg>`;

const ICON_SORT = `<svg class="list-tool-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
  <path fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" d="M5.25 3.25v9.5M5.25 3.25 3.5 5M5.25 3.25 7 5M10.75 12.75v-9.5M10.75 12.75 9 11M10.75 12.75 12.5 11"/>
</svg>`;

const ICON_CLOSE = `<svg class="list-tool-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
  <path fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" d="M4.25 4.25l7.5 7.5M11.75 4.25l-7.5 7.5"/>
</svg>`;

const ICON_CHEVRON = `<svg class="list-scope-chevron" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" focusable="false">
  <path fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" d="M2.75 4.5 6 7.75 9.25 4.5"/>
</svg>`;

const ICON_EXPAND = `<svg class="list-expand-icon" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" focusable="false">
  <path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" d="M5 2.5 9.5 7 5 11.5"/>
</svg>`;

export function createListTissue({ mailbox }) {
  let model = {
    tickets: [],
    selectedTicketId: null,
    views: [],
    counts: {},
    selectedViewId: "mine",
    channels: [],
    selectedChannelId: "",
    statuses: [],
    selectedStatusId: "",
    assignees: [],
    selectedAssigneeId: "",
    tags: [],
    selectedTagId: "",
    collapsed: false,
    unreadIds: [],
    searchQuery: "",
    searchAllViews: false,
    searchBounded: false,
    searchResults: null,
  };
  // #39 review: sort state lives in the organ; the tissue mirrors the id it
  // receives so the toolbar title matches what rendered.
  let ui = { viewOpen: false, filterOpen: false };
  // #36: the save-view name editor, mirroring the #41 rename editor — the
  // draft lives here; the organ owns the saved entry.
  let viewName = null;
  let host = null;

  function project(input) {
    return {
      tickets: input.tickets || [],
      error: input.error || "",
      notice: input.notice || "",
      noticeRefresh: input.noticeRefresh === true,
      pagination: input.pagination || null,
      selectedTicketId: input.selectedTicketId || null,
      views: input.views || [],
      counts: input.counts || {},
      selectedViewId: input.selectedViewId || "mine",
      channels: Array.isArray(input.channels) ? input.channels : [],
      selectedChannelId: typeof input.selectedChannelId === "string" ? input.selectedChannelId : "",
      statuses: Array.isArray(input.statuses) ? input.statuses : [],
      selectedStatusId: typeof input.selectedStatusId === "string" ? input.selectedStatusId : "",
      assignees: Array.isArray(input.assignees) ? input.assignees : [],
      selectedAssigneeId: typeof input.selectedAssigneeId === "string" ? input.selectedAssigneeId : "",
      tags: Array.isArray(input.tags) ? input.tags : [],
      selectedTagId: typeof input.selectedTagId === "string" ? input.selectedTagId : "",
      collapsed: Boolean(input.collapsed),
      unreadIds: Array.isArray(input.unreadIds) ? input.unreadIds : [],
      sortId: input.sortId || "default",
      bulkSelection: input.bulkSelection || null,
      searchQuery: typeof input.searchQuery === "string" ? input.searchQuery : "",
      searchAllViews: Boolean(input.searchAllViews),
      searchBounded: Boolean(input.searchBounded),
      searchResults: typeof input.searchResults === "number" ? input.searchResults : null,
      filterConditions: Array.isArray(input.filterConditions) ? input.filterConditions.map((condition) => ({...condition})) : [],
      filterMatch: input.filterMatch === "any" ? "any" : "all",
      filterFields: Array.isArray(input.filterFields) ? input.filterFields : [],
      savedViews: Array.isArray(input.savedViews) ? input.savedViews : [],
      // #38: the New ticket entry point renders only when the capability is
      // not explicitly off.
      canCreateTicket: input.canCreateTicket !== false,
    };
  }

  // #37: highlight the matched fragment. The needle is escaped for regex
  // assembly and the wrapped text goes through esc() so nothing renders raw.
  // #37: highlight the matched fragment. The match is found on folded text
  // but sliced from the RAW text — toLowerCase() can change string length
  // (e.g. İ → i̇), so a folded offset must never index the raw string. If
  // the needle folds to a different length the highlight is skipped rather
  // than misplaced. Both halves go through esc() so nothing renders raw.
  function markMatch(text, query) {
    const raw = String(text ?? "");
    const needle = String(query || "").trim();
    if (!needle) return esc(raw);
    const foldedNeedle = needle.toLowerCase();
    if (foldedNeedle.length !== needle.length) return esc(raw);
    const at = raw.toLowerCase().indexOf(foldedNeedle);
    if (at < 0 || at + needle.length > raw.length) return esc(raw);
    return `${esc(raw.slice(0, at))}<mark class="ticket-hit">${esc(raw.slice(at, at + needle.length))}</mark>${esc(raw.slice(at + needle.length))}`;
  }

  function menuItem(attrs, on, label, count) {
    const countHtml = count == null ? "" : `<span class="list-menu-count">${esc(count)}</span>`;
    return `<button type="button" class="list-menu-item${on ? " is-selected" : ""}" ${attrs} role="option" aria-selected="${on ? "true" : "false"}">
        <span class="list-menu-label">${esc(label)}</span>
        ${countHtml}
      </button>`;
  }

  function renderViewMenu(next) {
    const items = (next.views || []).map((view) =>
      menuItem(`data-view="${esc(view.id)}"`, view.id === next.selectedViewId, view.label, next.counts?.[view.id] ?? 0),
    ).join("");
    // #36: saved views list under the fixed ones, Gorgias-style, with a
    // remove control that only ever deletes the view entry.
    const saved = (next.savedViews || []).map((view) =>
      `<button type="button" class="list-menu-item" data-saved-view="${esc(view.id)}" role="option" aria-selected="false">
        <span class="list-menu-label">${esc(view.name)}</span>
        <span class="list-menu-count">${view.shared ? "Shared" : "Private"}</span>
      </button>`).join("");
    return `<div class="list-scope-menu list-view-menu${ui.viewOpen ? " is-open" : ""}" role="listbox" ${ui.viewOpen ? "" : "hidden"}>
      ${items}
      ${saved ? `<p class="list-filter-heading">Saved views</p>
      ${saved}
      ${next.savedViews?.map((view) => `<button type="button" class="btn-quiet" data-view-delete="${esc(view.id)}" aria-label="Remove saved view ${esc(view.name)}">Remove ${esc(view.name)}</button>`).join("")}` : ""}
    </div>`;
  }

  function renderFilterSection(heading, items) {
    if (!items) return "";
    return `<section class="list-filter-section" aria-label="${esc(heading)}">
      <p class="list-filter-heading">${esc(heading)}</p>
      ${items}
    </section>`;
  }

  function renderChannelSection(next) {
    const channels = next.channels || [];
    if (!channels.length) return "";
    const items = [
      menuItem('data-channel=""', !next.selectedChannelId, "All channels"),
      ...channels.map((channel) =>
        menuItem(`data-channel="${esc(channel.id)}"`, channel.id === next.selectedChannelId, channel.label || channel.id, channel.count ?? 0),
      ),
    ].join("");
    return renderFilterSection("Channel", items);
  }

  function renderStatusSection(next) {
    const statuses = next.statuses || [];
    if (!statuses.length) return "";
    const items = [
      menuItem('data-status-pick=""', !next.selectedStatusId, "All statuses"),
      ...statuses.map((entry) =>
        menuItem(`data-status-pick="${esc(entry.id)}"`, entry.id === next.selectedStatusId, screenStatus(entry.label || entry.id), entry.count ?? 0),
      ),
    ].join("");
    return renderFilterSection("Status", items);
  }

  function renderAssigneeSection(next) {
    const assignees = next.assignees || [];
    if (!assignees.length) return "";
    const items = [
      menuItem('data-assignee-pick=""', !next.selectedAssigneeId, "Everyone"),
      ...assignees.map((entry) =>
        menuItem(`data-assignee-pick="${esc(entry.id)}"`, entry.id === next.selectedAssigneeId, entry.label || entry.id, entry.count ?? 0),
      ),
    ].join("");
    return renderFilterSection("Assignee", items);
  }

  function renderTagSection(next) {
    const tags = next.tags || [];
    if (!tags.length) return "";
    const items = [
      menuItem('data-tag-pick=""', !next.selectedTagId, "All tags"),
      ...tags.map((entry) =>
        menuItem(`data-tag-pick="${esc(entry.id)}"`, entry.id === next.selectedTagId, entry.label || entry.id, entry.count ?? 0),
      ),
    ].join("");
    return renderFilterSection("Tag", items);
  }

  function filterActive(next) {
    // cubic: the icon reflects every committed condition, not just the four
    // quick facets — a builder-only or isNot filter lights it too.
    const activeCondition = (next.filterConditions || []).some((condition) => condition.values.length);
    return Boolean(next.selectedChannelId || next.selectedStatusId || next.selectedAssigneeId || next.selectedTagId) || activeCondition;
  }

  function hasFacetFilters(next) {
    return Boolean((next.channels || []).length || (next.statuses || []).length || (next.assignees || []).length || (next.tags || []).length);
  }

  // #36: Gorgias-style condition rows. Each row is field → operator → value
  // with a remove control; the builder publishes edits to the organ, which
  // owns the state and the filter predicate.
  function renderConditionRow(next, condition, index) {
    const fields = next.filterFields || [];
    const field = fields.find((entry) => entry.id === condition.field) || null;
    const ops = field?.ops || ["is"];
    const offers = (field?.values || []).map((offer) =>
      `<option value="${esc(offer.id)}"${condition.values.includes(offer.id) ? " selected" : ""}>${esc(offer.label || offer.id)}</option>`).join("");
    // cubic: a picked value must be removable — a multi-value row shows one
    // chosen value per chip with its own Clear, or "Pick a value" offers no
    // way back to an empty row.
    const chosen = condition.values.map((value) => {
      const offer = (field?.values || []).find((entry) => entry.id === value);
      return `<span class="list-filter-chip" data-filter-chip>
        ${esc(offer?.label || value)}
        <button type="button" class="btn-quiet" data-filter-value-remove="${index}:${esc(value)}" aria-label="Remove ${esc(offer?.label || value)} value">Clear</button>
      </span>`;
    }).join("");
    const valueControl = condition.field === "updated"
      ? `<input type="date" class="list-filter-date" data-filter-date="${index}" aria-label="Updated ${esc(condition.op)} date" value="${esc(condition.values[0] || "")}">`
      : condition.field === "customer"
        ? `<input type="search" class="list-filter-value" data-filter-value="${index}" aria-label="Customer contains" value="${esc(condition.values[0] || "")}">`
        : `<select class="list-filter-value" data-filter-value="${index}" aria-label="Filter value">
            <option value="">Pick a value</option>
            ${offers}
          </select>`;
    return `<div class="list-filter-row" data-filter-row="${index}">
      <select class="list-filter-field" data-filter-field="${index}" aria-label="Filter field">
        ${fields.map((entry) => `<option value="${esc(entry.id)}"${entry.id === condition.field ? " selected" : ""}>${esc(entry.label)}</option>`).join("")}
      </select>
      <select class="list-filter-op" data-filter-op="${index}" aria-label="Filter operator">
        ${ops.map((op) => `<option value="${esc(op)}"${op === condition.op ? " selected" : ""}>${esc(filterOpLabel(op))}</option>`).join("")}
      </select>
      ${chosen}
      ${valueControl}
      <button type="button" class="btn-quiet" data-filter-remove="${index}" aria-label="Remove condition">Remove</button>
    </div>`;
  }

  function filterOpLabel(op) {
    return {is: "is", isNot: "is not", contains: "contains", before: "before", after: "after"}[op] || op;
  }

  function filterBuilderRows(next) {
    const fields = next.filterFields || [];
    if (!fields.length) return [];
    return next.filterConditions.length ? next.filterConditions
      : [{field: fields[0].id, op: (fields[0].ops || ["is"])[0], values: []}];
  }

  function renderFilterBuilder(next) {
    const fields = next.filterFields || [];
    if (!fields.length) return "";
    const rows = filterBuilderRows(next)
      .map((condition, index) => renderConditionRow(next, condition, index)).join("");
    return `<div class="list-filter-builder" aria-label="Filter builder">
      <label class="list-filter-match">
        Match
        <select data-filter-match aria-label="Match all or any">
          <option value="all"${next.filterMatch !== "any" ? " selected" : ""}>all</option>
          <option value="any"${next.filterMatch === "any" ? " selected" : ""}>any</option>
        </select>
        conditions
      </label>
      ${rows}
      <button type="button" class="btn-hairline" data-add-condition>Add condition</button>
    </div>`;
  }

  function renderFilterChips(next) {
    const chips = (next.filterConditions || []).map((condition, index) => {
      const field = (next.filterFields || []).find((entry) => entry.id === condition.field);
      const label = field?.label || condition.field;
      return `<span class="list-filter-chip" data-filter-chip>
        ${esc(label)} ${esc(filterOpLabel(condition.op))} ${condition.field === "updated" || condition.field === "customer" ? esc(condition.values[0] || "…") : esc(condition.values.join(" or "))}
        <button type="button" class="btn-quiet" data-filter-remove="${index}" aria-label="Remove ${esc(label)} filter">Clear</button>
      </span>`;
    });
    if (!chips.length) return "";
    return `<div class="list-filter-chips" role="status" aria-label="Active filters">
      ${chips.join("")}
      <button type="button" class="btn-quiet" data-filter-clear>Clear all</button>
    </div>`;
  }

  function renderFilterMenu(next) {
    const sections = [renderChannelSection(next), renderStatusSection(next), renderAssigneeSection(next), renderTagSection(next)].filter(Boolean).join("");
    const builder = renderFilterBuilder(next);
    if (!sections && !builder) return "";
    return `<div class="list-scope-menu list-filter-menu${ui.filterOpen ? " is-open" : ""}" ${ui.filterOpen ? "" : "hidden"}>
      ${sections}
      ${sections && !viewName ? `<button type="button" class="btn-hairline" data-view-save>Save current filters as a view</button>` : ""}
      ${viewName ? `<div class="list-view-name" data-view-name>
        <input class="list-view-name-input" data-view-name-input value="${esc(viewName.draft)}" maxlength="80" placeholder="Name this view" aria-label="View name">
        <button type="button" class="btn-hairline" data-view-save-confirm>Save</button>
        <button type="button" class="btn-quiet" data-view-save-cancel>Cancel</button>
      </div>` : ""}
      ${builder}
    </div>`;
  }

  function renderToolbar(next = model) {
    // cubic: gate the filter control on the builder's fields — a snapshot
    // with no facet rows must still offer the seven-field builder.
    const facets = hasFacetFilters(next) || (next.filterFields || []).length > 0;
    return `<header class="pane-head list-toolbar">
      <a class="console-link" href="/console/">Console</a>
      <div class="list-toolbar-row list-toolbar-row--search">
        <div class="list-search">
          <input type="search" class="list-search-input" data-search-input placeholder="Search tickets" aria-label="Search tickets" value="${esc(next.searchQuery)}" autocomplete="off">
          ${next.searchQuery && !next.searchAllViews ? `<button type="button" class="btn-quiet" data-search-all title="No results in this view? Search every working view" aria-label="Search every view">Search every view</button>` : ""}
        </div>
      </div>
      <div class="list-toolbar-row list-toolbar-row--controls">
        <div class="list-scope">
          <button type="button" class="list-scope-btn" data-list-inbox aria-label="Inbox" title="Open the views menu" aria-haspopup="listbox" aria-expanded="${ui.viewOpen ? "true" : "false"}">
            <span class="list-scope-label">Inbox</span>
            ${ICON_CHEVRON}
          </button>
          ${renderViewMenu(next)}
        </div>
        <div class="list-tools" role="group" aria-label="List tools">
          ${next.canCreateTicket !== false ? `<button type="button" class="list-tool-btn" data-create-ticket title="New ticket" aria-label="Create a new ticket in this browser only">New ticket</button>` : ""}
          ${facets ? `<div class="list-filter-wrap">
            <button type="button" class="list-tool-btn${filterActive(next) ? " is-active" : ""}" data-list-filter title="Filter" aria-label="Filter" aria-haspopup="listbox" aria-expanded="${ui.filterOpen ? "true" : "false"}" aria-pressed="${ui.filterOpen || filterActive(next) ? "true" : "false"}">${ICON_FILTER}</button>
            ${renderFilterMenu(next)}
          </div>` : ""}
          <button type="button" class="list-tool-btn" data-list-sort title="Sort ${(next.sortId || "default") === "oldest" ? "newest first" : (next.sortId || "default") === "newest" ? "oldest first" : "newest first"}" aria-label="Sort list">${ICON_SORT}</button>
          <button type="button" class="list-tool-btn" data-list-collapse title="Collapse list" aria-label="Collapse ticket list">${ICON_CLOSE}</button>
        </div>
      </div>
      ${next.searchQuery ? `<p class="list-search-status" role="status">${next.searchAllViews
        ? `Searching all views · ${esc(next.searchResults)} result${next.searchResults === 1 ? "" : "s"} in the loaded history <button type="button" class="btn-quiet" data-search-view title="Back to searching the ${esc((next.views.find((view) => view.id === next.selectedViewId) || {label: "current"}).label)} view">Back to this view</button>`
        : `${esc(next.searchResults)} result${next.searchResults === 1 ? "" : "s"} in the loaded history`}</p>` : ""}
    </header>`;
  }

  // #39: "All selected" bar — count, per-action buttons (disabled state is
  // inherent: the bar only renders with a non-empty selection), Clear, and
  // role=status so the count is announced.
  function renderBulkBar(bulk) {
    const count = bulk.ids.length;
    return `<div class="bulk-bar" data-bulk-bar role="status" aria-label="${count} selected">
      <strong>${count} selected</strong>
      <div class="bulk-actions" role="group" aria-label="Bulk actions">
        <button type="button" class="btn-hairline" data-bulk-read title="Mark selected tickets read">Mark read</button>
        <button type="button" class="btn-hairline" data-bulk-unread title="Mark selected tickets unread">Mark unread</button>
        <button type="button" class="btn-hairline" data-bulk-export title="Download the selected observed rows as CSV">Export</button>
        ${bulk.canEscalate ? `<button type="button" class="btn-hairline" data-bulk-escalate title="Flag selected tickets for a human lead. Does not email the customer.">Escalate</button>` : ""}
      </div>
      ${bulk.escalated?.length ? `<span class="mute">Escalated ${bulk.escalated.length} ticket${bulk.escalated.length === 1 ? "" : "s"}</span>` : ""}
      ${bulk.error ? `<span class="bulk-error">${esc(bulk.error)}</span>` : ""}
      <button type="button" class="btn-quiet" data-bulk-clear title="Clear the ticket selection">Clear selection</button>
    </div>`;
  }

  function renderRow(ticket, selectedId, unreadIds, bulk, query = "") {
    const on = ticket.id === selectedId;
    const unread = unreadIds.includes(ticket.id);
    // #39 review: the checkbox is a SIBLING of the row button, not a child —
    // input-in-button is invalid interactive nesting. The wrapper div gives
    // the absolute-positioned controls their positioning context and the
    // list its role=listitem children.
    const checked = Boolean(bulk?.ids?.includes(ticket.id));
    const selectHtml = `<input type="checkbox" class="ticket-select" data-select-ticket="${esc(ticket.id)}" aria-label="Select ticket for ${esc(listCustomerName(ticket))}" ${checked ? "checked" : ""}>`;
    const status = ticket.status || "";
    const statusWord = status === "open" || status === "unknown" ? "" : screenStatus(status);
    const typeWord = requestTypeLabel(ticket.requestType);
    const severityWord = severityLabel(ticket.severity);
    const statusHtml = statusWord
      ? `<span class="ticket-status">${esc(statusWord)}</span>`
      : "";
    const typeHtml = typeWord
      ? `<span class="ticket-badge ticket-request" data-request-type="${esc(ticket.requestType)}">${esc(typeWord)}</span>`
      : "";
    const severityHtml = severityWord
      ? `<span class="ticket-badge ticket-severity" data-severity="${esc(ticket.severity)}">${esc(severityWord)}</span>`
      : "";
    const typeAttr = typeWord ? ` data-request-type="${esc(ticket.requestType)}"` : "";
    const severityAttr = severityWord ? ` data-severity="${esc(ticket.severity)}"` : "";
    const channelWord = typeof ticket.channel === "string" ? ticket.channel.trim().slice(0, 40) : "";
    const channelHtml = channelWord ? `<span class="ticket-badge ticket-channel">${esc(channelWord)}</span>` : "";
    const assigneeWord = typeof ticket.assignee === "string" ? ticket.assignee.trim().slice(0, 120) : "";
    const assigneeHtml = assigneeWord ? `<span class="ticket-badge ticket-assignee">${esc(assigneeWord)}</span>` : "";
    const ticketTagList = Array.isArray(ticket.tags)
      ? ticket.tags.map((tag) => (typeof tag === "string" ? tag.trim().slice(0, 40) : "")).filter(Boolean)
      : [];
    const tagBadges = ticketTagList.slice(0, 3)
      .map((tag) => `<span class="ticket-badge ticket-tag">${esc(tag)}</span>`).join("");
    const tagOverflow = ticketTagList.length > 3 ? `<span class="ticket-badge ticket-tag-more">+${ticketTagList.length - 3}</span>` : "";
    const gorgiasPriority = typeof ticket.gorgiasPriority === "string" ? ticket.gorgiasPriority.trim().slice(0, 20) : "";
    const gorgiasPriorityHtml = gorgiasPriority
      ? `<span class="ticket-badge ticket-gorgias-priority" title="Gorgias priority">${esc(gorgiasPriority)}</span>`
      : "";
    const gorgiasSpamHtml = ticket.gorgiasSpam
      ? `<span class="ticket-badge ticket-gorgias-spam" title="Marked as spam in Gorgias">Spam</span>`
      : "";
    const gorgiasTrashedHtml = ticket.gorgiasTrashed
      ? `<span class="ticket-badge ticket-gorgias-trashed" title="Trashed in Gorgias">Trashed</span>`
      : "";
    const gorgiasSnoozedHtml = ticket.gorgiasSnoozed
      ? `<span class="ticket-badge ticket-gorgias-snoozed" title="Snoozed in Gorgias">Snoozed</span>`
      : "";
    const deviceAttr = ticket.device ? ` data-device="${esc(ticket.device)}"` : "";
    const unreadClass = unread ? " is-unread" : "";
    const unreadHtml = unread
      ? `<span class="ticket-unread-dot" data-unread-dot role="img" aria-label="Unread"></span>`
      : "";
    return `<div class="ticket-item" role="listitem">
      ${selectHtml}
      <button type="button" class="ticket-row${on ? " is-selected" : ""}${unreadClass}" data-ticket="${esc(ticket.id)}" data-status="${esc(status)}"${typeAttr}${severityAttr}${deviceAttr} aria-current="${on ? "true" : "false"}">
      <span class="ticket-bar" aria-hidden="true"></span>
      ${unreadHtml}
      <span class="ticket-top">
        <span title="${esc(listCustomerName(ticket))}" class="ticket-name">${markMatch(listCustomerName(ticket), query)}</span>
        <span class="ticket-meta">
          ${typeHtml}
          ${severityHtml}
          ${channelHtml}
          ${assigneeHtml}
          ${tagBadges}
          ${tagOverflow}
          ${gorgiasSpamHtml}
          ${gorgiasTrashedHtml}
          ${gorgiasSnoozedHtml}
          ${gorgiasPriorityHtml}
          ${statusHtml}
          <time class="ticket-time" datetime="${esc(ticket.updatedAt || "")}" title="${esc(formatWhen(ticket.updatedAt))}">${esc(formatWhen(ticket.updatedAt, { relative: true }))}</time>
        </span>
      </span>
      <span class="ticket-subject" data-ticket-title="${esc(ticket.derivedTitle || ticket.subject)}">${markMatch(ticket.derivedTitle || ticket.subject, query)}</span>
      <span class="ticket-snippet">${markMatch(ticket.snippet, query)}</span>
    </button>
    </div>`;
  }

  function render(next = model) {
    if (next.collapsed) {
      // #40: the strip stays useful collapsed — it names the active view,
      // its ticket count, and the unread rows, not just "List".
      const view = (next.views || []).find((entry) => entry.id === next.selectedViewId) || {label: "Inbox"};
      const unread = (next.unreadIds || []).filter((id) => (next.tickets || []).some((ticket) => ticket.id === id)).length;
      const count = next.searchQuery ? (next.searchResults ?? (next.tickets || []).length) : (next.tickets || []).length;
      // cubic: an explicit aria-label replaces the name computed from the
      // contents, so the view/count/unread signal must live in the label.
      const name = `Expand ticket list (${esc(view.label)} view, ${count} tickets${unread ? `, ${unread} unread` : ""})`;
      return `<div class="pane-inner">
        <button type="button" class="list-expand-btn" data-list-expand aria-label="${name}" title="Show ticket list">
          ${ICON_EXPAND}
          <span class="list-expand-label list-strip-view">${esc(view.label)}</span>
          <span class="list-strip-count" data-strip-count="${count}">${count}</span>
          ${unread ? `<span class="list-strip-unread" data-strip-unread="${unread}">${unread}</span>` : ""}
        </button>
      </div>`;
    }
    // #39 review: the organ hands us the rendered order (sort applied there).
    const tickets = next.tickets;
    const unreadIds = next.unreadIds || [];
    const bulk = next.bulkSelection;
    const rows = tickets.length
      ? tickets.map((ticket) => renderRow(ticket, next.selectedTicketId, unreadIds, bulk, next.searchQuery)).join("")
      : next.searchQuery
        // #37: the search miss state names the query; the no-tickets state
        // never stands in for it.
        ? `<div class="empty-pane" role="status"><strong>No tickets match</strong><p>${next.searchBounded
          ? "Queries are limited to 200 characters — this one is longer, so it matches nothing. Try fewer words."
          : `Nothing in the loaded history matches “${esc(next.searchQuery)}”. ${next.searchAllViews ? "Try fewer words." : "Try fewer words, or search every view."}`}</p></div>`
        : `<div class="empty-pane" role="status"><strong>${next.error ? "Tickets unavailable" : "No tickets yet"}</strong><p>${esc(next.error || "This inbox has no conversations in this view. Customer support continues in the support console.")}</p><a href="/console/">Open support console</a></div>`;
    return `<div class="pane-inner">
      ${renderToolbar(next)}
      ${renderFilterChips(next)}
      ${bulk?.ids?.length ? renderBulkBar(bulk) : ""}
      ${next.notice ? `<p class="history-notice" role="status">${esc(next.notice)}${next.noticeRefresh ? ` <button type="button" class="btn-quiet history-refresh" data-history-refresh title="Re-read tickets, thread and details now">Refresh</button>` : ""}</p>` : ""}
      <div class="ticket-list" role="list">${rows}</div>
      ${next.pagination && tickets.length ? `<div class="list-pagination">
        <p role="status">${esc(next.pagination.error || `Showing ${next.pagination.loaded} of ${next.pagination.total} tickets`)}</p>
        ${next.pagination.loaded < next.pagination.total || next.pagination.error ? `<button type="button" class="btn-hairline" data-load-more ${next.pagination.loading ? 'disabled aria-busy="true"' : ""}>${next.pagination.loading ? "Loading…" : next.pagination.error ? "Try again" : "Load more"}</button>` : `<p class="mute">All available tickets loaded</p>`}
      </div>` : ""}
    </div>`;
  }

  function paint() {
    if (!host) return;
    host.innerHTML = render(model);
    afterPaint();
  }

  // #37: render is also the organ's paint path (safeMount calls render
  // through mount), so the focus restore must run there too — not just on
  // the tissue's own paint(). A query-model render with no live input
  // focus intent is a no-op. The repaint replaces the input node, so the
  // tissue restores focus and caret after every paint — the composer's
  // macro-search idiom — or typing would die on the first keystroke.
  let restoreSearchFocus = null;
  function afterPaint() {
    if (!restoreSearchFocus) return;
    const again = host?.querySelector?.("[data-search-input]");
    if (again?.focus) {
      again.focus();
      if (typeof restoreSearchFocus.start === "number") again.setSelectionRange?.(restoreSearchFocus.start, restoreSearchFocus.start);
    }
    restoreSearchFocus = null;
  }

  function fieldOps(fieldId) {
    return (model.filterFields.find((entry) => entry.id === fieldId)?.ops) || ["is"];
  }
  // The operator edits the rendered rows, which include the fresh default row
  // the builder shows when nothing is committed yet — an edit there is the
  // operator's first condition, so it must publish, not map over an empty list.
  function editableFilterRows() {
    return model.filterConditions.length ? model.filterConditions
      : filterBuilderRows(model);
  }
  function publishFilterEdit(conditions, match) {
    mailbox.publish(MAILBOX_TOPICS.FILTER_CHANGED, {conditions: conditions.map((condition) => ({...condition})), match: match === "any" ? "any" : "all"});
  }

  function mount(el) {
    host = el;
    paint();
    // #37: the operator's keystrokes publish live; the organ owns the bound
    // and the match.
    el.oninput = (event) => {
      const nameInput = event.target.closest?.("[data-view-name-input]");
      if (nameInput) {
        viewName = {draft: nameInput.value};
        return;
      }
      const input = event.target.closest?.("[data-search-input]");
      if (!input) return;
      restoreSearchFocus = { start: input.selectionStart };
      mailbox.publish(MAILBOX_TOPICS.LIST_SEARCHED, { query: input.value || "" });
    };
    // #36: builder edits publish one topic with the organ's committed
    // state; the organ owns the predicate and the URL.
    el.onchange = (event) => {
      const matchSelect = event.target.closest("[data-filter-match]");
      if (matchSelect) {
        publishFilterEdit(model.filterConditions, matchSelect.value);
        return;
      }
      const fieldSelect = event.target.closest("[data-filter-field]");
      if (fieldSelect) {
        const index = Number(fieldSelect.dataset.filterField);
        const next = editableFilterRows().map((condition, at) => at === index
          ? {field: fieldSelect.value, op: (fieldOps(fieldSelect.value)[0]), values: []}
          : {...condition});
        publishFilterEdit(next, model.filterMatch);
        return;
      }
      const opSelect = event.target.closest("[data-filter-op]");
      if (opSelect) {
        const index = Number(opSelect.dataset.filterOp);
        const next = editableFilterRows().map((condition, at) => at === index ? {...condition, op: opSelect.value} : {...condition});
        publishFilterEdit(next, model.filterMatch);
        return;
      }
      const valueSelect = event.target.closest("[data-filter-value]");
      if (valueSelect) {
        const index = Number(valueSelect.dataset.filterValue);
        const next = editableFilterRows().map((condition, at) => {
          if (at !== index) return {...condition};
          if (condition.field === "customer") return {...condition, values: valueSelect.value ? [valueSelect.value.slice(0, 120)] : []};
          const values = [...condition.values];
          if (valueSelect.value && !values.includes(valueSelect.value)) values.push(valueSelect.value);
          return {...condition, values};
        });
        publishFilterEdit(next, model.filterMatch);
        return;
      }
      const dateInput = event.target.closest("[data-filter-date]");
      if (dateInput) {
        const index = Number(dateInput.dataset.filterDate);
        const next = editableFilterRows().map((condition, at) => at === index ? {...condition, values: dateInput.value ? [dateInput.value] : []} : {...condition});
        publishFilterEdit(next, model.filterMatch);
        return;
      }
    };
    el.onclick = (event) => {
      if (event.target.closest("[data-load-more]")) { model.pagination?.loadMore?.(); return; }
      if (event.target.closest("[data-add-condition]")) {
        const first = (model.filterFields[0] || {id: "status", ops: ["is"]});
        const next = [...editableFilterRows(), {field: first.id, op: (first.ops || ["is"])[0], values: []}];
        publishFilterEdit(next, model.filterMatch);
        return;
      }
      if (event.target.closest("[data-filter-clear]")) {
        publishFilterEdit([], model.filterMatch);
        return;
      }
      const chipRemove = event.target.closest("[data-filter-remove]");
      if (chipRemove) {
        const next = model.filterConditions.filter((_, at) => at !== Number(chipRemove.dataset.filterRemove));
        publishFilterEdit(next, model.filterMatch);
        return;
      }
      const valueRemove = event.target.closest("[data-filter-value-remove]");
      if (valueRemove) {
        // Split on the first colon only — values themselves may carry colons
        // (a tag like sale:active).
        const packed = String(valueRemove.dataset.filterValueRemove);
        const split = packed.indexOf(":");
        const row = Number(packed.slice(0, split));
        const value = packed.slice(split + 1);
        const next = editableFilterRows().map((condition, at) => at === row
          ? {...condition, values: condition.values.filter((entry) => entry !== value)}
          : {...condition});
        publishFilterEdit(next, model.filterMatch);
        return;
      }
      if (event.target.closest("[data-view-save]")) {
        // #36: the view needs a name before it exists — the editor opens
        // here and Confirm publishes, mirroring the #41 rename flow.
        viewName = {draft: ""};
        paint();
        return;
      }
      if (event.target.closest("[data-view-save-cancel]")) {
        viewName = null;
        paint();
        return;
      }
      if (event.target.closest("[data-view-save-confirm]")) {
        mailbox.publish(MAILBOX_TOPICS.FILTER_VIEW_SAVE, {name: viewName?.draft || "", shared: false});
        viewName = null;
        paint();
        return;
      }
      const savedView = event.target.closest("[data-saved-view]");
      if (savedView) {
        mailbox.publish(MAILBOX_TOPICS.FILTER_VIEW_APPLY, {id: savedView.dataset.savedView});
        return;
      }
      const viewDelete = event.target.closest("[data-view-delete]");
      if (viewDelete) {
        mailbox.publish(MAILBOX_TOPICS.FILTER_VIEW_DELETE, {id: viewDelete.dataset.viewDelete});
        return;
      }
      if (event.target.closest("[data-search-all]")) {
        mailbox.publish(MAILBOX_TOPICS.LIST_SEARCHED, { query: model.searchQuery, allViews: true });
        return;
      }
      if (event.target.closest("[data-search-view]")) {
        mailbox.publish(MAILBOX_TOPICS.LIST_SEARCHED, { query: model.searchQuery, allViews: false });
        return;
      }
      const viewPick = event.target.closest("[data-view]");
      if (viewPick) {
        ui = { ...ui, viewOpen: false, filterOpen: false };
        paint();
        mailbox.publish(MAILBOX_TOPICS.VIEW_SELECTED, { viewId: viewPick.dataset.view });
        return;
      }
      if (event.target.closest("[data-list-filter]")) {
        ui = { ...ui, filterOpen: !ui.filterOpen, viewOpen: false };
        paint();
        return;
      }
      const channelPick = event.target.closest("[data-channel]");
      if (channelPick) {
        mailbox.publish(MAILBOX_TOPICS.CHANNEL_SELECTED, { channelId: channelPick.dataset.channel || "" });
        return;
      }
      const statusPick = event.target.closest("[data-status-pick]");
      if (statusPick) {
        mailbox.publish(MAILBOX_TOPICS.STATUS_SELECTED, { statusId: statusPick.dataset.statusPick || "" });
        return;
      }
      const assigneePick = event.target.closest("[data-assignee-pick]");
      if (assigneePick) {
        mailbox.publish(MAILBOX_TOPICS.ASSIGNEE_SELECTED, { assigneeId: assigneePick.dataset.assigneePick || "" });
        return;
      }
      const tagPick = event.target.closest("[data-tag-pick]");
      if (tagPick) {
        mailbox.publish(MAILBOX_TOPICS.TAG_SELECTED, { tagId: tagPick.dataset.tagPick || "" });
        return;
      }
      if (event.target.closest("[data-list-inbox]")) {
        ui = { ...ui, viewOpen: !ui.viewOpen, filterOpen: false };
        paint();
        return;
      }
      if (event.target.closest("[data-history-refresh]")) {
        mailbox.publish(MAILBOX_TOPICS.HISTORY_REFRESH, {});
        return;
      }
      if (event.target.closest("[data-list-sort]")) {
        // #39 review: the organ owns the sort; publish and let it repaint.
        const current = model.sortId || "default";
        const nextSort =
          current === "default" ? "newest" : current === "newest" ? "oldest" : "default";
        ui = { ...ui, viewOpen: false, filterOpen: false };
        mailbox.publish(MAILBOX_TOPICS.SORT_SELECTED, { sortId: nextSort });
        return;
      }
      if (event.target.closest("[data-create-ticket]")) {
        // #38: the organ owns the sheet state; the toolbar button just asks
        // for it to open.
        ui = { ...ui, viewOpen: false, filterOpen: false };
        mailbox.publish(MAILBOX_TOPICS.CREATE_TICKET_OPEN, {});
        return;
      }
      if (event.target.closest("[data-list-collapse]")) {
        mailbox.publish(MAILBOX_TOPICS.LIST_COLLAPSED, { collapsed: true });
        return;
      }
      if (event.target.closest("[data-list-expand]")) {
        mailbox.publish(MAILBOX_TOPICS.LIST_COLLAPSED, { collapsed: false });
        return;
      }
      // #39: checkbox and bulk-bar clicks select/act; they never fall through
      // to opening the thread.
      const selectBox = event.target.closest("[data-select-ticket]");
      if (selectBox) {
        mailbox.publish(MAILBOX_TOPICS.BULK_TOGGLE, {
          ticketId: selectBox.dataset.selectTicket,
          shiftKey: Boolean(event.shiftKey),
        });
        return;
      }
      const bulkAction = event.target.closest("[data-bulk-read],[data-bulk-unread],[data-bulk-export],[data-bulk-escalate],[data-bulk-clear]");
      if (bulkAction) {
        const action = bulkAction.dataset.bulkRead ? "markRead"
          : bulkAction.dataset.bulkUnread ? "markUnread"
          : bulkAction.dataset.bulkExport ? "export"
          : bulkAction.dataset.bulkEscalate ? "escalate"
          : "clear";
        model.bulkSelection?.actions?.[action]?.();
        return;
      }
      const button = event.target.closest("[data-ticket]");
      if (!button) return;
      mailbox.publish(MAILBOX_TOPICS.LIST_SELECTED, { ticketId: button.dataset.ticket });
    };
  }

  return {
    id: "list",
    project,
    render(next = model) {
      return render(next);
    },
    // #37: after the organ paints the list html into the pane, the
    // tissue's pending focus restore (if any) runs against the live DOM.
    afterPaint,
    update(input) {
      model = project(input);
      return model;
    },
    mount,
  };
}
