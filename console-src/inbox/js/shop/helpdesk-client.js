import { TOOL_NAMES } from "./helpdesk-tools.js";

// Keep the inbox mount: Caddy strips /inbox before proxying to the inbox API.
// Origin-root /console/api belongs to the separate dashboard service.
export const HELPDESK_HTTP_PATH = import.meta.url.startsWith("http") ? new URL("../../console/api/helpdesk", import.meta.url).pathname : "/console/api/helpdesk";

/**
 * Browser/Node client for the live helpdesk.* tools.
 * Same payloads as MCP and CLI. No GraphQL here.
 *
 * @param {{ invoke?: Function, url?: string, fetch?: typeof fetch }} [opts]
 */
export function createHelpdeskClient(opts = {}) {
  const url = opts.url || HELPDESK_HTTP_PATH;
  const fetchImpl = opts.fetch || globalThis.fetch;
  const custom = opts.invoke;

  async function invoke(tool, args = {}) {
    if (custom) return custom(tool, args);
    if (typeof fetchImpl !== "function") {
      throw new Error("helpdesk client has no fetch");
    }
    const response = await fetchImpl(url, {
      signal: AbortSignal.timeout(15000),
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ tool, arguments: args || {} }),
    });
    if (response.status === 401 || response.redirected) throw new Error("Your session expired. Sign in again to continue.");
    const payload = await response.json();
    if (!payload || typeof payload !== "object") {
      throw new Error("helpdesk client received an empty payload");
    }
    return payload;
  }

  return {
    id: "helpdesk-client",
    tools: TOOL_NAMES,
    invoke,
    listTickets(args) {
      return invoke("helpdesk.list_tickets", args);
    },
    getTicket(args) {
      return invoke("helpdesk.get_ticket", args);
    },
    getCustomer(args) {
      return invoke("helpdesk.get_customer", args);
    },
    getOrder(args) {
      return invoke("helpdesk.get_order", args);
    },
    getReturns(args) {
      return invoke("helpdesk.get_returns", args);
    },
    listPastOrders(args) {
      return invoke("helpdesk.list_past_orders", args);
    },
    draftReply(args) {
      return invoke("helpdesk.draft_reply", args);
    },
    summarizeThread(args) {
      return invoke("helpdesk.summarize_thread", args);
    },
    searchMacros(args) {
      return invoke("helpdesk.search_macros", args);
    },
    applyMacro(args) {
      return invoke("helpdesk.apply_macro", args);
    },
    ingestEmail(args) {
      return invoke("helpdesk.ingest_email", args);
    },
    ingestChat(args) {
      return invoke("helpdesk.ingest_chat", args);
    },
    pullMailbox(args) {
      return invoke("helpdesk.pull_mailbox", args);
    },
    escalateTicket(args) {
      return invoke("helpdesk.escalate_ticket", args);
    },
    writeGateStatus(args) {
      return invoke("helpdesk.write_gate_status", args);
    },
    bridgeStatus(args) {
      return invoke("helpdesk.bridge_status", args || {});
    },
    sendReply(args) {
      return invoke("helpdesk.send_reply", args);
    },
  };
}
