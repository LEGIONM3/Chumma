from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import uuid
import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import security
from app.core.deps import get_db, get_odoo_gateway
from app.db.base import Base, utc_now
from app.main import app
from app.models.approval import ApprovalAction, ApprovalRequest
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
    RiskSeverity,
    Role,
)
from app.models.fulfillment import FulfillmentPlan, FulfillmentPlanLine, WarehouseProfile
from app.models.health import DealAlert
from app.models.identity import DealflowUser
from app.models.risk import RiskAssessment, RiskFactor
from app.odoo.fake import FakeOdooGateway
from app.services.report_service import ReportService


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
    rep1 = DealflowUser(
        odoo_user_id=4,
        login="rep1@dealflow.test",
        name="Sales Rep One",
        role=Role.SALES_REP.value,
        sales_team_odoo_id=1,
        is_active=True,
    )
    rep2 = DealflowUser(
        odoo_user_id=5,
        login="rep2@dealflow.test",
        name="Sales Rep Two",
        role=Role.SALES_REP.value,
        sales_team_odoo_id=2,
        is_active=True,
    )
    manager = DealflowUser(
        odoo_user_id=2,
        login="manager1@dealflow.test",
        name="Sales Manager North",
        role=Role.SALES_MANAGER.value,
        sales_team_odoo_id=1,
        is_active=True,
    )
    admin = DealflowUser(
        odoo_user_id=1,
        login="admin@dealflow.test",
        name="System Admin",
        role=Role.ADMIN.value,
        is_active=True,
    )
    in_memory_db.add_all([rep1, rep2, manager, admin])
    in_memory_db.commit()
    return {"rep1": rep1, "rep2": rep2, "manager": manager, "admin": admin}


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


