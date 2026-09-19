/**
 * Inbox tissue contracts. Tissues are replaceable black boxes.
 * Other tissues know only these inputs and outputs — never internals.
 *
 * Field names on Clerk rail DTOs are locked to Shopify Admin GraphQL 2026-07
 * (read-only). The shop tissue is the only place those records are fetched.
 * This module is documentation-as-code; it exports no runtime behavior.
 */

/**
 * @typedef {object} MoneyV2
 * @property {string} amount
 * @property {string} currencyCode
 */

/**
 * @typedef {object} MoneyBag
 * @property {MoneyV2} shopMoney
 * @property {MoneyV2} presentmentMoney
 */

/**
 * @typedef {object} ClerkCustomer
 * @property {string} displayName
 * @property {{ emailAddress: string } | null} defaultEmailAddress
 * @property {string} createdAt
 * @property {string} numberOfOrders
 * @property {MoneyV2} amountSpent
 * @property {string[]} tags
 */

/**
 * @typedef {object} ClerkLineItem
 * @property {string} title
 * @property {string | null} sku
 * @property {number} quantity
 * @property {number} [unfulfilledQuantity]
 * @property {MoneyBag} originalUnitPriceSet
 * @property {{ url: string, altText?: string } | undefined} [image]
 */

/**
 * @typedef {object} ClerkAddress
 * @property {string | null} [name]
 * @property {string | null} [address1]
 * @property {string | null} [address2]
 * @property {string | null} [city]
 * @property {string | null} [province]
 * @property {string | null} [zip]
 * @property {string | null} [country]
 */

/**
 * @typedef {object} ClerkTrackingInfo
 * @property {string} number
 * @property {string} url
 * @property {string} company
 */

/**
 * @typedef {object} ClerkFulfillment
 * @property {ClerkTrackingInfo[]} trackingInfo
 * @property {string} [displayStatus]
 * @property {{ nodes: { quantity: number, lineItem: { title: string } }[] }} [fulfillmentLineItems]
 */

/**
 * @typedef {object} ClerkOrder
 * @property {string} id
 * @property {string} name
 * @property {string} createdAt
 * @property {string} displayFinancialStatus
 * @property {string} displayFulfillmentStatus
 * @property {MoneyBag} currentTotalPriceSet
 * @property {{ nodes: ClerkLineItem[] }} lineItems
 * @property {ClerkAddress | null} shippingAddress
 * @property {ClerkAddress | null} billingAddress
 * @property {ClerkFulfillment[]} fulfillments
 * @property {MoneyBag | null} [currentSubtotalPriceSet]
 * @property {MoneyBag | null} [totalShippingPriceSet]
 * @property {MoneyBag | null} [totalTaxSet]
 */

/**
 * @typedef {object} ClerkReturnItem
 * @property {string} title
 * @property {string | null} reason
 * @property {string | null} type
 */

/**
 * @typedef {object} ClerkReturn
 * @property {string} id
 * @property {string} status
 */

/**
 * @typedef {object} ClerkReturns
 * @property {{ nodes: ClerkReturn[] }} returns
 * @property {ClerkReturnItem[]} items
 * @property {MoneyBag | null} refundTotal
 * @property {MoneyBag | null} creditTotal
 * @property {ClerkTrackingInfo | null} tracking
 */

/**
 * @typedef {object} ClerkOrderHistoryRow
 * @property {string} id
 * @property {string} name
 * @property {string} createdAt
 * @property {string} displayFulfillmentStatus
 * @property {{ shopMoney: MoneyV2 }} currentTotalPriceSet
 */

/**
 * @typedef {object} ClerkTicketRow
 * @property {string} id
 * @property {string} customerName
 * @property {string} subject
 * @property {string} snippet
 * @property {"open" | "closed" | "snoozed"} status
 * @property {string} updatedAt
 * @property {string} customerId
 * @property {string | null} orderId
 * @property {"marketing_unsubscribe" | "privacy_request" | "bug" | null} [requestType]
 * @property {"low" | "medium" | "high" | "critical" | null} [severity]
 * @property {string | null} [device]
 */

