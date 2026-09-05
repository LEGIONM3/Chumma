# DealFlow360 — Complete API Guide & End-to-End Walkthrough

This guide provides exhaustive `curl` command sequences with real JSON request and response payloads executing in **Fake Mode** (`DEALFLOW_ODOO_MODE=fake`). It demonstrates the two primary commercial governance lifecycles of DealFlow360.

---

## Environment & Base URL

```bash
export API_URL="http://localhost:8000/api/v1"
```

Default seeded credentials:
- **Sales Rep**: `rep1@dealflow.local` / `rep1_pass` (Odoo User ID: 10)
- **Sales Manager**: `manager@dealflow.local` / `manager_pass` (Odoo User ID: 2)
- **Finance Officer**: `finance@dealflow.local` / `finance_pass` (Odoo User ID: 3)
- **Administrator**: `admin@dealflow.local` / `admin_pass` (Odoo User ID: 1)
- **Customer Acme Corp**: `contact@acme.com` (Partner ID: 1)

---

## Flow A: Risky Discount -> Finance Approval -> AI Upsell -> Warehouse Split

Demonstrates creating a quotation that exceeds the sales rep's discretionary discount threshold, requiring two-stage governance approval, accepting an AI co-purchase recommendation, and executing multi-warehouse fulfillment.

### Step 1: Sales Rep Authentication
```bash
curl -s -X POST "$API_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "rep1@dealflow.local", "password": "rep1_pass"}'
```
**Response (200 OK)**:
```json
{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
    "expires_in": 43200,
    "user": {
      "id": "u-10",
      "email": "rep1@dealflow.local",
      "full_name": "Sarah Rep",
      "role": "SALES_REP",
      "odoo_user_id": 10
    }
  }
}
```
```bash
export REP_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

---

### Step 2: Create Risky Quotation (18% Discount on Hardware)
Acme Corp (Partner ID 1) requests 10x Laptops (Product 101, List ₹1,200) with an 18.0% discount. This exceeds the rep's 15% discretionary limit.

```bash
curl -s -X POST "$API_URL/deals/create" \
  -H "Authorization: Bearer $REP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "odoo_partner_id": 1,
    "tier_code": "ENTERPRISE",
    "lines": [
      {
        "odoo_product_id": 101,
        "product_name": "Enterprise Laptop 15",
        "qty": 10,
        "price_unit": 1200.0,
        "discount_pct": 18.0,
        "cost_price": 800.0,
        "is_recurring": false
      }
    ]
  }'
```
**Response (200 OK)**:
```json
{
  "data": {
    "id": "c1f8a84b-0123-4567-89ab-cdef01234567",
    "reference": "D-1001",
    "odoo_sale_order_id": 501,
    "odoo_order_name": "S00501",
    "status": "DRAFT",
    "approval_state": "PENDING_MANAGER",
    "current_risk_score": "56.00",
    "required_level": "MANAGER_AND_FINANCE",
    "amount_untaxed": "9840.00",
    "amount_total": "11611.20"
  }
}
```
```bash
export DEAL_ID="c1f8a84b-0123-4567-89ab-cdef01234567"
```

---

### Step 3: Inspect Deal Workspace & AI Recommendation
```bash
curl -s -X GET "$API_URL/deals/$DEAL_ID/workspace" \
  -H "Authorization: Bearer $REP_TOKEN"
```
**Response (200 OK)**:
```json
{
  "data": {
    "deal": {
      "id": "c1f8a84b-0123-4567-89ab-cdef01234567",
      "reference": "D-1001",
      "status": "DRAFT"
    },
    "risk": {
      "score": "56.00",
      "severity": "HIGH",
      "required_approval_level": "MANAGER_AND_FINANCE",
      "factors": [
        {"code": "MARGIN_BELOW_TARGET", "points": 25.0},
        {"code": "REP_AUTHORITY_EXCEEDED", "points": 20.0},
        {"code": "WAREHOUSE_SPLIT_REQUIRED", "points": 8.0}
      ]
    },
    "recommendations": [
      {
        "id": "rec-dock-104",
        "recommended_product_id": 104,
        "product_name": "USB-C Universal Docking Station",
        "confidence_score": 0.82,
        "reason": "Frequently bought together with Enterprise Laptop 15 (Confidence: 82%)"
      }
    ]
  }
}
```

---

### Step 4: Add Co-Purchase Recommendation to Deal
```bash
curl -s -X POST "$API_URL/deals/$DEAL_ID/recommendations/rec-dock-104/add" \
  -H "Authorization: Bearer $REP_TOKEN"
