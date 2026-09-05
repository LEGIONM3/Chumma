from decimal import Decimal
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import security
from app.core.deps import get_db, get_odoo_gateway
from app.core.errors import BusinessRuleError
from app.db.base import Base
from app.guardian.fulfillment import (
    StockableLine,
    WarehouseInfo,
    calculate_fulfillment_plan,
)
from app.main import app
from app.models.deal import Deal
from app.models.enums import DealStatus, FulfillmentPlanStatus, Role
from app.models.fulfillment import FulfillmentPlan, FulfillmentPlanLine, WarehouseProfile
from app.models.identity import DealflowUser
from app.odoo.fake import FakeOdooGateway
from app.schemas.fulfillment import (
    FulfillmentOverrideLineItem,
    WarehouseProfileCreate,
    WarehouseProfileUpdate,
)
from app.services import deal_service, fulfillment_service


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
        name="Finance User",
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


@pytest.fixture
def standard_warehouses():
    return [
        WarehouseInfo(warehouse_id=1, name="Main Warehouse", shipping_cost_weight=Decimal("10.00"), priority=1),
        WarehouseInfo(warehouse_id=2, name="East Depot", shipping_cost_weight=Decimal("15.00"), priority=2),
        WarehouseInfo(warehouse_id=3, name="West Hub", shipping_cost_weight=Decimal("25.00"), priority=3),
    ]


def test_fulfillment_single_cheapest_full_coverage(standard_warehouses):
    """Test single warehouse with full coverage is chosen based on lowest shipping cost."""
    lines = [StockableLine(line_id=1, product_id=101, requested_qty=4)]
    # Both MAIN (10.00) and EAST (15.00) have enough stock
    availability = {
        101: {1: 8, 2: 5, 3: 0},
    }
    result = calculate_fulfillment_plan(lines=lines, warehouses=standard_warehouses, availability=availability)
    assert result.strategy == "FULL_COVERAGE"
    assert result.estimated_shipments == 1
    assert result.estimated_shipping_cost == Decimal("10.00")
    assert len(result.allocations) == 1
    assert result.allocations[0].warehouse_id == 1
    assert result.allocations[0].allocated_qty == 4
    assert result.allocations[0].backorder_qty == 0
    assert len(result.backorders) == 0


def test_fulfillment_multi_warehouse_split_canonical_10_laptops(standard_warehouses):
    """Test canonical worked example: 10 laptops split across MAIN (8) and EAST (2), 2 shipments, cost 25."""
    lines = [StockableLine(line_id=1, product_id=101, requested_qty=10)]
    availability = {
        101: {1: 8, 2: 5, 3: 0},
    }
    result = calculate_fulfillment_plan(lines=lines, warehouses=standard_warehouses, availability=availability)
    assert result.strategy == "EXHAUSTIVE"
    assert result.estimated_shipments == 2
    assert result.estimated_shipping_cost == Decimal("25.00")
    assert len(result.allocations) == 2
    alloc_main = next(a for a in result.allocations if a.warehouse_id == 1)
    alloc_east = next(a for a in result.allocations if a.warehouse_id == 2)
    assert alloc_main.allocated_qty == 8
    assert alloc_east.allocated_qty == 2
    assert len(result.backorders) == 0

    # Verify check constraint invariant on all lines
    for a in result.allocations:
        assert a.allocated_qty + a.backorder_qty == a.requested_qty


def test_fulfillment_shortfall_backorder_invariant(standard_warehouses):
    """Test shortfall creates backorder records and fulfills allocated + backorder == requested invariant."""
    lines = [StockableLine(line_id=1, product_id=101, requested_qty=15)]
    availability = {
        101: {1: 8, 2: 5, 3: 0},  # Total network = 13 < 15
    }
    result = calculate_fulfillment_plan(lines=lines, warehouses=standard_warehouses, availability=availability)
    assert result.estimated_shipments == 2
    assert len(result.allocations) == 2
    assert len(result.backorders) == 1

    total_alloc = sum(a.allocated_qty for a in result.allocations)
    total_bo = sum(b.backorder_qty for b in result.backorders)
    assert total_alloc == 13
    assert total_bo == 2
    assert total_alloc + total_bo == 15

    # Check invariant on every single allocation and backorder
    for a in result.allocations:
        assert a.allocated_qty + a.backorder_qty == a.requested_qty
    for b in result.backorders:
        assert b.allocated_qty + b.backorder_qty == b.requested_qty
        assert b.warehouse_id is None


