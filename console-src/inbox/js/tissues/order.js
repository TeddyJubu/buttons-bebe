import {
  addressPeek,
  discountPeek,
  DISCOUNTS_MISSING_LABEL,
  esc,
  safeWebUrl,
  firstTracking,
  formatMoney,
  formatSku,
  formatWhen,
  hasTracking,
  invoicePeek,
  invoiceUrlOf,
  INVOICE_MISSING_LABEL,
  formatEta,
  formatShippingZone,
  formatWarrantyEndsOn,
  lineFulfillmentLabel,
  etaOf,
  etaPeek,
  ETA_MISSING_LABEL,
  shippingZoneOf,
  warrantyOf,
  warrantyPeek,
  WARRANTY_MISSING_LABEL,
  shipmentPeek,
  statusLabel,
  TRACKING_MISSING_LABEL,
} from "../util.js";

/**
 * This-order rail tissue.
 * In: `{ shop, orderId }` via shop tissue.
 * Out: Clerk order DTO. Peek: name + Paid + Fulfilled.
 * Empty fulfillments/tracking: shipment peek "No tracking". Do not invent tracking.
 */
export function projectOrder(record) {
  if (!record) {
    return {
      ok: false,
      peek: "No order",
      record: null,
      skuLabels: [],
      addressPeek: "No address",
      hasTracking: false,
      tracking: null,
      shipmentPeek: TRACKING_MISSING_LABEL,
      lineFulfillLabels: [],
      discountCodes: [],
      discountPeek: DISCOUNTS_MISSING_LABEL,
      hasDiscounts: false,
      invoiceUrl: "",
      invoicePeek: INVOICE_MISSING_LABEL,
      hasInvoice: false,
      warranty: null,
      warrantyPeek: WARRANTY_MISSING_LABEL,
      hasWarranty: false,
      eta: "",
      shippingZone: "",
      etaPeek: ETA_MISSING_LABEL,
      hasEta: false,
    };
  }
  const nodes = record.lineItems?.nodes || [];
  const discountCodes = (record.discountCodes || [])
    .map((code) => String(code || "").trim())
    .filter(Boolean);
  const invoiceUrl = invoiceUrlOf(record);
  const warranty = warrantyOf(record);
  const eta = etaOf(record);
  const shippingZone = shippingZoneOf(record);
  return {
    ok: true,
    peek: [record.name, statusLabel(record.displayFinancialStatus), statusLabel(record.displayFulfillmentStatus)]
      .filter(Boolean)
      .join(" · "),
    skuLabels: nodes.map((item) => formatSku(item.sku)),
    addressPeek: addressPeek(record.shippingAddress, record.billingAddress),
    hasTracking: hasTracking(record),
    tracking: firstTracking(record),
    shipmentPeek: shipmentPeek(record),
    lineFulfillLabels: nodes.map((item) => lineFulfillmentLabel(item)),
    discountCodes,
    discountPeek: discountPeek(discountCodes),
    hasDiscounts: discountCodes.length > 0,
    invoiceUrl,
    invoicePeek: invoicePeek(invoiceUrl),
    hasInvoice: Boolean(invoiceUrl),
    warranty,
    warrantyPeek: warrantyPeek(warranty),
    hasWarranty: Boolean(warranty),
    eta,
    shippingZone,
    etaPeek: etaPeek(record),
    hasEta: Boolean(eta || shippingZone),
    record,
  };
}

function renderTrackingCopy(tracking) {
  const company = tracking.company
    ? `<span class="ship-company">${esc(tracking.company)}</span>`
    : "";
  const number = tracking.number
    ? `<span class="mono ship-number">${esc(tracking.number)}</span>`
    : "";
  const copy = company || number
    ? `<span class="ship-copy">${[company, number].filter(Boolean).join("")}</span>`
    : "";
  const track = safeWebUrl(tracking.url)
    ? `<a class="track-link" href="${esc(safeWebUrl(tracking.url))}" rel="noreferrer" target="_blank" title="Open carrier tracking in a new tab">Track</a>`
    : "";
  return `<p class="ship-track">${copy}${track}</p>`;
}

