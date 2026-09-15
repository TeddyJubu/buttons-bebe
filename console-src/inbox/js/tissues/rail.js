import { MAILBOX_TOPICS } from "../contracts.js";
import { esc, formatOrderCount, formatWhen } from "../util.js";
import { createCustomerTissue, renderCustomer } from "./customer.js";
import { projectCustomer } from "./customer.js";
import { createOrderHistoryTissue, renderOrderHistory } from "./order-history.js";
import { projectOrderHistory } from "./order-history.js";
import { createOrderTissue, renderOrder } from "./order.js";
import { projectOrder } from "./order.js";
import { createReturnsTissue, renderReturns } from "./returns.js";
import { projectReturns } from "./returns.js";

/** Locked first-paint defaults. Addresses and past orders never start open. */
export const RAIL_DEFAULTS = Object.freeze({
  customer: true,
  order: true,
  returns: false,
  "order-history": false,
  addresses: false,
  shipment: false,
  giftCards: false,
  discounts: false,
  invoice: false,
  warranty: false,
  eta: false,
});

/**
 * Rail organ: customer + this order + returns + past orders.
 * Collapsibles are independent (not an accordion).
 */
export function createRailOrgan({ shop, mailbox }) {
  const customer = createCustomerTissue({ shop });
  const order = createOrderTissue({ shop });
  const returns = createReturnsTissue({ shop });
  const history = createOrderHistoryTissue({ shop, mailbox });

  const open = { ...RAIL_DEFAULTS };

  let models = {
    fromSnapshot: false,
    customer: { ok: false, peek: "Customer", record: null },
    order: { ok: false, peek: "This order", record: null },
    returns: { ok: true, peek: "No returns", collapsedDefault: true, record: null },
    history: { ok: true, peek: formatOrderCount(0), rows: [] },
  };
  let peekedHistoryId = null;
  let currentOrderId = null;
  let currentTicketKey = null;
  let lastLoad = { shop: "", customerId: "", orderId: "", ticketId: "" };

  function ticketKey({ ticketId, customerId, orderId }) {
    if (ticketId) return `ticket:${ticketId}`;
    return `order:${customerId || ""}:${orderId || ""}`;
  }

  function applyLockDefaults(returnsModel, orderModel, customerModel) {
    Object.assign(open, RAIL_DEFAULTS);
    open.returns = Boolean(returnsModel?.inProgress);
    open.shipment = Boolean(orderModel?.hasTracking);
    open.giftCards = Boolean(customerModel?.hasGiftCards);
    open.discounts = Boolean(orderModel?.hasDiscounts);
    open.invoice = Boolean(orderModel?.hasInvoice);
    open.warranty = Boolean(orderModel?.hasWarranty);
    open.eta = Boolean(orderModel?.hasEta);
  }

  const ERROR_LABEL = {
    customer: "Customer",
    order: "Order",
    returns: "Returns",
    "order-history": "History",
  };

  function errorCopy(tissueId) {
    return `Couldn't load ${ERROR_LABEL[tissueId] || "section"}. Retry.`;
  }

  function renderError(tissueId, label, peek) {
    return `<section class="rail-card is-error" data-tissue="${esc(tissueId)}" data-open="true">
      <button type="button" class="rail-toggle" data-toggle="${esc(tissueId)}" aria-expanded="true">
        <h2>${esc(label)}</h2><span class="peek">${esc(peek)}</span>
      </button>
      <div class="rail-body">
        <p class="tissue-error">${esc(errorCopy(tissueId))}</p>
        <button type="button" class="btn-hairline" data-retry="${esc(tissueId)}" title="Try loading this section again">Retry</button>
      </div>
    </section>`;
  }

  function render() {
    if (shop.observedHistory && !models.fromSnapshot) {
      return `<div class="pane-inner"><h2>Context</h2><p class="mute">This view contains observed webhook messages and review drafts. Live customer, order, return, assignment and ticket status details are not connected.</p></div>`;
    }
    const customerHtml = models.customer.error
      ? renderError("customer", "Customer", models.customer.peek)
      : renderCustomer(models.customer, { open: open.customer, giftCardsOpen: open.giftCards, compact: models.fromSnapshot });
    const orderHtml = models.order.error
      ? renderError("order", "This order", models.order.peek)
      : renderOrder(models.order, {
        open: open.order,
        compact: models.fromSnapshot,
        addressesOpen: open.addresses,
        shipmentOpen: open.shipment,
        discountsOpen: open.discounts,
        invoiceOpen: open.invoice,
        warrantyOpen: open.warranty,
        etaOpen: open.eta,
      });
    const returnsHtml = models.fromSnapshot && !models.order.ok
      ? `<section class="rail-card"><h2>Returns</h2><p class="mute">Select a ticket with a matching order to see its returns.</p></section>`
      : models.returns.error
      ? renderError("returns", "Returns", models.returns.peek)
      : renderReturns(models.returns, { open: open.returns, compact: models.fromSnapshot, orderName: models.order.record?.name });
    const historyRows = (models.history.rows || []).filter((row) => row.id !== currentOrderId);
    const historyView = models.history.error
      ? models.history
      : { ...models.history, peek: formatOrderCount(historyRows.length), rows: historyRows };
    const historyHtml = models.history.error
      ? renderError("order-history", "Past orders", models.history.peek)
      : renderOrderHistory(historyView, { open: open["order-history"], peekedId: peekedHistoryId });
    return `<div class="pane-inner${models.fromSnapshot ? " rail-snapshot" : ""}">
      <div class="rail-toolbar">
        <span class="rail-heading">Customer details</span>
        <button type="button" class="list-tool-btn" data-rail-collapse title="Collapse customer rail" aria-label="Collapse customer rail">
          <svg class="list-tool-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
            <path fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" d="M4.25 4.25l7.5 7.5M11.75 4.25l-7.5 7.5"/>
          </svg>
        </button>
      </div>
      <div class="rail-inner">
        ${models.fromSnapshot ? `<p class="mute customer-source">${esc(models.snapshotNotice)}</p>` : ""}
        ${customerHtml}
        ${models.fromSnapshot && models.returns.inProgress ? returnsHtml + orderHtml : orderHtml + returnsHtml}
        ${historyHtml}
      </div>
    </div>`;
  }

  async function loadTissue(tissueId, { shop: shopId, customerId, orderId }) {
    if (tissueId === "customer") {
      return customer.load({ shop: shopId, customerId }).catch((err) => ({
        ok: false, peek: "Customer error", error: String(err?.message || err), record: null,
      }));
    }
    if (tissueId === "order") {
      return order.load({ shop: shopId, orderId }).catch((err) => ({
        ok: false, peek: "Order error", error: String(err?.message || err), record: null, skuLabels: [], addressPeek: "—",
      }));
    }
    if (tissueId === "returns") {
      return returns.load({ shop: shopId, orderId }).catch((err) => ({
        ok: false, peek: "Returns error", error: String(err?.message || err), collapsedDefault: true, inProgress: false,
      }));
    }
    return history.load({ shop: shopId, customerId }).catch((err) => ({
      ok: false, peek: formatOrderCount(0), rows: [], error: String(err?.message || err),
    }));
  }

  async function load({ shop: shopId, customerId, orderId, ticketId }) {
    lastLoad = { shop: shopId, customerId, orderId, ticketId };
    const nextKey = ticketKey({ ticketId, customerId, orderId });
    const switched = nextKey !== currentTicketKey;
    currentTicketKey = nextKey;
    currentOrderId = orderId || null;
    peekedHistoryId = null;
    const [customerModel, orderModel, returnsModel, historyModel] = await Promise.all([
      loadTissue("customer", lastLoad),
      loadTissue("order", lastLoad),
      loadTissue("returns", lastLoad),
      loadTissue("order-history", lastLoad),
    ]);
    models = {
      fromSnapshot: false,
      customer: customerModel,
      order: orderModel,
      returns: returnsModel,
      history: historyModel,
    };
    if (switched) applyLockDefaults(returnsModel, orderModel, customerModel);
    for (const [tissueId, model] of Object.entries({ customer: customerModel, order: orderModel, returns: returnsModel, "order-history": historyModel })) {
      if (model.error) mailbox.publish(MAILBOX_TOPICS.TISSUE_ERROR, { tissueId, message: model.error });
    }
    return models;
  }

  function loadSnapshot(rail, ticketId) {
    const snapshot = rail || {};
    models = {
      fromSnapshot: true,
      snapshotNotice: `Shopify snapshot${snapshot.fetchedAt ? " · " + formatWhen(snapshot.fetchedAt) : ""}${snapshot.stale ? " · Refresh delayed; details may be outdated." : ""}`,
      customer: projectCustomer(snapshot.customer || null),
      order: projectOrder(snapshot.order || null),
      returns: projectReturns(snapshot.returns || null),
      history: projectOrderHistory(snapshot.history || []),
    };
    currentOrderId = snapshot.orderId || snapshot.order?.id || null;
    currentTicketKey = ticketKey({
      ticketId,
      customerId: snapshot.customerId,
      orderId: currentOrderId,
    });
    peekedHistoryId = null;
    applyLockDefaults(models.returns, models.order, models.customer);
    return models;
  }

  async function retry(tissueId) {
    const key = tissueId === "order-history" ? "history" : tissueId;
    const next = await loadTissue(tissueId, lastLoad);
    models[key] = next;
    if (next.error) mailbox.publish(MAILBOX_TOPICS.TISSUE_ERROR, { tissueId, message: next.error });
    return next;
  }

  function mount(el) {
    el.innerHTML = render();
    el.onclick = (event) => {
      if (event.target.closest("[data-rail-collapse]")) {
        mailbox.publish(MAILBOX_TOPICS.RAIL_COLLAPSED, { collapsed: true });
        return;
      }
      if (event.target.closest("[data-customer-join-gate-open]")) {
        mailbox.publish(MAILBOX_TOPICS.CUSTOMER_JOIN_GATE_OPEN, {});
        return;
      }
      if (event.target.closest("[data-order-link-gate-open]")) {
        mailbox.publish(MAILBOX_TOPICS.ORDER_LINK_GATE_OPEN, {});
        return;
      }
      const gate = event.target.closest("[data-write-gate-open]");
      if (gate) {
        mailbox.publish(MAILBOX_TOPICS.WRITE_GATE_OPEN, {});
        return;
      }
      const retryBtn = event.target.closest("[data-retry]");
      if (retryBtn) {
        retry(retryBtn.dataset.retry).then(() => {
          el.innerHTML = render();
        });
        return;
      }
      const historyRow = event.target.closest("[data-history]");
      if (historyRow) {
        peekedHistoryId = historyRow.dataset.history;
        mailbox.publish(MAILBOX_TOPICS.HISTORY_PEEK, { orderId: peekedHistoryId, currentOrderId });
        el.innerHTML = render();
        return;
      }
      const toggle = event.target.closest("[data-toggle]");
      if (!toggle) return;
      const key = toggle.dataset.toggle;
      const scrollTop = el.querySelector?.(".rail-inner")?.scrollTop || 0;
      open[key] = !open[key];
      el.innerHTML = render();
      const scroll = el.querySelector?.(".rail-inner");
      if (scroll) scroll.scrollTop = scrollTop;
      el.querySelector?.(`[data-toggle="${key}"]`)?.focus({ preventScroll: true });
    };
  }

  return {
    id: "rail",
    load,
    loadSnapshot,
    render,
    mount,
    toggle(key) {
      if (!(key in open)) return open[key];
      open[key] = !open[key];
      return open[key];
    },
    retry,
    snapshot() {
      return {
        models,
        open: { ...open },
        peekedHistoryId,
        currentOrderId,
        currentTicketKey,
      };
    },
  };
}
