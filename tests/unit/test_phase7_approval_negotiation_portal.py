from decimal import Decimal
import uuid
import pytest
from fastapi.testclient import TestClient
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
    ApprovalActionType,
    ApprovalLevel,
    ApprovalRequestStatus,
    ApprovalState,
    DealStatus,
    NegotiationRequestStatus,
    Role,
)
from app.models.identity import DealflowUser
from app.models.risk import RiskAssessment
from app.odoo.fake import FakeOdooGateway
from app.schemas.portal import FORBIDDEN_PORTAL_KEYS, PortalConfirmDealRequest, PortalNegotiationSubmit
from app.services import approval_service, negotiation_service, portal_service


@pytest.fixture
def in_memory_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def fake_gateway():
    return FakeOdooGateway()


@pytest.fixture
def seed_users(in_memory_db):
    rep = DealflowUser(
        odoo_user_id=2,
        login="rep@dealflow.test",
        name="Sales Rep",
        role=Role.SALES_REP.value,
        is_active=True,
    )
    manager = DealflowUser(
        odoo_user_id=3,
        login="manager@dealflow.test",
        name="Sales Manager",
        role=Role.SALES_MANAGER.value,
        is_active=True,
    )
    finance = DealflowUser(
        odoo_user_id=4,
        login="finance@dealflow.test",
        name="Finance Approver",
        role=Role.FINANCE.value,
        is_active=True,
    )
    admin = DealflowUser(
        odoo_user_id=1,
        login="admin@dealflow.test",
        name="Admin User",
        role=Role.ADMIN.value,
        is_active=True,
    )
    in_memory_db.add_all([rep, manager, finance, admin])
    in_memory_db.commit()
    return {"rep": rep, "manager": manager, "finance": finance, "admin": admin}


@pytest.fixture
def client(in_memory_db, fake_gateway):
    def override_get_db():
        yield in_memory_db

    def override_gateway():
        return fake_gateway

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_odoo_gateway] = override_gateway
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_approval_self_approval_guard(in_memory_db, fake_gateway, seed_users):
    """Test that a sales rep cannot approve their own deal."""
    rep = seed_users["rep"]
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 2}])

    deal = Deal(
        reference="D-7001",
        odoo_sale_order_id=raw_so.header.id,
        odoo_order_name=raw_so.header.name,
        odoo_partner_id=1,
        owner_odoo_user_id=rep.odoo_user_id,
        status=DealStatus.DRAFT.value,
        approval_state=ApprovalState.PENDING_MANAGER.value,
        required_level=ApprovalLevel.MANAGER.value,
        order_discount_pct=Decimal("15.00"),
        amount_total_cache=Decimal("100000.00"),
        amount_untaxed_cache=Decimal("100000.00"),
        margin_pct_cache=Decimal("20.00"),
    )
    in_memory_db.add(deal)
    in_memory_db.flush()

    assessment = RiskAssessment(
        deal_id=deal.id,
        risk_score=Decimal("45.00"),
        severity="MEDIUM",
        required_level=ApprovalLevel.MANAGER.value,
        decision="MANAGER_APPROVAL_REQUIRED",
        trigger_type="MANUAL",
        policy_version="v1",
    )
    in_memory_db.add(assessment)
    in_memory_db.flush()

    req = ApprovalRequest(
        deal_id=deal.id,
        risk_assessment_id=assessment.id,
        required_level="SALES_MANAGER",
        sequence=1,
        status=ApprovalRequestStatus.PENDING.value,
    )
    in_memory_db.add(req)
    in_memory_db.commit()

    with pytest.raises(Exception) as exc_info:
        approval_service.decide_approval_request(
            db=in_memory_db,
            gateway=fake_gateway,
            request_id=req.id,
            actor=rep,
            action=ApprovalActionType.APPROVE,
            reason="Self-approval attempt",
        )
    assert "OWN_DEAL" in str(exc_info.value) or "cannot approve" in str(exc_info.value)