def test_fulfillment_services_and_subscriptions_ignored(standard_warehouses):
    """Test non-stockable products are completely ignored in warehouse fulfillment."""
    lines = []  # No stockable lines passed
    availability = {}
    result = calculate_fulfillment_plan(lines=lines, warehouses=standard_warehouses, availability=availability)
    assert result.strategy == "NO_STOCKABLE_ITEMS"
    assert result.estimated_shipments == 0
    assert result.estimated_shipping_cost == Decimal("0.00")
    assert len(result.allocations) == 0
    assert len(result.backorders) == 0


def test_fulfillment_exhaustive_equals_greedy_small_network(standard_warehouses):
    """Test that for canonical 10 laptops, exhaustive search and greedy produce the same chosen warehouses."""
    lines = [StockableLine(line_id=1, product_id=101, requested_qty=10)]
    availability = {
        101: {1: 8, 2: 5, 3: 0},
    }
    res_exh = calculate_fulfillment_plan(lines=lines, warehouses=standard_warehouses, availability=availability, force_strategy="EXHAUSTIVE")
    res_grd = calculate_fulfillment_plan(lines=lines, warehouses=standard_warehouses, availability=availability, force_strategy="GREEDY")

    assert res_exh.estimated_shipments == res_grd.estimated_shipments == 2
    assert res_exh.estimated_shipping_cost == res_grd.estimated_shipping_cost == Decimal("25.00")
    assert {a.warehouse_id for a in res_exh.allocations} == {a.warehouse_id for a in res_grd.allocations} == {1, 2}


def test_fulfillment_lifecycle_propose_accept_apply(in_memory_db, fake_gateway, seed_users):
    """Test full fulfillment service lifecycle: propose -> accept -> apply to Odoo."""
    rep = seed_users["rep"]
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 10}])
    deal = deal_service.sync_deal_from_odoo(
        db=in_memory_db,
        gateway=fake_gateway,
        odoo_sale_order_id=raw_so.header.id,
        actor_id=rep.odoo_user_id,
        actor_role=rep.role,
    )

    # 1. Propose fulfillment plan
    plan = fulfillment_service.propose_fulfillment_plan(
        db=in_memory_db,
        gateway=fake_gateway,
        deal_id=deal.id,
    )
    assert plan.status == FulfillmentPlanStatus.PROPOSED.value
    assert plan.estimated_shipments == 2
    assert plan.estimated_shipping_cost == Decimal("25.00")
    assert len(plan.lines) == 2

    # Invariant holds in DB
    for l in plan.lines:
        assert l.allocated_qty + l.backorder_qty == l.requested_qty

    # 2. Accept plan
    accepted_plan = fulfillment_service.accept_fulfillment_plan(
        db=in_memory_db,
        deal_id=deal.id,
        plan_id=plan.id,
        actor=rep,
    )
    assert accepted_plan.status == FulfillmentPlanStatus.ACCEPTED.value

    # 3. Cannot apply while deal is still DRAFT
    with pytest.raises(BusinessRuleError) as exc:
        fulfillment_service.apply_fulfillment_plan(
            db=in_memory_db,
            gateway=fake_gateway,
            deal_id=deal.id,
            plan_id=plan.id,
            actor=rep,
        )
    assert exc.value.code == "DEAL_NOT_CONFIRMED"

    # Confirm deal
    deal.status = DealStatus.CONFIRMED.value
    in_memory_db.commit()

    # 4. Apply plan
    apply_res = fulfillment_service.apply_fulfillment_plan(
        db=in_memory_db,
        gateway=fake_gateway,
        deal_id=deal.id,
        plan_id=plan.id,
        actor=rep,
    )
    assert apply_res["plan"].status == FulfillmentPlanStatus.APPLIED.value
    assert len(apply_res["picking_ids"]) == 2
    assert deal.status == DealStatus.IN_FULFILLMENT.value

    # Verify stock in fake gateway was decremented
    # Laptop was 8 in MAIN and 5 in EAST
    avail_after = fake_gateway.get_availability([101], [1, 2])
    assert avail_after[101][1] == 0  # 8 - 8
    assert avail_after[101][2] == 3  # 5 - 2