function renderShipment(model, shipOpen, compact = false) {
  const tracking = model.tracking;
  const shipLines = (model.record?.fulfillments || []).flatMap((fulfillment) =>
    (fulfillment.fulfillmentLineItems?.nodes || []).map((item) => {
      const title = item.lineItem?.title || "";
      const qty = item.quantity;
      if (!title) return "";
      return `<p class="ship-line">${esc(title)}${qty != null ? ` · ${esc(qty)}` : ""}</p>`;
    }),
  ).join("");
  if (model.hasTracking && tracking) {
    const peek = model.shipmentPeek
      ? ` <span class="peek">${esc(model.shipmentPeek)}</span>`
      : "";
    return `<div class="rail-sub" data-open="${shipOpen ? "true" : "false"}">
        <button type="button" class="rail-sub-toggle" data-toggle="shipment" aria-expanded="${shipOpen ? "true" : "false"}" title="Show or hide Shipment"><h3>Shipment</h3>${peek}</button>
        <div class="rail-sub-body"${shipOpen ? "" : " hidden"}>
          ${renderTrackingCopy(tracking)}
          ${compact ? "" : shipLines}
        </div>
      </div>`;
  }
  return `<div class="rail-sub" data-open="${shipOpen ? "true" : "false"}">
      <button type="button" class="rail-sub-toggle" data-toggle="shipment" aria-expanded="${shipOpen ? "true" : "false"}" title="Show or hide Shipment">
        <h3>Shipment</h3> <span class="peek">${esc(model.shipmentPeek || TRACKING_MISSING_LABEL)}</span>
      </button>
      <div class="rail-sub-body"${shipOpen ? "" : " hidden"}>
        <p class="tissue-empty">${esc(TRACKING_MISSING_LABEL)}</p>
      </div>
    </div>`;
}

function renderDiscounts(model, discountsOpen) {
  const codes = model.discountCodes || [];
  if (!codes.length) {
    return `<div class="rail-sub" data-open="${discountsOpen ? "true" : "false"}">
      <button type="button" class="rail-sub-toggle" data-toggle="discounts" aria-expanded="${discountsOpen ? "true" : "false"}" title="Show or hide Discounts">
        <h3>Discounts</h3> <span class="peek">${esc(DISCOUNTS_MISSING_LABEL)}</span>
      </button>
      <div class="rail-sub-body"${discountsOpen ? "" : " hidden"}>
        <p class="tissue-empty">${esc(DISCOUNTS_MISSING_LABEL)}</p>
      </div>
    </div>`;
  }
  const rows = codes.map((code) => `<p class="mono discount-code">${esc(code)}</p>`).join("");
  return `<div class="rail-sub" data-open="${discountsOpen ? "true" : "false"}">
    <button type="button" class="rail-sub-toggle" data-toggle="discounts" aria-expanded="${discountsOpen ? "true" : "false"}" title="Show or hide Discounts">
      <h3>Discounts</h3> <span class="peek">${esc(model.discountPeek)}</span>
    </button>
    <div class="rail-sub-body"${discountsOpen ? "" : " hidden"}>
      ${rows}
    </div>
  </div>`;
}

function renderInvoice(model, invoiceOpen) {
  const url = safeWebUrl(model.invoiceUrl);
  if (!url) {
    return `<div class="rail-sub" data-open="${invoiceOpen ? "true" : "false"}">
      <button type="button" class="rail-sub-toggle" data-toggle="invoice" aria-expanded="${invoiceOpen ? "true" : "false"}" title="Show or hide Invoice">
        <h3>Invoice</h3> <span class="peek">${esc(INVOICE_MISSING_LABEL)}</span>
      </button>
      <div class="rail-sub-body"${invoiceOpen ? "" : " hidden"}>
        <p class="tissue-empty">${esc(INVOICE_MISSING_LABEL)}</p>
      </div>
    </div>`;
  }
  return `<div class="rail-sub" data-open="${invoiceOpen ? "true" : "false"}">
    <button type="button" class="rail-sub-toggle" data-toggle="invoice" aria-expanded="${invoiceOpen ? "true" : "false"}" title="Show or hide Invoice">
      <h3>Invoice</h3> <span class="peek">${esc(model.invoicePeek)}</span>
    </button>
    <div class="rail-sub-body"${invoiceOpen ? "" : " hidden"}>
      <p class="ship-track"><a class="invoice-link" href="${esc(url)}" rel="noreferrer" target="_blank">Invoice</a></p>
    </div>
  </div>`;
}

