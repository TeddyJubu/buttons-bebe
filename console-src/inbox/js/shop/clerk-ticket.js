/**
 * First-party Clerk ticket projection.
 * customerName is ours — never Customer.displayName.
 * status is open / closed / snoozed — never Return.status OPEN.
 * Inbound From is the customer persona, not the AgentMail/shop mailbox login.
 */

export const TICKET_STATUSES = Object.freeze(["open", "closed", "snoozed"]);
export const STORE_NAME = "Buttons Bebe";

const MAILBOX_EMAILS = new Set([
  "helpdesk-support@agentmail.to",
  "teddyjubu@agentmail.to",
]);
const MAILBOX_NAMES = new Set([
  "demo shop support",
  "demo shop",
  "agentmail",
  "teddyjubu",
  "helpdesk-support",
]);

function clean(value) {
  return String(value ?? "").trim();
}

function emailLocal(email) {
  const addr = clean(email).toLowerCase();
  return addr.includes("@") ? addr.split("@", 1)[0] : addr;
}

/** Gorgias-style display name from the address local part. Issue #35.
 * Gorgias masks addresses in webhook payloads (e***a@gmail.com); the mask
 * asterisks are observed data, so they stay in the derived name. */
export function derivedCustomerName(email) {
  const addr = clean(email).toLowerCase();
  if (!addr || addr.split("@").length - 1 !== 1) return "";
  const [local] = addr.split("@");
  // A mailbox/login address is never a customer persona.
  if (isMailboxEmail(addr)) return "";
  const words = local.split(/[^a-z0-9*]+/i).filter(Boolean);
  if (!words.length) return "";
  return words.map((word) => word[0].toUpperCase() + word.slice(1)).join(" ");
}

export function isMailboxEmail(email) {
  const addr = clean(email).toLowerCase();
  if (!addr) return false;
  if (MAILBOX_EMAILS.has(addr)) return true;
  return addr.endsWith("@agentmail.to") && MAILBOX_NAMES.has(emailLocal(addr));
}

export function isMailboxName(name, email) {
  const named = clean(name);
  if (!named) return true;
  const lower = named.toLowerCase();
  if (MAILBOX_NAMES.has(lower)) return true;
  if (isMailboxEmail(named)) return true;
  const addr = clean(email).toLowerCase();
  const local = emailLocal(addr);
  return Boolean(isMailboxEmail(email) && (lower === addr || (local && lower === local)));
}

export function isAgentMessage(message) {
  if (!message) return false;
  if (message.fromAgent === true) return true;
  return clean(message.from).toLowerCase() === "agent";
}

function splitFrom(value) {
  const text = clean(value);
  const angled = text.match(/^(.*?)\s*<\s*([^<>\s]+@[^<>\s]+)\s*>$/);
  if (angled) return { name: clean(angled[1]), email: angled[2].toLowerCase() };
  const bare = text.match(/^([^\s@]+@[^\s@]+)$/);
  if (bare) return { name: bare[1], email: bare[1].toLowerCase() };
  return { name: text, email: "" };
}

export function messageSpeaker(ticket, message) {
  if (isAgentMessage(message)) {
    const name = clean(message?.fromName || message?.name) || STORE_NAME;
    return { role: "agent", name, email: "" };
  }
  let fromEmail = clean(message?.fromEmail || ticket?.fromEmail);
  const candidates = [message?.fromName, message?.name, ticket?.customerName];
  const rawFrom = message?.from;
  if (rawFrom && !["customer", "agent", ""].includes(clean(rawFrom).toLowerCase())) {
    const parsed = splitFrom(rawFrom);
    if (parsed.name) candidates.unshift(parsed.name);
    fromEmail = fromEmail || parsed.email;
  }
  let name = "";
  for (const candidate of candidates) {
    const text = clean(candidate);
    // An address passed through customerName is not a display name (#35).
    const textIsAddress = text && text.toLowerCase() === fromEmail.toLowerCase();
    if (text && !textIsAddress && !isMailboxName(text, fromEmail)) {
      name = text;
      break;
    }
  }
  if (!name) {
    name = derivedCustomerName(fromEmail) || (fromEmail && !isMailboxEmail(fromEmail) ? fromEmail : "Customer");
  }
  return {
    role: "customer",
    name,
    email: fromEmail && !isMailboxEmail(fromEmail) ? fromEmail : "",
  };
}

export function listCustomerName(ticket) {
  const name = clean(ticket?.customerName);
  const email = clean(ticket?.fromEmail);
  // The projection exports the address as customerName when nothing better
  // was observed; that is "no observed name", not a real display name.
  const nameIsAddress = name && name.toLowerCase() === email.toLowerCase();
  if (name && !nameIsAddress && !isMailboxName(name, email) && !isMailboxEmail(name)) return name;
  for (const message of ticket?.messages || []) {
    if (isAgentMessage(message)) continue;
    const speaker = messageSpeaker(ticket, message);
    if (speaker.name && !isMailboxName(speaker.name, speaker.email || email)) {
      return speaker.name;
    }
  }
  // No observed name ⇒ derive from the address; never print the raw address.
  const derived = derivedCustomerName(email);
  if (derived) return derived;
  if (email && !isMailboxEmail(email)) return email;
  if (name && !isMailboxName(name, email)) return name;
  return isMailboxName(name, email) ? "Customer" : (name || "Customer");
}

export function clerkTicketRow(ticket) {
  if (!ticket) return null;
  return {
    id: ticket.id,
    customerName: listCustomerName(ticket),
    subject: ticket.subject || "",
    snippet: ticket.snippet || "",
    status: ticket.status,
    updatedAt: ticket.updatedAt,
    customerId: ticket.customerId || null,
    orderId: ticket.orderId ?? null,
    requestType: ticket.requestType || null,
    privacySubtype: ticket.privacySubtype || null,
    privacyHandled: Boolean(ticket.privacyHandled),
    unsubscribeHandled: Boolean(ticket.unsubscribeHandled),
    bugHandled: Boolean(ticket.bugHandled),
    severity: ticket.severity || null,
    device: ticket.device || null,
  };
}

export function talkMessages(ticket) {
  return (ticket?.messages || []).filter((message) => message.kind !== "status");
}

export function clerkStatusEvents(ticket) {
  if (Array.isArray(ticket?.statusEvents) && ticket.statusEvents.length) {
    return ticket.statusEvents.map((event) => ({
      at: event.at,
      status: event.status,
      note: event.note || "",
    }));
  }
  return (ticket?.messages || [])
    .filter((message) => message.kind === "status")
    .map((message) => ({
      at: message.at,
      status: ticket.status,
      note: message.body || "",
    }));
}

export function clerkTicket(ticket) {
  if (!ticket) return null;
  return {
    ...clerkTicketRow(ticket),
    messages: talkMessages(ticket).map((message) => ({ ...message })),
    statusEvents: clerkStatusEvents(ticket),
    stubDraft: ticket.stubDraft,
    stubSummary: ticket.stubSummary,
    toEmail: ticket.toEmail,
    assignee: ticket.assignee,
    view: ticket.view,
    escalated: Boolean(ticket.escalated),
    escalationReason: ticket.escalationReason || "",
  };
}