/**
 * @typedef {object} ClerkTicket
 * @property {string} id
 * @property {string} customerName
 * @property {string} subject
 * @property {string} snippet
 * @property {"open" | "closed" | "snoozed"} status
 * @property {string} updatedAt
 * @property {string} customerId
 * @property {string | null} orderId
 * @property {"marketing_unsubscribe" | "privacy_request" | "bug" | null} [requestType]
 * @property {"access" | "delete" | "export" | null} [privacySubtype]
 * @property {boolean} [privacyHandled]
 * @property {boolean} [unsubscribeHandled]
 * @property {boolean} [bugHandled]
 * @property {"low" | "medium" | "high" | "critical" | null} [severity]
 * @property {string | null} [device]
 * @property {object[]} messages — each talk message has `from` (`customer`|`agent`),
 *   `fromName`, optional `fromEmail`, and `name` (same as fromName). Inbound From
 *   is the customer persona, never the AgentMail/shop mailbox login.

 * @property {{ at: string, status: string, note?: string }[]} statusEvents
 * @property {boolean} [escalated]
 * @property {string} [escalationReason]
 */

/**
 * Shop tissue. The inbox rail is a client of helpdesk.* tools
 * (`get_customer`, `get_order`, `get_returns`, `list_past_orders`) via
 * `createHelpdeskShop`. Views / list / thread are clients of `list_tickets`
 * and `get_ticket` on the same invoke(). Composer is a client of `draft_reply`
 * and `summarize_thread` / `search_macros` / `apply_macro`. Fixtures are the
 * fallback when mint/Admin is down. Ticket rows carry first-party
 * `customerName` (never `displayName`) and ticket status open/closed/snoozed
 * (never Return.status OPEN). Must not mutate Shopify.
 * `SHOPIFY_MUTATIONS_ENABLED` stays 0.
 *
 * @typedef {object} ShopTissue
 * @property {(req: { shop: string, customerId: string }) => Promise<ClerkCustomer | null> | ClerkCustomer | null} getCustomer
 * @property {(req: { shop: string, orderId: string }) => Promise<ClerkOrder | null> | ClerkOrder | null} getOrder
 * @property {(req: { shop: string, orderId: string }) => Promise<ClerkReturns> | ClerkReturns} getReturns
 * @property {(req: { shop: string, customerId: string }) => Promise<ClerkOrderHistoryRow[]> | ClerkOrderHistoryRow[]} getOrderHistory
 * @property {(req: { view?: string, limit?: number }) => Promise<ClerkTicketRow[]> | ClerkTicketRow[]} [listTickets]
 * @property {(req: { ticketId: string }) => Promise<ClerkTicket | null> | ClerkTicket | null} [getTicket]
 */

/**
 * Mailbox topics. Tissues publish and subscribe; they do not import each other.
 *
 * `view/selected`        { viewId }
 * `channel/selected`     { channelId } — observed-history channel filter; never Send
 * `status/selected`      { statusId } — observed-history status filter; never Send
 * `assignee/selected`    { assigneeId } — observed-history assignee filter; never Send
 * `tag/selected`         { tagId } — observed-history tag filter; never Send
 * `list/filter-changed`  { conditions, match } — builder edits; client-side read over loaded rows; never Send
 * `list/filter-view-save` { name, shared } — first-party browser store; never Gorgias
 * `list/filter-view-apply` { id } — first-party browser store; never Gorgias
 * `list/filter-view-delete` { id } — first-party browser store; never Gorgias
 * `list/selected`        { ticketId }
 * `list/bulk-toggle`     { ticketId, shiftKey } — row checkbox toggle; never Send
 * `list/sort-selected`   { sortId } — display order only ("default"|"newest"|"oldest"); never Send
 * `list/collapsed`       { collapsed: boolean }
 * `rail/collapsed`       { collapsed: boolean }
 * `create-ticket/open`   {} — New ticket sheet; the created ticket is first-party browser store only; never Gorgias, never a customer notification
 * `create-ticket/close`  {}
 * `composer/body`        { text }
 * `composer/insert`      { text } — Use draft; never Send
 * `composer/discard`     {} — Dismiss strip; never Send
 * `composer/regenerate`  {} — re-call draft_reply; never Send
 * `composer/macros`      { open } — picker only; never Send
 * `composer/send`        { text, close: boolean }
 * `composer/summarize`   { ticketId }
 * `thread/step`          { delta } — previous/next nav over the current list; never Send
 * `thread/state`         { ticketId, field, value } — first-party browser store; never Gorgias
 * `thread/mark-unread`   { ticketId } — first-party read state; never Gorgias
 * `thread/escalate`      { ticketId, reason? } — first-party; never Send
 * `thread/rename`        { ticketId, title } — first-party browser store; never Gorgias
 * `thread/copy-link`     { ticketId } — boot copies the console deep link; never Gorgias
 * `write-gate/open`      {} — This order hairline opens the payments sheet
 * `write-gate/close`     {}
 * `customer-join-gate/open`  {} — Find customer lock sheet (no live join)
 * `customer-join-gate/close` {}
 * `order-link-gate/open`     {} — Link order lock sheet (no live link)
 * `order-link-gate/close`    {}
 * `privacy-gate/open`    {} — thread-header hairline opens the gated lock sheet
 * `privacy-gate/close`   {}
 * `privacy-gate/handled` { ticketId } — first-party flag only; never Shopify
 * `marketing-gate/open`  {} — thread-header hairline opens the gated lock sheet
 * `marketing-gate/close` {}
 * `marketing-gate/handled` { ticketId } — first-party flag only; never Shopify
 * `bug-handled`          { ticketId } — first-party flag only; never Shopify
 * `history/peek`         { orderId }  — does not replace This order
 * `tissue/error`         { tissueId, message }
 */

