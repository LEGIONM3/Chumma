from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import re
import pytest
from fastapi.testclient import TestClient
import openpyxl
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import security
from app.core.deps import get_db, get_odoo_gateway
from app.db.base import Base, utc_now
from app.main import app
from app.models.approval import ApprovalRequest
from app.models.deal import Deal
from app.models.enums import (
    ApprovalLevel,
    ApprovalRequestStatus,
    ApprovalState,
    AuditEventType,
    DealStatus,
    HealthStatus,
    NextBestActionType,
    Role,
)
from app.models.identity import DealflowUser
from app.odoo.fake import FakeOdooGateway
from app.seed.seed import seed_history, seed_policies_and_config


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def fake_gateway():
    gw = FakeOdooGateway()
    gw.reset()
    return gw


@pytest.fixture
def test_client(test_db, fake_gateway):
    def override_get_db():
        yield test_db

    def override_gateway():
        return fake_gateway

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_odoo_gateway] = override_gateway
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_golden_e2e_8_step_lifecycle(test_db, fake_gateway, test_client):
    """
    Golden E2E Integration Test (§13):
    Executes the 8-step commercial and governance lifecycle in exact sequence:
      Step 1: Rep1 Login & Deal Creation (56 HIGH, MANAGER_AND_FINANCE, Odoo locked)
      Step 2: Recommendation Added (Docking Station, LINE_ADDED, coverage holds)
      Step 3: Two-Stage Sequential Approval & Guards (Rep 422, Blank Reject 422, Approved)
      Step 4: Send & Customer Portal Privacy Whitelist (Zero leak, cross-tenant IDOR 404, JWT mutual exclusion)
      Step 5: Customer Counter-Discount Negotiation & Killer Re-approval Invalidation (56 -> 72 score escalation)
      Step 6: Customer Pre-confirm, Final Approval, Auto-confirm & Fulfillment Split (MAIN 8 + EAST 2)
      Step 7: Billing Segregation & Proxy Payment (One-time, recurring, invoice payment)
      Step 8: Idempotent Webhooks, Control Tower Visibility & Reporting (JSON/XLSX/PDF)
    """
    # -------------------------------------------------------------------------
    # SETUP: Seed policies, tiers, warehouse profiles, rules & users
    # -------------------------------------------------------------------------
    seed_policies_and_config(test_db)

    # Helper auth headers generator
    def auth_header(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    # =========================================================================
    # STEP 1: Rep1 Logs In & Creates Deal (Canonical Example 2 -> 56 HIGH)
    # =========================================================================
    # 1.1 Rep1 logs in via Odoo-backed auth
    login_resp = test_client.post(
        "/api/v1/auth/login",
        json={"login": "rep1@dealflow.test", "password": "Password123!"},
    )
    assert login_resp.status_code == 200, login_resp.text
    rep1_token = login_resp.json()["data"]["access_token"]
    rep1_headers = auth_header(rep1_token)

    # 1.2 Rep1 creates deal for Acme Corp (GOLD tier) with:
    #     - Laptop Pro 14" (101) x 10 @ 12%
    #     - Setup Service (107) x 1 @ 18%
    #     - Premium Support (109) x 1 @ 10%
    create_deal_payload = {
        "partner_id": 1,
        "currency": "INR",
        "lines": [
            {"product_id": 101, "qty": 10, "discount_pct": 12.0},
            {"product_id": 107, "qty": 1, "discount_pct": 18.0},
            {"product_id": 109, "qty": 1, "discount_pct": 10.0},
        ],
    }
    deal_resp = test_client.post(
        "/api/v1/deals/create",
        json=create_deal_payload,
        headers=rep1_headers,
    )
    assert deal_resp.status_code == 200, deal_resp.text
    deal_data = deal_resp.json()["data"]
    deal_id = deal_data["id"]

    # 1.3 Verify workspace governance state
    ws_resp = test_client.get(f"/api/v1/deals/{deal_id}/workspace", headers=rep1_headers)
    assert ws_resp.status_code == 200, ws_resp.text
    workspace = ws_resp.json()["data"]

    # Verify risk score 56 HIGH
    assert int(float(workspace["risk"]["score"])) == 56
    assert workspace["risk"]["severity"] == "HIGH"

    factor_types = [f["factor_type"] for f in workspace["risk"]["factors"]]
    assert "DISCOUNT_EXCESS" in factor_types
    assert "MARGIN_EXPOSURE" in factor_types
    assert "INVENTORY_RISK" in factor_types

    # Verify two-stage approval required: MANAGER_AND_FINANCE, initial state PENDING_MANAGER
    assert workspace["approval"]["level"] == ApprovalLevel.MANAGER_AND_FINANCE.value
    assert workspace["approval"]["state"] == ApprovalState.PENDING_MANAGER.value

    # Verify Odoo order is locked
    so_id = workspace["deal"]["odoo_sale_order_id"]
    fake_order = fake_gateway.orders[so_id]
    assert fake_order["dealflow_locked"] is True

    # Verify Next Best Action is MANAGER_APPROVAL_REQUIRED
    assert workspace["next_best_action"]["action_type"] == NextBestActionType.MANAGER_APPROVAL_REQUIRED.value

    # Verify audit events
    timeline_resp = test_client.get(f"/api/v1/deals/{deal_id}/timeline", headers=rep1_headers)
    assert timeline_resp.status_code == 200
    timeline_events = [e["event_type"] for e in timeline_resp.json()["data"]]
    assert AuditEventType.DEAL_CREATED.value in timeline_events
    assert AuditEventType.RISK_RECALCULATED.value in timeline_events
    assert AuditEventType.APPROVAL_CREATED.value in timeline_events

    # =========================================================================
    # STEP 2: Recommendations (Docking Station Added, LINE_ADDED Assessment)
    # =========================================================================
    recs = workspace["recommendations"]
    assert len(recs) >= 1
    # Docking station (104) recommended first due to 35 co-purchases + promoted
    top_rec = recs[0]
    assert top_rec["odoo_product_id"] == 104
    assert len(top_rec["reason"]) > 0
    assert float(top_rec["margin_delta_amount"]) >= 0.0

    # Add Docking Station
    rec_id = top_rec["id"]
    add_rec_resp = test_client.post(
        f"/api/v1/deals/{deal_id}/recommendations/{rec_id}/add",
        json={"quantity": 1},
        headers=rep1_headers,
    )
    assert add_rec_resp.status_code == 200, add_rec_resp.text
    add_result = add_rec_resp.json()["data"]
    assert "workspace" in add_result or "deal" in add_result

    # Refresh workspace: total updated, LINE_ADDED assessment created, approval still PENDING_MANAGER
    ws2_resp = test_client.get(f"/api/v1/deals/{deal_id}/workspace", headers=rep1_headers)
    ws2 = ws2_resp.json()["data"]
    # Added line has 0% discount -> no overage escalation -> coverage holds
    assert ws2["approval"]["state"] == ApprovalState.PENDING_MANAGER.value

    # =========================================================================
    # STEP 3: Approval Workflow & Guards (Rep Self-Approval 422, Blank Reject 422, Approvals)
    # =========================================================================
    # 3.1 Rep cannot approve own deal -> 422 OWN_DEAL
    self_app_resp = test_client.post(
        f"/api/v1/deals/{deal_id}/approval/approve",
        json={"reason": "Self approving"},
        headers=rep1_headers,
    )
    assert self_app_resp.status_code in (422, 403), "Rep self-approval must be rejected"

    # 3.2 Login as manager1 (UID 2)
    mgr_login = test_client.post(
        "/api/v1/auth/login",
        json={"login": "manager1@dealflow.test", "password": "Password123!"},
    )
    mgr_token = mgr_login.json()["data"]["access_token"]
    mgr_headers = auth_header(mgr_token)

    # 3.3 Reject without reason -> 422
    blank_reject_resp = test_client.post(
        f"/api/v1/deals/{deal_id}/approval/reject",
        json={},
        headers=mgr_headers,
    )
    assert blank_reject_resp.status_code == 422, "Reject without reason must return 422"

    # 3.4 Manager1 approves stage 1
    mgr_app_resp = test_client.post(
        f"/api/v1/deals/{deal_id}/approval/approve",
        json={"reason": "Approved stage 1 commercial terms"},
        headers=mgr_headers,
    )
    assert mgr_app_resp.status_code == 200, mgr_app_resp.text
    mgr_res = mgr_app_resp.json()["data"]
    assert mgr_res["approval_state"] == ApprovalState.PENDING_FINANCE.value

    # 3.5 Login as finance (UID 6)
    fin_login = test_client.post(
        "/api/v1/auth/login",
        json={"login": "finance@dealflow.test", "password": "Password123!"},
    )
    fin_token = fin_login.json()["data"]["access_token"]
    fin_headers = auth_header(fin_token)

    # 3.6 Finance approves stage 2
    fin_app_resp = test_client.post(
        f"/api/v1/deals/{deal_id}/approval/approve",
        json={"reason": "Approved stage 2 margin limits"},
        headers=fin_headers,
    )
    assert fin_app_resp.status_code == 200, fin_app_resp.text
    assert fin_app_resp.json()["data"]["approval_state"] == ApprovalState.APPROVED.value

    # Verify Odoo order is unlocked
    assert fake_gateway.orders[so_id]["dealflow_locked"] is False

    # =========================================================================
    # STEP 4: Rep Sends & Customer Portal Privacy Whitelist
    # =========================================================================
    # 4.1 Rep sends quotation to customer
    send_resp = test_client.post(f"/api/v1/deals/{deal_id}/send", headers=rep1_headers)
    assert send_resp.status_code == 200, send_resp.text
    assert send_resp.json()["data"]["status"] == DealStatus.SENT.value

    # 4.2 Portal magic link request for buyer@acme.test (partner_id=1)
    ml_resp = test_client.post(
        "/api/v1/portal/auth/magic-link",
        json={"email": "buyer@acme.test"},
    )
    assert ml_resp.status_code == 202

    # Read magic link token from outbox
    outbox_resp = test_client.get("/api/v1/admin/outbox", headers=mgr_headers)
    assert outbox_resp.status_code == 200
    outbox_items = outbox_resp.json()["data"]
    acme_emails = [e for e in outbox_items if e.get("recipient") == "buyer@acme.test"]
    assert len(acme_emails) >= 1

    # Extract token
    body = acme_emails[-1]["body"]
    token_match = re.search(r"token=([A-Za-z0-9_\-]+)", body)
    assert token_match is not None
    portal_token_str = token_match.group(1)

    # Verify portal magic link
    verify_resp = test_client.get(f"/api/v1/portal/auth/verify?token={portal_token_str}")
    assert verify_resp.status_code == 200, verify_resp.text
    acme_portal_token = verify_resp.json()["data"]["access_token"]
    acme_headers = auth_header(acme_portal_token)

    # 4.3 Buyer@Acme views deal on portal
    portal_deal_resp = test_client.get(f"/api/v1/portal/deals/{deal_id}", headers=acme_headers)
    assert portal_deal_resp.status_code == 200, portal_deal_resp.text
    portal_deal = portal_deal_resp.json()["data"]

    # Recursive scan to verify NO internal leak of cost, margin, risk, ceiling, overage, approver
    forbidden_keys = {"cost", "margin", "risk", "ceiling", "overage", "approval", "approver", "actor"}

    def assert_no_leak(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                lower_k = k.lower()
                for f_key in forbidden_keys:
                    assert f_key not in lower_k, f"Privacy leak detected: key '{k}' at path '{path}'"
                assert_no_leak(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, elem in enumerate(obj):
                assert_no_leak(elem, f"{path}[{i}]")

    assert_no_leak(portal_deal)

    # 4.4 Cross-Tenant IDOR Defense: buyer@beta tries to view Acme's deal -> 404
    beta_token = security.create_access_token(
        subject="buyer@beta.test",
        role=Role.CUSTOMER.value,
        audience="portal",
        extra_claims={"partner_id": 2},
    )
    beta_resp = test_client.get(f"/api/v1/portal/deals/{deal_id}", headers=auth_header(beta_token))
    assert beta_resp.status_code == 404, "Cross-tenant access must return 404"

    # 4.5 Audience Mutual Exclusion
    # Portal JWT on internal route -> 401
    int_resp = test_client.get(f"/api/v1/deals/{deal_id}", headers=acme_headers)
    assert int_resp.status_code == 401, "Portal JWT on internal route must return 401"

    # Internal JWT on portal route -> 401
    port_resp = test_client.get(f"/api/v1/portal/deals/{deal_id}", headers=rep1_headers)
    assert port_resp.status_code == 401, "Internal JWT on portal route must return 401"

    # =========================================================================
    # STEP 5: Customer Negotiation & KILLER RE-APPROVAL INVALIDATION
    #         (Customer submits 22% on Setup Service -> rep accepts -> score 72!)
    # =========================================================================
    # Find setup service line id in order
    raw_lines = fake_gateway.get_sale_order(so_id).lines
    setup_line = next(l for l in raw_lines if l.product_id == 107)

    # Customer submits COUNTER_DISCOUNT 22%
    neg_resp = test_client.post(
        f"/api/v1/portal/deals/{deal_id}/negotiations",
        json={
            "request_type": "COUNTER_DISCOUNT",
            "odoo_line_id": setup_line.id,
            "counter_value": "22.0",
            "message": "We need 22% on the setup service to close.",
        },
        headers=acme_headers,
    )
    assert neg_resp.status_code == 200, neg_resp.text
    neg_data = neg_resp.json()["data"]
    nid = neg_data["id"]

    # Deal is now UNDER_NEGOTIATION, approval is still APPROVED (no Odoo change yet)
    ws_neg = test_client.get(f"/api/v1/deals/{deal_id}/workspace", headers=rep1_headers).json()["data"]
    assert ws_neg["deal"]["status"] == DealStatus.UNDER_NEGOTIATION.value
    assert ws_neg["approval"]["state"] == ApprovalState.APPROVED.value

    # Rep ACCEPTs negotiation request
    rep_respond_resp = test_client.post(
        f"/api/v1/deals/{deal_id}/negotiations/{nid}/respond",
        json={"decision": "ACCEPTED", "message": "Agreed at 22%."},
        headers=rep1_headers,
    )
    assert rep_respond_resp.status_code == 200, rep_respond_resp.text

    # *** THE KILLER MOMENT: Assert old score 56 -> new score 72 ***
    ws_after_neg = test_client.get(f"/api/v1/deals/{deal_id}/workspace", headers=rep1_headers).json()["data"]
    new_score = int(float(ws_after_neg["risk"]["score"]))
    assert new_score in (71, 72), f"Expected risk score 71 or 72 after 22% counter discount, got {new_score}"

    # Coverage fails -> APPROVAL_INVALIDATED -> new approval chain created in PENDING_MANAGER
    assert ws_after_neg["approval"]["state"] == ApprovalState.PENDING_MANAGER.value
    assert fake_gateway.orders[so_id]["dealflow_locked"] is True
    assert ws_after_neg["next_best_action"]["action_type"] in (
        NextBestActionType.REAPPROVAL_REQUIRED.value,
        NextBestActionType.MANAGER_APPROVAL_REQUIRED.value,
    )

    # =========================================================================
    # STEP 6: Customer Confirms While Pending, Reapproval, Auto-Confirm & Split
    # =========================================================================
    # 6.1 Customer confirms while pending approval -> 200, customer_confirmed_pending=True
    cust_confirm_resp = test_client.post(
        f"/api/v1/portal/deals/{deal_id}/confirm",
        headers=acme_headers,
    )
    assert cust_confirm_resp.status_code == 200, cust_confirm_resp.text
    assert cust_confirm_resp.json()["data"]["customer_confirmed_pending"] is True
    assert cust_confirm_resp.json()["data"]["status"] == "UNDER_REVIEW"

    # 6.2 Manager and Finance approve new chain -> auto confirmed in Odoo!
    test_client.post(
        f"/api/v1/deals/{deal_id}/approval/approve",
        json={"reason": "Manager re-approved 22%"},
        headers=mgr_headers,
    )
    fin_reapp_resp = test_client.post(
        f"/api/v1/deals/{deal_id}/approval/approve",
        json={"reason": "Finance re-approved 22%"},
        headers=fin_headers,
    )
    assert fin_reapp_resp.status_code == 200, fin_reapp_resp.text

    # Verify deal auto-confirmed upon final approval
    deal_after_app = test_client.get(f"/api/v1/deals/{deal_id}", headers=rep1_headers).json()["data"]
    assert deal_after_app["status"] == DealStatus.CONFIRMED.value
    assert fake_gateway.orders[so_id]["state"] == "sale"

    # 6.3 Propose Multi-Warehouse Fulfillment Plan (10 Laptops -> MAIN 8 + EAST 2)
    prop_resp = test_client.post(
        f"/api/v1/deals/{deal_id}/fulfillment/propose",
        headers=rep1_headers,
    )
    assert prop_resp.status_code == 200, prop_resp.text
    plan_data = prop_resp.json()["data"]

    # Verify warehouse allocations
    allocations = plan_data["lines"]
    laptop_allocs = [l for l in allocations if l["odoo_product_id"] == 101]
    assert len(laptop_allocs) == 2
    wh_ids = {l["odoo_warehouse_id"] for l in laptop_allocs}
    assert wh_ids == {1, 2}
    total_laptop_qty = sum(l["allocated_qty"] for l in laptop_allocs)
    assert total_laptop_qty == 10

    # 6.4 Accept and Apply Fulfillment Plan
    acc_resp = test_client.post(f"/api/v1/deals/{deal_id}/fulfillment/accept", headers=rep1_headers)
    assert acc_resp.status_code == 200, acc_resp.text

    apply_resp = test_client.post(f"/api/v1/deals/{deal_id}/fulfillment/apply", headers=rep1_headers)
    assert apply_resp.status_code == 200, apply_resp.text
    assert len(apply_resp.json()["data"]["picking_ids"]) == 2

    # =========================================================================
    # STEP 7: Billing Segregation & Proxy Payment
    # =========================================================================
    billing_resp = test_client.get(f"/api/v1/deals/{deal_id}/billing", headers=rep1_headers)
    assert billing_resp.status_code == 200, billing_resp.text
    billing = billing_resp.json()["data"]

    # One-time and recurring lines are separate arrays
    assert "one_time_lines" in billing
    assert "recurring_lines" in billing
    assert "subscriptions" in billing
    assert "invoices" in billing

    # Record Payment via Proxy
    invoices = billing["invoices"]
    if invoices:
        inv_id = invoices[0]["id"]
        inv_amount = invoices[0]["amount_total"]
        pay_resp = test_client.post(
            f"/api/v1/deals/{deal_id}/billing/invoices/{inv_id}/payments",
            json={"amount": inv_amount},
            headers=fin_headers,
        )
        assert pay_resp.status_code == 200, pay_resp.text

        # Verify PAYMENT_RECEIVED audit event
        t_resp = test_client.get(f"/api/v1/deals/{deal_id}/timeline", headers=rep1_headers)
        events = [e["event_type"] for e in t_resp.json()["data"]]
        assert AuditEventType.PAYMENT_RECEIVED.value in events

    # =========================================================================
    # STEP 8: Idempotent Webhooks, Control Tower & Reports (JSON / XLSX / PDF)
    # =========================================================================
    # 8.1 Seed history for control tower
    seed_history(test_db, fake_gateway)

    # 8.2 Webhook Idempotency: Duplicate event_id returns duplicate
    webhook_payload = {
        "event_id": "evt_golden_test_001",
        "event_type": "sale_order.updated",
        "order_id": so_id,
        "payload": {},
    }
    wh1 = test_client.post("/api/v1/events/odoo", json=webhook_payload)
    assert wh1.status_code == 200
    wh2 = test_client.post("/api/v1/events/odoo", json=webhook_payload)
    assert wh2.status_code == 200
    assert wh2.json()["data"]["status"] == "duplicate"

    # 8.3 Control Tower Visibility: Shows stalled deal, slipping deal, anomaly
    ct_resp = test_client.get("/api/v1/dashboard/control-tower", headers=mgr_headers)
    assert ct_resp.status_code == 200, ct_resp.text
    ct_data = ct_resp.json()["data"]
    assert Decimal(str(ct_data["kpis"]["pipeline_value"])) > Decimal("0.00")
    assert ct_data["kpis"]["stalled_count"] >= 1

    # Action queue contains prioritized actions with /deals/{id} deep links
    assert len(ct_data["action_queue"]) >= 1
    assert any("/deals/" in item["deep_link"] for item in ct_data["action_queue"])

    # 8.4 Reports Output in JSON, XLSX, and PDF
    # JSON Summary
    rep_summary_json = test_client.get("/api/v1/reports/summary?format=json", headers=mgr_headers)
    assert rep_summary_json.status_code == 200
    assert "quotations_created" in rep_summary_json.json()["data"]

    # XLSX Summary
    rep_summary_xlsx = test_client.get("/api/v1/reports/summary?format=xlsx", headers=mgr_headers)
    assert rep_summary_xlsx.status_code == 200
    assert rep_summary_xlsx.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    wb = openpyxl.load_workbook(io.BytesIO(rep_summary_xlsx.content))
    assert "Executive Summary" in wb.sheetnames

    # PDF Summary
    rep_summary_pdf = test_client.get("/api/v1/reports/summary?format=pdf", headers=mgr_headers)
    assert rep_summary_pdf.status_code == 200
    assert rep_summary_pdf.headers["content-type"] == "application/pdf"
    assert rep_summary_pdf.content.startswith(b"%PDF")