def test_approval_role_authorization_guard(in_memory_db, fake_gateway, seed_users):
    """Test role authority: Manager cannot decide Finance, Finance cannot decide Manager."""
    manager = seed_users["manager"]
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 2}])

    deal = Deal(
        reference="D-7002",
        odoo_sale_order_id=raw_so.header.id,
        odoo_order_name=raw_so.header.name,
        odoo_partner_id=1,
        owner_odoo_user_id=2,  # Rep
        status=DealStatus.DRAFT.value,
        approval_state=ApprovalState.PENDING_FINANCE.value,
        required_level=ApprovalLevel.MANAGER_AND_FINANCE.value,
    )
    in_memory_db.add(deal)
    in_memory_db.flush()

    assessment = RiskAssessment(
        deal_id=deal.id,
        risk_score=Decimal("60.00"),
        severity="HIGH",
        required_level=ApprovalLevel.MANAGER_AND_FINANCE.value,
        decision="MANAGER_AND_FINANCE_APPROVAL_REQUIRED",
        trigger_type="MANUAL",
        policy_version="v1",
    )
    in_memory_db.add(assessment)
    in_memory_db.flush()

    req = ApprovalRequest(
        deal_id=deal.id,
        risk_assessment_id=assessment.id,
        required_level="FINANCE",
        sequence=2,
        status=ApprovalRequestStatus.PENDING.value,
    )
    in_memory_db.add(req)
    in_memory_db.commit()

    # Sales Manager attempts to decide Finance stage -> Should fail
    with pytest.raises(Exception) as exc_info:
        approval_service.decide_approval_request(
            db=in_memory_db,
            gateway=fake_gateway,
            request_id=req.id,
            actor=manager,
            action=ApprovalActionType.APPROVE,
            reason="Manager approving finance stage",
        )
    assert "Finance" in str(exc_info.value) or "Forbidden" in str(type(exc_info.value).__name__)


def test_two_stage_approval_sequencing(in_memory_db, fake_gateway, seed_users):
    """Test 2-stage approval: Manager approval creates Finance request; Finance approval unlocks Odoo."""
    manager = seed_users["manager"]
    finance = seed_users["finance"]
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 5, "discount_pct": 25.0}])
    so_id = raw_so.header.id

    deal = Deal(
        reference="D-7003",
        odoo_sale_order_id=so_id,
        odoo_order_name=raw_so.header.name,
        odoo_partner_id=1,
        owner_odoo_user_id=2,  # Rep
        status=DealStatus.DRAFT.value,
        approval_state=ApprovalState.PENDING_MANAGER.value,
        required_level=ApprovalLevel.MANAGER_AND_FINANCE.value,
        current_risk_score=Decimal("65.00"),
    )
    in_memory_db.add(deal)
    in_memory_db.flush()

    assessment = RiskAssessment(
        deal_id=deal.id,
        risk_score=Decimal("65.00"),
        severity="HIGH",
        required_level=ApprovalLevel.MANAGER_AND_FINANCE.value,
        decision="MANAGER_AND_FINANCE_APPROVAL_REQUIRED",
        trigger_type="MANUAL",
        policy_version="v1",
    )
    in_memory_db.add(assessment)
    in_memory_db.flush()

    stage1_req = ApprovalRequest(
        deal_id=deal.id,
        risk_assessment_id=assessment.id,
        required_level="SALES_MANAGER",
        sequence=1,
        status=ApprovalRequestStatus.PENDING.value,
    )
    in_memory_db.add(stage1_req)
    in_memory_db.commit()

    # Make sure Odoo order starts locked
    fake_gateway.set_governance(so_id, "PENDING_MANAGER", 65.0, locked=True)

    # Step 1: Sales Manager approves Stage 1
    approval_service.decide_approval_request(
        db=in_memory_db,
        gateway=fake_gateway,
        request_id=stage1_req.id,
        actor=manager,
        action=ApprovalActionType.APPROVE,
        reason="Manager approved pricing",
    )

    in_memory_db.refresh(deal)
    assert deal.approval_state == ApprovalState.PENDING_FINANCE.value

    # Verify Stage 2 request created
    stage2_req = (
        in_memory_db.query(ApprovalRequest)
        .filter(ApprovalRequest.deal_id == deal.id, ApprovalRequest.sequence == 2)
        .first()
    )
    assert stage2_req is not None
    assert stage2_req.required_level == "FINANCE"
    assert stage2_req.status == ApprovalRequestStatus.PENDING.value

    # Odoo order should STILL be locked
    order = fake_gateway.get_sale_order(so_id)
    assert order.header.dealflow_locked is True

    # Step 2: Finance approves Stage 2
    approval_service.decide_approval_request(
        db=in_memory_db,
        gateway=fake_gateway,
        request_id=stage2_req.id,
        actor=finance,
        action=ApprovalActionType.APPROVE,
        reason="Finance approved credit terms",
    )

    in_memory_db.refresh(deal)
    assert deal.approval_state == ApprovalState.APPROVED.value
    assert deal.approved_assessment_id == assessment.id

    # Odoo order must now be UNLOCKED!
    order_after = fake_gateway.get_sale_order(so_id)
    assert order_after.header.dealflow_locked is False
    assert order_after.header.dealflow_approval_state == "APPROVED"


