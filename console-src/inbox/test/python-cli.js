import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createHelpdeskClient } from "../js/shop/helpdesk-client.js";

// Shared python-CLI bridge for tests: one argv mapping, kept in sync with the
// helpdesk CLI as it evolves (cubic: two diverging copies were worse than one).
const here = dirname(fileURLToPath(import.meta.url));
const helpdeskRoot = join(here, "../../helpdesk-agent");
const selectedPython = process.env.INBOX_PYTHON || process.env.PYTHON || "python3";
const python = selectedPython.includes("/") ? resolve(process.cwd(), selectedPython) : selectedPython;

export function pythonInvoke(tool, args) {
  const argv = [python, "-m", "helpdesk"];
  if (tool === "helpdesk.list_tickets") {
    argv.push("list-tickets", "--view", String(args.view || "open"), "--limit", String(args.limit || 20));
  } else if (tool === "helpdesk.get_ticket") {
    argv.push("get-ticket", "--ticket-id", String(args.ticketId));
  } else if (tool === "helpdesk.get_customer") {
    argv.push("get-customer", "--shop", args.shop, "--customer-id", args.customerId);
  } else if (tool === "helpdesk.get_order") {
    argv.push("get-order", "--shop", args.shop, "--order-id", args.orderId);
  } else if (tool === "helpdesk.get_returns") {
    argv.push("get-returns", "--shop", args.shop, "--order-id", args.orderId);
  } else if (tool === "helpdesk.list_past_orders") {
    argv.push("list-past-orders", "--shop", args.shop, "--customer-id", args.customerId);
  } else if (tool === "helpdesk.draft_reply") {
    argv.push("draft-reply", "--ticket", String(args.ticketId));
    if (args.shop) argv.push("--shop", args.shop);
  } else if (tool === "helpdesk.summarize_thread") {
    argv.push("summarize-thread", "--ticket", String(args.ticketId));
    if (args.shop) argv.push("--shop", args.shop);
  } else if (tool === "helpdesk.search_macros") {
    argv.push("search-macros", "--query", String(args.query || ""));
  } else if (tool === "helpdesk.apply_macro") {
    argv.push("apply-macro", "--macro-id", String(args.macroId), "--mode", String(args.mode || "replace"));
    if (args.currentBody) argv.push("--current-body", String(args.currentBody));
  } else if (tool === "helpdesk.ingest_email") {
    argv.push(
      "ingest-email",
      "--from", String(args.from),
      "--subject", String(args.subject),
      "--body", String(args.body),
      "--received-at", String(args.receivedAt),
    );
  } else if (tool === "helpdesk.ingest_chat") {
    argv.push(
      "ingest-chat",
      "--from-name", String(args.fromName),
      "--body", String(args.body),
      "--received-at", String(args.receivedAt),
    );
  } else if (tool === "helpdesk.pull_mailbox") {
    argv.push("pull-mailbox", "--limit", String(args.limit || 20));
  } else if (tool === "helpdesk.escalate_ticket") {
    argv.push("escalate-ticket", "--ticket-id", String(args.ticketId));
    if (args.reason) argv.push("--reason", String(args.reason));
  } else if (tool === "helpdesk.write_gate_status") {
    argv.push("write-gate-status");
  } else if (tool === "helpdesk.bridge_status") {
    argv.push("bridge-status");
  } else if (tool === "helpdesk.send_reply") {
    argv.push("send-reply", "--ticket-id", String(args.ticketId), "--text", String(args.text));
    if (args.confirmed) argv.push("--confirmed");
    if (args.close) argv.push("--close");
  } else {
    throw new Error(`unknown tool ${tool}`);
  }
  const result = spawnSync(argv[0], argv.slice(1), {
    cwd: helpdeskRoot,
    encoding: "utf8",
    env: {
      ...process.env,
      PYTHONPATH: helpdeskRoot,
      HELPDESK_SOURCE: args._source || "sample",
    },
  });
  assert.equal(result.error, undefined, result.stderr);
  // A non-zero exit that still prints JSON is an expected payload (the
  // send_reply human-only case asserts _exit); an empty stdout means the CLI
  // died before emitting anything — surface stderr instead of a bare
  // SyntaxError so the real cause stays diagnosable.
  let payload;
  try {
    payload = JSON.parse(result.stdout);
  } catch (err) {
    throw new Error(`helpdesk CLI ${tool} exited ${result.status} with no JSON: ${result.stderr || err.message}`);
  }
  payload._exit = result.status;
  return payload;
}

export function clientFromPython(source = "sample") {
  return createHelpdeskClient({
    invoke(tool, args) {
      return pythonInvoke(tool, { ...args, _source: source });
    },
  });
}
