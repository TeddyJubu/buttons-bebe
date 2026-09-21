import { MAILBOX_TOPICS } from "../contracts.js";
import { esc, formatOrderCount, formatWhen, statusLabel } from "../util.js";

/** Project a helpdesk.list_past_orders Clerk row (or a legacy view row) for render. */
export function projectHistoryRow(row) {
  if (!row) return null;
  const money = row.currentTotalPriceSet?.shopMoney;
  return {
    id: row.id,
    name: row.name,
    createdAt: row.createdAt,
    total: row.total ?? (money ? `${money.amount} ${money.currencyCode}` : ""),
    fulfillmentStatus: row.fulfillmentStatus ?? row.displayFulfillmentStatus ?? "",
  };
}

/**
 * Past-orders rail tissue.
 * In: `{ shop, customerId }` via shop tissue.
 * Shop out: Clerk `list_past_orders` rows (`displayFulfillmentStatus`,
 * `currentTotalPriceSet.shopMoney`). View rows keep `total` + `fulfillmentStatus`.
 * Click peeks a row. It does not replace This order.
 */
export function projectOrderHistory(rows) {
  const list = [...(rows || [])]
    .map(projectHistoryRow)
    .filter(Boolean)
    .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
  return {
    ok: true,
    peek: formatOrderCount(list.length),
    rows: list,
  };
}

export function renderOrderHistory(model, { open = false, peekedId = null } = {}) {
  const rows = (model.rows || []).map((row) => {
    const on = row.id === peekedId;
    return `<button type="button" class="history-row${on ? " is-peeked" : ""}" data-history="${esc(row.id)}" title="Peek this past order. It does not replace This order.">
      <span class="mono">${esc(row.name)}</span>
      <span>${esc(statusLabel(row.fulfillmentStatus))}</span>
      <span class="mono">${esc(row.total)}</span>
      <span class="mute">${esc(formatWhen(row.createdAt))}</span>
    </button>`;
  }).join("");
  return `<section class="rail-card" data-tissue="order-history" data-open="${open ? "true" : "false"}">
    <button type="button" class="rail-toggle" data-toggle="order-history" aria-expanded="${open ? "true" : "false"}" title="Show or hide Past orders">
      <h2>Past orders</h2>
      <span class="peek">${esc(model.peek)}</span>
    </button>
    <div class="rail-body"${open ? "" : " hidden"}>${rows || `<p class="tissue-empty">No past orders</p>`}</div>
  </section>`;
}

export function createOrderHistoryTissue({ shop, mailbox }) {
  return {
    id: "order-history",
    async load({ shop: shopId, customerId }) {
      try {
        const rows = await shop.getOrderHistory({ shop: shopId, customerId });
        return projectOrderHistory(rows);
      } catch (err) {
        return { ok: false, peek: formatOrderCount(0), rows: [], error: String(err?.message || err) };
      }
    },
    project: projectOrderHistory,
    render: renderOrderHistory,
    bind(el) {
      el.onclick = (event) => {
        const row = event.target.closest("[data-history]");
        if (!row) return;
        mailbox.publish(MAILBOX_TOPICS.HISTORY_PEEK, { orderId: row.dataset.history });
      };
    },
  };
}
