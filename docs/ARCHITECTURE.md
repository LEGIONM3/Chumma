# DealFlow360 — Technical Architecture Specification

> **Core Directive:** *"Odoo owns the transaction. DealFlow governs the transaction."*

---

## 1. System Philosophy & Doctrine

DealFlow360 is an enterprise-grade sales operations and commercial governance overlay designed specifically for **Odoo 18 / 17**. It operates as an autonomous, high-integrity governance engine that enforces commercial policies, mitigates rogue discounting, optimizes multi-warehouse inventory allocations, and orchestrates negotiations—without displacing Odoo as the central transactional ledger of record.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        DEALFLOW360 PLATFORM                            │
│                       "GOVERN THE TRANSACTION"                         │
│  - Real-Time Margin & Risk Scoring (Deal Guardian)                     │
│  - 2-Stage Hierarchical Approval Engine (Manager + Finance)            │
│  - Automated Materiality & Approval Coverage Invalidation              │
│  - Greedy Multi-Warehouse Inventory Splitting (Stockable goods only)   │
│  - Zero-Leak Customer Negotiation Portal (Magic-Link + HMAC token)     │
│  - Health Decay Detection & Next-Best-Action Decision Rules (1-13)     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                         OdooGateway Interface
                     (Fake InMemory / XML-RPC Live)
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                              ODOO ERP                                  │
│                        "OWN THE TRANSACTION"                           │
│  - Partner & Customer Master Catalog (`res.partner`)                   │
│  - Product Catalog, Costs & Pricelists (`product.product`)             │
│  - Quotations & Sales Orders (`sale.order`, `sale.order.line`)         │
│  - Physical Warehouses, Stock Quants & Pickings (`stock.picking`)      │
│  - Invoicing, Accounting Moves & Payment Ledgers (`account.move`)      │
└────────────────────────────────────────────────────────────────────────┘
```

### Architectural Division of Responsibilities
1. **Odoo (Transactional Core)**:
   - Holds legal customer records (`res.partner`), standard cost and list prices (`product.product`), stock inventory levels (`stock.quant`), physical pickings (`stock.picking`), and GL entries (`account.move`).
   - Does not contain complex commercial risk algorithms or recursive policy resolution trees.
   - Enforces a boolean lock flag (`dealflow_locked`) and records the current approval state (`dealflow_approval_state`) and risk score (`dealflow_risk_score`).
2. **DealFlow360 (Governance Core)**:
   - Operates a dedicated relational database (`PostgreSQL`) storing deal workspaces, line-level discount overrides, approval chains, negotiation threads, fulfillment plans, health alerts, and immutable audit logs.
   - References Odoo entities strictly by integer ID (`odoo_partner_id`, `odoo_sale_order_id`, `odoo_product_id`, `odoo_warehouse_id`). Never uses cross-database foreign keys.
   - Caches immutable master values (`partner_name_cache`, `amount_total_cache`) to serve portal and control tower reads with sub-millisecond latencies.

---

## 2. Component Architecture

```
                               ┌───────────────────────────┐
                               │   Customer Portal / Web   │
                               │  (Vue / React / Mobile)   │
                               └─────────────┬─────────────┘
                                             │ HTTP / JSON
                                             ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ FastAPI Application Boundary (`app/`)                                                   │
│                                                                                         │
│  ┌──────────────────────┐  ┌──────────────────────┐  ┌───────────────────────────────┐  │
│  │   Auth & RBAC        │  │  Portal Controller   │  │   Commercial Operations       │  │
│  │  - JWT Bearer        │  │  - Magic-Link Auth   │  │  - Deals & Workspaces         │  │
│  │  - Role Guard (5)    │  │  - Strict Whitelist  │  │  - Approvals & Decisions      │  │
│  │  - Self-Approval Blk │  │  - IDOR Defense      │  │  - Multi-Warehouse Split     │  │
│  └──────────────────────┘  └──────────────────────┘  └───────────────────────────────┘  │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │ Guardian Decision Core (`app/guardian/`)                                          │  │
│  │  - Risk Scoring Engine (Margin, Customer Tier, Category Caps, Warehouse Split)    │  │
│  │  - Approval Coverage Engine (Exact match, tolerance, delta invalidation)          │  │
│  │  - Next-Best-Action Rule Hierarchy (13 strict sequential deterministic rules)     │  │
│  │  - Co-Purchase Product Recommendation Mining (Support, Confidence, Promo Boost)  │  │
│  └───────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                         │
│  ┌───────────────────────────────────────────────────────────────────────────────────┐  │
│  │ Integration Boundary (`app/odoo/`)                                                 │  │
│  │  - OdooGateway (Abstract Base Class)                                              │  │
│  │  - FakeOdooGateway (High-fidelity test harness with isolated memory stores)       │  │
│  │  - XmlRpcOdooGateway (Odoo 18 / 17 XML-RPC protocol implementation)               │  │
│  │  - Outbox Pattern (Transactional delivery with exponential backoff & idempotency) │  │
│  └───────────────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────┬────────────────────────────────────────────┘
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       ▼                                           ▼
            ┌─────────────────────┐                     ┌─────────────────────┐
            │  DealFlow Postgres  │                     │   Odoo 18 Server    │
            │  (Governance State) │                     │ (Transactional ERP) │
            └─────────────────────┘                     └─────────────────────┘