function renderWarranty(model, warrantyOpen) {
  const warranty = model.warranty;
  if (!warranty) {
    return `<div class="rail-sub" data-open="${warrantyOpen ? "true" : "false"}">
      <button type="button" class="rail-sub-toggle" data-toggle="warranty" aria-expanded="${warrantyOpen ? "true" : "false"}" title="Show or hide Warranty">
        <h3>Warranty</h3> <span class="peek">${esc(WARRANTY_MISSING_LABEL)}</span>
      </button>
      <div class="rail-sub-body"${warrantyOpen ? "" : " hidden"}>
        <p class="tissue-empty">${esc(WARRANTY_MISSING_LABEL)}</p>
      </div>
    </div>`;
  }
  const peek = model.warrantyPeek || warrantyPeek(warranty);
  const ends = formatWarrantyEndsOn(warranty.endsOn);
  const rows = [
    peek && peek !== WARRANTY_MISSING_LABEL
      ? `<p class="mute warranty-line">${esc(peek)}</p>`
      : "",
    ends ? `<p class="mute warranty-line">${esc(ends)}</p>` : "",
  ].filter(Boolean).join("");
  return `<div class="rail-sub" data-open="${warrantyOpen ? "true" : "false"}">
    <button type="button" class="rail-sub-toggle" data-toggle="warranty" aria-expanded="${warrantyOpen ? "true" : "false"}" title="Show or hide Warranty">
      <h3>Warranty</h3> <span class="peek">${esc(peek)}</span>
    </button>
    <div class="rail-sub-body"${warrantyOpen ? "" : " hidden"}>
      ${rows || `<p class="tissue-empty">${esc(WARRANTY_MISSING_LABEL)}</p>`}
    </div>
  </div>`;
}

function renderEta(model, etaOpen) {
  const etaLine = formatEta(model.eta);
  const zoneLine = formatShippingZone(model.shippingZone);
  if (!etaLine && !zoneLine) {
    return `<div class="rail-sub" data-open="${etaOpen ? "true" : "false"}">
      <button type="button" class="rail-sub-toggle" data-toggle="eta" aria-expanded="${etaOpen ? "true" : "false"}" title="Show or hide ETA">
        <h3>ETA</h3> <span class="peek">${esc(ETA_MISSING_LABEL)}</span>
      </button>
      <div class="rail-sub-body"${etaOpen ? "" : " hidden"}>
        <p class="tissue-empty">${esc(ETA_MISSING_LABEL)}</p>
      </div>
    </div>`;
  }
  const peek = model.etaPeek || etaPeek({ eta: model.eta, shippingZone: model.shippingZone, fulfillments: [] });
  const rows = [
    etaLine ? `<p class="mute eta-line">${esc(etaLine)}</p>` : "",
    zoneLine ? `<p class="mute eta-line">${esc(zoneLine)}</p>` : "",
  ].filter(Boolean).join("");
  return `<div class="rail-sub" data-open="${etaOpen ? "true" : "false"}">
    <button type="button" class="rail-sub-toggle" data-toggle="eta" aria-expanded="${etaOpen ? "true" : "false"}" title="Show or hide ETA">
      <h3>ETA</h3> <span class="peek">${esc(peek)}</span>
    </button>
    <div class="rail-sub-body"${etaOpen ? "" : " hidden"}>
      ${rows}
    </div>
  </div>`;
}

function renderAddress(label, address) {
  if (!address) return `<div class="addr"><h4>${esc(label)}</h4><p>Absent</p></div>`;
  const lines = [address.name, address.address1, address.address2, [address.city, address.province, address.zip].filter(Boolean).join(", "), address.country]
    .filter(Boolean);
  return `<div class="addr"><h4>${esc(label)}</h4>${lines.map((line) => `<p>${esc(line)}</p>`).join("")}</div>`;
}