```
**Response (200 OK)**:
```json
{
  "data": {
    "deal_id": "c1f8a84b-0123-4567-89ab-cdef01234567",
    "odoo_line_id": 1002,
    "product_name": "USB-C Universal Docking Station",
    "qty": 10,
    "discount_pct": 10.0,
    "message": "Recommendation successfully added to deal."
  }
}
```

---

### Step 5: Sales Manager Approves Stage 1
```bash
# Login as Sales Manager
curl -s -X POST "$API_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "manager@dealflow.local", "password": "manager_pass"}'
export MGR_TOKEN="<manager_jwt>"

# Approve Stage 1
curl -s -X POST "$API_URL/deals/$DEAL_ID/approval/approve" \
  -H "Authorization: Bearer $MGR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Discount approved based on Enterprise volume commitment."}'
```
**Response (200 OK)**:
```json
{
  "data": {
    "id": "c1f8a84b-0123-4567-89ab-cdef01234567",
    "approval_state": "PENDING_FINANCE"
  }
}
```

---

### Step 6: Finance Officer Approves Stage 2 (Final Signoff)
```bash
# Login as Finance Officer
curl -s -X POST "$API_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "finance@dealflow.local", "password": "finance_pass"}'
export FIN_TOKEN="<finance_jwt>"

# Approve Stage 2
curl -s -X POST "$API_URL/deals/$DEAL_ID/approval/approve" \
  -H "Authorization: Bearer $FIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Blended margin satisfies Enterprise hurdle rate."}'
```
**Response (200 OK)**:
```json
{
  "data": {
    "id": "c1f8a84b-0123-4567-89ab-cdef01234567",
    "approval_state": "APPROVED",
    "status": "DRAFT"
  }
}
```
*At this point, DealFlow atomically unlocks the quotation in Odoo (`dealflow_locked=False`).*

---

### Step 7: Rep Sends Quotation to Customer
```bash
curl -s -X POST "$API_URL/deals/$DEAL_ID/send" \
  -H "Authorization: Bearer $REP_TOKEN"
```
**Response (200 OK)**:
```json
{
  "data": {
    "id": "c1f8a84b-0123-4567-89ab-cdef01234567",
    "status": "SENT"
  }
}
```

---

### Step 8: Multi-Warehouse Fulfillment Optimization
Primary warehouse WH1 Main has only 8 Laptops; WH2 East has 15. The engine plans an 8 + 2 split:

```bash
# 8.1 Propose Fulfillment Plan
curl -s -X POST "$API_URL/deals/$DEAL_ID/fulfillment/propose" \
  -H "Authorization: Bearer $REP_TOKEN"
```
**Response (200 OK)**:
```json
{
  "data": {
    "id": "plan-789",
    "deal_id": "c1f8a84b-0123-4567-89ab-cdef01234567",
    "status": "PROPOSED",
    "strategy": "CHEAPEST",
    "estimated_shipments": 2,
    "estimated_shipping_cost": "750.00",
    "lines": [
      {
        "odoo_product_id": 101,
        "odoo_warehouse_id": 1,
        "allocated_qty": 8.0,
        "backorder_qty": 0.0
      },
      {
        "odoo_product_id": 101,
        "odoo_warehouse_id": 2,
        "allocated_qty": 2.0,
        "backorder_qty": 0.0
      },
      {
        "odoo_product_id": 104,
        "odoo_warehouse_id": 1,
        "allocated_qty": 10.0,
        "backorder_qty": 0.0
      }
    ]
  }
}
```

```bash
# 8.2 Accept & Apply Plan
curl -s -X POST "$API_URL/deals/$DEAL_ID/fulfillment/accept" \
  -H "Authorization: Bearer $REP_TOKEN"

curl -s -X POST "$API_URL/deals/$DEAL_ID/fulfillment/apply" \
  -H "Authorization: Bearer $REP_TOKEN"