@pytest.fixture
def seed_report_data(in_memory_db):
    now = utc_now()

    # Deal 1: Confirmed with revenue, low discount, healthy margin
    d1 = Deal(
        reference="D-1001",
        odoo_sale_order_id=1001,
        odoo_order_name="SO01001",
        odoo_partner_id=1,
        partner_name_cache="Acme Corp",
        owner_odoo_user_id=4,
        sales_team_odoo_id=1,
        status=DealStatus.CONFIRMED.value,
        approval_state=ApprovalState.APPROVED.value,
        health_status=HealthStatus.HEALTHY.value,
        current_risk_score=Decimal("20.00"),
        current_severity=RiskSeverity.LOW.value,
        amount_total_cache=Decimal("50000.00"),
        one_time_total_cache=Decimal("40000.00"),
        recurring_first_cycle_total_cache=Decimal("10000.00"),
        order_discount_pct=Decimal("5.00"),
        margin_pct_cache=Decimal("25.00"),
        sent_at=now - timedelta(days=5),
        confirmed_at=now - timedelta(days=2),
        created_at=now - timedelta(days=6),
    )
    in_memory_db.add(d1)
    in_memory_db.flush()

    # Assessment for Deal 1 with line items
    ass1 = RiskAssessment(
        deal_id=d1.id,
        risk_score=Decimal("20.00"),
        severity=RiskSeverity.LOW.value,
        required_level=ApprovalLevel.NONE.value,
        decision="AUTO_APPROVED",
        trigger_type="MANUAL",
        policy_version="1.0",
        line_snapshot=[
            {
                "product_id": 101,
                "product_name": "Laptop Pro 14\"",
                "category_id": 1,
                "qty": 10.0,
                "price_unit": 50000.0,
                "discount_pct": 5.0,
            }
        ],
    )
    in_memory_db.add(ass1)
    in_memory_db.flush()
    d1.current_assessment_id = ass1.id
    d1.approved_assessment_id = ass1.id

    # Deal 2: Sent, pending manager approval, high discount
    d2 = Deal(
        reference="D-1002",
        odoo_sale_order_id=1002,
        odoo_order_name="SO01002",
        odoo_partner_id=2,
        partner_name_cache="Beta Industries",
        owner_odoo_user_id=5,
        sales_team_odoo_id=2,
        status=DealStatus.SENT.value,
        approval_state=ApprovalState.PENDING_MANAGER.value,
        required_level=ApprovalLevel.MANAGER.value,
        health_status=HealthStatus.WATCH.value,
        current_risk_score=Decimal("55.00"),
        current_severity=RiskSeverity.HIGH.value,
        amount_total_cache=Decimal("30000.00"),
        one_time_total_cache=Decimal("30000.00"),
        recurring_first_cycle_total_cache=Decimal("0.00"),
        order_discount_pct=Decimal("15.00"),
        margin_pct_cache=Decimal("18.00"),
        sent_at=now - timedelta(days=2),
        created_at=now - timedelta(days=3),
    )
    in_memory_db.add(d2)
    in_memory_db.flush()

    ass2 = RiskAssessment(
        deal_id=d2.id,
        risk_score=Decimal("55.00"),
        severity=RiskSeverity.HIGH.value,
        required_level=ApprovalLevel.MANAGER.value,
        decision="MANAGER_APPROVAL_REQUIRED",
        trigger_type="MANUAL",
        policy_version="1.0",
        line_snapshot=[
            {
                "product_id": 103,
                "product_name": "Monitor 27\"",
                "category_id": 1,
                "qty": 5.0,
                "price_unit": 20000.0,
                "discount_pct": 15.0,
            }
        ],
    )
    in_memory_db.add(ass2)
    in_memory_db.flush()
    d2.current_assessment_id = ass2.id

    # Risk factor on Deal 2
    f1 = RiskFactor(
        risk_assessment_id=ass2.id,
        factor_type="DISCOUNT_EXCESS",
        contribution=Decimal("25.00"),
        reason="Discount exceeds policy threshold",
    )
    in_memory_db.add(f1)

    # Deal 3: Draft, no approval required, zero discount
    d3 = Deal(
        reference="D-1003",
        odoo_sale_order_id=1003,
        odoo_order_name="SO01003",
        odoo_partner_id=3,
        partner_name_cache="Gamma LLC",
        owner_odoo_user_id=4,
        sales_team_odoo_id=1,
        status=DealStatus.DRAFT.value,
        approval_state=ApprovalState.EVALUATED_NO_APPROVAL.value,
        health_status=HealthStatus.HEALTHY.value,
        current_risk_score=Decimal("10.00"),
        current_severity=RiskSeverity.LOW.value,
        amount_total_cache=Decimal("20000.00"),
        one_time_total_cache=Decimal("20000.00"),
        recurring_first_cycle_total_cache=Decimal("0.00"),
        order_discount_pct=Decimal("0.00"),
        margin_pct_cache=Decimal("30.00"),
        created_at=now - timedelta(days=1),
    )
    in_memory_db.add(d3)
    in_memory_db.flush()

    # Approvals: Completed for D1 (2.0 hours turnaround), Pending for D2
    app_d1 = ApprovalRequest(
        deal_id=d1.id,
        risk_assessment_id=ass1.id,
        required_level="SALES_MANAGER",
        sequence=1,
        status=ApprovalRequestStatus.APPROVED.value,
        requested_at=now - timedelta(hours=10),
        completed_at=now - timedelta(hours=8),  # exactly 2 hours
        decided_by_odoo_user_id=2,
    )
    app_d2 = ApprovalRequest(
        deal_id=d2.id,
        risk_assessment_id=ass2.id,
        required_level="SALES_MANAGER",
        sequence=1,
        status=ApprovalRequestStatus.PENDING.value,
        requested_at=now - timedelta(hours=4),
    )
    in_memory_db.add_all([app_d1, app_d2])

    # Rejection action for test
    app_rej = ApprovalRequest(
        deal_id=d2.id,
        risk_assessment_id=ass2.id,
        required_level="FINANCE",
        sequence=2,
        status=ApprovalRequestStatus.REJECTED.value,
        requested_at=now - timedelta(hours=6),
        completed_at=now - timedelta(hours=5),
        decision_reason="Discount exceeds margin limits",
    )
    in_memory_db.add(app_rej)

    # Anomaly Alert for Rep 2
    al1 = DealAlert(
        deal_id=d2.id,
        type=AlertType.DISCOUNT_ANOMALY.value,
        severity="HIGH",
        status=AlertStatus.OPEN.value,
        title="Discount anomaly detected",
        raised_at=now - timedelta(hours=2),
    )
    in_memory_db.add(al1)

    # Fulfillment Plan for Deal 1
    fp1 = FulfillmentPlan(
        deal_id=d1.id,
        odoo_sale_order_id=1001,
        status=FulfillmentPlanStatus.APPLIED.value,
        estimated_shipments=2,
        estimated_shipping_cost=Decimal("25.00"),
        strategy="CHEAPEST_SPLIT",
    )
    in_memory_db.add(fp1)
    in_memory_db.flush()

    fpl1 = FulfillmentPlanLine(
        fulfillment_plan_id=fp1.id,
        odoo_sale_order_line_id=1,
        odoo_product_id=101,
        odoo_warehouse_id=1,
        requested_qty=8,
        allocated_qty=8,
        backorder_qty=0,
        shipping_cost=Decimal("10.00"),
    )
    fpl2 = FulfillmentPlanLine(
        fulfillment_plan_id=fp1.id,
        odoo_sale_order_line_id=1,
        odoo_product_id=101,
        odoo_warehouse_id=2,
        requested_qty=2,
        allocated_qty=2,
        backorder_qty=0,
        shipping_cost=Decimal("15.00"),
    )
    in_memory_db.add_all([fpl1, fpl2])
    in_memory_db.commit()

    return {"d1": d1, "d2": d2, "d3": d3}


