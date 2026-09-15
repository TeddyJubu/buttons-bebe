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
    const status = typeof ticket?.status === "string" ? ticket.status.trim().toLowerCase() : "";
    if (!status || status === "unknown") return ticket?.projectionSource ? "Status unknown" : screenStatus(ticket?.status);
    return screenStatus(status);
  }
  let model = { ticket: null };
  let lightbox = null;
  let host = null;

  function project(input) {
    return { ticket: input.ticket || null, capabilities: input.capabilities || {} };
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
    return items.map((item) => item.html).join("");
  }

  function render(next = model) {
    const ticket = next.ticket;
    if (!ticket) {
      return `<div class="pane-inner"><p class="empty-pane">Select a ticket.</p></div>${renderLightbox()}`;
    }
    if (ticket.historyUnavailable) return `<div class="pane-inner"><p role="alert">Ticket history is unavailable. Refresh to retry. Existing customer records have not been reset.</p><a href="/console/">Open support console</a></div>`;
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
    return `<div class="pane-inner thread-inner">
      <header class="thread-head">
        <div>
          <h2>${esc(listCustomerName(ticket))}</h2>
          <p class="thread-subject">${esc(ticket.subject)}</p>
          ${typeLine}
        </div>
        <div class="thread-head-actions">
          <span class="status-badge" title="Ticket status">${esc(observedTicketStatus(ticket))}</span>
          ${typeof ticket.gorgiasPriority === "string" && ticket.gorgiasPriority.trim() ? `<span class="status-badge" title="Gorgias priority">${esc(ticket.gorgiasPriority.trim().slice(0, 20))}</span>` : ""}
          ${ticket.gorgiasSpam ? `<span class="status-badge" title="Marked as spam in Gorgias">Spam</span>` : ""}
          ${ticket.gorgiasTrashed ? `<span class="status-badge" title="Trashed in Gorgias">Trashed</span>` : ""}
          ${ticket.gorgiasSnoozed ? `<span class="status-badge" title="Snoozed in Gorgias">Snoozed</span>` : ""}
          ${escalateControl}
        </div>
      </header>
      <div class="thread-scroll">${ticket.projectionSource ? `<p class="history-notice" role="status">Partial webhook history; earlier messages may be missing. ${ticket.truncated ? "History or text is truncated." : ""} ${ticket.projection?.stale ? "Snapshot is stale; refresh is delayed." : ""}</p>` : ""}${timeline(ticket)}
      ${ticket.draftSuperseded ? `<p class="mute">An earlier draft is withheld because a newer customer message needs review.</p>` : ""}
      ${ticket.readonlyDraft ? `<article class="bubble"><strong>AI draft · not sent · read only</strong><p>${esc(ticket.readonlyDraft)}</p><p class="mute">Source message: ${esc(ticket.draftSourceMessageId || "")} · ${esc(ticket.draftSourceMessageAt || "")}</p><p class="mute">${esc(ticket.draftReason || "")}</p></article>` : ""}</div>
      ${next.capabilities?.summarizeThread === false ? "" : `<div class="summarize-row">
        <button type="button" class="btn-quiet" data-summarize="${esc(ticket.id)}" title="Show a short mute summary above the reply box">${esc(summarizeLabel)}</button>
      </div>`}
    </div>${renderLightbox()}`;
  }

  function paint() {
    if (!host) return;
    host.innerHTML = render(model);
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
      if (!button) return;
      mailbox.publish(MAILBOX_TOPICS.COMPOSER_SUMMARIZE, { ticketId: button.dataset.summarize });
    };
    el.onkeydown = (event) => {
      if (event.key === "Escape" && lightbox) {
        event.preventDefault?.();
        closeLightbox();
      }
    };
  }

  return {
    id: "thread",
    project,
    render,
    update(input) {
      model = project(input);
      return model;
    },
    mount,
  };
}