```
**Response (200 OK)**:
```json
{
  "data": {
    "plan": {
      "id": "plan-789",
      "status": "APPLIED"
    },
    "picking_ids": [301, 302]
  }
}
```

---

## Flow B: Portal Counter-Offer -> Invalidation -> Re-Approval -> Payment

Demonstrates the **killer governance moment**: an approved quotation receives a customer counter-offer that invalidates prior signoffs, requiring fresh approval before final confirmation.

### Step 1: Customer Magic-Link Authentication
Customer requests a one-time login link:
```bash
curl -s -X POST "$API_URL/portal/auth/magic-link" \
  -H "Content-Type: application/json" \
  -d '{"email": "contact@acme.com"}'
```
**Response (202 Accepted)**:
```json
{
  "data": {
    "message": "If this email matches an active account, a login link has been sent."
  }
}
```

Fetch the generated magic link token from the outbox (in development/fake mode):
```bash
curl -s -X GET "$API_URL/admin/outbox"
```
```bash
export MAGIC_TOKEN="<token_from_outbox>"

# Verify token to obtain customer bearer JWT
curl -s -X GET "$API_URL/portal/auth/verify?token=$MAGIC_TOKEN"
```
**Response (200 OK)**:
```json
{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI...",
    "token_type": "bearer",
    "partner_id": 1,
    "partner_name": "Acme Corp"
  }
}
```
```bash
export CUST_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI..."
```

---

### Step 2: Customer Views Sanitized Quotation
Customer views quotation through the portal. Internal margins and risk scores are strictly stripped:

```bash
curl -s -X GET "$API_URL/portal/deals/$DEAL_ID" \
  -H "Authorization: Bearer $CUST_TOKEN"
```
**Response (200 OK)**:
```json
{
  "data": {
    "reference": "D-1001",
    "odoo_sale_order_id": 501,
    "partner_name": "Acme Corp",
    "status": "SENT",
    "amount_untaxed": "11190.00",
    "amount_total": "13204.20",
    "customer_confirmed_pending": false,
    "lines": [
      {
        "odoo_product_id": 101,
        "product_name": "Enterprise Laptop 15",
        "product_uom_qty": 10,
        "price_unit": 1200.0,
        "discount_pct": 18.0,
        "price_subtotal": 9840.0
      },
      {
        "odoo_product_id": 104,
        "product_name": "USB-C Universal Docking Station",
        "product_uom_qty": 10,
        "price_unit": 150.0,
        "discount_pct": 10.0,
        "price_subtotal": 1350.0
      }
    ],
    "comments": [],
    "negotiations": []
  }
}
```

---

### Step 3: Customer Submits 22% Counter-Offer Discount
Customer asks for a 22.0% discount on the laptops:
```bash
curl -s -X POST "$API_URL/portal/deals/$DEAL_ID/negotiations" \
  -H "Authorization: Bearer $CUST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "DISCOUNT",
    "odoo_sale_order_line_id": 1001,
    "counter_value": 22.0,
    "message": "We can sign today if discount is increased to 22% on laptops."
  }'
```
**Response (200 OK)**:
```json
{
  "data": {
    "id": "neg-555",
    "type": "DISCOUNT",
    "status": "SUBMITTED",
    "counter_value": "22.00",
    "message": "We can sign today if discount is increased to 22% on laptops."
  }
}
```

---

### Step 4: Sales Rep Accepts 22% Counter-Offer
```bash
curl -s -X POST "$API_URL/deals/$DEAL_ID/negotiations/neg-555/respond" \
  -H "Authorization: Bearer $REP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"decision": "ACCEPTED", "message": "Agreed at 22%."}'
```
**Response (200 OK)**:
```json
{
  "data": {
    "id": "neg-555",
    "status": "ACCEPTED",
    "response_message": "Agreed at 22%."
  }
}
```

---

### Step 5: The Killer Governance Moment — Automatic Invalidation
When the rep accepted 22%, the discount rose beyond the approved 18%.
Let's inspect the workspace:

```bash
curl -s -X GET "$API_URL/deals/$DEAL_ID/workspace" \
  -H "Authorization: Bearer $REP_TOKEN"
