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
  // #42: boot owns the address bar. The organ reports selection+view; boot
  // turns that into ?view=…&ticket=… entries so the URL can be copied,
  // bookmarked and traversed with back/forward.
  history: {
    replace({ticket, view}) {
      history.replaceState(null, "", buildUrl({ticket, view}));
    },
    push({ticket, view}) {
      history.pushState(null, "", buildUrl({ticket, view}));
    },
  },
  // #42: boot owns the clipboard for the thread's Copy link control. The
  // organ builds the same-origin deep link; boot writes it to the OS
  // clipboard, falling back to a hidden textarea for older browsers.
  clipboard: {
    async writeText(text) {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch {
        /* fall through to the legacy path */
      }
      const helper = document.createElement("textarea");
      helper.value = text;
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.select();
      document.execCommand("copy");
      helper.remove();
    },
  },
  // #39: Export is a local download of observed rows only. It never posts
  // anywhere; the blob lives and dies in this tab.
  downloads: {
    download(name, text, type) {
      const url = URL.createObjectURL(new Blob([text], {type}));
      const link = Object.assign(document.createElement("a"), {href: url, download: name});
      link.click();
      URL.revokeObjectURL(url);
    },
  },
});
organ.mount(root);

function buildUrl({ticket, view}) {
  const next = new URLSearchParams();
  if (view && view !== "all") next.set("view", view);
  if (ticket) next.set("ticket", ticket);
  const query = next.toString();
  return `${location.pathname}${query ? `?${query}` : ""}`;
}

// #42: back/forward re-selects without pushing (replace, not push) so the
// history stack stays the operator's own path through the inbox.
window.addEventListener("popstate", () => {
  const next = new URLSearchParams(location.search);
  const ticket = next.get("ticket") || null;
  if (ticket) organ.selectTicket(ticket, {fromHistory: true});
});

// Expose the capability-locked organ for local accessibility verification.
globalThis.__inboxOrgan = organ;

// WebMCP: register Document-scoped chrome tools after mount. No-op without
// modelContext (chrome://flags/#enable-webmcp-testing). Abort on page hide.
const webmcp = registerInboxWebMcp(organ);
await webmcp.ready;
globalThis.__inboxWebMcp = webmcp;
window.addEventListener("pagehide", () => webmcp.dispose(), { once: true });