def test_approval_rejection_and_return(in_memory_db, fake_gateway, seed_users):
    """Test approval REJECT keeps lock; RETURN unlocks quotation for rep editing."""
    manager = seed_users["manager"]

    raw_so_rej = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 1}])
    raw_so_ret = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 102, "qty": 1}])

    # Test REJECT
    deal_rej = Deal(
        reference="D-7004-REJ",
        odoo_sale_order_id=raw_so_rej.header.id,
        odoo_order_name=raw_so_rej.header.name,
        odoo_partner_id=1,
        owner_odoo_user_id=2,
        status=DealStatus.DRAFT.value,
        approval_state=ApprovalState.PENDING_MANAGER.value,
        required_level=ApprovalLevel.MANAGER.value,
        current_risk_score=Decimal("40.00"),
    )
    in_memory_db.add(deal_rej)
    in_memory_db.flush()

    req_rej = ApprovalRequest(
        deal_id=deal_rej.id,
        risk_assessment_id=uuid.uuid4(),
        required_level="SALES_MANAGER",
        sequence=1,
        status=ApprovalRequestStatus.PENDING.value,
    )
    in_memory_db.add(req_rej)
    in_memory_db.commit()

    fake_gateway.set_governance(raw_so_rej.header.id, "PENDING_MANAGER", 40.0, locked=True)

    approval_service.decide_approval_request(
        db=in_memory_db,
        gateway=fake_gateway,
        request_id=req_rej.id,
        actor=manager,
        action=ApprovalActionType.REJECT,
        reason="Discount unacceptable",
    )

    in_memory_db.refresh(deal_rej)
    assert deal_rej.approval_state == ApprovalState.REJECTED.value
    assert fake_gateway.get_sale_order(raw_so_rej.header.id).header.dealflow_locked is True

    # Test RETURN
    deal_ret = Deal(
        reference="D-7005-RET",
        odoo_sale_order_id=raw_so_ret.header.id,
        odoo_order_name=raw_so_ret.header.name,
        odoo_partner_id=1,
        owner_odoo_user_id=2,
        status=DealStatus.DRAFT.value,
        approval_state=ApprovalState.PENDING_MANAGER.value,
        required_level=ApprovalLevel.MANAGER.value,
        current_risk_score=Decimal("40.00"),
    )
    in_memory_db.add(deal_ret)
    in_memory_db.flush()

    req_ret = ApprovalRequest(
        deal_id=deal_ret.id,
        risk_assessment_id=uuid.uuid4(),
        required_level="SALES_MANAGER",
        sequence=1,
        status=ApprovalRequestStatus.PENDING.value,
    )
    in_memory_db.add(req_ret)
    in_memory_db.commit()

    fake_gateway.set_governance(raw_so_ret.header.id, "PENDING_MANAGER", 40.0, locked=True)

    approval_service.decide_approval_request(
        db=in_memory_db,
        gateway=fake_gateway,
        request_id=req_ret.id,
        actor=manager,
        action=ApprovalActionType.RETURN,
        reason="Please bundle service line to justify discount",
    )

    in_memory_db.refresh(deal_ret)
    assert deal_ret.approval_state == ApprovalState.RETURNED.value
    # RETURN unlocks quotation so sales rep can edit in Odoo!
    assert fake_gateway.get_sale_order(raw_so_ret.header.id).header.dealflow_locked is False