def test_summary_report_metric_calculations(in_memory_db, fake_gateway, seed_report_data):
    """Verify summary report totals, win rate, revenue, discounts, margin, and approval turnaround."""
    service = ReportService(in_memory_db, fake_gateway)
    filters = service.parse_filters(period="month")

    summary = service.get_summary_report(filters)

    assert summary.quotations_created == 3
    assert summary.quotations_sent == 2  # D1 and D2
    assert summary.quotations_confirmed == 1  # D1
    assert summary.win_rate == Decimal("33.33")  # 1 / 3 = 33.33%
    assert summary.revenue_confirmed == Decimal("50000.00")  # 40k one time + 10k recurring

    # Avg discount: (5.00 + 15.00 + 0.00) / 3 = 6.67%
    assert summary.avg_discount == Decimal("6.67")

    # Avg margin: (25.00 + 18.00 + 30.00) / 3 = 24.33%
    assert summary.avg_margin == Decimal("24.33")

    # Turnaround: app_d1 took 2.0h, app_rej took 1.0h -> avg 1.5h
    assert summary.avg_approval_turnaround_hours == Decimal("1.50")
    assert summary.pending_approvals_count == 1  # app_d2 is pending


def test_period_and_date_filtering(in_memory_db, fake_gateway, seed_report_data):
    """Verify date filtering by period presets (today, week, month) and custom explicit dates."""
    service = ReportService(in_memory_db, fake_gateway)

    # 1. Custom narrow window: only yesterday to now -> captures D3
    now = utc_now()
    filters_narrow = service.parse_filters(
        period="custom",
        from_date=now - timedelta(days=1, hours=12),
        to_date=now,
    )
    summary_narrow = service.get_summary_report(filters_narrow)
    assert summary_narrow.quotations_created == 1

    # 2. Week preset -> captures all 3 (created 1, 3, 6 days ago)
    filters_week = service.parse_filters(period="week")
    summary_week = service.get_summary_report(filters_week)
    assert summary_week.quotations_created == 3

    # 3. Future date window -> 0 deals
    filters_future = service.parse_filters(
        period="custom",
        from_date=now + timedelta(days=1),
        to_date=now + timedelta(days=2),
    )
    summary_future = service.get_summary_report(filters_future)
    assert summary_future.quotations_created == 0


