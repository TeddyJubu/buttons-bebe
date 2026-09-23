# Inbox lock

Chrome is list / thread / rail (views live in the list toolbar). Column
balance is roughly **24% / 54% / 22%** (list / thread / rail) with
sensible min-widths; list and rail each collapse to a ~36px strip.
Selected list row is a **narrow accent edge** (`3–4px` `#B5471D`) plus a
**pale accent wash** (`color-mix` accent into surface ~8–12%). No grey
wash. No ink-only bar. IBM Plex.
List row time is relative (`2h`, `Yesterday`). Absolute stays in the
thread message header and the list tooltip.
Ticket details starts toggled off (collapsed, peeking the observed status);
the operator opens it per ticket and a context switch collapses it again.
Unread is session-local (no Shopify field): unread names bold; selecting
a ticket marks it read. Omit repeating **Open** in open queues; keep
Closed / Snoozed. Privacy / Unsubscribe / Bug / severity are small mute
badges — not purple.
Palette: ground `#F4F0EA`, surface `#FFFDF9`, ink `#1C1916`, mute `#5C564F`,
accent `#B5471D`. Corners use a small shared radius (chips/thumbs 4px,
controls 6px, cards/panes/sheets 8px). Human Send only. No Gorgias chrome, purple, Gaia, or a
fifth AI column. No Customer Edit, Refund, or Cancel.
Composer stays anchored under the thread scroll. Attachment images are
small expandable thumbs. AI draft is full-width with **Use draft** /
**Regenerate** / **Dismiss** under the text — never Send from the strip.

Empty-copy voice is short and parallel:

- No billing
- No returns
- No tracking
- No gift cards
- No discounts
- No invoice
- No warranty
- No ETA
- No customer
- No order
- No Shopify write

Empty Customer / This order bodies stay compact (one short line). Peek
stays `No customer` / `No order`. **Find customer** / **Link order** open
gated lock sheets only — no live join tool in this lock.

Thread inbound From is the customer persona (`From Ada Demo`), not the
AgentMail/shop mailbox login. Mute the email when it helps; omit mailbox
addresses (`helpdesk-support@`, `teddyjubu@agentmail.to`). Staff/outbound
From stays the shop identity (`From Demo Shop`). Mute status events stay
(`Closed · Tuesday`, `Escalated · …`).

## This order — fulfillment + partial ship

The rail is a client of `helpdesk.get_order` (same `dispatch()` / `invoke()`
path as MCP + CLI). No second data path. Do not invent tracking.

1. **Unfulfilled / empty shipment** — peek `No tracking`. Shipment stays
   collapsed. Ada-style `#1001` / empty `fulfillments` is this state.
2. **Fulfilled / in transit** — when `fulfillments[].trackingInfo` has a
   number or URL, open Shipment. Show **mute company name** + **tracking
   number as copy** (IBM Plex Mono, not a link). A separate **Track**
   control opens the carrier URL when `url` is present. Do not merge
   carrier + number into one accent link. `Fulfillment.displayStatus`
   (e.g. `IN_TRANSIT`) may peek **In transit**. Accent stays on Track only.
3. **Partially fulfilled** — `Order.displayFulfillmentStatus` is
   `PARTIALLY_FULFILLED`. Each line shows an official
   `LineItem.unfulfilledQuantity` cue so some lines read **Shipped** and
   the rest **Unfulfilled** (or `N of Q unfulfilled`). Shopkeep can answer
   “where’s the rest?” without leaving the inbox.

Miss → `No tracking` / unfulfilled line cue. Never invent a tracking number.
Title-case on screen (`Unfulfilled`, `Partially Fulfilled`, `In transit`).
Enums in data stay Shopify (`UNFULFILLED`, `PARTIALLY_FULFILLED`, `IN_TRANSIT`).

## Gift cards + discounts

Read-only peeks. Same `dispatch()` / `invoke()` path. No refund, cancel, or
gift-card write.

1. **Gift cards** — under Customer. Fixture `GiftCard` rows show masked
   last-four (`lastCharacters` / `maskedCode`), MoneyV2 `balance`, and
   Enabled/Disabled from `enabled`. Empty peek `No gift cards`. Live stays
   empty until Clerk wires `giftCards(query: "customer_id:…")`.
2. **Discounts** — under This order. Official `Order.discountCodes`. Empty
   peek `No discounts`.
3. **Invoice** — under This order. Fixture `invoiceUrl` is a normal **Invoice**
   link (new tab). Empty peek `No invoice`. Live stays empty: Admin GraphQL
   2026-07 `Order` has no invoice/receipt URL. Do not send invoices. Do not
   use `orderInvoiceSend`.