def test_customer_portal_whitelist_zero_leak(in_memory_db, fake_gateway):
    """Verify that customer portal payload enforces strict whitelist with zero internal data leaks."""
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 10, "discount_pct": 18.0}])

    deal = Deal(
        reference="D-7006-PORTAL",
        odoo_sale_order_id=raw_so.header.id,
        odoo_order_name=raw_so.header.name,
        odoo_partner_id=1,  # Acme Corp
        partner_name_cache="Acme Corp",
        owner_odoo_user_id=2,
        currency_code="INR",
        status=DealStatus.SENT.value,
        approval_state=ApprovalState.PENDING_MANAGER.value,
        current_risk_score=Decimal("56.00"),
        current_severity="HIGH",
        required_level=ApprovalLevel.MANAGER_AND_FINANCE.value,
        margin_pct_cache=Decimal("12.50"),
        amount_untaxed_cache=Decimal("450000.00"),
        amount_total_cache=Decimal("531000.00"),
        order_discount_pct=Decimal("18.00"),
    )
    in_memory_db.add(deal)
    in_memory_db.commit()

    # Add both public and internal comments
    negotiation_service.add_portal_comment(
        db=in_memory_db,
        deal_id=deal.id,
        author_odoo_user_id=2,
        author_role="SALES_REP",
        body="Public proposal note for Acme",
        is_internal=False,
    )
    negotiation_service.add_portal_comment(
        db=in_memory_db,
        deal_id=deal.id,
        author_odoo_user_id=2,
        author_role="SALES_REP",
        body="CONFIDENTIAL INTERNAL NOTE: Customer margin is paper thin",
        is_internal=True,
    )

    portal_view = portal_service.get_deal_for_portal(
        db=in_memory_db,
        gateway=fake_gateway,
        deal_id=deal.id,
        customer_partner_id=1,
    )

    dumped = portal_view.model_dump()

    # Check for forbidden internal keys
    for key in FORBIDDEN_PORTAL_KEYS:
        assert key not in dumped, f"CRITICAL LEAK: '{key}' found in top-level portal payload"

    for line in dumped.get("lines", []):
        assert "cost" not in line
        assert "cost_price" not in line
        assert "margin_pct" not in line
        assert "margin_amount" not in line

    # Verify only public comment is returned
    assert len(portal_view.comments) == 1
    assert portal_view.comments[0].body == "Public proposal note for Acme"
    assert "CONFIDENTIAL" not in [c.body for c in portal_view.comments]


def test_customer_deal_confirmation_flow(in_memory_db, fake_gateway, seed_users):
    """Test customer confirmation:
    1. If not yet approved -> marks customer_confirmed_pending=True, order stays unconfirmed
    2. Once final approval is given -> automatically confirms order in Odoo
    """
    manager = seed_users["manager"]
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 3, "discount_pct": 10.0}])
    so_id = raw_so.header.id

    deal = Deal(
        reference="D-7007-CONFIRM",
        odoo_sale_order_id=so_id,
        odoo_order_name=raw_so.header.name,
        odoo_partner_id=1,  # Acme Corp
        owner_odoo_user_id=2,
        status=DealStatus.SENT.value,
        approval_state=ApprovalState.PENDING_MANAGER.value,
        required_level=ApprovalLevel.MANAGER.value,
        current_risk_score=Decimal("35.00"),
        customer_confirmed_pending=False,
    )
    in_memory_db.add(deal)
    in_memory_db.flush()

    req = ApprovalRequest(
        deal_id=deal.id,
        risk_assessment_id=uuid.uuid4(),
        required_level="SALES_MANAGER",
        sequence=1,
        status=ApprovalRequestStatus.PENDING.value,
    )
    in_memory_db.add(req)
    in_memory_db.commit()

    fake_gateway.set_governance(so_id, "PENDING_MANAGER", 35.0, locked=True)

    # 1. Customer accepts quotation on portal
    confirm_req = PortalConfirmDealRequest(
        accepted=True,
        signature_name="Jane Doe, VP Procurement",
        comment="Approved on our side",
    )
    portal_service.confirm_deal_by_customer(
        db=in_memory_db,
        gateway=fake_gateway,
        deal_id=deal.id,
        customer_partner_id=1,
        payload=confirm_req,
    )

    in_memory_db.refresh(deal)
    assert deal.customer_confirmed_pending is True
    # Odoo sale order is NOT yet confirmed because internal governance approval is pending!
    assert fake_gateway.get_sale_order(so_id).header.state == "draft"

    # 2. Sales Manager approves the deal
    approval_service.decide_approval_request(
        db=in_memory_db,
        gateway=fake_gateway,
        request_id=req.id,
        actor=manager,
        action=ApprovalActionType.APPROVE,
        reason="Final pricing approved",
    )

    in_memory_db.refresh(deal)
    assert deal.approval_state == ApprovalState.APPROVED.value
    assert deal.status == DealStatus.CONFIRMED.value
    assert deal.customer_confirmed_pending is False
    # Now Odoo sale order IS confirmed!
    assert fake_gateway.get_sale_order(so_id).header.state == "sale"


