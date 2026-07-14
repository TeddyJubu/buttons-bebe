---
type: Index
title: Buttons Bebe Knowledge Base
description: Complete knowledge base for the Teddy AI support agent. All 31 customer intents covered.
timestamp: 2026-06-28
---

# Buttons Bebe Knowledge Base

Edit any file to update the agent instantly — no restart needed.
The agent reads all files fresh on every ticket.

---

## Core Rules (read first)

- [Core Agent Rules](core-rules.md) — order identification, action before response, when to escalate, tone

---

## Policies

| File | Covers |
|---|---|
| [Returns and Refunds](policies/returns.md) | 7-day refund window, store credit, restocking fees, return conditions, gift-wrapped orders, baby gifts |
| [Shipping](policies/shipping.md) | Processing time, sale-season delays, USPS/UPS/ETA estimates, urgent orders |
| [Order Changes](policies/exchanges.md) | Pickup↔shipping switch, size changes, sale-season in-warehouse exchanges |
| [Discounts](policies/discount-codes.md) | No first-time discount, promo codes, expired codes, no price adjustments |
| [Final Sale](policies/final-sale.md) | Discount thresholds (20%+), no price adjustments, warehouse-only exchange |
| [Address Changes](policies/address-changes.md) | Zip code and address corrections |
| [Package Protection](policies/package-protection.md) | Coverage, lost/stolen claims, remove or refund protection |
| [International Orders](policies/international-orders.md) | International/Israel shipping, no prepaid return labels |

---

## Product Guides

| File | Covers |
|---|---|
| [Sizing Guide](products/sizing-guide.md) | DO NOT GUESS — sizing, fit, sleeve length, measurements |
| [Materials](products/materials.md) | DO NOT GUESS — fabric and material questions |
| [Care Instructions](products/care-instructions.md) | Washing and care |

---

## FAQ

| File | Covers |
|---|---|
| [Order Tracking](faq/order-tracking.md) | Status labels, tracking not updating, delivered but not received (lost/stolen) |
| [Damaged or Wrong Items](faq/damaged-items.md) | Photos required, warehouse escalation, color/photo discrepancy |
| [Refund Explanation](faq/refund-explanation.md) | Why a refund was issued, reviewing return refunds |
| [Gift Packages](faq/gift-packages.md) | Identifying sender of gift package |
| [Brand Launches](faq/brand-launches.md) | When a brand or new arrival is coming |
| [Gift Wrapping](faq/gift-wrapping.md) | Gift wrap eligibility ($35), checkout requirement, gift-wrapped returns |
| [Pickup and Warehouse](faq/pickup-and-warehouse.md) | Warehouse address/hours, pickup bins, drop-off, try-on, contacts |

---

## How to update

1. Open the relevant .md file
2. Edit the content
3. Save — done. No restart needed.

To add a new intent:
1. Create a `.md` file in the right folder
2. Copy the frontmatter from any existing file
3. Set `type`, `title`, `tags` (crucial for search), and `links`
4. Write the policy, agent steps, and customer response templates in the body

---

## Intents covered (31 total)

1. First-time customer discount → [Discounts](policies/discount-codes.md)
2. Pickup → Shipping switch → [Order Changes](policies/exchanges.md)
3. Shipping → Pickup switch → [Order Changes](policies/exchanges.md)
4. Sizing for multiple items → [Sizing Guide](products/sizing-guide.md)
5. How item runs → [Sizing Guide](products/sizing-guide.md)
6. Package protection refund → [Package Protection](policies/package-protection.md)
7. Gift package sender → [Gift Packages](faq/gift-packages.md)
8. Wrong size — change before shipping → [Order Changes](policies/exchanges.md)
9. Measurements → [Sizing Guide](products/sizing-guide.md)
10. Address/zip correction → [Address Changes](policies/address-changes.md)
11. Fabric/material question → [Materials](products/materials.md)
12. Final sale exception → [Final Sale](policies/final-sale.md)
13. Sleeve length → [Sizing Guide](products/sizing-guide.md)
14. Refund explanation → [Refund Explanation](faq/refund-explanation.md)
15. Wrong item received → [Damaged/Wrong Items](faq/damaged-items.md)
16. Damaged item received → [Damaged/Wrong Items](faq/damaged-items.md)
17. When will order ship → [Shipping](policies/shipping.md)
18. Urgent order → [Shipping](policies/shipping.md)
19. Store credit instead of refund → [Returns](policies/returns.md)
20. How long order is refundable → [Returns](policies/returns.md)
21. Brand launch date → [Brand Launches](faq/brand-launches.md)
22. How long shipping takes → [Shipping](policies/shipping.md)
23. Restocking fee question / dispute → [Returns](policies/returns.md)
24. Refund window / baby-gift / gift-wrapped order returns → [Returns](policies/returns.md)
25. Item became final sale (20%+ discount) → [Final Sale](policies/final-sale.md)
26. Forgot / expired promo code, no price adjustments → [Discounts](policies/discount-codes.md)
27. Sale-season processing delay & in-warehouse exchange → [Shipping](policies/shipping.md) / [Order Changes](policies/exchanges.md)
28. Lost / stolen / delivered-but-not-received package → [Order Tracking](faq/order-tracking.md) / [Package Protection](policies/package-protection.md)
29. Pickup, warehouse hours, drop-off, try-on → [Pickup and Warehouse](faq/pickup-and-warehouse.md)
30. International / Israel orders & returns → [International Orders](policies/international-orders.md)
31. Item looks different from photo (color/material discrepancy) → [Damaged/Wrong Items](faq/damaged-items.md)