def test_fulfillment_manual_override(in_memory_db, fake_gateway, seed_users):
    """Test manual fulfillment plan override requires reason and updates allocations."""
    rep = seed_users["rep"]
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 10}])
    deal = deal_service.sync_deal_from_odoo(
        db=in_memory_db,
        gateway=fake_gateway,
        odoo_sale_order_id=raw_so.header.id,
        actor_id=rep.odoo_user_id,
        actor_role=rep.role,
    )
    plan = fulfillment_service.propose_fulfillment_plan(
        db=in_memory_db,
        gateway=fake_gateway,
        deal_id=deal.id,
    )

    so_line_id = raw_so.lines[0].id
    manual_allocations = [
        FulfillmentOverrideLineItem(
            odoo_sale_order_line_id=so_line_id,
            odoo_warehouse_id=1,
            allocated_qty=6,
            backorder_qty=0,
        ),
        FulfillmentOverrideLineItem(
            odoo_sale_order_line_id=so_line_id,
            odoo_warehouse_id=2,
            allocated_qty=4,
            backorder_qty=0,
        ),
    ]

    # Attempt override without reason -> fails
    with pytest.raises(BusinessRuleError) as exc:
        fulfillment_service.override_fulfillment_plan(
            db=in_memory_db,
            gateway=fake_gateway,
            deal_id=deal.id,
            plan_id=plan.id,
            allocations=manual_allocations,
            actor=rep,
            reason="",
        )
    assert exc.value.code == "REASON_REQUIRED"

    # Valid override
    overridden_plan = fulfillment_service.override_fulfillment_plan(
        db=in_memory_db,
        gateway=fake_gateway,
        deal_id=deal.id,
        plan_id=plan.id,
        allocations=manual_allocations,
        actor=rep,
        reason="Manual dispatch preference requested by customer logistics.",
    )
    assert overridden_plan.status == FulfillmentPlanStatus.OVERRIDDEN.value
    assert overridden_plan.strategy == "MANUAL"
    assert overridden_plan.estimated_shipments == 2
    assert overridden_plan.estimated_shipping_cost == Decimal("25.00")


def test_fulfillment_consolidate_backorder(in_memory_db, fake_gateway, seed_users):
    """Test consolidating backorder replans fulfillment when stock becomes available."""
    rep = seed_users["rep"]
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 106, "qty": 15}])
    # Mice: MAIN 2, EAST 2, WEST 10 = total 14 < 15
    deal = deal_service.sync_deal_from_odoo(
        db=in_memory_db,
        gateway=fake_gateway,
        odoo_sale_order_id=raw_so.header.id,
        actor_id=rep.odoo_user_id,
        actor_role=rep.role,
    )
    plan = fulfillment_service.propose_fulfillment_plan(
        db=in_memory_db,
        gateway=fake_gateway,
        deal_id=deal.id,
    )
    bo_lines = [l for l in plan.lines if l.backorder_qty > 0]
    assert len(bo_lines) >= 1

    # Restock MAIN with 10 units
    fake_gateway.stock[106][1] += 10

    # Consolidate backorder
    replanned = fulfillment_service.consolidate_backorder(
        db=in_memory_db,
        gateway=fake_gateway,
        deal_id=deal.id,
        plan_id=plan.id,
        actor=rep,
    )
    assert replanned.id != plan.id
    # Backorder should now be satisfied!
    assert all(l.backorder_qty == 0 for l in replanned.lines)


