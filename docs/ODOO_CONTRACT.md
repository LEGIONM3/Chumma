# DealFlow360 — Odoo Integration & Governance Contract

## 1. Architectural Boundary & Core Principle

> **Odoo owns the transaction. DealFlow governs the transaction.**

- **Odoo** is the single source of truth for: Customers (`res.partner`), Catalog & Variants (`product.product`, `product.template`), Pricelists & Rules (`product.pricelist`), Quotations & Orders (`sale.order`), Inventory Quants (`stock.quant`), Delivery Pickings (`stock.picking`), Subscriptions & Recurrences, Invoices (`account.move`), and Payments (`account.payment`).
- **DealFlow** is the deterministic, pure-Python governance decision engine (**Deal Guardian**) for: Commercial Discount Policies, Multi-factor Risk Scoring, Automated Approval Chains, Multi-warehouse Splitting, Cross-sell & Upsell Recommendations, Deal Health Monitoring, and Customer Negotiation Guardrails.
- **DealFlow Database** stores no duplicate transactional truth. It holds references (`odoo_*_id`), cached totals (`*_cache`), and immutable governance decision history (`risk_assessment`, `approval_request`, `audit_event`, `fulfillment_plan`).

---

## 2. Gateway API Specification (`OdooGateway`)

All communication between DealFlow and Odoo flows through the `OdooGateway` interface (`app/odoo/interface.py`). In production, this uses `XmlRpcOdooGateway` via standard XML-RPC endpoints `/xmlrpc/2/common` and `/xmlrpc/2/object`. In test and offline development environments, it uses `FakeOdooGateway` with in-memory fixtures.

### 2.1 Gateway Methods

| Method | Target Odoo Model | Odoo Method / RPC Call | Fields Read / Modified | Description |
|---|---|---|---|---|
| `authenticate(login, password)` | `res.users`, `res.groups` | `common.authenticate`, `res.users.read`, `res.groups.read` | `id, name, login, email, share, partner_id, groups_id` | Authenticates internal sales, manager, finance, or portal users; resolves groups. |
| `get_sale_order(order_id)` | `sale.order`, `sale.order.line` | `sale.order.read`, `sale.order.line.read` | `id, name, partner_id, state, currency_id, validity_date, dealflow_*` | Extracts order header and active lines for `DealContext`. |
| `list_sale_orders(...)` | `sale.order` | `sale.order.search_read` | Filtered headers | Used by background sync job and portal queries. |
| `get_partner(partner_id)` | `res.partner` | `res.partner.read` | `name, x_dealflow_tier, email, property_payment_term_id` | Retrieves customer tier and payment terms. |
| `get_products(product_ids)` | `product.product`, `product.category` | `product.product.read`, `product.category.read` | `name, categ_id, parent_id, standard_price, list_price, type` | Resolves product types, standard cost, and category hierarchy. |
| `get_price(partner, prod, qty)` | `product.pricelist` | `product.pricelist._compute_price_rule` or list_price | Calculated price | Resolves customer-specific unit price. |
| `get_warehouses()` | `stock.warehouse` | `stock.warehouse.search_read` | `id, name, code` | Fetches active warehouse locations. |
| `get_availability(prods, whs)` | `stock.quant` | `stock.quant.search_read` | `product_id, warehouse_id, quantity, reserved_quantity` | Calculates available inventory (`quantity - reserved_quantity`). |
| `get_pickings(order_id)` | `stock.picking` | `stock.picking.search_read` | `id, warehouse_id, state, scheduled_date, date_done` | Monitors fulfillment progress and delivery promise slippage. |
| `get_billing_summary(order_id)` | `sale.order` | `dealflow_billing_summary` | Summary JSON | Extracts one-time lines, recurring lines, invoices, payments, and schedules. |
| `apply_line_changes(order, changes)` | `sale.order` | `dealflow_apply_line_changes` (fallback: `write`) | `order_line: [(1, id, vals)]` | Atomically applies line discount or quantity adjustments. |
| `add_line(order, prod, qty, disc)` | `sale.order.line` | `sale.order.line.create` | Line fields | Appends recommendation or negotiated product to order. |
| `set_governance(order, state, risk, lock)` | `sale.order` | `dealflow_set_governance` (fallback: `write`) | `dealflow_approval_state, dealflow_risk_score, dealflow_locked` | Synchronizes DealFlow approval state and lock flag. |
| `confirm(order_id)` | `sale.order` | `dealflow_confirm` (fallback: `action_confirm`) | `state` | Confirms quotation into sale order; respects `dealflow_locked`. |
| `cancel(order_id)` | `sale.order` | `action_cancel` | `state` | Formally cancels the order in Odoo. |
| `apply_fulfillment_plan(order, allocs)` | `sale.order` | `dealflow_apply_fulfillment_plan` | Pickings generated | Creates multi-warehouse delivery pickings and backorders. |
| `register_payment(inv, amt, journal)` | `account.move` | `dealflow_register_payment` | Payment wizard | Records customer payment against invoice. |
| `get_confirmed_orders_for_mining(...)` | `sale.order`, `sale.order.line` | `sale.order.search_read` | `id, order_line.product_id` | Mined historical co-purchase product sets. |

