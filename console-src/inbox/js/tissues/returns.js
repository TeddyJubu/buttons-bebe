import { esc, formatMoney, formatWhen, statusLabel } from "../util.js";

/** Admin GraphQL 2026-07: in-progress is Return.status === "OPEN" only. */
export const OPEN_RETURN_STATUS = "OPEN";

export function isOpenReturnStatus(status) {
  return String(status || "").toUpperCase() === OPEN_RETURN_STATUS;
}

function itemCountLabel(count) {
  return `${count} item${count === 1 ? "" : "s"}`;
}

function returnsPeek({ empty, inProgress, status, items, tracking, nodes }) {
  if (empty) return "No returns";
  const count = (items || []).length || (nodes || []).length;
  const itemBit = itemCountLabel(count);
  if (inProgress && tracking) return `In transit · ${itemBit}`;
  if (inProgress && status) return `${statusLabel(status)} · ${itemBit}`;
  return status || `${nodes.length} return${nodes.length === 1 ? "" : "s"}`;
}

/**
 * Returns rail tissue.
 * In: `{ shop, orderId }` via shop tissue.
 * Out: `returns` + Return.status, in-progress flag (OPEN only), items, refund/credit, tracking.
 * Empty tickets: peek "No returns", collapsed. OPEN: default-open on first paint.
 * Do not read Order.returnStatus.
 */
export function projectReturns(record) {
  const nodes = record?.returns?.nodes || [];
  const items = record?.items || [];
  const inProgress = nodes.some((node) => isOpenReturnStatus(node.status));
  const status = (nodes.find((node) => isOpenReturnStatus(node.status)) || nodes[0])?.status || null;
  const empty = nodes.length === 0 && items.length === 0;
  return {
    ok: true,
    peek: returnsPeek({ empty, inProgress, status, items, tracking: record?.tracking, nodes }),
    collapsedDefault: !inProgress,
    inProgress,
    record: {
      returns: { nodes },
      status,
      inProgress,
      items,
      refundTotal: record?.refundTotal || null,
      creditTotal: record?.creditTotal || null,
      tracking: record?.tracking || null,
    },
  };
}

function renderReturnItems(rec) {
  // Snapshot returns carry per-item detail (#46): the returned line's title,
  // quantity, price, and the return reason with its note. Nodes without
  // items (live/demo rows, old snapshots) fall back to the record's flat
  // item list — both render, neither invents.
  const nodes = rec.returns.nodes || [];
  // cubic: a bare snapshot node (returnType/createdAt but no items) beside a
  // detailed one still renders its own line — only include nodes with the
  // snapshot signatures, never the bare {id,status} of live/demo rows.
  const detailed = nodes.filter((node) => (node.items || []).length || node.returnType || node.createdAt);
  const blocks = detailed.map((node) => {
    const items = (node.items || []).map((item) => (
      `<li>${esc(item.title || "Return on file")} · ${esc(item.quantity != null ? `${item.quantity}×` : "")} ${esc(formatMoney(item.price, ""))} ${item.reason ? `· ${esc(item.reason)}` : ""}${item.note ? ` (${esc(item.note)})` : ""}</li>`
    )).join("");
    const typeLabel = node.returnType === "EXCHANGE" ? "Exchange" : "Return";
    return `${node.createdAt ? `<p class="return-created">${esc(formatWhen(node.createdAt))}</p>` : ""}
      <p class="return-line">${esc(typeLabel)}${node.name ? ` ${esc(node.name)}` : ""} · ${esc(node.status ? statusLabel(node.status) : "—")}</p>
      ${items ? `<ul class="return-items">${items}</ul>` : ""}${node.itemsTruncated ? `<p class="mute">Some items are not shown.</p>` : ""}`;
  }).join("");
  if (blocks) return blocks;
  // Live/demo shape: flat items list on the record itself.
  const flat = (rec.items || []).map((item) => (
    `<li>${esc(item.title)} · ${esc(item.reason || "—")} · ${esc(item.type || "—")}</li>`
  )).join("");
  return flat ? `<ul class="return-items">${flat}</ul>` : "";
}

export function renderReturns(model, { open, compact = false, orderName = "" } = {}) {
  const isOpen = open == null ? !model.collapsedDefault : open;
  const rec = model.record;
  let body = `<p class="tissue-empty">No returns</p>`;
  if (rec && (rec.returns.nodes.length || rec.items.length)) {
    const detailed = renderReturnItems(rec);
    body = detailed || "<ul class=\"return-items\"><li>Return on file</li></ul>";
    // Live/demo rows keep their status/refund summary line; snapshot nodes
    // carry their own per-return status (#46).
    const nodesCarryDetail = (rec.returns.nodes || []).some((node) => (node.items || []).length);
    if (!nodesCarryDetail) {
      body += `
      <p>Status ${esc(rec.status ? statusLabel(rec.status) : "—")}</p>
      <p>Refund ${esc(formatMoney(rec.refundTotal, "—"))} · Credit ${esc(formatMoney(rec.creditTotal, "—"))}</p>`;
    }
  }
  const snapshotPeek = compact && !rec?.items?.length && rec?.returns.nodes.length
    ? `${statusLabel(rec.status)} · ${rec.returns.nodes.length} return${rec.returns.nodes.length === 1 ? "" : "s"}` : model.peek;
  if (compact && rec?.returns.nodes.length) {
    // #46: snapshot mode renders each return's own detail — created date,
    // return/exchange type, status, and items with prices and reason. The
    // flat-items fallback covers old snapshots that carry none of it.
    const detailed = renderReturnItems(rec);
    body = `${orderName ? `<p class="mute">Order ${esc(orderName)}</p>` : ""}
      ${detailed || (rec.items.length ? `<ul class="return-items">${rec.items.map(item => `<li>${esc(item.title)}</li>`).join("")}</ul>` : `<p class="mute">Return on file. Item details unavailable.</p>`)}
      ${rec.refundTotal ? `<p>Refund ${esc(formatMoney(rec.refundTotal))}</p>` : ""}
      ${rec.creditTotal ? `<p>Credit ${esc(formatMoney(rec.creditTotal))}</p>` : ""}`;
  }
  return `<section class="rail-card${compact && model.inProgress ? " return-active" : ""}" data-tissue="returns" data-open="${isOpen ? "true" : "false"}">
    <button type="button" class="rail-toggle" data-toggle="returns" aria-expanded="${isOpen ? "true" : "false"}">
      <h2>Returns</h2>
      <span class="peek">${esc(snapshotPeek)}</span>
    </button>
    <div class="rail-body"${isOpen ? "" : " hidden"}>${body}</div>
  </section>`;
}

export function createReturnsTissue({ shop }) {
  return {
    id: "returns",
    async load({ shop: shopId, orderId }) {
      try {
        const record = await shop.getReturns({ shop: shopId, orderId });
        return projectReturns(record);
      } catch (err) {
        return {
          ok: false,
          peek: "Returns error",
          collapsedDefault: true,
          inProgress: false,
          record: null,
          error: String(err?.message || err),
        };
      }
    },
    project: projectReturns,
    render: renderReturns,
  };
}
