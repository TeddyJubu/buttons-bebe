import { MAILBOX_TOPICS } from "../contracts.js";
import { ACTIVATE_SEND_MESSAGE } from "../send-access.js";
import { listCustomerName } from "../shop/clerk-ticket.js";
import { esc } from "../util.js";

function macroTitle(macro) {
  return macro.title || macro.name || "";
}

function macroHaystack(macro) {
  return `${macro.id || ""} ${macroTitle(macro)} ${(macro.tags || []).join(" ")} ${macro.body || ""}`.toLowerCase();
}

export function composeMacroText(macro, currentBody = "", mode = "replace") {
  const text = macro?.body || "";
  const current = String(currentBody || "");
  if (mode === "append" && current.trim()) return `${current.trimEnd()}\n\n${text}`;
  return text;
}

/**
 * Composer tissue.
 * In: `{ ticket, draft, summarize, macros, body }`
 * Out: body / insert / discard / regenerate / send. AI strip and macros never Send.
 * Default paint is Draft strip + textarea + Send. Macro search stays collapsed
 * until focus, `/`, or the Macros hairline. Once open, search lives inside the
 * composer box. Replace overwrites; Append adds. Draft-strip verbs are
 * Use draft / Regenerate / Dismiss. The strip sits above the composer box.
 */