4. **Warranty** — under This order. Fixture `warranty` peeks period · status
   (`1 year · Active`) and may show `Ends 12 Mar 2027` when open. Empty peek
   `No warranty`. Live stays empty: Admin GraphQL 2026-07 `Order` / `LineItem`
   have no warranty field. Do not invent metafield namespace/key names.
5. **ETA / zone** — under This order, next to Shipment. Official
   `Fulfillment.estimatedDeliveryAt` peeks `ETA Tue 8 Sep`. Fixture
   `shippingZone` may add mute `Zone: Domestic`. Empty peek `No ETA`
   covers both. Live zone stays empty: Admin GraphQL 2026-07 Order /
   Fulfillment have no shipping-zone field. Track chrome stays company +
   mono number + Track.

Payments chrome (`Payments locked`, Refund, Cancel) shows only when
This order has an `orderId`. Empty tickets peek `No order` and hide the
money gate. Compact **Find customer** / **Link order** open gated lock
sheets only.

## Marketing unsubscribe

First-party ticket `requestType`. Not a Shopify Marketing write. Human
owns the preference change out of band.

1. List meta mutes `Unsubscribe` when `requestType` is
   `marketing_unsubscribe`. Same mute status voice as Open / Closed /
   Snoozed. No purple badge.
2. Thread header mutes `Marketing unsubscribe` under the subject.
3. Optional hairline `Mark unsubscribed` in the thread header. First-party
   ticket flag only. It does not write Shopify.
4. Gated lock copy:
   `Marketing consent stays locked. No live unsubscribe.`
5. Draft strip (`helpdesk.draft_reply`) confirms opt-out / marketing
   preference path. Verbs: Use draft / Regenerate / Dismiss — never Send.
   Never invent order, catalog, or destination copy.

No fifth rail tissue. Customer stays the top rail section.

Do not call marketing unsubscribe mutations. Do not query invented
consent fields. Official `Customer.emailMarketingConsent` stays unread
on this slice.

## Privacy request

First-party ticket `requestType`. Same pattern as Unsubscribe. Not a
Shopify Customer Privacy / GDPR Admin write. Human handles export or
deletion out of band. No fifth rail tissue.

1. List mutes chip `Privacy` when `requestType` is `privacy_request`.
   Same mute status voice as Open / Closed / Snoozed / Unsubscribe.
   Not purple. Not color-alone.
2. Thread header mutes `Privacy request` under the subject.
3. Optional subtype peek `Access` / `Delete` / `Export` when intake
   sets it. Lives next to the thread-header mute label.
4. Optional hairline `Mark privacy handled` in the thread header.
   First-party ticket flag only. It does not write Shopify.
5. Gated lock copy:
   `Privacy tools stay locked. No live data erase or export.`
   Confirm in the gate sheet can be ink.
6. Draft strip explains the privacy path out of band (export / deletion).
   Verbs: Use draft / Regenerate / Dismiss — never Send.
   Never invent order, catalog, or destination copy.

Customer stays the top rail section.

Intake keywords (fixture-ok): privacy / GDPR / delete my data /
data request. Do not call Customer privacy / GDPR / data-request
Admin mutations. Do not query invented privacy fields.

## Bug report

First-party ticket `requestType`. Same mute voice as Unsubscribe /
Privacy. Not a Shopify product or catalog write. Severity and device
are helpdesk fields only.

1. List mutes chip `Bug` when `requestType` is `bug`, plus the
   severity word (`Low` / `Medium` / `High` / `Critical`). Same mute
   status voice as Open / Closed / Snoozed. Not purple. Not
   color-alone.
2. Thread header mutes `Bug report` under the subject, then optional
   `High · iOS` (severity word · device).
3. Optional hairline `Mark bug handled` in the thread header.
   First-party ticket flag only. It does not write Shopify.
4. Draft strip acknowledges the report and asks for the repro device
   (`iOS` / `Android`). Verbs: Use draft / Regenerate / Dismiss — never
   Send. Never invent order, catalog, or destination copy. Human Send only.

## Damage / torn photo (no order)

When a ticket is a damage / torn / cracked photo without an order GID
(`t-demo-03-damaged-rattle`, `t-demo-17-plush`, or keyword match), the
draft acknowledges the tear/photo and asks for an order number. Never
say “destination.” Never invent order or catalog copy. Human Send only.

No fifth rail tissue. Customer stays the top rail section.

Intake keywords (fixture-ok): bug / crash; broken when paired with
iOS / Android / device / app. Device peek is `iOS` or `Android`.
Do not call product create / update mutations. Do not invent
Shopify product fields.
