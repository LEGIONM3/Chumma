from datetime import datetime, timedelta, timezone
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
from app.guardian.health import calculate_deal_health
from app.guardian.next_action import determine_next_best_action
from app.main import app
from app.models.approval import ApprovalRequest
from app.models.deal import Deal
from app.models.enums import (
    AlertStatus,
    AlertType,
    ApprovalLevel,
    ApprovalRequestStatus,
    ApprovalState,
    DealStatus,
    FulfillmentPlanStatus,
    HealthStatus,
    NextBestActionType,
    Role,
)
from app.models.fulfillment import FulfillmentPlan, FulfillmentPlanLine
from app.models.health import DealAlert, DealHealthSnapshot, RepDiscountStats
from app.models.identity import DealflowUser
from app.models.negotiation import NegotiationRequest
from app.models.recommendation import Recommendation
from app.models.risk import RiskAssessment, RiskFactor
from app.odoo.fake import FakeOdooGateway
from app.services import health_service


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
        odoo_user_id=4,
        login="rep1@dealflow.test",
        name="Sales Rep One",
        role=Role.SALES_REP.value,
        is_active=True,
    )
    manager = DealflowUser(
        odoo_user_id=2,
        login="manager1@dealflow.test",
        name="Sales Manager North",
        role=Role.SALES_MANAGER.value,
        is_active=True,
    )
    finance = DealflowUser(
        odoo_user_id=6,
        login="finance@dealflow.test",
        name="Finance Approver",
        role=Role.FINANCE.value,
        is_active=True,
    )
    admin = DealflowUser(
        odoo_user_id=1,
        login="admin@dealflow.test",
        name="System Admin",
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


def test_deal_health_scoring_pure_engine():
    """Verify health score formula components and status thresholds (§7.7)."""
    # 1. Pristine clean deal
    res_clean = calculate_deal_health(deal_status=DealStatus.DRAFT.value)
    assert res_clean.overall_score == Decimal("0.00")
    assert res_clean.health_status == HealthStatus.HEALTHY
    assert res_clean.stalled_score == Decimal("0.00")
    assert res_clean.approval_delay_score == Decimal("0.00")

    # 2. Stalled active deal (14 days without activity with 7-day threshold = 40 max points)
    res_stalled = calculate_deal_health(
        deal_status=DealStatus.SENT.value,
        days_since_activity=Decimal("14.00"),
        stalled_threshold_days=7,
    )
    assert res_stalled.stalled_score == Decimal("40.00")
    assert res_stalled.overall_score == Decimal("40.00")
    assert res_stalled.health_status == HealthStatus.WATCH  # 31 - 60 is WATCH

    # 3. Terminal deal status zeros out stalled score
    for terminal_st in ["CONFIRMED", "IN_FULFILLMENT", "FULFILLED", "INVOICED", "PAID", "CANCELLED", "EXPIRED"]:
        res_term = calculate_deal_health(
            deal_status=terminal_st,
            days_since_activity=Decimal("60.00"),
            stalled_threshold_days=7,
        )
        assert res_term.stalled_score == Decimal("0.00")

    # 4. Approval delay score: min(20, 5 * days_pending)
    res_delay = calculate_deal_health(
        deal_status=DealStatus.DRAFT.value,
        days_pending_approval=Decimal("3.00"),
    )
    assert res_delay.approval_delay_score == Decimal("15.00")

    res_delay_capped = calculate_deal_health(
        deal_status=DealStatus.DRAFT.value,
        days_pending_approval=Decimal("10.00"),
    )
    assert res_delay_capped.approval_delay_score == Decimal("20.00")

    # 5. Discount anomaly score: 25 for HIGH, 15 for MEDIUM, 0 for None
    res_anom_high = calculate_deal_health(deal_status=DealStatus.DRAFT.value, anomaly_severity="HIGH")
    assert res_anom_high.discount_anomaly_score == Decimal("25.00")

    res_anom_med = calculate_deal_health(deal_status=DealStatus.DRAFT.value, anomaly_severity="MEDIUM")
    assert res_anom_med.discount_anomaly_score == Decimal("15.00")

    # 6. Delivery risk score: 30 slipping, 20 backorder, 10 split
    res_slip = calculate_deal_health(deal_status=DealStatus.CONFIRMED.value, has_slipping_delivery=True)
    assert res_slip.delivery_risk_score == Decimal("30.00")

    res_bo = calculate_deal_health(deal_status=DealStatus.CONFIRMED.value, has_backorder=True)
    assert res_bo.delivery_risk_score == Decimal("20.00")

    res_split = calculate_deal_health(deal_status=DealStatus.CONFIRMED.value, has_warehouse_split=True)
    assert res_split.delivery_risk_score == Decimal("10.00")

    # 7. Negotiation score: min(15, 5 * open_reqs)
    res_neg = calculate_deal_health(deal_status=DealStatus.UNDER_NEGOTIATION.value, open_negotiation_requests=2)
    assert res_neg.negotiation_score == Decimal("10.00")

    res_neg_capped = calculate_deal_health(deal_status=DealStatus.UNDER_NEGOTIATION.value, open_negotiation_requests=5)
    assert res_neg_capped.negotiation_score == Decimal("15.00")

    # 8. Blended AT_RISK threshold (overall >= 61)
    # Stalled 40 + Anomaly HIGH 25 = 65 -> AT_RISK
    res_at_risk = calculate_deal_health(
        deal_status=DealStatus.SENT.value,
        days_since_activity=Decimal("14.00"),
        stalled_threshold_days=7,
        anomaly_severity="HIGH",
    )
    assert res_at_risk.overall_score == Decimal("65.00")
    assert res_at_risk.health_status == HealthStatus.AT_RISK


def test_next_best_action_rule_order_1_to_13():
    """Verify deterministic evaluation hierarchy of Next Best Action rules (§7.8)."""
    deal_id = uuid.uuid4()

    # Rule 1: PENDING_FINANCE
    nba1 = determine_next_best_action(
        deal_id=deal_id,
        approval_state=ApprovalState.PENDING_FINANCE.value,
        open_negotiation_requests=3,  # Rule 4 is present, but Rule 1 should win
    )
    assert nba1.type == NextBestActionType.FINANCE_APPROVAL_REQUIRED.value
    assert nba1.priority == "HIGH"

    # Rule 1 approver view
    nba1_app = determine_next_best_action(
        deal_id=deal_id,
        approval_state=ApprovalState.PENDING_FINANCE.value,
        user_role="FINANCE",
    )
    assert nba1_app.type == NextBestActionType.AWAITING_APPROVER.value

    # Rule 2: PENDING_MANAGER
    nba2 = determine_next_best_action(
        deal_id=deal_id,
        approval_state=ApprovalState.PENDING_MANAGER.value,
        deal_status=DealStatus.DRAFT.value,
    )
    assert nba2.type == NextBestActionType.MANAGER_APPROVAL_REQUIRED.value
    assert nba2.priority == "HIGH"

    # Rule 3: just INVALIDATED
    nba3 = determine_next_best_action(
        deal_id=deal_id,
        approval_state=ApprovalState.INVALIDATED.value,
    )
    assert nba3.type == NextBestActionType.REAPPROVAL_REQUIRED.value
    assert nba3.priority == "HIGH"

    # Rule 4: open negotiation requests
    nba4 = determine_next_best_action(
        deal_id=deal_id,
        approval_state=ApprovalState.APPROVED.value,
        deal_status=DealStatus.UNDER_NEGOTIATION.value,
        open_negotiation_requests=1,
    )
    assert nba4.type == NextBestActionType.RESPOND_TO_CUSTOMER.value
    assert nba4.priority == "HIGH"

    # Rule 5: RETURNED/REJECTED & max_overage > 0
    nba5 = determine_next_best_action(
        deal_id=deal_id,
        approval_state=ApprovalState.RETURNED.value,
        max_overage=Decimal("4.00"),
        worst_line_id=101,
        worst_line_name="Pro Laptop 15",
        worst_line_ceiling=Decimal("15.00"),
    )
    assert nba5.type == NextBestActionType.REDUCE_DISCOUNT.value
    assert nba5.priority == "HIGH"
    assert nba5.payload["target_discount_pct"] == 15.0

    # Rule 6: margin_exposure > 0
    nba6 = determine_next_best_action(
        deal_id=deal_id,
        approval_state=ApprovalState.APPROVED.value,
        margin_exposure=Decimal("12.50"),
        top_active_recommendation={"product_name": "Premium Care", "projected_margin_delta": 3.5},
    )
    assert nba6.type == NextBestActionType.RESTORE_MARGIN.value
    assert nba6.priority == "MEDIUM"

    # Rule 7: fulfillment plan PROPOSED & deal CONFIRMED
    nba7 = determine_next_best_action(
        deal_id=deal_id,
        deal_status=DealStatus.CONFIRMED.value,
        approval_state=ApprovalState.APPROVED.value,
        fulfillment_plan_status=FulfillmentPlanStatus.PROPOSED.value,
    )
    assert nba7.type == NextBestActionType.ACCEPT_FULFILLMENT_PLAN.value
    assert nba7.priority == "MEDIUM"

    # Rule 8: consolidatable backorder
    nba8 = determine_next_best_action(
        deal_id=deal_id,
        deal_status=DealStatus.IN_FULFILLMENT.value,
        approval_state=ApprovalState.APPROVED.value,
        has_consolidatable_backorder=True,
    )
    assert nba8.type == NextBestActionType.CONSOLIDATE_BACKORDER.value
    assert nba8.priority == "MEDIUM"

    # Rule 9: deal DRAFT & approval APPROVED -> SEND_TO_CUSTOMER
    nba9 = determine_next_best_action(
        deal_id=deal_id,
        deal_status=DealStatus.DRAFT.value,
        approval_state=ApprovalState.APPROVED.value,
    )
    assert nba9.type == NextBestActionType.SEND_TO_CUSTOMER.value
    assert nba9.priority == "MEDIUM"

    # Rule 10: deal SENT & stalled
    nba10 = determine_next_best_action(
        deal_id=deal_id,
        deal_status=DealStatus.SENT.value,
        approval_state=ApprovalState.APPROVED.value,
        days_since_activity=8,
        stalled_days=7,
    )
    assert nba10.type == NextBestActionType.FOLLOW_UP_CUSTOMER.value
    assert nba10.priority == "MEDIUM"

    # Rule 11: customer_confirmed_pending & approved
    nba11 = determine_next_best_action(
        deal_id=deal_id,
        deal_status=DealStatus.SENT.value,
        approval_state=ApprovalState.APPROVED.value,
        customer_confirmed_pending=True,
    )
    assert nba11.type == NextBestActionType.CONFIRM_ORDER.value
    assert nba11.priority == "HIGH"

    # Rule 12: top active recommendation exists
    nba12 = determine_next_best_action(
        deal_id=deal_id,
        deal_status=DealStatus.DRAFT.value,
        approval_state=ApprovalState.NOT_EVALUATED.value,
        top_active_recommendation={"product_name": "Docking Station", "projected_margin_delta": 2.1},
    )
    assert nba12.type == NextBestActionType.ADD_RECOMMENDATION.value
    assert nba12.priority == "LOW"

    # Rule 13: else -> NONE
    nba13 = determine_next_best_action(
        deal_id=deal_id,
        deal_status=DealStatus.IN_FULFILLMENT.value,
        approval_state=ApprovalState.APPROVED.value,
    )
    assert nba13.type == NextBestActionType.NONE.value
    assert nba13.priority == "LOW"


def test_rep_discount_baseline_fallback_chain(in_memory_db: Session):
    """Test rep discount stats fallback: rep -> team -> company (§6.13)."""
    # Create 3 deals for rep2 (team 2)
    for i in range(3):
        d = Deal(
            reference=f"D-T2-{i}",
            odoo_sale_order_id=2000 + i,
            odoo_order_name=f"SO200{i}",
            odoo_partner_id=1,
            owner_odoo_user_id=5,  # rep2
            sales_team_odoo_id=2,  # South Team
            odoo_company_id=1,
            status=DealStatus.SENT.value,
            order_discount_pct=Decimal("8.00"),
            amount_total_cache=Decimal("1000.00"),
            amount_untaxed_cache=Decimal("1000.00"),
        )
        in_memory_db.add(d)
    in_memory_db.commit()

    # Rep 2 has 3 deals -> source is REP
    avg, std, src, count = health_service.calculate_rep_discount_baseline(
        db=in_memory_db,
        odoo_user_id=5,
        sales_team_id=2,
        odoo_company_id=1,
    )
    assert src == "REP"
    assert count == 3
    assert avg == Decimal("8.00")

    # Rep 3 has 0 deals but belongs to team 2 (which has 3 deals) -> fallback to TEAM
    avg_t, std_t, src_t, count_t = health_service.calculate_rep_discount_baseline(
        db=in_memory_db,
        odoo_user_id=999,  # Non-existent rep
        sales_team_id=2,
        odoo_company_id=1,
    )
    assert src_t == "TEAM"
    assert count_t == 3
    assert avg_t == Decimal("8.00")

    # Non-existent team -> fallback to COMPANY (which has 3 deals)
    avg_c, std_c, src_c, count_c = health_service.calculate_rep_discount_baseline(
        db=in_memory_db,
        odoo_user_id=999,
        sales_team_id=999,
        odoo_company_id=1,
    )
    assert src_c == "COMPANY"
    assert count_c == 3
    assert avg_c == Decimal("8.00")


def test_stalled_deal_detector_and_auto_resolution(in_memory_db: Session):
    """Test stalled deal detection and auto-resolution lifecycle (§6.12)."""
    old_time = utc_now() - timedelta(days=10)

    stalled_deal = Deal(
        reference="D-STALL-1",
        odoo_sale_order_id=3001,
        odoo_order_name="SO3001",
        odoo_partner_id=1,
        owner_odoo_user_id=4,
        sales_team_odoo_id=1,
        status=DealStatus.SENT.value,
        last_activity_at=old_time,
    )
    in_memory_db.add(stalled_deal)
    in_memory_db.commit()

    # Run detector
    alerts = health_service.detect_stalled_deals(db=in_memory_db, stalled_days=7)
    assert len(alerts) >= 1
    stalled_alert = in_memory_db.query(DealAlert).filter(
        DealAlert.deal_id == stalled_deal.id,
        DealAlert.type == AlertType.STALLED_DEAL.value,
    ).first()
    assert stalled_alert is not None
    assert stalled_alert.status == AlertStatus.OPEN.value
    assert stalled_alert.severity == "LOW"  # 10 days < 14 (2x) is LOW

    # Refresh activity -> auto-resolve alert
    stalled_deal.last_activity_at = utc_now()
    in_memory_db.commit()

    health_service.detect_stalled_deals(db=in_memory_db, stalled_days=7)
    in_memory_db.refresh(stalled_alert)
    assert stalled_alert.status == AlertStatus.RESOLVED.value


def test_delivery_slippage_detector(in_memory_db: Session, fake_gateway: FakeOdooGateway):
    """Test delivery slippage detection based on promised date (§6.14)."""
    past_delivery = utc_now() - timedelta(days=5)

    slipping_deal = Deal(
        reference="D-SLIP-1",
        odoo_sale_order_id=4001,
        odoo_order_name="SO4001",
        odoo_partner_id=1,
        owner_odoo_user_id=4,
        status=DealStatus.CONFIRMED.value,
        promised_delivery_date=past_delivery,
    )
    in_memory_db.add(slipping_deal)
    in_memory_db.commit()

    alerts = health_service.detect_delivery_slippages(db=in_memory_db, gateway=fake_gateway)
    assert len(alerts) >= 1
    slip_alert = in_memory_db.query(DealAlert).filter(
        DealAlert.deal_id == slipping_deal.id,
        DealAlert.type == AlertType.DELIVERY_SLIPPAGE.value,
    ).first()
    assert slip_alert is not None
    assert slip_alert.status == AlertStatus.OPEN.value
    assert slip_alert.severity == "HIGH"  # 5 days late > 3 is HIGH


def test_alert_lifecycle_nudge_escalate(in_memory_db: Session, seed_users):
    """Test alert acknowledge, resolve, nudge, and escalate actions (§6.15)."""
    deal = Deal(
        reference="D-ALERT-TEST",
        odoo_sale_order_id=5001,
        odoo_order_name="SO5001",
        odoo_partner_id=1,
        owner_odoo_user_id=4,  # rep1
        sales_team_odoo_id=1,  # manager1
        status=DealStatus.SENT.value,
    )
    in_memory_db.add(deal)
    in_memory_db.flush()

    alert = DealAlert(
        deal_id=deal.id,
        type=AlertType.STALLED_DEAL.value,
        severity="LOW",
        status=AlertStatus.OPEN.value,
        title="Stalled Deal Alert",
    )
    in_memory_db.add(alert)
    in_memory_db.commit()

    # 1. Nudge (allowed for rep)
    nudged = health_service.nudge_or_escalate_alert(
        db=in_memory_db,
        alert_id=alert.id,
        action="NUDGE",
        message="Please check with customer",
        actor_id=4,
        actor_role="SALES_REP",
    )
    assert nudged.last_action == "NUDGE"

    # 2. Escalate as rep should be forbidden (403)
    with pytest.raises(Exception) as exc:
        health_service.nudge_or_escalate_alert(
            db=in_memory_db,
            alert_id=alert.id,
            action="ESCALATE",
            message="Escalating to manager",
            actor_id=4,
            actor_role="SALES_REP",
        )
    assert "Only SALES_MANAGER or ADMIN" in str(exc.value)

    # 3. Escalate as manager succeeds
    escalated = health_service.nudge_or_escalate_alert(
        db=in_memory_db,
        alert_id=alert.id,
        action="ESCALATE",
        message="Escalating stalled deal",
        actor_id=2,
        actor_role="SALES_MANAGER",
    )
    assert escalated.last_action == "ESCALATE"
    assert escalated.severity == "HIGH"

    # 4. Acknowledge
    ack = health_service.acknowledge_alert(db=in_memory_db, alert_id=alert.id, user_id=2)
    assert ack.status == AlertStatus.ACKNOWLEDGED.value
    assert ack.acknowledged_by == 2

    # 5. Resolve
    res = health_service.resolve_alert(db=in_memory_db, alert_id=alert.id, user_id=2)
    assert res.status == AlertStatus.RESOLVED.value


def test_control_tower_kpis_and_action_queue(in_memory_db: Session):
    """Test Control Tower dashboard KPI aggregations and action queue deep links (§8.7)."""
    deal = Deal(
        reference="D-TOWER-1",
        odoo_sale_order_id=6001,
        odoo_order_name="SO6001",
        odoo_partner_id=1,
        partner_name_cache="Acme Corp",
        owner_odoo_user_id=4,
        sales_team_odoo_id=1,
        status=DealStatus.SENT.value,
        health_status=HealthStatus.AT_RISK.value,
        amount_total_cache=Decimal("50000.00"),
        amount_untaxed_cache=Decimal("50000.00"),
        order_discount_pct=Decimal("10.00"),
    )
    in_memory_db.add(deal)
    in_memory_db.flush()

    # Open alert
    alert = DealAlert(
        deal_id=deal.id,
        type=AlertType.DISCOUNT_ANOMALY.value,
        severity="HIGH",
        status=AlertStatus.OPEN.value,
        title="Excessive discount anomaly",
    )
    in_memory_db.add(alert)

    # Pending approval
    assessment = RiskAssessment(
        deal_id=deal.id,
        risk_score=Decimal("65.00"),
        severity="HIGH",
        required_level="MANAGER",
        decision="APPROVAL_REQUIRED",
        trigger_type="MANUAL",
        policy_version="1.0",
    )
    in_memory_db.add(assessment)
    in_memory_db.flush()

    app_req = ApprovalRequest(
        deal_id=deal.id,
        risk_assessment_id=assessment.id,
        required_level="SALES_MANAGER",
        sequence=1,
        status=ApprovalRequestStatus.PENDING.value,
    )
    in_memory_db.add(app_req)
    in_memory_db.commit()

    tower = health_service.get_control_tower_data(db=in_memory_db)
    assert tower.kpis.pipeline_value >= Decimal("50000.00")
    assert tower.kpis.at_risk_count >= 1
    assert tower.kpis.pending_approvals >= 1
    assert tower.kpis.discount_exposure_amount >= Decimal("5000.00")

    # Action queue contains both alert and approval with deep link
    deep_links = [item.deep_link for item in tower.action_queue]
    assert f"/deals/{deal.id}" in deep_links


def test_api_endpoints_health_control_tower_and_jobs(
    in_memory_db: Session,
    client: TestClient,
    seed_users,
):
    """Test REST API routes for health, control-tower, alerts, and background jobs."""
    manager_token = security.create_access_token(
        subject="2",
        role=Role.SALES_MANAGER.value,
        company_id="1",
        extra_claims={"odoo_user_id": 2, "login": "manager1@dealflow.test"},
    )
    auth_headers = {"Authorization": f"Bearer {manager_token}"}

    # 1. Control Tower Dashboard
    resp = client.get("/api/v1/dashboard/control-tower", headers=auth_headers)
    assert resp.status_code == 200
    res_data = resp.json()["data"]
    assert "kpis" in res_data
    assert "action_queue" in res_data

    # 2. Registered Jobs List
    resp_jobs = client.get("/api/v1/admin/jobs", headers=auth_headers)
    assert resp_jobs.status_code == 200
    jobs_list = resp_jobs.json()["data"]
    job_names = [j["name"] for j in jobs_list]
    assert "detect_stalled_deals" in job_names
    assert "detect_discount_anomalies" in job_names
    assert "detect_slippage" in job_names

    # 3. Run Job Endpoint
    resp_run = client.post("/api/v1/admin/jobs/run/detect_stalled_deals", headers=auth_headers)
    assert resp_run.status_code == 200
    assert resp_run.json()["data"]["job"] == "detect_stalled_deals"

    # 4. Recompute Alerts Endpoint
    resp_recompute = client.post("/api/v1/alerts/recompute", headers=auth_headers)
    assert resp_recompute.status_code == 200
    assert "scanned_deals" in resp_recompute.json()["data"]