export const MAILBOX_TOPICS = Object.freeze({
  VIEW_SELECTED: "view/selected",
  LIST_SEARCHED: "list/searched",
  FILTER_CHANGED: "list/filter-changed",
  FILTER_VIEW_SAVE: "list/filter-view-save",
  FILTER_VIEW_APPLY: "list/filter-view-apply",
  FILTER_VIEW_DELETE: "list/filter-view-delete",
  CHANNEL_SELECTED: "channel/selected",
  STATUS_SELECTED: "status/selected",
  ASSIGNEE_SELECTED: "assignee/selected",
  TAG_SELECTED: "tag/selected",
  LIST_SELECTED: "list/selected",
  BULK_TOGGLE: "list/bulk-toggle",
  SORT_SELECTED: "list/sort-selected",
  LIST_COLLAPSED: "list/collapsed",
  RAIL_COLLAPSED: "rail/collapsed",
  CREATE_TICKET_OPEN: "create-ticket/open",
  CREATE_TICKET_CLOSE: "create-ticket/close",
  COMPOSER_BODY: "composer/body",
  COMPOSER_INSERT: "composer/insert",
  COMPOSER_DISCARD: "composer/discard",
  COMPOSER_REGENERATE: "composer/regenerate",
  COMPOSER_MACROS: "composer/macros",
  COMPOSER_SEND: "composer/send",
  COMPOSER_SUMMARIZE: "composer/summarize",
  THREAD_ESCALATE: "thread/escalate",
  THREAD_RENAME: "thread/rename",
  THREAD_COPY_LINK: "thread/copy-link",
  // #44: first-party ticket-detail controls — browser-store state only.
  THREAD_STEP: "thread/step",
  THREAD_STATE: "thread/state",
  THREAD_MARK_UNREAD: "thread/mark-unread",
  WRITE_GATE_OPEN: "write-gate/open",
  WRITE_GATE_CLOSE: "write-gate/close",
  CUSTOMER_JOIN_GATE_OPEN: "customer-join-gate/open",
  CUSTOMER_JOIN_GATE_CLOSE: "customer-join-gate/close",
  ORDER_LINK_GATE_OPEN: "order-link-gate/open",
  ORDER_LINK_GATE_CLOSE: "order-link-gate/close",
  PRIVACY_GATE_OPEN: "privacy-gate/open",
  PRIVACY_GATE_CLOSE: "privacy-gate/close",
  PRIVACY_HANDLED: "privacy-gate/handled",
  MARKETING_GATE_OPEN: "marketing-gate/open",
  MARKETING_GATE_CLOSE: "marketing-gate/close",
  MARKETING_HANDLED: "marketing-gate/handled",
  BUG_HANDLED: "bug-handled",
  HISTORY_PEEK: "history/peek",
  TISSUE_ERROR: "tissue/error",
});

/** LOCK voice. Gated confirm. Not a Customer Privacy write. */
export const PRIVACY_LOCKED_COPY =
  "Privacy tools stay locked. No live data erase or export.";

/** LOCK voice. Gated confirm. Not a live marketing unsubscribe write. */
export const MARKETING_LOCKED_COPY =
  "Marketing consent stays locked. No live unsubscribe.";

/** LOCK voice. Hairline + gate sheet. Not a Refund or Cancel control. */
export const PAYMENTS_LOCKED_COPY =
  "Payments are locked until Syeed names an exact write. Refunds and cancels stay refused.";

/** LOCK voice. Find customer — gated only. No live join tool. */
export const CUSTOMER_JOIN_LOCKED_COPY =
  "Customer join stays locked. No live find yet.";

/** LOCK voice. Link order — gated only. No live link tool. */
export const ORDER_LINK_LOCKED_COPY =
  "Order link stays locked. No live link yet.";

export const FORBIDDEN_CONTROLS = Object.freeze([
  "gaia",
  "refund",
  "cancel",
  "edit",
  "edit order",
  "edit-order",
  "duplicate",
  "create order",
  "customerupdate",
]);