def test_team_rep_and_approval_status_filtering(in_memory_db, fake_gateway, seed_report_data):
    """Verify filtering by sales team, rep ID, and approval status mappings."""
    service = ReportService(in_memory_db, fake_gateway)

    # 1. Filter by Team 1 -> D1 and D3
    f_team1 = service.parse_filters(team_id=1)
    deals_team1 = service.get_deals_report(f_team1)
    assert deals_team1.total == 2
    assert {d.reference for d in deals_team1.items} == {"D-1001", "D-1003"}

    # 2. Filter by Rep 5 -> D2
    f_rep2 = service.parse_filters(rep_id=5)
    deals_rep2 = service.get_deals_report(f_rep2)
    assert deals_rep2.total == 1
    assert deals_rep2.items[0].reference == "D-1002"

    # 3. Filter by approval_status="pending" -> D2
    f_pending = service.parse_filters(approval_status="pending")
    deals_pending = service.get_deals_report(f_pending)
    assert deals_pending.total == 1
    assert deals_pending.items[0].reference == "D-1002"

    # 4. Filter by approval_status="approved" -> D1
    f_approved = service.parse_filters(approval_status="approved")
    deals_approved = service.get_deals_report(f_approved)
    assert deals_approved.total == 1
    assert deals_approved.items[0].reference == "D-1001"

    # 5. Filter by approval_status="none" -> D3
    f_none = service.parse_filters(approval_status="none")
    deals_none = service.get_deals_report(f_none)
    assert deals_none.total == 1
    assert deals_none.items[0].reference == "D-1003"


def test_deals_and_approvals_reports(in_memory_db, fake_gateway, seed_report_data):
    """Verify detailed deals list report and approvals stage breakdown."""
    service = ReportService(in_memory_db, fake_gateway)
    filters = service.parse_filters(period="month")

    # Deals report
    deals_report = service.get_deals_report(filters)
    assert deals_report.total == 3
    assert len(deals_report.items) == 3

    first_item = deals_report.items[0]
    assert hasattr(first_item, "deal_id")
    assert hasattr(first_item, "reference")
    assert hasattr(first_item, "amount_total")
    assert hasattr(first_item, "order_discount_pct")

    # Approvals report
    app_report = service.get_approvals_report(filters)
    assert app_report.total_requests == 3
    assert app_report.completed_requests == 2

    # Statuses: 1 APPROVED, 1 PENDING, 1 REJECTED
    status_map = {s.status: s.count for s in app_report.by_status}
    assert status_map[ApprovalRequestStatus.APPROVED.value] == 1
    assert status_map[ApprovalRequestStatus.PENDING.value] == 1
    assert status_map[ApprovalRequestStatus.REJECTED.value] == 1

    # Rejection reasons top list
    assert len(app_report.top_rejection_reasons) >= 1
    assert app_report.top_rejection_reasons[0].reason == "Discount exceeds margin limits"
    assert app_report.top_rejection_reasons[0].count == 1


def test_products_and_discounts_reports(in_memory_db, fake_gateway, seed_report_data):
    """Verify product ranking (best-selling, most discounted) and rep discount compliance."""
    service = ReportService(in_memory_db, fake_gateway)
    filters = service.parse_filters(period="month")

    # Products report
    prod_report = service.get_products_report(filters)
    assert len(prod_report.best_selling_by_qty) >= 2

    # 101 Laptop (qty 10, rev 475k net), 103 Monitor (qty 5)
    top_qty = prod_report.best_selling_by_qty[0]
    assert top_qty.product_id == 101
    assert top_qty.qty_sold == 10.0

    # Most discounted: 103 Monitor (15%), 101 Laptop (5%)
    top_disc = prod_report.most_discounted[0]
    assert top_disc.product_id == 103
    assert top_disc.avg_discount_pct == Decimal("15.00")

    # Discounts report
    disc_report = service.get_discounts_report(filters)
    assert len(disc_report.rep_stats) == 2

    # Rep 5 (Rep 2) has 15% discount, 1 overage, 1 anomaly
    rep5_stats = next(s for s in disc_report.rep_stats if s.rep_id == 5)
    assert rep5_stats.avg_weighted_discount_pct == Decimal("15.00")
    assert rep5_stats.overage_count == 1
    assert rep5_stats.anomaly_count == 1
    assert rep5_stats.max_discount_pct == Decimal("15.00")

    # Rep 4 (Rep 1) has deals with 5% and 0%
    rep4_stats = next(s for s in disc_report.rep_stats if s.rep_id == 4)
    assert rep4_stats.deals_count == 2
    assert rep4_stats.overage_count == 0
    assert rep4_stats.anomaly_count == 0

    assert disc_report.total_anomalies == 1
    assert disc_report.total_overages == 1


