import {
  esc,
  formatMoney,
  formatWhen,
  giftCardHint,
  giftCardPeek,
  giftCardStatusLabel,
  GIFT_CARDS_MISSING_LABEL,
} from "../util.js";

/**
 * Customer rail tissue.
 * In: `{ shop, customerId }` via shop tissue.
 * Out: Clerk customer DTO. Peek: displayName.
 * Gift cards: official GiftCard lastCharacters / maskedCode / enabled / balance.
 */
export function projectCustomer(record) {
  if (!record) {
    return {
      ok: false,
      peek: "No customer",
      record: null,
      giftCards: [],
      giftCardPeek: GIFT_CARDS_MISSING_LABEL,
      hasGiftCards: false,
    };
  }
  const giftCards = Array.isArray(record.giftCards) ? record.giftCards.filter(Boolean) : [];
  return {
    ok: true,
    peek: record.displayName || "Customer",
    giftCards,
    giftCardPeek: giftCardPeek(giftCards),
    hasGiftCards: giftCards.length > 0,
    record: {
      displayName: record.displayName,
      defaultEmailAddress: record.defaultEmailAddress
        ? { emailAddress: record.defaultEmailAddress.emailAddress }
        : null,
      createdAt: record.createdAt,
      numberOfOrders: record.numberOfOrders,
      amountSpent: record.amountSpent,
      tags: record.tags || [],
      giftCards,
    },
  };
}

function renderGiftCards(model, giftCardsOpen) {
  const cards = model.giftCards || [];
  if (!cards.length) {
    return `<div class="rail-sub" data-open="${giftCardsOpen ? "true" : "false"}">
      <button type="button" class="rail-sub-toggle" data-toggle="giftCards" aria-expanded="${giftCardsOpen ? "true" : "false"}" title="Show or hide Gift cards">
        <h3>Gift cards</h3> <span class="peek">${esc(GIFT_CARDS_MISSING_LABEL)}</span>
      </button>
      <div class="rail-sub-body"${giftCardsOpen ? "" : " hidden"}>
        <p class="tissue-empty">${esc(GIFT_CARDS_MISSING_LABEL)}</p>
      </div>
    </div>`;
  }
  const rows = cards.map((card) => {
    const hint = giftCardHint(card);
    const status = giftCardStatusLabel(card.enabled);
    const balance = formatMoney(card.balance, "");
    return `<p class="gift-row">
      <span class="mono gift-hint">${esc(hint)}</span>
      <span class="mute">${esc([balance, status].filter(Boolean).join(" · "))}</span>
    </p>`;
  }).join("");
  return `<div class="rail-sub" data-open="${giftCardsOpen ? "true" : "false"}">
    <button type="button" class="rail-sub-toggle" data-toggle="giftCards" aria-expanded="${giftCardsOpen ? "true" : "false"}" title="Show or hide Gift cards">
      <h3>Gift cards</h3> <span class="peek">${esc(model.giftCardPeek)}</span>
    </button>
    <div class="rail-sub-body"${giftCardsOpen ? "" : " hidden"}>
      ${rows}
    </div>
  </div>`;
}

export function renderCustomer(model, { open = true, giftCardsOpen, compact = false } = {}) {
  const record = model.record;
  const cardsOpen = giftCardsOpen == null ? Boolean(model.hasGiftCards) : giftCardsOpen;
  let body = !model.ok || !record
    ? `<div class="rail-empty-next">
        <p class="tissue-empty">No customer</p>
        <button type="button" class="btn-hairline" data-customer-join-gate-open title="Find customer stays locked. No live join yet.">Find customer</button>
      </div>`
    : `<dl class="rail-dl">
        <div><dt>Name</dt><dd>${esc(record.displayName)}</dd></div>
        <div><dt>Email</dt><dd>${esc(record.defaultEmailAddress?.emailAddress || "—")}</dd></div>
        <div><dt>Customer since</dt><dd>${esc(formatWhen(record.createdAt))}</dd></div>
        <div><dt>Orders</dt><dd>${esc(record.numberOfOrders)}</dd></div>
        <div><dt>Spent</dt><dd>${esc(record.amountSpent ? `${record.amountSpent.amount} ${record.amountSpent.currencyCode}` : "—")}</dd></div>
        <div><dt>Tags</dt><dd>${esc((record.tags || []).join(", ") || "—")}</dd></div>
      </dl>
      ${renderGiftCards(model, cardsOpen)}`;
  if (compact && model.ok && record) {
    body = `<p class="customer-email">${esc(record.defaultEmailAddress?.emailAddress || "Email unavailable")}</p>
      <dl class="customer-stats">
        <div><dt>Orders</dt><dd>${esc(record.numberOfOrders ?? "—")}</dd></div>
        <div><dt>Total spent</dt><dd>${esc(formatMoney(record.amountSpent, "—"))}</dd></div>
      </dl>
      ${record.createdAt ? `<p class="customer-since mute">Customer since ${esc(formatWhen(record.createdAt).split(",")[0])}</p>` : ""}
      ${record.tags?.length ? `<p class="customer-tags mute">${esc(record.tags.join(" · "))}</p>` : ""}
      ${model.hasGiftCards ? renderGiftCards(model, cardsOpen) : ""}`;
  }
  return `<section class="rail-card" data-tissue="customer" data-open="${open ? "true" : "false"}">
    <button type="button" class="rail-toggle" data-toggle="customer" aria-expanded="${open ? "true" : "false"}" title="Show or hide Customer">
      <h2>${compact && model.ok ? esc(model.peek) : "Customer"}</h2>
      ${compact && model.ok ? "" : `<span class="peek">${esc(model.peek)}</span>`}
    </button>
    <div class="rail-body">${body}</div>
  </section>`;
}

export function createCustomerTissue({ shop }) {
  return {
    id: "customer",
    async load({ shop: shopId, customerId }) {
      try {
        const record = await shop.getCustomer({ shop: shopId, customerId });
        return projectCustomer(record);
      } catch (err) {
        return { ok: false, peek: "Customer error", record: null, error: String(err?.message || err) };
      }
    },
    project: projectCustomer,
    render: renderCustomer,
  };
}