def test_negotiation_workflow_lifecycle(in_memory_db, fake_gateway, seed_users):
    """Test complete negotiation workflow: customer counter-request -> rep accept -> Odoo update & re-evaluation."""
    rep = seed_users["rep"]
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 5, "discount_pct": 0.0}])
    so_id = raw_so.header.id
    line_id = raw_so.lines[0].id

    deal = Deal(
        reference="D-7008-NEG",
        odoo_sale_order_id=so_id,
        odoo_order_name=raw_so.header.name,
        odoo_partner_id=1,
        owner_odoo_user_id=rep.odoo_user_id,
        status=DealStatus.SENT.value,
        approval_state=ApprovalState.EVALUATED_NO_APPROVAL.value,
        required_level=ApprovalLevel.NONE.value,
    )
    in_memory_db.add(deal)
    in_memory_db.commit()

    # 1. Customer requests quantity increase on line
    portal_submit = PortalNegotiationSubmit(
        type="QTY_CHANGE",
        message="Can we increase to 12 units if price is maintained?",
        odoo_sale_order_line_id=line_id,
        requested_qty=Decimal("12.0"),
    )
    neg_req = negotiation_service.submit_negotiation_request(
        db=in_memory_db,
        deal_id=deal.id,
        customer_partner_id=1,
        actor_user_id=1,
        payload=portal_submit,
    )

    in_memory_db.refresh(deal)
    assert deal.status == DealStatus.UNDER_NEGOTIATION.value
    assert neg_req.status == NegotiationRequestStatus.OPEN.value

    # 2. Sales rep accepts the negotiation
    negotiation_service.respond_negotiation_request(
        db=in_memory_db,
        gateway=fake_gateway,
        request_id=neg_req.id,
        actor=rep,
        action="ACCEPT",
        response_message="Accepted! Updated to 12 units.",
    )

    in_memory_db.refresh(neg_req)
    assert neg_req.status == NegotiationRequestStatus.ACCEPTED.value

    # Verify line quantity was updated in Odoo
    order = fake_gateway.get_sale_order(so_id)
    line1 = next(l for l in order.lines if l.id == line_id)
    assert line1.qty == 12.0


def test_api_endpoints_approvals_and_portal(client, in_memory_db, fake_gateway, seed_users):
    """Test approvals and portal REST endpoints with JWT authentication."""
    manager = seed_users["manager"]
    manager_token = security.create_access_token(
        subject=str(manager.odoo_user_id),
        role=manager.role,
        company_id="1",
        audience="internal",
    )

    # Customer portal token
    customer_token = security.create_portal_token(
        subject="1",
        customer_id="1",
        company_id="1",
    )

    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 3}])
    so_id = raw_so.header.id

    deal = Deal(
        reference="D-7009-API",
        odoo_sale_order_id=so_id,
        odoo_order_name=raw_so.header.name,
        odoo_partner_id=1,
        partner_name_cache="Acme Corp",
        owner_odoo_user_id=2,  # Rep
        status=DealStatus.DRAFT.value,
        approval_state=ApprovalState.PENDING_MANAGER.value,
        required_level=ApprovalLevel.MANAGER.value,
        current_risk_score=Decimal("42.00"),
        amount_total_cache=Decimal("150000.00"),
    )
    in_memory_db.add(deal)
    in_memory_db.flush()

    req = ApprovalRequest(
        deal_id=deal.id,
        risk_assessment_id=uuid.uuid4(),
        required_level="SALES_MANAGER",
        sequence=1,
        status=ApprovalRequestStatus.PENDING.value,
    )
    in_memory_db.add(req)
    in_memory_db.commit()

    # 1. Test GET /api/v1/approvals/pending
    res = client.get(
        "/api/v1/approvals/pending",
        headers={"Authorization": f"Bearer {manager_token}"},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert len(data) >= 1
    assert any(item["approval_request_id"] == str(req.id) for item in data)

    # 2. Test POST /api/v1/approvals/{request_id}/action
    res_action = client.post(
        f"/api/v1/approvals/{req.id}/action",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"action": "APPROVE", "reason": "Approved via API"},
    )
    assert res_action.status_code == 200
    assert res_action.json()["data"]["status"] == "APPROVED"

    # 3. Test GET /api/v1/portal/deals/{deal_id}
    res_portal = client.get(
        f"/api/v1/portal/deals/{deal.id}",
        headers={"Authorization": f"Bearer {customer_token}"},
    )
    assert res_portal.status_code == 200
    portal_data = res_portal.json()["data"]
    assert portal_data["reference"] == "D-7009-API"
    assert "margin_pct" not in portal_data
    assert "current_risk_score" not in portal_data