```

---

## 3. Entity-Relationship Diagram (ERD)

The DealFlow relational schema isolates commercial decision states, approvals, and audit trails:

```mermaid
erDiagram
    DEALS ||--o{ DEAL_LINES : contains
    DEALS ||--o{ APPROVAL_REQUESTS : governs
    APPROVAL_REQUESTS ||--o{ APPROVAL_ACTIONS : records
    DEALS ||--o{ NEGOTIATION_REQUESTS : negotiates
    NEGOTIATION_REQUESTS ||--o{ NEGOTIATION_COMMENTS : comments
    DEALS ||--o{ FULFILLMENT_PLANS : plans
    FULFILLMENT_PLANS ||--o{ FULFILLMENT_PLAN_LINES : allocates
    DEALS ||--o{ RECOMMENDATIONS : recommends
    DEALS ||--o{ DEAL_HEALTH_SNAPSHOTS : evaluates
    DEALS ||--o{ DEAL_ALERTS : triggers
    DEALS ||--o{ AUDIT_EVENTS : audits

    DEALS {
        uuid id PK
        string reference "Unique D-xxxx"
        bigint odoo_sale_order_id "Unique ref to Odoo"
        string odoo_order_name "Cached (e.g. S00012)"
        int odoo_partner_id "Ref to res.partner"
        string partner_name_cache
        string tier_code "ENTERPRISE, MID_MARKET, SMB"
        int owner_odoo_user_id
        string status "DealStatus enum"
        string approval_state "ApprovalState enum"
        string health_status "HEALTHY, AT_RISK, CRITICAL"
        numeric current_risk_score "0.00 to 100.00"
        uuid current_assessment_id FK
        uuid approved_assessment_id FK
        numeric order_discount_pct "Virtual header discount"
        numeric amount_total_cache
        numeric amount_untaxed_cache
        numeric margin_pct_cache
        bool customer_confirmed_pending
        timestamp sent_at
        timestamp confirmed_at
        timestamp last_activity_at
        timestamp created_at
    }

    DEAL_LINES {
        uuid id PK
        uuid deal_id FK
        bigint odoo_sale_order_line_id
        int odoo_product_id
        string product_name
        numeric qty
        numeric price_unit
        numeric discount_pct
        numeric cost_price
        numeric margin_pct
        bool is_recurring
    }

    APPROVAL_REQUESTS {
        uuid id PK
        uuid deal_id FK
        uuid risk_assessment_id
        string required_level "SALES_MANAGER, FINANCE"
        int sequence "1 or 2"
        string status "PENDING, APPROVED, REJECTED, RETURNED, INVALIDATED"
        int decided_by_odoo_user_id
        string decision_reason
        timestamp requested_at
        timestamp completed_at
    }

    APPROVAL_ACTIONS {
        uuid id PK
        uuid approval_request_id FK
        int actor_odoo_user_id
        string actor_role
        string action "APPROVE, REJECT, RETURN, ESCALATE"
        string reason
        timestamp created_at
    }

    NEGOTIATION_REQUESTS {
        uuid id PK
        uuid deal_id FK
        string type "DISCOUNT, PAYMENT_TERMS, DELIVERY_DATE, SCOPE_CHANGE, COMMENT"
        string status "SUBMITTED, UNDER_REVIEW, ACCEPTED, REJECTED, COUNTERED"
        numeric counter_value
        string message
        string response_message
        timestamp created_at
        timestamp processed_at
    }

    FULFILLMENT_PLANS {
        uuid id PK
        uuid deal_id FK
        bigint odoo_sale_order_id
        string status "PROPOSED, ACCEPTED, OVERRIDDEN, APPLIED, SUPERSEDED"
        int estimated_shipments
        numeric estimated_shipping_cost
        string strategy "CHEAPEST, FASTEST, CONSOLIDATED, MANUAL"
        timestamp generated_at
        timestamp accepted_at
    }

    FULFILLMENT_PLAN_LINES {
        uuid id PK
        uuid fulfillment_plan_id FK
        bigint odoo_sale_order_line_id
        int odoo_product_id
        int odoo_warehouse_id "Nullable for backorders"
        numeric requested_qty
        numeric allocated_qty
        numeric backorder_qty
        numeric shipping_cost
    }

    AUDIT_EVENTS {
        uuid id PK
        uuid deal_id FK
        string event_type "AuditEventType"
        string entity_type
        string entity_id
        string actor_type "USER, CUSTOMER, SYSTEM"
        int actor_id
        string reason
        json metadata
        timestamp created_at
    }
```

---

## 4. Approval State Machine & Invalidation Engine

DealFlow360 enforces a deterministic, two-stage hierarchical approval state machine:

```mermaid
stateDiagram-v2
    [*] --> DRAFT : Create / Edit Quotation
    DRAFT --> EVALUATED_NO_APPROVAL : Risk < 30 (Rep Authority)
    DRAFT --> PENDING_MANAGER : Risk >= 30 or Tier Violation

    PENDING_MANAGER --> REJECTED : Manager Rejects (Quote Locked)
    PENDING_MANAGER --> RETURNED : Manager Returns for Revision (Unlocked)
    PENDING_MANAGER --> APPROVED : Manager Approves (If Level == MANAGER)
    PENDING_MANAGER --> PENDING_FINANCE : Manager Approves (If Level == MANAGER_AND_FINANCE)

    PENDING_FINANCE --> REJECTED : Finance Rejects (Quote Locked)
    PENDING_FINANCE --> RETURNED : Finance Returns for Revision (Unlocked)
    PENDING_FINANCE --> APPROVED : Finance Approves (Final Signoff)

    APPROVED --> INVALIDATED : Material Deal Change (Discount +2% / Line Edit)
    INVALIDATED --> PENDING_MANAGER : Auto-generate new approval chain

    APPROVED --> CONFIRMED : Customer Confirmation
    PENDING_MANAGER --> PENDING_MANAGER : Customer Confirms While Pending (customer_confirmed_pending=True)
    PENDING_FINANCE --> PENDING_FINANCE : Customer Confirms While Pending (customer_confirmed_pending=True)

    state APPROVED {
        [*] --> OrderUnlockedInOdoo
        OrderUnlockedInOdoo --> AutoConfirmIfPending : Check customer_confirmed_pending
    }
```

### Invalidation & Approval Coverage Rules
- When a deal is modified (e.g. rep increases discount, or accepts customer counter-offer):
  1. The Guardian Risk Engine recalculates the risk score and required approval level.
  2. The **Coverage Evaluator** checks the current state against `approved_assessment_id`.
  3. If current terms exceed approved boundaries (e.g. discount increase $\ge 2.0\%$, margin drop, or new unreviewed line items), the live approval is immediately **INVALIDATED**.
  4. The quotation is automatically re-locked in Odoo (`dealflow_locked=True`), and a new approval chain is initialized at `PENDING_MANAGER`.

---

## 5. Multi-Warehouse Fulfillment Splitting Engine

Physical delivery execution follows a greedy optimization algorithm that strictly differentiates stockable goods from non-deliverable items:

```mermaid
flowchart TD
    A[Trigger: Order Confirmed or Plan Requested] --> B[Fetch Sale Order Lines & Product Types]
    B --> C{Product Type?}
    C -- "Service / Consumable" --> D[Ignore: No Delivery Needed]
    C -- "Recurring Subscription" --> E[Route to Subscription Billing Engine]
    C -- "Stockable Hardware" --> F[Query Warehouse Stock Quants]

    F --> G[Sort Warehouses by Sequence & Shipping Weight]
    G --> H{Primary WH Available >= Requested?}
    H -- Yes --> I[Allocate 100% to Primary WH - 1 Shipment]
    H -- No --> J[Allocate available stock from Primary WH]
    J --> K[Allocate remainder from Secondary WH in priority order]
    K --> L{Total Stock < Requested?}
    L -- Yes --> M[Record Backorder Qty with Null Warehouse]
    L -- No --> N[Complete Split Plan]

    I --> O[Fulfillment Plan Proposed]
    N --> O
    M --> O
    O --> P[User / Manager Accepts or Overrides]
    P --> Q[Apply Plan: Atomically generate Odoo stock.picking per warehouse]
```

### Invariants Enforced
- **Line Quantity Conservation**: For every order line, $\sum(\text{allocated\_qty}) + \text{backorder\_qty} = \text{requested\_qty}$.
- **Zero Overselling**: $\text{allocated\_qty} \le \text{available\_qty}$ for each individual warehouse.
- **Service Isolation**: Intangible services and recurring licenses are strictly excluded from inventory reservation.

---

## 6. Zero-Leak Customer Portal Security Architecture

The DealFlow Customer Portal provides interactive counter-negotiation capabilities while guaranteeing zero leakage of proprietary internal commercial data:

```mermaid
sequenceDiagram
    autonumber
    actor Customer
    participant Portal as Customer Portal
    participant API as DealFlow API Gateway
    participant Guard as Privacy Whitelist Filter
    participant DB as DealFlow Postgres
    participant Odoo as Odoo Transactional Core

    Customer->>API: POST /portal/auth/magic-link {email}
    API->>Odoo: Lookup res.partner by email
    API->>API: Generate HMAC-SHA256 Token (240 min TTL)
    API-->>Customer: Magic Link via Outbox / Email

    Customer->>API: GET /portal/deals/{id} (Bearer Token)
    API->>API: Verify Token & Check partner_id matches deal.odoo_partner_id
    alt IDOR Attempt (Cross-Tenant)
        API-->>Customer: 404 Not Found (Obscures deal existence)
    else Authorized Tenant
        API->>DB: Fetch Deal, Public Comments & Negotiations
        API->>Guard: Enforce STRICT WHITELIST FILTER
        Note over Guard: Strips cost_price, margin, risk_score,<br/>internal comments & approval history
        Guard-->>Customer: 200 OK (Sanitized PortalDealRead)
    end

    Customer->>API: POST /portal/deals/{id}/negotiations (Counter-Offer 22%)
    API->>DB: Create NegotiationRequest (SUBMITTED)
    API->>DB: Record Audit Event & Notify Sales Rep
    API-->>Customer: 200 OK (Negotiation Recorded)
```

### Portal Data Privacy Defense Matrix
| Field Name | Rep / Manager Workspace | Customer Portal (`PortalDealRead`) |
| :--- | :---: | :---: |
| `cost_price` | ✅ Included | 🚫 **Strip & Forbid** |
| `margin_pct` | ✅ Included | 🚫 **Strip & Forbid** |
| `margin_amount` | ✅ Included | 🚫 **Strip & Forbid** |
| `current_risk_score` | ✅ Included | 🚫 **Strip & Forbid** |
| `approval_state` | ✅ Included | 🚫 **Strip & Forbid** |
| Internal Notes / Chatter | ✅ Included | 🚫 **Strip & Forbid** |
| List Price & Discount | ✅ Included | ✅ Included |
| Subtotal & Tax | ✅ Included | ✅ Included |
| Public Comments | ✅ Included | ✅ Included |
| Delivery Dates | ✅ Included | ✅ Included |

---

## 7. Next-Best-Action (NBA) Engine Architecture

The DealFlow Control Tower executes a deterministic 13-rule priority cascade to present sales reps and managers with their single most urgent required action:

```
Priority 1:  REAPPROVAL_REQUIRED           (Invalidated approval chain)
Priority 2:  FINANCE_APPROVAL_REQUIRED      (Stage 2 pending finance)
Priority 3:  MANAGER_APPROVAL_REQUIRED      (Stage 1 pending manager)
Priority 4:  CUSTOMER_COUNTER_OFFER_PENDING (Customer submitted counter-offer)
Priority 5:  QUOTATION_EXPIRED              (Quotation validity expired)
Priority 6:  DELIVERY_SLIPPAGE_RISK         (Warehouse shortage / delay)
Priority 7:  PROPOSED_FULFILLMENT_AVAILABLE (Fulfillment plan needs acceptance)
Priority 8:  PAYMENT_OVERDUE                (Invoice past payment term)
Priority 9:  STALLED_DEAL_DETECTED          (Inactivity threshold breached)
Priority 10: DISCOUNT_ANOMALY_DETECTED      (Discount deviates from baseline)
Priority 11: RECOMMENDATIONS_AVAILABLE      (High-confidence co-purchase item)
Priority 12: READY_TO_SEND                  (Quotation approved, not sent)
Priority 13: REGULAR_CADENCE_FOLLOWUP       (Healthy deal cadence)
```

Each action item includes an actionable title, human-readable rationale, and a deep link (`/deals/{deal_id}`) directing the user directly to the relevant workspace view.

---

## 8. Webhook & Event Bus Flow

Outbound and inbound events are orchestrated through an idempotent, transaction-safe event bus:

1. **Inbound Odoo Webhooks (`POST /api/v1/events/odoo`)**:
   - Webhook requests verify HMAC-SHA256 signatures via `X-Odoo-Signature` or Bearer tokens against `ODOO_WEBHOOK_SECRET`.
   - The handler checks `ProcessedEvent` table by `event_id`. Duplicate events return HTTP 200 `{"status": "duplicate"}` immediately without duplicate reprocessing.
   - Synchronizes order status (`sale_order.cancelled`, `sale_order.updated`) and records `AuditEvent` records with full traceability.
2. **Outbound Notification Dispatch**:
   - In-memory event dispatcher broadcasts notifications with SSRF URL validation, blocking loopback addresses, RFC-1918 private ranges, and cloud metadata endpoints (`169.254.169.254`).