export function renderOrder(model, { open = true, addressesOpen = false, shipmentOpen, discountsOpen, invoiceOpen, warrantyOpen, etaOpen, compact = false } = {}) {
  const record = model.record;
  const gateHairline = record
    ? `<div class="order-gate">
      <button type="button" class="btn-hairline" data-write-gate-open title="Refunds and cancels stay locked until someone names an exact write">Payments locked</button>
    </div>`
    : "";
  if (!model.ok || !record) {
    return `<section class="rail-card" data-tissue="order" data-open="${open ? "true" : "false"}">
      <button type="button" class="rail-toggle" data-toggle="order" aria-expanded="${open ? "true" : "false"}" title="Show or hide This order">
        <h2>This order</h2><span class="peek">${esc(model.peek)}</span>
      </button>
      <div class="rail-body">
        <div class="rail-empty-next">
          <p class="tissue-empty">No order</p>
          <button type="button" class="btn-hairline" data-order-link-gate-open title="Link order stays locked. No live link yet.">Link order</button>
        </div>
      </div>
    </section>`;
  }
  const shipOpen = shipmentOpen == null ? model.hasTracking : shipmentOpen;
  const codesOpen = discountsOpen == null ? Boolean(model.hasDiscounts) : discountsOpen;
  const invoiceIsOpen = invoiceOpen == null ? Boolean(model.hasInvoice) : invoiceOpen;
  const warrantyIsOpen = warrantyOpen == null ? Boolean(model.hasWarranty) : warrantyOpen;
  const etaIsOpen = etaOpen == null ? Boolean(model.hasEta) : etaOpen;
  const items = (record.lineItems?.nodes || []).map((item, index) => {
    const skuLabel = model.skuLabels[index] ?? formatSku(item.sku);
    const skuRow = skuLabel
      ? `<p class="mono line-sku" data-sku="${esc(skuLabel)}">${esc(skuLabel)}</p>`
      : "";
    const imageUrl = safeWebUrl(item.image?.url);
    const imageAlt = item.image?.altText || item.title || "Product";
    const thumb = imageUrl
      ? `<img class="line-thumb" src="${esc(imageUrl)}" alt="${esc(imageAlt)}" loading="lazy" />`
      : `<span class="line-thumb line-thumb-empty" aria-hidden="true"></span>`;
    const fulfillLabel = model.lineFulfillLabels?.[index] || lineFulfillmentLabel(item);
    const fulfillCue = fulfillLabel
      ? ` · <span data-line-fulfill="${esc(fulfillLabel)}">${esc(fulfillLabel)}</span>`
      : "";
    return `<li class="line">
      <div class="line-row">
        ${thumb}
        <div class="line-copy">
          <p class="line-title">${esc(item.title)}</p>
          ${skuRow}
          <p class="line-meta">${esc(item.quantity)} · <span class="mono">${esc(formatMoney(item.originalUnitPriceSet, ""))}</span>${fulfillCue}</p>
        </div>
      </div>
    </li>`;
  }).join("");
  const shipment = renderShipment(model, shipOpen, compact);
  return `<section class="rail-card" data-tissue="order" data-open="${open ? "true" : "false"}">
    <button type="button" class="rail-toggle" data-toggle="order" aria-expanded="${open ? "true" : "false"}" title="Show or hide This order">
      <h2>${compact ? `Order ${esc(record.name)}` : "This order"}</h2>
      ${compact ? "" : `<span class="peek">${esc(model.peek)}</span>`}
    </button>
    <div class="rail-body">
      ${compact ? `<div class="order-statuses">${[record.displayFinancialStatus, record.displayFulfillmentStatus].filter(Boolean).map(status => `<span class="order-status">${esc(statusLabel(status))}</span>`).join("")}</div>` : `<p class="mono order-name">${esc(record.name)}</p>`}
      <p class="mute">${esc(formatWhen(record.createdAt))}</p>
      ${compact ? shipment : ""}
      <ul class="lines">${items}</ul>
      <dl class="totals">
        ${!compact || record.currentSubtotalPriceSet ? `<div><dt>Subtotal</dt><dd class="mono">${esc(formatMoney(record.currentSubtotalPriceSet, "—"))}</dd></div>` : ""}
        ${!compact || record.totalShippingPriceSet ? `<div><dt>Shipping</dt><dd class="mono">${esc(formatMoney(record.totalShippingPriceSet, "—"))}</dd></div>` : ""}
        ${!compact || record.totalTaxSet ? `<div><dt>Tax</dt><dd class="mono">${esc(formatMoney(record.totalTaxSet, "—"))}</dd></div>` : ""}
        <div><dt>Total</dt><dd class="mono">${esc(formatMoney(record.currentTotalPriceSet, "—"))}</dd></div>
      </dl>
      ${gateHairline}
      ${!compact || model.hasDiscounts ? renderDiscounts(model, codesOpen) : ""}
      ${!compact || model.hasInvoice ? renderInvoice(model, invoiceIsOpen) : ""}
      ${!compact || model.hasWarranty ? renderWarranty(model, warrantyIsOpen) : ""}
      <div class="rail-sub" data-open="${addressesOpen ? "true" : "false"}">
        <button type="button" class="rail-sub-toggle" data-toggle="addresses" aria-expanded="${addressesOpen ? "true" : "false"}" title="Show or hide Addresses">
          <h3>Addresses</h3> <span class="peek">${esc(model.addressPeek)}</span>
        </button>
        <div class="rail-sub-body addr-stack"${addressesOpen ? "" : " hidden"}>
          ${renderAddress("Shipping", record.shippingAddress)}
          ${renderAddress("Billing", record.billingAddress)}
        </div>
      </div>
      ${!compact || model.hasEta ? renderEta(model, etaIsOpen) : ""}
      ${compact ? "" : shipment}
    </div>
  </section>`;
}

export function createOrderTissue({ shop }) {
  return {
    id: "order",
    async load({ shop: shopId, orderId }) {
      try {
        if (!orderId) return projectOrder(null);
        const record = await shop.getOrder({ shop: shopId, orderId });
        return projectOrder(record);
      } catch (err) {
        return { ok: false, peek: "Order error", record: null, error: String(err?.message || err), skuLabels: [], addressPeek: "—" };
      }
    },
    project: projectOrder,
    render: renderOrder,
  };
}