def test_portal_cross_tenant_idor_defense(client, in_memory_db, fake_gateway):
    """Test that customer from partner 2 cannot view or modify partner 1's deal."""
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 1}])

    deal = Deal(
        reference="D-7010-IDOR",
        odoo_sale_order_id=raw_so.header.id,
        odoo_order_name=raw_so.header.name,
        odoo_partner_id=1,  # Belongs to Partner 1
        owner_odoo_user_id=2,
        status=DealStatus.SENT.value,
        approval_state=ApprovalState.APPROVED.value,
    )
    in_memory_db.add(deal)
    in_memory_db.commit()

    # Token for Partner 2 (Attacker attempting cross-tenant access)
    attacker_token = security.create_portal_token(
        subject="2",
        customer_id="2",
        company_id="1",
    )

    # 1. Attempt GET
    res_get = client.get(
        f"/api/v1/portal/deals/{deal.id}",
        headers={"Authorization": f"Bearer {attacker_token}"},
    )
    assert res_get.status_code in (403, 404)

    # 2. Attempt negotiate
    res_neg = client.post(
        f"/api/v1/portal/deals/{deal.id}/negotiate",
        headers={"Authorization": f"Bearer {attacker_token}"},
        json={"type": "COMMENT", "message": "IDOR exploit attempt"},
    )
    assert res_neg.status_code in (403, 404)

    # 3. Attempt confirm
    res_conf = client.post(
        f"/api/v1/portal/deals/{deal.id}/confirm",
        headers={"Authorization": f"Bearer {attacker_token}"},
        json={"accepted": True},
    )
    assert res_conf.status_code in (403, 404)


def test_approval_escalation_and_timeline(in_memory_db, fake_gateway, seed_users):
    """Test approval escalation and full timeline inspection."""
    manager = seed_users["manager"]
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 10}])

    deal = Deal(
        reference="D-7011-TIMELINE",
        odoo_sale_order_id=raw_so.header.id,
        odoo_order_name=raw_so.header.name,
        odoo_partner_id=1,
        owner_odoo_user_id=2,
        status=DealStatus.DRAFT.value,
        approval_state=ApprovalState.PENDING_MANAGER.value,
        required_level=ApprovalLevel.MANAGER.value,
    )
    in_memory_db.add(deal)
    in_memory_db.flush()

    req = ApprovalRequest(
        deal_id=deal.id,
        risk_assessment_id=uuid.uuid4(),
        required_level="SALES_MANAGER",
        sequence=1,
        status=ApprovalRequestStatus.PENDING.value,
    )
    in_memory_db.add(req)
    in_memory_db.commit()

    # Escalate request
    approval_service.decide_approval_request(
        db=in_memory_db,
        gateway=fake_gateway,
        request_id=req.id,
        actor=manager,
        action=ApprovalActionType.ESCALATE,
        reason="Requires executive exception due to volume",
    )

    timeline = approval_service.get_deal_approval_timeline(db=in_memory_db, deal_id=deal.id)
    assert len(timeline) == 1
    assert timeline[0].action == "ESCALATE"
    assert "executive exception" in (timeline[0].reason or "")


def test_negotiation_counter_offer_flow(in_memory_db, fake_gateway, seed_users):
    """Test customer counter offer -> rep counter offer -> negotiation tracking."""
    rep = seed_users["rep"]
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 4, "discount_pct": 5.0}])

    deal = Deal(
        reference="D-7012-COUNTER",
        odoo_sale_order_id=raw_so.header.id,
        odoo_order_name=raw_so.header.name,
        odoo_partner_id=1,
        owner_odoo_user_id=rep.odoo_user_id,
        status=DealStatus.SENT.value,
        approval_state=ApprovalState.APPROVED.value,
    )
    in_memory_db.add(deal)
    in_memory_db.commit()

    # Customer asks for 20% discount
    cust_req = negotiation_service.submit_negotiation_request(
        db=in_memory_db,
        deal_id=deal.id,
        customer_partner_id=1,
        actor_user_id=1,
        payload=PortalNegotiationSubmit(
            type="COUNTER_DISCOUNT",
            message="We can close today at 20% discount",
            counter_value=Decimal("20.00"),
        ),
    )
    assert cust_req.status == NegotiationRequestStatus.OPEN.value

    # Rep counters with 12%
    counter_res = negotiation_service.respond_negotiation_request(
        db=in_memory_db,
        gateway=fake_gateway,
        request_id=cust_req.id,
        actor=rep,
        action="COUNTER",
        response_message="Best we can do is 12% with expedited shipping",
        counter_value=Decimal("12.00"),
    )
    assert counter_res.status == NegotiationRequestStatus.COUNTERED.value
    assert counter_res.counter_value == Decimal("12.00")