---

## 3. Required Odoo Custom Fields & Server Methods

The DealFlow governance system interacts with the custom fields and server methods defined in the `dealflow_odoo` module:

### 3.1 Custom Fields on `sale.order`
- `dealflow_deal_id` (`Char`): DealFlow deal reference UUID.
- `dealflow_risk_score` (`Float`): Calculated risk score (0.00–100.00).
- `dealflow_approval_state` (`Selection`): `draft`, `pending_approval`, `approved`, `rejected`, `reapproval_required`.
- `dealflow_health_status` (`Selection`): `healthy`, `at_risk`, `critical`.
- `dealflow_locked` (`Boolean`): Prevents order confirmation when `True`.
- `dealflow_blended_discount` (`Float`): Weighted average discount across active lines.

### 3.2 Custom Fields on `res.partner`
- `x_dealflow_tier` (`Selection` / `Char`): `BRONZE`, `SILVER`, `GOLD`.

### 3.3 Custom Methods Expected on `sale.order`
1. `dealflow_apply_line_changes(changes: list)`: Atomic updates for discounts and quantities.
2. `dealflow_set_governance(approval_state, risk_score, locked)`: Writes overlay governance attributes.
3. `dealflow_confirm()`: Confirms order, rejecting execution if `dealflow_locked` is active.
4. `dealflow_apply_fulfillment_plan(allocations: list)`: Creates pickings per warehouse and reserves inventory.
5. `dealflow_billing_summary()`: Returns billing breakdown across one-time and recurring items.

> **Graceful Degradation (`OdooCapabilityMissing`):**
> If a specialized method is absent on the Odoo instance, the gateway raises `OdooCapabilityMissing(method_name)`. The DealFlow API catches this and returns an informative HTTP 501 status code detailing the missing Odoo method.

---

## 4. Inbound Webhook Specification (`POST /api/v1/events/odoo`)

Odoo triggers event callbacks to DealFlow when material changes occur.

### 4.1 Headers & Authentication
- `X-DealFlow-Signature`: `HMAC-SHA256(secret=ODOO_WEBHOOK_SECRET, payload=raw_body)`
- If signature does not match, DealFlow returns `401 Unauthorized`.

### 4.2 Webhook Payload Schema
```json
{
  "event_id": "evt_20260905_0019283",
  "event_type": "sale.order.changed",
  "model": "sale.order",
  "res_id": 3812,
  "sale_order_id": 3812,
  "occurred_at": "2026-09-05T15:00:00Z",
  "payload": {
    "modified_fields": ["order_line", "dealflow_blended_discount"],
    "state": "draft"
  }
}
```

### 4.3 Idempotency Guarantee
DealFlow records every `event_id` in PostgreSQL table `processed_event` (`event_id` PRIMARY KEY). If an `event_id` is re-delivered, DealFlow halts execution and responds immediately with `200 OK`:
```json
{ "status": "duplicate", "event_id": "evt_20260905_0019283" }
```

---

## 5. Security Group & Role Mapping

Odoo internal users map to DealFlow roles using `ROLE_GROUP_MAP`:
- `ADMIN`: `base.group_system` or `dealflow_odoo.group_dealflow_admin`
- `SALES_MANAGER`: `sales_team.group_sale_manager` or `dealflow_odoo.group_dealflow_sales_manager`
- `SALES_REP`: `sales_team.group_sale_salesman` or `dealflow_odoo.group_dealflow_sales_rep`
- `FINANCE`: `account.group_account_manager` or `dealflow_odoo.group_dealflow_finance`
- `CUSTOMER`: `res.users` with `share=True` (portal access, isolated to own partner record)

---

## 6. Policy Resolution Precedence

When determining the discount ceiling and margin floor for a line item, the Deal Guardian resolves policies in strict descending order of specificity:

1. **Tier + Specific Category Ancestor**: Matching `customer_tier_code` AND line category (or nearest ancestor in `category_path`), ordered by distance to leaf category ASC, then `priority ASC`.
2. **Compound Minimum**: If both a tier-only policy and a category-only policy match, ceiling is $\min(\text{tier\_ceiling}, \text{category\_ceiling})$, minimum margin is $\max(\text{non-null})$.
3. **Single Rule Match**: A single matching tier-only or category-only policy.
4. **Company Global Policy**: A global rule where `customer_tier_code IS NULL` AND `odoo_product_category_id IS NULL`.
5. **Customer Tier Baseline**: Fallback to `customer_tier.default_max_discount_pct` with no minimum margin floor.