def test_fulfillment_api_endpoints(client, in_memory_db, fake_gateway, seed_users):
    """Test full REST API suite for deal fulfillment and warehouse profiles."""
    admin = seed_users["admin"]
    token = security.create_access_token(
        subject=str(admin.odoo_user_id),
        role=admin.role,
        company_id="1",
        audience="internal",
    )
    headers = {"Authorization": f"Bearer {token}"}

    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 10}])
    deal = deal_service.sync_deal_from_odoo(
        db=in_memory_db,
        gateway=fake_gateway,
        odoo_sale_order_id=raw_so.header.id,
        actor_id=admin.odoo_user_id,
        actor_role=admin.role,
    )

    # 1. POST /api/v1/deals/{id}/fulfillment/propose
    res_prop = client.post(f"/api/v1/deals/{deal.id}/fulfillment/propose", headers=headers)
    assert res_prop.status_code == 200
    plan_data = res_prop.json()["data"]
    plan_id = plan_data["id"]
    assert plan_data["status"] == "PROPOSED"
    assert plan_data["estimated_shipments"] == 2

    # 2. GET /api/v1/deals/{id}/fulfillment
    res_get = client.get(f"/api/v1/deals/{deal.id}/fulfillment", headers=headers)
    assert res_get.status_code == 200
    f_bundle = res_get.json()["data"]
    assert f_bundle["plan"]["id"] == plan_id

    # 3. POST /api/v1/deals/{id}/fulfillment/{plan_id}/accept
    res_accept = client.post(f"/api/v1/deals/{deal.id}/fulfillment/{plan_id}/accept", headers=headers)
    assert res_accept.status_code == 200
    assert res_accept.json()["data"]["status"] == "ACCEPTED"

    # 4. POST /api/v1/deals/{id}/fulfillment/{plan_id}/override
    so_line_id = raw_so.lines[0].id
    res_override = client.post(
        f"/api/v1/deals/{deal.id}/fulfillment/{plan_id}/override",
        headers=headers,
        json={
            "lines": [
                {"odoo_sale_order_line_id": so_line_id, "odoo_warehouse_id": 1, "allocated_qty": 7, "backorder_qty": 0},
                {"odoo_sale_order_line_id": so_line_id, "odoo_warehouse_id": 2, "allocated_qty": 3, "backorder_qty": 0},
            ],
            "reason": "Logistics optimization override",
        },
    )
    assert res_override.status_code == 200
    assert res_override.json()["data"]["status"] == "OVERRIDDEN"

    # 5. POST /api/v1/deals/{id}/fulfillment/{plan_id}/apply
    # Confirm deal first
    deal.status = DealStatus.CONFIRMED.value
    in_memory_db.commit()

    res_apply = client.post(f"/api/v1/deals/{deal.id}/fulfillment/{plan_id}/apply", headers=headers)
    assert res_apply.status_code == 200
    assert len(res_apply.json()["data"]["picking_ids"]) == 2

    # 6. Warehouse Profiles CRUD
    # List
    res_wh = client.get("/api/v1/warehouse-profiles", headers=headers)
    assert res_wh.status_code == 200
    profiles = res_wh.json()["data"]
    assert len(profiles) >= 3

    # Create
    res_create_wh = client.post(
        "/api/v1/warehouse-profiles",
        headers=headers,
        json={
            "odoo_warehouse_id": 99,
            "shipping_cost_weight": 30.0,
            "priority": 4,
            "lead_time_days": 2,
            "active": True,
        },
    )
    assert res_create_wh.status_code == 201
    wh_profile_id = res_create_wh.json()["data"]["id"]

    # Get
    res_get_wh = client.get(f"/api/v1/warehouse-profiles/{wh_profile_id}", headers=headers)
    assert res_get_wh.status_code == 200
    assert res_get_wh.json()["data"]["priority"] == 4

    # Patch
    res_patch_wh = client.patch(
        f"/api/v1/warehouse-profiles/{wh_profile_id}",
        headers=headers,
        json={"shipping_cost_weight": 35.0},
    )
    assert res_patch_wh.status_code == 200
    assert float(res_patch_wh.json()["data"]["shipping_cost_weight"]) == 35.0

    # Delete
    res_del_wh = client.delete(f"/api/v1/warehouse-profiles/{wh_profile_id}", headers=headers)
    assert res_del_wh.status_code == 200