```
**Response (200 OK)**:
```json
{
  "data": {
    "deal": {
      "id": "c1f8a84b-0123-4567-89ab-cdef01234567",
      "reference": "D-1001",
      "status": "SENT"
    },
    "risk": {
      "score": "72.00",
      "severity": "CRITICAL"
    },
    "approval": {
      "state": "PENDING_MANAGER",
      "coverage_valid": false,
      "message": "Approval invalidated: Discount increased from 18.0% to 22.0%."
    },
    "next_best_action": {
      "action_type": "REAPPROVAL_REQUIRED",
      "title": "Re-approval Required Following Counter-Offer Acceptance",
      "priority": 1,
      "deep_link": "/deals/c1f8a84b-0123-4567-89ab-cdef01234567"
    }
  }
}
```
*The quotation is automatically re-locked in Odoo (`dealflow_locked=True`).*

---

### Step 6: Customer Confirms While Deal is Pending Re-Approval
Customer clicks "Confirm Deal" in the portal while the deal is waiting for Manager + Finance re-approval:

```bash
curl -s -X POST "$API_URL/portal/deals/$DEAL_ID/confirm" \
  -H "Authorization: Bearer $CUST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"accepted": true, "signature_name": "Alice Acme"}'
```
**Response (200 OK)**:
```json
{
  "data": {
    "reference": "D-1001",
    "status": "UNDER_REVIEW",
    "customer_confirmed_pending": true
  }
}
```
*The order is NOT yet confirmed in Odoo because governance has not re-approved.*

---

### Step 7: Manager & Finance Re-Approve -> Auto-Confirm in Odoo!
```bash
# Manager re-approves
curl -s -X POST "$API_URL/deals/$DEAL_ID/approval/approve" \
  -H "Authorization: Bearer $MGR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Manager re-approved 22% counter"}'

# Finance re-approves
curl -s -X POST "$API_URL/deals/$DEAL_ID/approval/approve" \
  -H "Authorization: Bearer $FIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Finance re-approved 22% counter"}'
```
**Response (200 OK)**:
```json
{
  "data": {
    "id": "c1f8a84b-0123-4567-89ab-cdef01234567",
    "approval_state": "APPROVED"
  }
}
```
*Because `customer_confirmed_pending=True`, the deal immediately auto-confirms in Odoo (`state='sale'`, `deal.status='CONFIRMED'`)!*

---

### Step 8: Billing Segregation & Payment Registration Proxy
```bash
# 8.1 Query billing summary
curl -s -X GET "$API_URL/deals/$DEAL_ID/billing" \
  -H "Authorization: Bearer $REP_TOKEN"
```
**Response (200 OK)**:
```json
{
  "data": {
    "one_time_lines": [1001, 1002],
    "recurring_lines": [],
    "invoices": [
      {
        "id": 1011,
        "name": "INV/2026/00101",
        "amount_total": 12637.80,
        "state": "posted",
        "payment_state": "not_paid"
      }
    ],
    "payments": [],
    "subscriptions": [],
    "schedule": []
  }
}
```

```bash
# 8.2 Register payment via proxy
curl -s -X POST "$API_URL/deals/$DEAL_ID/billing/invoices/1011/payments" \
  -H "Authorization: Bearer $FIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"amount": 12637.80}'
```
**Response (200 OK)**:
```json
{
  "data": {
    "success": true,
    "invoice_id": 1011,
    "payment_id": 401,
    "payment_state": "paid"
  }
}
```

---

### Step 9: Control Tower & Multi-Format Reporting (JSON / XLSX / PDF)

```bash
# 9.1 Query Control Tower
curl -s -X GET "$API_URL/dashboard/control-tower" \
  -H "Authorization: Bearer $MGR_TOKEN"
```
**Response (200 OK)**:
```json
{
  "data": {
    "kpis": {
      "pipeline_value": "184500.00",
      "deals_at_risk": 2,
      "stalled_count": 1,
      "unapproved_count": 0
    },
    "action_queue": [
      {
        "deal_id": "c1f8a84b-0123-4567-89ab-cdef01234567",
        "action_type": "REGULAR_CADENCE_FOLLOWUP",
        "title": "Healthy Deal Cadence",
        "priority": 13,
        "deep_link": "/deals/c1f8a84b-0123-4567-89ab-cdef01234567"
      }
    ]
  }
}
```

```bash
# 9.2 Download Excel Executive Summary Report
curl -s -X GET "$API_URL/reports/summary?format=xlsx" \
  -H "Authorization: Bearer $MGR_TOKEN" \
  -o executive_summary.xlsx

# 9.3 Download PDF Executive Summary Report
curl -s -X GET "$API_URL/reports/summary?format=pdf" \
  -H "Authorization: Bearer $MGR_TOKEN" \
  -o executive_summary.pdf
```