def test_api_endpoints_negotiation_and_comments(client, in_memory_db, fake_gateway, seed_users):
    """Test REST endpoints for negotiation listing, responding, and comment threading."""
    rep = seed_users["rep"]
    rep_token = security.create_access_token(
        subject=str(rep.odoo_user_id),
        role=rep.role,
        company_id="1",
        audience="internal",
    )
    customer_token = security.create_portal_token(
        subject="1",
        customer_id="1",
        company_id="1",
    )

    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 5}])
    so_id = raw_so.header.id

    deal = Deal(
        reference="D-7013-COMM",
        odoo_sale_order_id=so_id,
        odoo_order_name=raw_so.header.name,
        odoo_partner_id=1,
        owner_odoo_user_id=rep.odoo_user_id,
        status=DealStatus.SENT.value,
        approval_state=ApprovalState.APPROVED.value,
    )
    in_memory_db.add(deal)
    in_memory_db.commit()

    # 1. Customer posts a public comment via portal
    res_cust_comm = client.post(
        f"/api/v1/portal/deals/{deal.id}/comments",
        headers={"Authorization": f"Bearer {customer_token}"},
        json={"body": "Can delivery be done by end of month?"},
    )
    assert res_cust_comm.status_code == 200
    assert res_cust_comm.json()["data"]["body"] == "Can delivery be done by end of month?"

    # 2. Customer submits negotiation request via portal
    res_neg_post = client.post(
        f"/api/v1/portal/deals/{deal.id}/negotiate",
        headers={"Authorization": f"Bearer {customer_token}"},
        json={"type": "COMMENT", "message": "Requesting standard payment terms"},
    )
    assert res_neg_post.status_code == 200
    neg_id = res_neg_post.json()["data"]["id"]

    # 3. Sales rep lists negotiations via internal endpoint
    res_negs = client.get(
        f"/api/v1/negotiations/deals/{deal.id}",
        headers={"Authorization": f"Bearer {rep_token}"},
    )
    assert res_negs.status_code == 200
    assert len(res_negs.json()["data"]) >= 1

    # 4. Sales rep responds to negotiation via internal endpoint
    res_respond = client.post(
        f"/api/v1/negotiations/{neg_id}/respond",
        headers={"Authorization": f"Bearer {rep_token}"},
        json={"action": "ACCEPT", "response_message": "Terms accepted"},
    )
    assert res_respond.status_code == 200
    assert res_respond.json()["data"]["status"] == "ACCEPTED"

    # 5. Sales rep posts an internal comment
    res_internal = client.post(
        f"/api/v1/negotiations/deals/{deal.id}/comments",
        headers={"Authorization": f"Bearer {rep_token}"},
        json={"body": "Internal sales team note: verified with warehouse", "is_internal": True},
    )
    assert res_internal.status_code == 200
    assert res_internal.json()["data"]["is_internal"] is True

    # 6. Customer portal view MUST NOT see the internal comment
    res_portal_view = client.get(
        f"/api/v1/portal/deals/{deal.id}",
        headers={"Authorization": f"Bearer {customer_token}"},
    )
    assert res_portal_view.status_code == 200
    portal_comments = res_portal_view.json()["data"]["comments"]
    assert len(portal_comments) == 1
    assert portal_comments[0]["body"] == "Can delivery be done by end of month?"
    assert "Internal sales team note" not in [c["body"] for c in portal_comments]

    # 7. Internal user list comments CAN see internal comments
    res_all_comments = client.get(
        f"/api/v1/negotiations/deals/{deal.id}/comments",
        headers={"Authorization": f"Bearer {rep_token}"},
    )
    assert res_all_comments.status_code == 200
    assert len(res_all_comments.json()["data"]) == 2
