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
  };
  // #39 review: sort state lives in the organ; the tissue mirrors the id it
  // receives so the toolbar title matches what rendered.
  let ui = { viewOpen: false, filterOpen: false };
  let host = null;

  function project(input) {
    return {
      tickets: input.tickets || [],
      error: input.error || "",
      notice: input.notice || "",
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
    };
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
    return `<div class="list-scope-menu list-view-menu${ui.viewOpen ? " is-open" : ""}" role="listbox" ${ui.viewOpen ? "" : "hidden"}>
      ${items}
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
    return Boolean(next.selectedChannelId || next.selectedStatusId || next.selectedAssigneeId || next.selectedTagId);
  }

  function hasFacetFilters(next) {
    return Boolean((next.channels || []).length || (next.statuses || []).length || (next.assignees || []).length || (next.tags || []).length);
  }

  function renderFilterMenu(next) {
    const sections = [renderChannelSection(next), renderStatusSection(next), renderAssigneeSection(next), renderTagSection(next)].filter(Boolean).join("");
    if (!sections) return "";
    return `<div class="list-scope-menu list-filter-menu${ui.filterOpen ? " is-open" : ""}" role="listbox" ${ui.filterOpen ? "" : "hidden"}>
      ${sections}
    </div>`;
  }

  function renderToolbar(next = model) {
    const facets = hasFacetFilters(next);
    return `<header class="pane-head list-toolbar">
      <a class="console-link" href="/console/">Console</a>
      <div class="list-toolbar-row">
        <div class="list-scope">
          <button type="button" class="list-scope-btn" data-list-inbox aria-label="Inbox" title="Open the views menu" aria-haspopup="listbox" aria-expanded="${ui.viewOpen ? "true" : "false"}">
            <span class="list-scope-label">Inbox</span>
            ${ICON_CHEVRON}
          </button>
          ${renderViewMenu(next)}
        </div>
        <div class="list-tools" role="group" aria-label="List tools">
          ${facets ? `<div class="list-filter-wrap">
            <button type="button" class="list-tool-btn${filterActive(next) ? " is-active" : ""}" data-list-filter title="Filter" aria-label="Filter" aria-haspopup="listbox" aria-expanded="${ui.filterOpen ? "true" : "false"}" aria-pressed="${ui.filterOpen || filterActive(next) ? "true" : "false"}">${ICON_FILTER}</button>
            ${renderFilterMenu(next)}
          </div>` : ""}
          <button type="button" class="list-tool-btn" data-list-sort title="Sort ${(next.sortId || "default") === "oldest" ? "newest first" : (next.sortId || "default") === "newest" ? "oldest first" : "newest first"}" aria-label="Sort list">${ICON_SORT}</button>
          <button type="button" class="list-tool-btn" data-list-collapse title="Collapse list" aria-label="Collapse ticket list">${ICON_CLOSE}</button>
        </div>
      </div>
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

  function renderRow(ticket, selectedId, unreadIds, bulk) {
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
        <span title="${esc(listCustomerName(ticket))}" class="ticket-name">${esc(listCustomerName(ticket))}</span>
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
      <span class="ticket-subject">${esc(ticket.subject)}</span>
      <span class="ticket-snippet">${esc(ticket.snippet || "")}</span>
    </button>
    </div>`;
  }

  function render(next = model) {
    if (next.collapsed) {
      return `<div class="pane-inner">
        <button type="button" class="list-expand-btn" data-list-expand aria-label="Expand ticket list" title="Show ticket list">
          ${ICON_EXPAND}
          <span class="list-expand-label">List</span>
        </button>
      </div>`;
    }
    // #39 review: the organ hands us the rendered order (sort applied there).
    const tickets = next.tickets;
    const unreadIds = next.unreadIds || [];
    const bulk = next.bulkSelection;
    const rows = tickets.length
      ? tickets.map((ticket) => renderRow(ticket, next.selectedTicketId, unreadIds, bulk)).join("")
      : `<div class="empty-pane" role="status"><strong>${next.error ? "Tickets unavailable" : "No tickets yet"}</strong><p>${esc(next.error || "This inbox has no conversations in this view. Customer support continues in the support console.")}</p><a href="/console/">Open support console</a></div>`;
    return `<div class="pane-inner">
      ${renderToolbar(next)}
      ${bulk?.ids?.length ? renderBulkBar(bulk) : ""}
      ${next.notice ? `<p class="history-notice" role="status">${esc(next.notice)}</p>` : ""}
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
  }

  function mount(el) {
    host = el;
    paint();
    el.onclick = (event) => {
      if (event.target.closest("[data-load-more]")) { model.pagination?.loadMore?.(); return; }
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
      if (event.target.closest("[data-list-sort]")) {
        // #39 review: the organ owns the sort; publish and let it repaint.
        const current = model.sortId || "default";
        const nextSort =
          current === "default" ? "newest" : current === "newest" ? "oldest" : "default";
        ui = { ...ui, viewOpen: false, filterOpen: false };
        mailbox.publish(MAILBOX_TOPICS.SORT_SELECTED, { sortId: nextSort });
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
    render,
    update(input) {
      model = project(input);
      return model;
    },
    mount,
  };
}
