import assert from "node:assert/strict";
import test from "node:test";

import { createInboxOrgan as createProductionInbox } from "../js/inbox.js";
import { createFixtureShop } from "../js/shop/fixture-shop.js";
import {
  buildInboxWebMcpTools,
  registerInboxWebMcp,
  registerWebMcpTools,
  resolveModelContext,
} from "../js/webmcp.js";

test("resolveModelContext prefers document over navigator", () => {
  const docCtx = { registerTool() {} };
  const navCtx = { registerTool() {} };
  assert.equal(resolveModelContext({ modelContext: docCtx }, { modelContext: navCtx }), docCtx);
  assert.equal(resolveModelContext({}, { modelContext: navCtx }), navCtx);
  assert.equal(resolveModelContext({}, {}), null);
});

test("registerWebMcpTools is a no-op when modelContext is absent", async () => {
  const handle = registerWebMcpTools(
    [{ name: "select_ticket", description: "x", execute: () => "ok" }],
    { modelContext: null },
  );
  assert.equal(handle.available, false);
  assert.deepEqual(handle.registeredNames, []);
  await handle.ready;
  handle.dispose();
});

test("registerWebMcpTools awaits registerTool and aborts on dispose", async () => {
  const registered = [];
  const unregistered = [];
  const modelContext = {
    async registerTool(tool, opts) {
      registered.push({ name: tool.name, signal: opts?.signal });
    },
    unregisterTool(name) {
      unregistered.push(name);
    },
  };
  const handle = registerWebMcpTools(
    [
      { name: "select_view", description: "Switch view", execute: () => "{}" },
      { name: "select_ticket", description: "Pick ticket", execute: () => "{}" },
    ],
    { modelContext },
  );
  await handle.ready;
  assert.equal(handle.available, true);
  assert.deepEqual(handle.registeredNames, ["select_view", "select_ticket"]);
  assert.equal(registered.length, 2);
  assert.ok(registered[0].signal instanceof AbortSignal);
  handle.dispose();
  assert.deepEqual(unregistered, ["select_ticket", "select_view"]);
  assert.equal(registered[0].signal.aborted, true);
});

test("buildInboxWebMcpTools registers v1 + optional organ tools, never Send", () => {
  const organ = createInboxOrgan({ shop: createFixtureShop(), viewId: "mine", ticketId: "t-ada-track" });
  const tools = buildInboxWebMcpTools(organ);
  const names = tools.map((t) => t.name);
  assert.deepEqual(
    names.filter((n) =>
      ["select_view", "select_ticket", "use_draft", "regenerate_draft", "dismiss_draft"].includes(n),
    ),
    ["select_view", "select_ticket", "use_draft", "regenerate_draft", "dismiss_draft"],
  );
  assert.ok(names.includes("summarize_thread"));
  assert.ok(names.includes("open_macros"));
  assert.ok(names.includes("apply_macro"));
  assert.ok(!names.some((n) => /send|refund|cancel/i.test(n)));
  for (const tool of tools) {
    assert.equal(tool.annotations?.consequentialHint, false);
    assert.match(tool.name, /^[a-z][a-z0-9_]{0,29}$/);
    assert.ok(!tool.name.startsWith("helpdesk."));
  }
});

test("select_ticket / use_draft / dismiss_draft execute organ paths", async () => {
  const organ = createInboxOrgan({
    shop: createFixtureShop(),
    viewId: "all",
    ticketId: "t-casey-visor",
  });
  await organ.ready();
  const tools = Object.fromEntries(buildInboxWebMcpTools(organ).map((t) => [t.name, t]));

  const selected = JSON.parse(await tools.select_ticket.execute({ ticketId: "t-ada-track" }, {}));
  assert.equal(selected.ok, true);
  assert.equal(selected.selectedId, "t-ada-track");

  const used = JSON.parse(await tools.use_draft.execute({}, {}));
  assert.equal(used.ok, true);
  assert.equal(used.action, "use_draft");
  assert.ok(used.bodyLen > 0);

  await organ.regenerateDraft();
  const dismissed = JSON.parse(await tools.dismiss_draft.execute({}, {}));
  assert.equal(dismissed.ok, true);
  assert.equal(dismissed.strip, false);
});

test("registerInboxWebMcp wires organ tools when context present", async () => {
  const names = [];
  const modelContext = {
    async registerTool(tool) {
      names.push(tool.name);
    },
  };
  const organ = createInboxOrgan({ shop: createFixtureShop() });
  const handle = registerInboxWebMcp(organ, { modelContext });
  await handle.ready;
  assert.ok(names.includes("select_ticket"));
  assert.ok(names.includes("use_draft"));
  assert.ok(!names.some((n) => n.includes("send")));
  handle.dispose();
});

function createInboxOrgan(opts = {}) { return createProductionInbox({shop: createFixtureShop(), ...opts}); }

test("select_view enum matches the view-model menu list exactly (report 10-7)", async () => {
  const { views } = await import("../js/view-model.js");
  const selectView = buildInboxWebMcpTools(createInboxOrgan()).find((t) => t.name === "select_view");
  assert.deepEqual(selectView.inputSchema.properties.viewId.enum, views.map((v) => v.id));
});
