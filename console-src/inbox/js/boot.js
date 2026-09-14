import { createInboxOrgan } from "./inbox.js";
import { createHelpdeskClient } from "./shop/helpdesk-client.js";
import { createHelpdeskShop } from "./shop/production-shop.js";
import { views } from "./view-model.js";
import { registerInboxWebMcp } from "./webmcp.js";

const root = document.getElementById("inbox-root");
const client = createHelpdeskClient();
const shop = createHelpdeskShop({ client });
const params = new URLSearchParams(location.search);
// Unknown ?view= values fall back to the whole observed snapshot; a plain /inbox/
// landing also defaults to All so every observed row is reachable.
const requestedView = params.get("view");
const viewId = views.some((view) => view.id === requestedView) ? requestedView : "all";
// Load the inbox store only. Never probe a test shop or seed demo tickets.
const organ = createInboxOrgan({
  shop,
  viewId,
  ticketId: params.get("ticket") || undefined,
  privacyGate: params.get("gate") === "privacy",
});
organ.mount(root);

// Expose the capability-locked organ for local accessibility verification.
globalThis.__inboxOrgan = organ;

// WebMCP: register Document-scoped chrome tools after mount. No-op without
// modelContext (chrome://flags/#enable-webmcp-testing). Abort on page hide.
const webmcp = registerInboxWebMcp(organ);
await webmcp.ready;
globalThis.__inboxWebMcp = webmcp;
window.addEventListener("pagehide", () => webmcp.dispose(), { once: true });