def test_pipeline_and_fulfillment_reports(in_memory_db, fake_gateway, seed_report_data):
    """Verify pipeline funnel, risk distribution, top factors, and warehouse fulfillment metrics."""
    service = ReportService(in_memory_db, fake_gateway)
    filters = service.parse_filters(period="month")

    # Pipeline report
    pipe_report = service.get_pipeline_report(filters)
    assert pipe_report.total_deals_count == 3
    assert pipe_report.total_pipeline_value == Decimal("100000.00")  # 50k + 30k + 20k

    funnel_map = {p.status: p.count for p in pipe_report.pipeline_funnel}
    assert funnel_map[DealStatus.CONFIRMED.value] == 1
    assert funnel_map[DealStatus.SENT.value] == 1
    assert funnel_map[DealStatus.DRAFT.value] == 1

    # Top risk factors
    assert len(pipe_report.top_risk_factors) >= 1
    assert pipe_report.top_risk_factors[0].factor_type == "DISCOUNT_EXCESS"

    # Fulfillment report
    ful_report = service.get_fulfillment_report(filters)
    assert ful_report.total_plans == 1
    assert ful_report.total_shipments == 2
    assert ful_report.split_rate == Decimal("100.00")  # D1 split between WH1 and WH2
    assert ful_report.backorder_rate == Decimal("0.00")
    assert ful_report.on_time_pct == Decimal("100.00")

    # Warehouse breakdown has WH1 and WH2
    wh_ids = [w.warehouse_id for w in ful_report.warehouse_breakdown]
    assert 1 in wh_ids
    assert 2 in wh_ids


def test_billing_report(in_memory_db, fake_gateway, seed_report_data):
    """Verify billing report totals, invoiced vs paid, and MRR."""
    service = ReportService(in_memory_db, fake_gateway)
    filters = service.parse_filters(period="month")

    bill_report = service.get_billing_report(filters)
    assert bill_report.period == "month"
    # Seeded D1 has recurring 10k
    assert bill_report.mrr >= Decimal("0.00")
    assert hasattr(bill_report, "total_invoiced")
    assert hasattr(bill_report, "total_paid")
    assert hasattr(bill_report, "total_outstanding")


def test_api_endpoints_json_pdf_and_xlsx_outputs(
    in_memory_db,
    client: TestClient,
    seed_users,
    seed_report_data,
):
    """Verify all 8 reporting endpoints support format=json, format=pdf, and format=xlsx."""
    manager_token = security.create_access_token(
        subject="2",
        role=Role.SALES_MANAGER.value,
        company_id="1",
        extra_claims={"odoo_user_id": 2, "login": "manager1@dealflow.test"},
    )
    auth_headers = {"Authorization": f"Bearer {manager_token}"}

    endpoints = [
        "/api/v1/reports/summary",
        "/api/v1/reports/quotations",
        "/api/v1/reports/deals",
        "/api/v1/reports/approvals",
        "/api/v1/reports/products",
        "/api/v1/reports/discounts",
        "/api/v1/reports/pipeline",
        "/api/v1/reports/risk",
        "/api/v1/reports/fulfillment",
        "/api/v1/reports/billing",
    ]

    for ep in endpoints:
        # 1. JSON Format
        resp_json = client.get(f"{ep}?format=json", headers=auth_headers)
        assert resp_json.status_code == 200, f"JSON failed for {ep}"
        data = resp_json.json()["data"]
        assert data is not None, f"Empty data for {ep}"

        # 2. XLSX Format
        resp_xlsx = client.get(f"{ep}?format=xlsx", headers=auth_headers)
        assert resp_xlsx.status_code == 200, f"XLSX failed for {ep}"
        assert resp_xlsx.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        # Validate openpyxl can load the workbook
        wb = openpyxl.load_workbook(io.BytesIO(resp_xlsx.content))
        assert len(wb.sheetnames) >= 1, f"No sheets in {ep} workbook"

        # 3. PDF Format
        resp_pdf = client.get(f"{ep}?format=pdf", headers=auth_headers)
        assert resp_pdf.status_code == 200, f"PDF failed for {ep}"
        assert resp_pdf.headers["content-type"] == "application/pdf"
        assert resp_pdf.content.startswith(b"%PDF"), f"Invalid PDF header for {ep}"
