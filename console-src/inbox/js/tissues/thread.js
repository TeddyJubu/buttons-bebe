import { MAILBOX_TOPICS } from "../contracts.js";
import { clerkStatusEvents, listCustomerName, messageSpeaker, talkMessages } from "../shop/clerk-ticket.js";
import { esc, formatWeekday, formatWhen, initials, requestTypeChrome, safeWebUrl, screenStatus } from "../util.js";

/**
 * Thread tissue.
 * In: `{ ticket }` from helpdesk.get_ticket
 * Out: summarize on `composer/summarize`; escalate on `thread/escalate`.
 * Inbound From is the customer persona, not the AgentMail/shop mailbox login.
 * Status-change events are muted as `Closed · Tuesday`. Escalate writes
 * `Escalated · Tuesday` the same way. No Escalated badge. No Send.
 * Attachment images are small thumbs; click opens a simple lightbox.
 */
export function createThreadTissue({ mailbox }) {
  function observedTicketStatus(ticket) {
    // #44: the badge keeps naming what Gorgias reported — the observed
    // status, never the console-only override the picker carries.
    const raw = ticket?.observedStatus ?? ticket?.status;
    const status = typeof raw === "string" ? raw.trim().toLowerCase() : "";
    if (!status || status === "unknown") return ticket?.projectionSource ? "Status unknown" : screenStatus(raw);
    return screenStatus(status);
  }
  // Task 4: the status word always carries the meaning; the dot is decoration
  // only (aria-hidden, no contrast requirement). Open reads as attention
  // (brand accent), urgent priority as red, snoozed/medium as amber.
  function statusDotClass(kind, value) {
    const text = String(value || "").trim().toLowerCase();
    if (kind === "priority") {
      if (/urgent|high|critical/.test(text)) return "is-urgent";
      if (/medium/.test(text)) return "is-warn";
      return "";
    }
    if (text === "open") return "is-open";
    if (text === "closed") return "is-closed";
    if (text === "snoozed") return "is-snoozed";
    return "is-unknown";
  }
  function statusDot(kind, value) {
    const cls = statusDotClass(kind, value);
    return `<span class="status-dot${cls ? ` ${cls}` : ""}" aria-hidden="true"></span>`;
  }
  let model = { ticket: null };
  let lightbox = null;
  let renaming = null;
  // #44: the three-dot overflow menu's open state. Like the rename editor,
  // a mid-edit repaint keeps it open.
  let menuOpen = false;
  let menuTicketId = null;
  let detailsOpen = false;
  // #43: message ids whose Show-original disclosure is open. The details
  // element's open state is native DOM a repaint would otherwise discard.
  const openOriginals = new Set();
  let host = null;

  function project(input) {
    // #41: `title` is the organ-derived display title (first-party rename →
    // linked order name → subject → "New ticket"); the rename control
    // publishes, the organ owns the store. `ticket` being swapped for a new
    // object by refresh is normal — but the open rename editor keeps its
    // draft: a mid-edit repaint (bridge poll) must not wipe the typing.
    // #42: `missingTicketId` names a deep-linked id the snapshot does not
    // hold; the thread says so instead of showing another ticket.
    return {
      ticket: input.ticket || null,
      capabilities: input.capabilities || {},
      title: input.title || "",
      missingTicketId: input.missingTicketId || null,
      // #44: list-position context for previous/next, and the operator's own
      // address as the local-assignee choice.
      nav: input.nav || {position: -1, total: 0, hasPrev: false, hasNext: false},
      operatorEmail: input.operatorEmail || "",
      // #44: the raw local overrides, for re-selecting picker values the
      // row model normalizes away (an "Unassigned" pick reads as null).
      ticketState: input.ticketState || null,
    };
  }

  function renderAttachments(message) {
    const rows = Array.isArray(message.attachments) ? message.attachments : [];
    if (!rows.length) return "";
    const figures = rows
      .filter((item) => item && safeWebUrl(item.url))
      .map((item) => {
        const alt = item.alt || "Attachment";
        return `<figure class="bubble-attach">
          <button type="button" class="bubble-thumb" data-attach-open data-attach-url="${esc(safeWebUrl(item.url))}" data-attach-alt="${esc(alt)}" aria-label="Expand ${esc(alt)}" title="Click to enlarge photo">
            <img src="${esc(safeWebUrl(item.url))}" alt="${esc(alt)}" loading="lazy" width="80" height="80" />
          </button>
          <figcaption>${esc(alt)}</figcaption>
        </figure>`;
      })
      .join("");
    return figures ? `<div class="bubble-attachments">${figures}</div>` : "";
  }

  function renderLightbox() {
    if (!safeWebUrl(lightbox?.url)) return "";
    return `<div class="attach-lightbox-backdrop" data-attach-lightbox role="dialog" aria-modal="true" aria-label="Attachment">
      <figure class="attach-lightbox">
        <img src="${esc(safeWebUrl(lightbox.url))}" alt="${esc(lightbox.alt || "Attachment")}" />
        <figcaption>${esc(lightbox.alt || "Attachment")}</figcaption>
      </figure>
    </div>`;
  }

  function renderMessage(ticket, message) {
    const speaker = messageSpeaker(ticket, message);
    const email = speaker.email && speaker.email !== speaker.name
      ? `<span class="from-email">${esc(speaker.email)}</span>`
      : "";
    let via = "";
    if (speaker.role === "agent" && message.via) {
      const label = message.via === "gorgias"
        ? "via Gorgias"
        : message.via === "email"
          ? "by email"
          : message.via === "local"
            ? "local only"
            : "";
      if (label) via = `<span class="mute message-via">${esc(label)}</span>`;
    }
    return `<article class="bubble ${speaker.role}">
      <div class="bubble-meta">
        <span class="avatar">${esc(initials(speaker.name))}</span>
        <strong>From ${esc(speaker.name)}</strong>
        ${email}
        ${via}
        <time>${esc(formatWhen(message.at))}</time>
      </div>
      <p>${esc(message.body)}</p>
      ${message.originalText && message.originalText !== message.body
        ? `<details class="bubble-original"${openOriginals.has(message.id) ? " open" : ""} data-original-toggle data-original-id="${esc(message.id)}"><summary>Show original</summary><p class="mute">${esc(message.originalText)}</p></details>`
        : ""}
      ${renderAttachments(message)}
    </article>`;
  }

  function isEscalateEvent(event) {
    return /^escalated\b/i.test(event?.note || "") || String(event?.status || "").toLowerCase() === "escalated";
  }

  function renderStatus(event) {
    const escalated = isEscalateEvent(event);
    const word = escalated ? "Escalated" : screenStatus(event.status);
    const mark = escalated ? " data-escalated" : "";
    return `<p class="status-line"${mark}>${esc(word)} · ${esc(formatWeekday(event.at))}</p>`;
  }

  function timeline(ticket) {
    const events = clerkStatusEvents(ticket);
    const items = [
      ...talkMessages(ticket).map((message) => ({ at: message.at, html: renderMessage(ticket, message) })),
      ...events.map((event) => ({ at: event.at, html: renderStatus(event) })),
    ];
    if (ticket.escalated && !events.some(isEscalateEvent)) {
      const at = ticket.updatedAt || ticket.statusEvents?.at?.(-1)?.at || "";
      items.push({ at, html: renderStatus({ at, status: ticket.status, note: "escalated" }) });
    }
    items.sort((a, b) => String(a.at || "").localeCompare(String(b.at || "")));
    if (!items.length) {
      // An empty timeline (e.g. a projection ticket whose messages never
      // arrived) must say so — otherwise .thread-scroll collapses to the
      // history notice and the pane reads as a sliver.
      return `<p class="thread-empty" role="status">No messages in this snapshot yet.</p>`;
    }
    return items.map((item) => item.html).join("");
  }

  // #44: first-party ticket-detail controls. Every control is a browser
  // write — the Gorgias value stays visible, and the local override is
  // badged "Console only" so the operator can tell the two apart.
  const STATE_OPTIONS = {
    status: ["open", "closed", "snoozed"],
    priority: ["low", "normal", "high", "urgent"],
  };
  const stateOptionLabel = (value) => String(value).charAt(0).toUpperCase() + String(value).slice(1);
  function detailControls(ticket, next) {
    const nav = next.nav || {hasPrev: false, hasNext: false, position: -1, total: 0};
    // Task 7: a disabled Previous/Next names why — position included — so it
    // never reads as a mystery greyed button. (Disabled buttons take no
    // hover, so the reason lives in the title up front.)
    const navCount = Number.isInteger(nav.total) && nav.total > 0 ? nav.total : 0;
    const prevTitle = nav.hasPrev
      ? "Go to the previous ticket in this view"
      : navCount
        ? `First of ${navCount} tickets — no previous ticket.`
        : "No previous ticket.";
    const nextTitle = nav.hasNext
      ? "Go to the next ticket in this view"
      : navCount
        ? `Last of ${navCount} tickets — no next ticket.`
        : "No next ticket.";
    const overridden = ticket.localOverrides || {};
    const stamp = (field) => overridden[field]
      ? ` title="Console-only override set by ${esc(next.operatorEmail || "the operator")}. The observed value never changes."`
      : "";
    const statusEscape = overridden.status || !STATE_OPTIONS.status.includes(ticket.status)
      ? `<option value=""${overridden.status ? "" : " selected"}>Observed</option>`
      : "";
    const statusOptions = statusEscape + STATE_OPTIONS.status.map((value) =>
      `<option value="${value}"${ticket.status === value ? " selected" : ""}>${stateOptionLabel(value)}</option>`).join("");
    const priorityOptions = ["", ...STATE_OPTIONS.priority].map((value) =>
      `<option value="${value}"${(ticket.priority || "").toLowerCase() === value ? " selected" : ""}>${value ? stateOptionLabel(value) : "Observed"}</option>`).join("");
    const observedAssignee = typeof ticket.observedAssignee === "string" && ticket.observedAssignee.trim()
      ? ticket.observedAssignee.trim() : "";
    const assigneeOverride = next.ticketState?.assignee?.value ?? "";
    const assigneeSelected = (value) => assigneeOverride
      ? (assigneeOverride === value ? " selected" : "")
      : "";
    const observedSelected = assigneeOverride ? "" : " selected";
    const assigneeOptions = [
      `<option value=""${observedSelected}>Observed${observedAssignee ? ` (${esc(observedAssignee)})` : ""}</option>`,
      ...(next.operatorEmail
        ? [`<option value="${esc(next.operatorEmail)}"${assigneeSelected(next.operatorEmail)}>Me</option>`]
        : []),
      ...(observedAssignee && observedAssignee !== next.operatorEmail
        ? [`<option value="${esc(observedAssignee)}"${assigneeSelected(observedAssignee)}>${esc(observedAssignee)}</option>`]
        : []),
      // A saved pick whose observed value moved on stays selectable, so the
      // badge and the picker never disagree.
      ...(assigneeOverride && assigneeOverride !== next.operatorEmail
        && assigneeOverride !== "unassigned" && assigneeOverride !== observedAssignee
        ? [`<option value="${esc(assigneeOverride)}"${assigneeSelected(assigneeOverride)}>${esc(assigneeOverride)}</option>`]
        : []),
      `<option value="unassigned"${assigneeSelected("unassigned")}>Unassigned</option>`,
    ].join("");
    const overrideBadge = (field) => overridden[field] ? `<span class="local-override-badge" title="This value is a console-only override. Clearing the control restores the observed value.">Console only</span>` : "";
    return `<div class="thread-detail-controls">
          <label class="detail-field">
            <span class="detail-label">Status</span>
            <select data-detail-status data-ticket-id="${esc(ticket.id)}" aria-label="Ticket status (console only)"${stamp("status")}>
              ${statusOptions}
            </select>
            ${overrideBadge("status")}
          </label>
          <label class="detail-field">
            <span class="detail-label">Priority</span>
            <select data-detail-priority data-ticket-id="${esc(ticket.id)}" aria-label="Ticket priority (console only)"${stamp("priority")}>
              ${priorityOptions}
            </select>
            ${overrideBadge("priority")}
          </label>
          <label class="detail-field">
            <span class="detail-label">Assignee</span>
            <select data-detail-assignee data-ticket-id="${esc(ticket.id)}" aria-label="Ticket assignee (console only)"${stamp("assignee")}>
              ${assigneeOptions}
            </select>
            ${overrideBadge("assignee")}
          </label>
          <span class="thread-menu-anchor">
            <button type="button" class="btn-quiet" data-detail-menu data-menu-toggle aria-expanded="${menuOpen ? "true" : "false"}" aria-haspopup="menu" title="Other first-party actions for this ticket (browser only)">⋯</button>
            <span class="thread-menu" role="menu" ${menuOpen ? "" : "hidden"}>
              <button type="button" class="thread-menu-item" role="menuitem" data-menu-mark-unread data-ticket-id="${esc(ticket.id)}" title="Mark this ticket unread in your browser only. Never writes Gorgias.">Mark as unread</button>
            </span>
          </span>
          <span class="detail-nav">
            <button type="button" class="btn-quiet" data-ticket-prev ${nav.hasPrev ? "" : "disabled"} title="${esc(prevTitle)}">Previous</button>
            <button type="button" class="btn-quiet" data-ticket-next ${nav.hasNext ? "" : "disabled"} title="${esc(nextTitle)}">Next</button>
          </span>
        </div>`;
  }

  function render(next = model) {
    const ticket = next.ticket;
    if (next.missingTicketId) {
      // #42: a deep link that names an unknown ticket must not quietly show
      // a different row. The URL stays; picking any real ticket recovers.
      return `<div class="pane-inner"><p class="empty-pane" role="alert">Ticket not found: ${esc(next.missingTicketId)}. It may be outside the observed history window. Pick a ticket from the list.</p></div>${renderLightbox()}`;
    }
    if (!ticket) {
      return `<div class="pane-inner"><p class="empty-pane">Select a ticket.</p></div>${renderLightbox()}`;
    }
    if (ticket.historyUnavailable) return `<div class="pane-inner"><p role="alert">Ticket history is unavailable. Refresh to retry. Existing customer records have not been reset.</p><a href="/console/" target="_top">Open support console</a></div>`;
    const count = talkMessages(ticket).length;
    const summarizeLabel = count === 1 ? "Summarize 1 message" : `Summarize ${count} messages`;
    const escalateControl = ticket.escalated || next.capabilities?.escalateTicket === false
      ? ""
      : `<button type="button" class="btn-quiet" data-escalate="${esc(ticket.id)}" title="Flag this ticket for a human lead. Does not email the customer.">Escalate</button>`;
    const chrome = requestTypeChrome(ticket);
    const subtype = chrome?.subtype
      ? `<span class="thread-request-subtype mute">${esc(chrome.subtype)}</span>`
      : "";
    const capability = { privacy_request: "markPrivacyHandled", marketing_unsubscribe: "markUnsubscribed", bug: "markBugHandled" }[ticket.requestType];
    const mark = !chrome || next.capabilities?.[capability] === false
      ? ""
      : chrome.handled
        ? `<p class="thread-request-handled mute">${esc(chrome.doneLabel)}</p>`
        : `<button type="button" class="btn-hairline" ${chrome.gateAttr} title="Mark this request handled on the ticket only. No Shopify write.">${esc(chrome.markLabel)}</button>`;
    const typeLine = chrome
      ? `<div class="thread-request-row">
          <p class="thread-request mute" data-request-type="${esc(chrome.type)}"${chrome.severityAttr || ""}>${esc(chrome.title)}</p>
          ${subtype}
          ${mark}
        </div>`
      : "";
    // #41: the derived title line, or the inline rename editor when open.
    const titleLine = renaming?.ticketId === ticket.id
      ? `<p class="thread-subject thread-rename" data-ticket-title="${esc(renaming.original)}">
          <input class="thread-rename-input" data-rename-input value="${esc(renaming.draft ?? renaming.original)}" maxlength="120" aria-label="Ticket title">
          <button type="button" class="btn-hairline" data-rename-save title="Save the title in your browser only">Save</button>
          <button type="button" class="btn-quiet" data-rename-cancel title="Keep the current title">Cancel</button>
        </p>`
      : `<p class="thread-subject" data-ticket-title="${esc(next.title)}">${esc(next.title)}
          <button type="button" class="title-edit" data-rename-open data-ticket-id="${esc(ticket.id)}" data-ticket-title="${esc(next.title)}" title="Rename this ticket in your browser only. The Gorgias subject never changes." aria-label="Rename ticket">Rename</button>
        </p>`;
    return `<div class="pane-inner thread-inner">
      <header class="thread-head">
        <button type="button" class="thread-details-toggle" data-thread-details-toggle aria-expanded="${detailsOpen}" aria-controls="thread-details">
          <span data-thread-details-label>${detailsOpen ? "Hide" : "Show"} ticket details</span>
          <span class="thread-details-chevron" aria-hidden="true"></span>
        </button>
        <div class="thread-details" id="thread-details" data-thread-details ${detailsOpen ? "" : "hidden"}>
        <div>
          <h2>${esc(listCustomerName(ticket))}</h2>
          ${titleLine}
          ${typeLine}
        </div>
        <div class="thread-head-actions">
          <span class="ticket-id-badge" data-ticket-id-badge="${esc(ticket.id)}" title="Unique ticket id">${esc(ticket.id)}</span>
          <button type="button" class="btn-hairline" data-copy-link data-ticket-id="${esc(ticket.id)}" title="Copy a link to this ticket. The link opens this inbox with this ticket selected.">Copy link</button>
          <span class="status-line" title="Ticket status">${statusDot("status", observedTicketStatus(ticket))}${esc(observedTicketStatus(ticket))}</span>
          ${typeof ticket.gorgiasPriority === "string" && ticket.gorgiasPriority.trim() ? `<span class="status-line" title="Gorgias priority">${statusDot("priority", ticket.gorgiasPriority)}${esc(ticket.gorgiasPriority.trim().slice(0, 20))}</span>` : ""}
          ${ticket.gorgiasSpam ? `<span class="status-line" title="Marked as spam in Gorgias">${statusDot()}Spam</span>` : ""}
          ${ticket.gorgiasTrashed ? `<span class="status-line" title="Trashed in Gorgias">${statusDot()}Trashed</span>` : ""}
          ${ticket.gorgiasSnoozed ? `<span class="status-line" title="Snoozed in Gorgias">${statusDot("status", "snoozed")}Snoozed</span>` : ""}
          ${escalateControl}
          ${detailControls(ticket, next)}
        </div>
        </div>
      </header>
      <div class="thread-scroll">${ticket.projectionSource ? `<p class="history-notice" role="status">Partial webhook history; earlier messages may be missing. ${ticket.truncated ? "History or text is truncated." : ""}</p>` : ""}${timeline(ticket)}
      ${ticket.draftSuperseded ? `<p class="mute">An earlier draft is withheld because a newer customer message needs review.</p>` : ""}</div>
      ${next.capabilities?.summarizeThread === false ? "" : `<div class="summarize-row">
        <button type="button" class="btn-quiet" data-summarize="${esc(ticket.id)}" title="Show a short mute summary above the reply box">${esc(summarizeLabel)}</button>
      </div>`}
    </div>${renderLightbox()}`;
  }

  function paint() {
    if (!host) return;
    const wasRenaming = Boolean(renaming) && Boolean(host.querySelector?.("[data-rename-input]"));
    host.innerHTML = render(model);
    // #41: reopening the editor after a repaint refocuses. A mid-edit
    // repaint keeps the draft (render uses renaming.draft) and moves the
    // caret to the end — select-all would wipe the operator's typing.
    const editInput = host.querySelector("[data-rename-input]");
    if (editInput) {
      editInput.focus();
      if (wasRenaming) {
        editInput.setSelectionRange?.(editInput.value.length, editInput.value.length);
      } else {
        editInput.select();
      }
    }
  }

  function closeLightbox() {
    if (!lightbox) return;
    lightbox = null;
    paint();
  }

  function mount(el) {
    host = el;
    paint();
    el.onclick = (event) => {
      const detailsToggle = event.target.closest("[data-thread-details-toggle]");
      if (detailsToggle) {
        detailsOpen = !detailsOpen;
        // Keep keyboard focus and the conversation's scroll position intact.
        detailsToggle.setAttribute("aria-expanded", String(detailsOpen));
        detailsToggle.querySelector("[data-thread-details-label]").textContent = `${detailsOpen ? "Hide" : "Show"} ticket details`;
        el.querySelector("[data-thread-details]").hidden = !detailsOpen;
        return;
      }
      const renameOpen = event.target.closest("[data-rename-open]");
      if (renameOpen) {
        // #41: swap the title line for an inline input. Save on Enter or
        // blur; Escape cancels. The organ owns the persisted store.
        renaming = {
          ticketId: renameOpen.dataset.ticketId,
          original: renameOpen.dataset.ticketTitle || "",
          draft: renameOpen.dataset.ticketTitle || "",
        };
        paint();
        return;
      }
      const renameInput = event.target.closest?.("[data-rename-input]");
      if (renameInput) {
        // #41: clicks inside the input do nothing; the input event owns drafts.
        return;
      }
      const renameCancel = event.target.closest("[data-rename-cancel]");
      if (renameCancel) {
        renaming = null;
        paint();
        return;
      }
      const renameSave = event.target.closest("[data-rename-save]");
      if (renameSave) {
        const input = host.querySelector("[data-rename-input]");
        mailbox.publish(MAILBOX_TOPICS.THREAD_RENAME, {
          ticketId: renaming?.ticketId,
          title: input?.value || "",
        });
        renaming = null;
        paint();
        return;
      }
      // #43: native details toggling plus remembered state — a later
      // repaint re-renders the disclosure as it was left.
      const originalToggle = event.target.closest?.("[data-original-toggle]");
      if (originalToggle) {
        const id = originalToggle.dataset.originalId;
        if (id) originalToggle.hasAttribute("open") ? openOriginals.delete(id) : openOriginals.add(id);
        return;
      }
      const openAttach = event.target.closest("[data-attach-open]");
      if (openAttach) {
        lightbox = {
          url: openAttach.dataset.attachUrl || "",
          alt: openAttach.dataset.attachAlt || "Attachment",
        };
        paint();
        return;
      }
      if (event.target.closest("[data-attach-lightbox]") === event.target) {
        closeLightbox();
        return;
      }
      const copyLink = event.target.closest("[data-copy-link]");
      if (copyLink) {
        // #42: boot owns the clipboard and the address bar; the organ only
        // publishes the id. Never a Gorgias link.
        mailbox.publish(MAILBOX_TOPICS.THREAD_COPY_LINK, { ticketId: copyLink.dataset.ticketId });
        return;
      }
      const escalate = event.target.closest("[data-escalate]");
      if (escalate) {
        mailbox.publish(MAILBOX_TOPICS.THREAD_ESCALATE, { ticketId: escalate.dataset.escalate });
        return;
      }
      if (event.target.closest("[data-privacy-gate-open]")) {
        mailbox.publish(MAILBOX_TOPICS.PRIVACY_GATE_OPEN, {});
        return;
      }
      if (event.target.closest("[data-marketing-gate-open]")) {
        mailbox.publish(MAILBOX_TOPICS.MARKETING_GATE_OPEN, {});
        return;
      }
      if (event.target.closest("[data-bug-handled]")) {
        mailbox.publish(MAILBOX_TOPICS.BUG_HANDLED, { ticketId: model.ticket?.id });
        return;
      }
      const button = event.target.closest("[data-summarize]");
      if (button) {
        mailbox.publish(MAILBOX_TOPICS.COMPOSER_SUMMARIZE, { ticketId: button.dataset.summarize });
        return;
      }
      // #44: first-party detail controls publish through the mailbox; the
      // organ owns the browser store. Nothing here is a Gorgias write.
      const menuToggle = event.target.closest("[data-detail-menu]");
      if (menuToggle) {
        menuOpen = !menuOpen;
        menuTicketId = menuOpen ? model.ticket?.id || null : null;
        paint();
        return;
      }
      const markUnread = event.target.closest("[data-menu-mark-unread]");
      if (markUnread) {
        menuOpen = false;
        mailbox.publish(MAILBOX_TOPICS.THREAD_MARK_UNREAD, { ticketId: markUnread.dataset.ticketId });
        paint();
        return;
      }
      const prev = event.target.closest("[data-ticket-prev]");
      if (prev && !prev.disabled) {
        mailbox.publish(MAILBOX_TOPICS.THREAD_STEP, { delta: -1 });
        return;
      }
      const nextTicket = event.target.closest("[data-ticket-next]");
      if (nextTicket && !nextTicket.disabled) {
        mailbox.publish(MAILBOX_TOPICS.THREAD_STEP, { delta: 1 });
        return;
      }
    };
    el.onchange = (event) => {
      const status = event.target.closest?.("[data-detail-status]");
      if (status) {
        mailbox.publish(MAILBOX_TOPICS.THREAD_STATE, { ticketId: status.dataset.ticketId, field: "status", value: status.value });
        return;
      }
      const priority = event.target.closest?.("[data-detail-priority]");
      if (priority) {
        mailbox.publish(MAILBOX_TOPICS.THREAD_STATE, { ticketId: priority.dataset.ticketId, field: "priority", value: priority.value });
        return;
      }
      const assignee = event.target.closest?.("[data-detail-assignee]");
      if (assignee) {
        mailbox.publish(MAILBOX_TOPICS.THREAD_STATE, { ticketId: assignee.dataset.ticketId, field: "assignee", value: assignee.value });
        return;
      }
    };
    el.oninput = (event) => {
      // #41: keep the typed draft so a mid-edit repaint re-renders it.
      const input = event.target.closest?.("[data-rename-input]");
      if (input && renaming) renaming.draft = input.value;
    };
    el.onkeydown = (event) => {
      if (event.key === "Escape" && lightbox) {
        event.preventDefault?.();
        closeLightbox();
      }
      const input = event.target.closest?.("[data-rename-input]");
      if (input) {
        if (event.key === "Enter") {
          event.preventDefault();
          mailbox.publish(MAILBOX_TOPICS.THREAD_RENAME, {
            ticketId: renaming?.ticketId,
            title: input.value || "",
          });
          renaming = null;
          paint();
        } else if (event.key === "Escape") {
          event.preventDefault();
          renaming = null;
          paint();
        }
      }
    };
    el.onfocusout = (event) => {
      // #41: blur saves unless the blur went to Save/Cancel themselves.
      const input = event.target.closest?.("[data-rename-input]");
      if (!input || !renaming) return;
      if (event.relatedTarget?.closest?.("[data-rename-save],[data-rename-cancel]")) return;
      mailbox.publish(MAILBOX_TOPICS.THREAD_RENAME, {
        ticketId: renaming.ticketId,
        title: input.value || "",
      });
      renaming = null;
      paint();
    };
  }

  return {
    id: "thread",
    project,
    render,
    update(input) {
      const next = project(input);
      if (next.ticket?.id !== model.ticket?.id) detailsOpen = false;
      // #44: the overflow menu is per-ticket; navigating away closes it.
      if (menuOpen && menuTicketId && next.ticket?.id !== menuTicketId) menuOpen = false;
      model = next;
      return model;
    },
    mount,
  };
}