export function createComposerTissue({ mailbox }) {
  let model = {
    ticket: null,
    draft: "",
    summarize: "",
    macros: [],
    body: "",
    strip: "",
    rewriteViaParent: false,
    sendViaParent: false,
    sendConfirm: false,
    sendBusy: false,
    sendInfo: "",
    sendCloseRequested: false,
    rewriteInstruction: "",
    rewriteError: "",
    rewriteBusy: false,
    noteViaParent: false,
    noteConfirm: false,
    noteBusy: false,
    noteError: "",
    noteInfo: "",
    query: "",
    selectedMacroId: "",
    searchOpen: false,
    writeGate: null,
    bridgeStatus: null,
    sendError: "",
  };

  function project(input) {
    return {
      capabilities: input.capabilities || {},
      ticket: input.ticket || null,
      draft: input.draft || "",
      summarize: input.summarize || "",
      macros: input.macros || [],
      body: input.body || "",
      strip: input.strip ?? input.draft ?? "",
      rewriteViaParent: input.rewriteViaParent === true,
      rewriteInstruction: input.rewriteInstruction || "",
      rewriteError: input.rewriteError || "",
      rewriteBusy: input.rewriteBusy === true,
      sendViaParent: input.sendViaParent === true,
      sendConfirm: input.sendConfirm === true,
      sendBusy: input.sendBusy === true,
      sendInfo: input.sendInfo || "",
      sendCloseRequested: input.sendCloseRequested === true,
      noteViaParent: input.noteViaParent === true,
      noteConfirm: input.noteConfirm === true,
      noteBusy: input.noteBusy === true,
      noteError: input.noteError || "",
      noteInfo: input.noteInfo || "",
      stripReadonly: input.stripReadonly === true,
      stripNote: input.stripNote || "",
      query: input.query || "",
      selectedMacroId: input.selectedMacroId || "",
      searchOpen: input.searchOpen === true,
      writeGate: input.writeGate || null,
      bridgeStatus: input.bridgeStatus || null,
      sendError: input.sendError || "",
    };
  }

  function routeHint(_next = model) {
    return ACTIVATE_SEND_MESSAGE;
  }

  function sendDisabled(next = model) {
    // In production this control explains the permanent lock, not delivery.
    if (next.capabilities?.sendReply === false) return false;
    return !String(next.body || "").trim();
  }

  function hideSendAndClose(next = model) {
    return next.ticket?.status === "closed";
  }

  function visibleMacros(next = model) {
    const q = String(next.query || "").trim().toLowerCase();
    return (next.macros || []).filter((macro) => !q || macroHaystack(macro).includes(q));
  }

  function selectedMacro(next = model) {
    return (next.macros || []).find((item) => item.id === next.selectedMacroId) || null;
  }

  function recipient(ticket) {
    if (!ticket) return { name: "", email: "" };
    const candidates = [ticket.fromEmail, ticket.toEmail];
    let email = "";
    for (const candidate of candidates) {
      const addr = String(candidate || "").trim().toLowerCase();
      if (!addr || !addr.includes("@")) continue;
      // Never show the shop/AgentMail login as the customer To line.
      if (addr.endsWith("@agentmail.to")) continue;
      email = String(candidate).trim();
      break;
    }
    return {
      name: listCustomerName(ticket) || ticket.messages?.[0]?.fromName || ticket.messages?.[0]?.name || "",
      email,
    };
  }

  /** Issue #35: print the address once when the display name is the address. */
  function toLine(ticket) {
    const to = recipient(ticket);
    if (!to.name) return `<span class="mute">${esc(to.email)}</span>`;
    if (!to.email || to.name.toLowerCase() === to.email.toLowerCase()) {
      return `<strong>${esc(to.name)}</strong>`;
    }
    return `<strong>${esc(to.name)}</strong> <span class="mute">${esc(to.email)}</span>`;
  }

  function render(next = model) {
    const ticket = next.ticket;
    if (!ticket) return `<div class="composer empty-pane">Select a ticket to reply.</div>`;
    const to = recipient(ticket);
    const gate = next.writeGate || {};
    const refused = Array.isArray(gate.refused) ? gate.refused : ["refund", "cancel"];
    const moneyGated = refused.includes("refund") || refused.includes("cancel") || gate.mutationsEnabled === false;
    const hasJoinedOrder = Boolean(ticket.orderId);
    const writeGate = moneyGated && hasJoinedOrder
      ? `<p class="write-gate" data-write-gate>Refunds and cancels are gated.</p>`
      : "";
    const macros = visibleMacros(next);
    const selected = selectedMacro(next);
    const macroItems = macros.map((macro) => (
      `<button type="button" class="macro-row${macro.id === next.selectedMacroId ? " is-selected" : ""}" data-macro="${esc(macro.id)}">
        <span class="macro-bar"></span>
        <span class="macro-title">${esc(macroTitle(macro))}</span>
      </button>`
    )).join("");
    const peek = next.summarize
      ? `<div class="summarize-peek" data-summarize-peek>
          <p class="draft-kicker">Thread</p>
          <p class="summarize-text">${esc(next.summarize)}</p>
        </div>`
      : "";
    const stripText = next.strip || "";
    // A projection draft ([SENSITIVE…]) must never read as a calm note.
    const sensitive = /\[SENSITIVE/i.test(stripText);
    const kicker = sensitive
      ? "Sensitive draft — review before sending"
      : next.stripReadonly
        ? "AI draft · not sent · read only"
        : "AI draft";
    // No live draft lane, no Regenerate: there is nothing to ask for a new
    // draft from. Use draft still copies the shown text; Dismiss still hides it.
    const canRegenerate = next.capabilities?.draftReply !== false;
    const showRegenerate = next.rewriteViaParent || canRegenerate;
    const noteBtn = next.noteViaParent ? `<button type="button" class="btn-quiet" data-note${next.noteBusy ? " disabled" : ""} title="Post this draft as a staff-only internal note">Post as note</button>` : "";
    const noteConfirmPanel = next.noteViaParent && next.noteConfirm ? `<div class="note-confirm" data-note-confirm><p class="note-confirm-title">Post as a staff-only internal note?</p><p class="note-confirm-text">${esc(next.strip)}</p><div class="note-confirm-actions"><button type="button" class="btn-quiet" data-note-confirm>Confirm post</button><button type="button" class="btn-quiet btn-dismiss" data-note-cancel>Cancel</button></div></div>` : "";
    const noteStatus = next.noteInfo ? `<p class="note-info" role="status">${esc(next.noteInfo)}</p>` : next.noteError ? `<p class="rewrite-error" role="alert">${esc(next.noteError)}</p>` : "";
    const regenLabel = next.rewriteBusy ? "Rewriting…" : "Regenerate";
    const regenDisabled = next.rewriteBusy ? " disabled" : "";
    const regenBtn = showRegenerate ? `<button type="button" class="btn-quiet" data-regenerate${regenDisabled} title="Ask the console for a new AI draft">${regenLabel}</button>` : "";
    const rewriteRow = next.rewriteViaParent ? `<label class="rewrite-row"><span>Tell the AI how to change it</span><input class="rewrite-input" data-rewrite-instruction type="text" placeholder="e.g. make it warmer and offer to reship free" value="${esc(next.rewriteInstruction)}"></label>${next.rewriteError ? `<p class="rewrite-error" role="alert">${esc(next.rewriteError)}</p>` : ""}` : "";
    const strip = stripText
      ? `<div class="draft-strip${sensitive ? " is-sensitive" : ""}" data-draft-strip${sensitive ? ' data-sensitive="true"' : ""}>
          <div class="draft-body">
            <p class="draft-kicker">${esc(kicker)}</p>
            <p class="draft-text">${esc(stripText)}</p>
            ${next.stripNote ? `<p class="mute draft-note">${esc(next.stripNote)}</p>` : ""}
          </div>
          <div class="draft-actions">
            <button type="button" class="btn-quiet" data-insert title="Copy the AI draft into the reply box">Use draft</button>
            ${regenBtn}
            ${rewriteRow}
            ${noteBtn}
            ${noteConfirmPanel}
            ${noteStatus}
            <button type="button" class="btn-quiet btn-dismiss" data-discard title="Hide this AI draft">Dismiss</button>
          </div>
        </div>`
      : "";
    const idle = sendDisabled(next);
    const sendTitle = next.sendViaParent ? "Send this reply to the customer via the console" : ACTIVATE_SEND_MESSAGE;
    const sendLabel = next.sendBusy ? "Sending…" : "Send";
    const sendBtn = `<button type="button" class="btn-ink btn-send${idle && !next.sendViaParent ? " is-disabled" : ""}" data-send ${idle && !next.sendViaParent ? "disabled" : ""} title="${esc(sendTitle)}">${sendLabel}</button>`;
    // Fail-closed: an organ repaint clears a stale confirm instead of posting it.
    const sendConfirmPanel = next.sendViaParent && next.sendConfirm ? `<div class="note-confirm" data-send-confirm><p class="note-confirm-title">Send this reply to ${esc(to.name || to.email)}${to.name && to.email ? ` (${esc(to.email)})` : ""}${next.sendCloseRequested ? " and mark closed in this inbox" : ""}?</p><p class="note-confirm-text">${esc(next.body)}</p>${next.sendCloseRequested ? `<p class="mute">Gorgias stays as-is; only this inbox view changes.</p>` : ""}<label class="send-approve"><input type="checkbox" data-send-approve> Approve as a learning example after confirmed delivery</label><div class="note-confirm-actions"><button type="button" class="btn-ink btn-send" data-send-confirm>Confirm send</button><button type="button" class="btn-quiet btn-dismiss" data-send-cancel>Cancel</button></div></div>` : "";
    const sendStatus = next.sendInfo ? `<p class="note-info" role="status">${esc(next.sendInfo)}</p>` : "";
    const sendClose = hideSendAndClose(next)
      ? ""
      : `<button type="button" class="btn-hairline${idle ? " is-disabled" : ""}" data-send-close ${idle ? "disabled" : ""} title="${esc(ACTIVATE_SEND_MESSAGE)}">Send &amp; close</button>`;
    const macroLocked = selected ? "" : "disabled";
    const searchOpen = next.searchOpen === true;
    const picker = searchOpen
      ? `<input class="macro-search" data-macro-search type="search" placeholder="Search macros by name or tags" value="${esc(next.query)}" aria-label="Search macros" title="Search saved reply macros">
        <div class="macro-list" data-macro-list>${macroItems || `<p class="macro-empty">No macros match.</p>`}</div>
        <div class="macro-actions">
          <button type="button" class="btn-quiet" data-macro-insert ${macroLocked} title="Replace the reply box with this macro">Replace</button>
          <button type="button" class="btn-quiet" data-macro-append ${macroLocked} title="Add this macro after the current reply">Append</button>
        </div>`
      : "";
    const routeLine = `<p class="composer-route mute" data-send-route>${esc(routeHint(next))}</p>`;
    const err = next.sendError
      ? `<p class="composer-send-error" data-send-error role="alert">${esc(next.sendError)}</p>`
      : "";
    return `<section class="composer" data-composer>
      ${peek}
      <div class="composer-to"><span>To</span> ${toLine(ticket)}</div>
      ${writeGate}
      ${strip}
      <div class="composer-box" data-macro-open="${searchOpen ? "true" : "false"}">
        ${picker}
        <textarea data-body placeholder="Write the reply. The human always sends." title="Type the customer reply here. You still choose Send."${next.sendConfirm ? " disabled" : ""}>${esc(next.body)}</textarea>
      </div>
      <div class="composer-actions">
        ${next.capabilities?.searchMacros === false ? "" : `<button type="button" class="btn-hairline" data-macros aria-expanded="${searchOpen ? "true" : "false"}" title="Open saved reply macros">Macros</button>`}
        <div class="composer-send">
          ${sendBtn}
          ${sendClose}
        </div>
      </div>
      ${routeLine}
      ${sendConfirmPanel}
      ${sendStatus}
      ${err}
    </section>`;
  }

  function emitBody(text) {
    model = { ...model, body: text };
    mailbox.publish(MAILBOX_TOPICS.COMPOSER_BODY, { text });
  }

  function setSearchOpen(open) {
    const next = Boolean(open);
    if (model.searchOpen === next) return false;
    model = {
      ...model,
      searchOpen: next,
      query: next ? model.query : "",
      selectedMacroId: next ? model.selectedMacroId : "",
    };
    mailbox.publish(MAILBOX_TOPICS.COMPOSER_MACROS, { open: next });
    return true;
  }

  function applySelected(mode) {
    const macro = selectedMacro(model);
    if (!macro) return;
    const text = composeMacroText(macro, model.body, mode);
    emitBody(text);
    model = {
      ...model,
      body: text,
      searchOpen: false,
      query: "",
      selectedMacroId: "",
    };
    mailbox.publish(MAILBOX_TOPICS.COMPOSER_MACROS, { open: false });
    mailbox.publish(MAILBOX_TOPICS.COMPOSER_INSERT, { text, mode, macroId: macro.id });
  }

  function focusSearch(el) {
    const again = el.querySelector?.("[data-macro-search]");
    if (again?.focus) again.focus();
    return again;
  }

  function mount(el) {
    el.innerHTML = render(model);
    let macrosPointer = false;
    el.oninput = (event) => {
      if (event.target.matches("[data-body]")) emitBody(event.target.value);
      if (event.target.matches("[data-rewrite-instruction]")) model = { ...model, rewriteInstruction: event.target.value };
      if (event.target.matches("[data-macro-search]")) {
        model = { ...model, query: event.target.value, searchOpen: true };
        const keep = event.target;
        const start = keep.selectionStart;
        el.innerHTML = render(model);
        const again = el.querySelector("[data-macro-search]");
        if (again) {
          again.focus();
          again.setSelectionRange(start, start);
        }
      }
    };
    el.onpointerdown = (event) => {
      macrosPointer = Boolean(event.target.closest?.("[data-macros]"));
    };
    el.onfocusin = (event) => {
      if (macrosPointer) return;
      if (event.target.matches?.("[data-macros]") && setSearchOpen(true)) {
        el.innerHTML = render(model);
        focusSearch(el);
      }
    };
    el.onkeydown = (event) => {
      if (event.target.matches?.("[data-body]") && event.key === "/" && !model.searchOpen) {
        event.preventDefault?.();
        setSearchOpen(true);
        el.innerHTML = render(model);
        focusSearch(el);
      }
    };
    el.onclick = (event) => {
      if (event.target.closest?.("[data-macros]")) {
        macrosPointer = false;
        if (setSearchOpen(true)) {
          el.innerHTML = render(model);
          focusSearch(el);
        }
        return;
      }
      const row = event.target.closest("[data-macro]");
      if (row) {
        model = { ...model, selectedMacroId: row.dataset.macro };
        el.innerHTML = render(model);
        return;
      }
      if (event.target.closest("[data-macro-insert]")) {
        applySelected("replace");
        el.innerHTML = render(model);
        return;
      }
      if (event.target.closest("[data-macro-append]")) {
        applySelected("append");
        el.innerHTML = render(model);
        return;
      }
      if (event.target.closest("[data-insert]")) {
        const text = model.strip || model.draft || "";
        const next = model.body ? `${model.body}\n\n${text}` : text;
        emitBody(next);
        model = { ...model, body: next, strip: "" };
        mailbox.publish(MAILBOX_TOPICS.COMPOSER_INSERT, { text });
        el.innerHTML = render(model);
        return;
      }
      if (event.target.closest("[data-regenerate]")) {
        mailbox.publish(MAILBOX_TOPICS.COMPOSER_REGENERATE, { instruction: model.rewriteInstruction || "" });
        return;
      }
      if (event.target.closest("[data-discard]")) {
        model = { ...model, strip: "" };
        mailbox.publish(MAILBOX_TOPICS.COMPOSER_DISCARD, {});
        el.innerHTML = render(model);
        return;
      }
      if (event.target.closest("[data-note]")) {
        if (model.noteBusy) return;
        model = { ...model, noteConfirm: true, noteError: "", noteInfo: "" };
        el.innerHTML = render(model);
        return;
      }
      if (event.target.closest("[data-note-cancel]")) {
        model = { ...model, noteConfirm: false };
        el.innerHTML = render(model);
        return;
      }
      if (event.target.closest("[data-note-confirm]")) {
        model = { ...model, noteConfirm: false, noteBusy: true, noteError: "", noteInfo: "" };
        el.innerHTML = render(model);
        mailbox.publish(MAILBOX_TOPICS.COMPOSER_NOTE, { text: model.strip || "" });
        return;
      }
      if (event.target.closest("[data-send-close]")) {
        if (model.sendViaParent) {
          if (model.sendBusy) return;
          if (hideSendAndClose(model)) return;
          if (!String(model.body || "").trim()) {
            model = { ...model, sendError: "Write the reply first." };
            el.innerHTML = render(model);
            return;
          }
          model = { ...model, sendConfirm: true, sendCloseRequested: true, sendError: "", sendInfo: "" };
          el.innerHTML = render(model);
          return;
        }
        if (sendDisabled(model) || hideSendAndClose(model)) return;
        mailbox.publish(MAILBOX_TOPICS.COMPOSER_SEND, { text: model.body, close: true });
        return;
      }
      if (event.target.closest("[data-send]")) {
        if (model.sendViaParent) {
          if (model.sendBusy) return;
          if (!String(model.body || "").trim()) {
            model = { ...model, sendError: "Write the reply first." };
            el.innerHTML = render(model);
            return;
          }
          model = { ...model, sendConfirm: true, sendError: "", sendInfo: "" };
          el.innerHTML = render(model);
          return;
        }
        if (sendDisabled(model)) return;
        mailbox.publish(MAILBOX_TOPICS.COMPOSER_SEND, { text: model.body, close: false });
      }
      if (event.target.closest("[data-send-cancel]")) {
        model = { ...model, sendConfirm: false, sendCloseRequested: false };
        el.innerHTML = render(model);
        return;
      }
      if (event.target.closest("[data-send-confirm]")) {
        const approveBox = el.querySelector("[data-send-approve]");
        model = { ...model, sendConfirm: false, sendBusy: true, sendError: "", sendInfo: "" };
        el.innerHTML = render(model);
        mailbox.publish(MAILBOX_TOPICS.COMPOSER_SEND_CONFIRMED, { text: model.body || "", approveLearning: !!(approveBox && approveBox.checked), close: model.sendCloseRequested === true });
        model = { ...model, sendCloseRequested: false };
        return;
      }
    };
  }

  return {
    id: "composer",
    project,
    render,
    sendDisabled,
    hideSendAndClose,
    visibleMacros,
    selectedMacro,
    update(input) {
      model = project({ ...model, ...input });
      return model;
    },
    mount,
  };
}
