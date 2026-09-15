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
 * Chrome: Inbox title + filter (views) / sort / collapse — no separate views pane.
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
    collapsed: false,
    unreadIds: [],
  };
  let ui = { sort: "default", filterOpen: false };
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
      collapsed: Boolean(input.collapsed),
      unreadIds: Array.isArray(input.unreadIds) ? input.unreadIds : [],
    };
  }

  function sortedTickets(tickets) {
    let rows = Array.isArray(tickets) ? [...tickets] : [];
    if (ui.sort === "newest" || ui.sort === "oldest") {
      rows.sort((a, b) => {
        const left = Date.parse(a.updatedAt || 0) || 0;
        const right = Date.parse(b.updatedAt || 0) || 0;
        return ui.sort === "oldest" ? left - right : right - left;
      });
    }
    return rows;
  }

  function renderViewMenu(next) {
    const items = (next.views || []).map((view) => {
      const on = view.id === next.selectedViewId;
      const count = next.counts?.[view.id] ?? 0;
      return `<button type="button" class="list-menu-item${on ? " is-selected" : ""}" data-view="${esc(view.id)}" role="option" aria-selected="${on ? "true" : "false"}">
        <span class="list-menu-label">${esc(view.label)}</span>
        <span class="list-menu-count">${esc(count)}</span>
      </button>`;
    }).join("");
    return `<div class="list-scope-menu list-view-menu${ui.filterOpen ? " is-open" : ""}" role="listbox" ${ui.filterOpen ? "" : "hidden"}>
      ${items}
    </div>`;
  }

  function renderToolbar(next = model) {
    return `<header class="pane-head list-toolbar">
      <a class="console-link" href="/console/">Console</a>
      <div class="list-toolbar-row">
        <div class="list-scope">
          <button type="button" class="list-scope-btn" data-list-inbox aria-label="Inbox" title="Open the views menu">
            <span class="list-scope-label">Inbox</span>
            ${ICON_CHEVRON}
          </button>
        </div>
        <div class="list-tools" role="group" aria-label="List tools">
          <div class="list-filter-wrap">
            <button type="button" class="list-tool-btn" data-list-filter title="Views" aria-label="Views" aria-haspopup="listbox" aria-expanded="${ui.filterOpen ? "true" : "false"}" aria-pressed="${ui.filterOpen ? "true" : "false"}">${ICON_FILTER}</button>
            ${renderViewMenu(next)}
          </div>
          <button type="button" class="list-tool-btn" data-list-sort title="Sort ${ui.sort === "oldest" ? "newest first" : ui.sort === "newest" ? "oldest first" : "newest first"}" aria-label="Sort list">${ICON_SORT}</button>
          <button type="button" class="list-tool-btn" data-list-collapse title="Collapse list" aria-label="Collapse ticket list">${ICON_CLOSE}</button>
        </div>
      </div>
    </header>`;
  }

  function renderRow(ticket, selectedId, unreadIds) {
    const on = ticket.id === selectedId;
    const unread = unreadIds.includes(ticket.id);
    const status = ticket.status || "";
    const statusWord = status === "open" || ticket.projectionSource ? "" : screenStatus(status);
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
    const deviceAttr = ticket.device ? ` data-device="${esc(ticket.device)}"` : "";
    const unreadClass = unread ? " is-unread" : "";
    return `<button type="button" class="ticket-row${on ? " is-selected" : ""}${unreadClass}" data-ticket="${esc(ticket.id)}" data-status="${esc(status)}"${typeAttr}${severityAttr}${deviceAttr} aria-current="${on ? "true" : "false"}">
      <span class="ticket-bar" aria-hidden="true"></span>
      <span class="ticket-top">
        <span title="${esc(listCustomerName(ticket))}" class="ticket-name">${esc(listCustomerName(ticket))}</span>
        <span class="ticket-meta">
          ${typeHtml}
          ${severityHtml}
          ${statusHtml}
          <time class="ticket-time" datetime="${esc(ticket.updatedAt || "")}" title="${esc(formatWhen(ticket.updatedAt))}">${esc(formatWhen(ticket.updatedAt, { relative: true }))}</time>
        </span>
      </span>
      <span class="ticket-subject">${esc(ticket.subject)}</span>
      <span class="ticket-snippet">${esc(ticket.snippet || "")}</span>
    </button>`;
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
    const tickets = sortedTickets(next.tickets);
    const unreadIds = next.unreadIds || [];
    const rows = tickets.length
      ? tickets.map((ticket) => renderRow(ticket, next.selectedTicketId, unreadIds)).join("")
      : `<div class="empty-pane" role="status"><strong>${next.error ? "Tickets unavailable" : "No tickets yet"}</strong><p>${esc(next.error || "This inbox has no conversations in this view. Customer support continues in the support console.")}</p><a href="/console/">Open support console</a></div>`;
    return `<div class="pane-inner">
      ${renderToolbar(next)}
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
        ui = { ...ui, filterOpen: false };
        paint();
        mailbox.publish(MAILBOX_TOPICS.VIEW_SELECTED, { viewId: viewPick.dataset.view });
        return;
      }
      if (event.target.closest("[data-list-filter]")) {
        ui = { ...ui, filterOpen: !ui.filterOpen };
        paint();
        return;
      }
      if (event.target.closest("[data-list-inbox]")) {
        // Title affordance — views live under the filter control.
        ui = { ...ui, filterOpen: !ui.filterOpen };
        paint();
        return;
      }
      if (event.target.closest("[data-list-sort]")) {
        const nextSort =
          ui.sort === "default" ? "newest" : ui.sort === "newest" ? "oldest" : "default";
        ui = { ...ui, sort: nextSort, filterOpen: false };
        paint();
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
