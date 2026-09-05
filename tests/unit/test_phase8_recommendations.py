from decimal import Decimal
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import security
from app.core.deps import get_db, get_odoo_gateway
from app.db.base import Base
from app.guardian.evaluator import evaluate_deal
from app.guardian.recommend import evaluate_recommendations
from app.main import app
from app.models.deal import Deal
from app.models.enums import ApprovalLevel, ApprovalState, DealStatus, RecommendationStatus, Role
from app.models.identity import DealflowUser
from app.models.recommendation import Recommendation, RecommendationRule
from app.odoo.fake import FakeOdooGateway
from app.odoo.interface import RawProduct
from app.schemas.recommendation import RecommendationRuleCreate, RecommendationRuleUpdate
from app.services import deal_service, recommendation_service


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
    admin = DealflowUser(
        odoo_user_id=1,
        login="admin@dealflow.test",
        name="Admin User",
        role=Role.ADMIN.value,
        is_active=True,
    )
    in_memory_db.add_all([rep, manager, admin])
    in_memory_db.commit()
    return {"rep": rep, "manager": manager, "admin": admin}


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
def seed_rules(in_memory_db):
    """Seed the standard canonical rules from §12."""
    rules = [
        RecommendationRule(
            odoo_company_id=1,
            trigger_product_id=101,  # Laptop
            suggested_product_id=104,  # Docking Station
            co_purchase_count=35,
            is_promoted_override=True,
            active=True,
        ),
        RecommendationRule(
            odoo_company_id=1,
            trigger_product_id=101,  # Laptop
            suggested_product_id=105,  # Laptop Bag
            co_purchase_count=42,
            active=True,
        ),
        RecommendationRule(
            odoo_company_id=1,
            trigger_product_id=101,  # Laptop
            suggested_product_id=109,  # Premium Support
            co_purchase_count=30,
            active=True,
        ),
        RecommendationRule(
            odoo_company_id=1,
            trigger_product_id=101,  # Laptop
            suggested_product_id=106,  # Wireless Mouse
            co_purchase_count=12,
            manual_boost=Decimal("0.20"),
            active=True,
        ),
        RecommendationRule(
            odoo_company_id=1,
            trigger_product_id=107,  # Setup Service
            suggested_product_id=108,  # Training Day
            co_purchase_count=9,
            active=True,
        ),
    ]
    in_memory_db.add_all(rules)
    in_memory_db.commit()
    return rules


def test_recommendation_math_and_filtering(seed_rules):
    """Test pure-Python recommendation evaluation: candidate selection, score formula, margin filtering, and explanations."""
    class DummyLine:
        def __init__(self, pid, name, cat_id, cat_path, price, qty):
            self.product_id = pid
            self.product_name = name
            self.category_id = cat_id
            self.category_path = cat_path
            self.price_unit = price
            self.qty = qty

    deal_lines = [
        DummyLine(101, "Laptop Pro 14\"", 1, [1], Decimal("50000.00"), Decimal("1.0")),
    ]

    # Mock catalog
    products_map = {
        101: RawProduct(101, "Laptop Pro 14\"", 1, [1], "STOCKABLE", 35000.0, 50000.0),
        104: RawProduct(104, "Docking Station", 4, [4], "STOCKABLE", 3000.0, 6000.0, tags=["DealFlow Promo"]),
        105: RawProduct(105, "Laptop Bag", 4, [4], "STOCKABLE", 750.0, 2000.0),
        106: RawProduct(106, "Wireless Mouse", 4, [4], "STOCKABLE", 400.0, 1200.0),
        109: RawProduct(109, "Premium Support", 3, [3], "SUBSCRIPTION", 6000.0, 20000.0),
    }

    prices_map = {
        104: Decimal("6000.00"),
        105: Decimal("2000.00"),
        106: Decimal("1200.00"),
        109: Decimal("20000.00"),
    }

    candidates = evaluate_recommendations(
        deal_lines=deal_lines,
        deal_amount_untaxed=Decimal("50000.00"),
        deal_total_cost=Decimal("35000.00"),
        deal_total_margin_pct=Decimal("30.00"),
        rules=seed_rules,
        products_map=products_map,
        prices_map=prices_map,
        dismissed_product_ids=set(),
    )

    assert len(candidates) > 0
    # Docking station should rank high due to promo + high co-purchase
    top_cand = candidates[0]
    assert top_cand.odoo_product_id in (104, 105, 109)
    assert top_cand.score > Decimal("0.5")
    assert "projected margin" in top_cand.reason
    assert "Frequently bought with" in top_cand.reason

    # Test that dismissed product is excluded
    dismissed_candidates = evaluate_recommendations(
        deal_lines=deal_lines,
        deal_amount_untaxed=Decimal("50000.00"),
        deal_total_cost=Decimal("35000.00"),
        deal_total_margin_pct=Decimal("30.00"),
        rules=seed_rules,
        products_map=products_map,
        prices_map=prices_map,
        dismissed_product_ids={104},  # Docking station dismissed
    )
    pids = [c.odoo_product_id for c in dismissed_candidates]
    assert 104 not in pids


def test_recommendation_lifecycle_add_and_dismiss(in_memory_db, fake_gateway, seed_users, seed_rules):
    """Test evaluating deal recommendations, dismissing one, and adding another to the deal."""
    rep = seed_users["rep"]
    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 2}])
    so_id = raw_so.header.id

    # Sync and evaluate deal
    deal = deal_service.sync_deal_from_odoo(
        db=in_memory_db,
        gateway=fake_gateway,
        odoo_sale_order_id=so_id,
        actor_id=rep.odoo_user_id,
        actor_role=rep.role,
    )
    eval_res = evaluate_deal(
        db=in_memory_db,
        gateway=fake_gateway,
        deal_id=deal.id,
        actor_id=rep.odoo_user_id,
        actor_role=rep.role,
    )

    # Verify recommendations were generated and persisted
    recs = recommendation_service.list_deal_recommendations(db=in_memory_db, deal_id=deal.id)
    assert len(recs) >= 1
    pids = [r.odoo_product_id for r in recs]
    assert 104 in pids or 105 in pids

    # 1. Dismiss a recommendation (e.g. 105 Laptop Bag)
    rec_to_dismiss = next((r for r in recs if r.odoo_product_id == 105), recs[0])
    dismiss_res = recommendation_service.apply_recommendation_action(
        db=in_memory_db,
        gateway=fake_gateway,
        deal_id=deal.id,
        recommendation_id=rec_to_dismiss.id,
        actor=rep,
        action="DISMISS",
    )
    assert dismiss_res["status"] == "dismissed"
    in_memory_db.refresh(rec_to_dismiss)
    assert rec_to_dismiss.status == RecommendationStatus.DISMISSED.value

    # Re-evaluating must NOT bring back the dismissed product
    recs_after_dismiss = recommendation_service.generate_and_persist_recommendations(
        db=in_memory_db,
        gateway=fake_gateway,
        deal=deal,
        risk_assessment_id=eval_res.risk_assessment.id,
    )
    assert rec_to_dismiss.odoo_product_id not in [r.odoo_product_id for r in recs_after_dismiss]

    # 2. Add an active recommendation to the deal (e.g. Docking Station 104)
    rec_to_add = next(r for r in recs_after_dismiss if r.odoo_product_id == 104)
    old_amount = deal.amount_total_cache

    add_res = recommendation_service.apply_recommendation_action(
        db=in_memory_db,
        gateway=fake_gateway,
        deal_id=deal.id,
        recommendation_id=rec_to_add.id,
        actor=rep,
        action="ADD",
        quantity=Decimal("1.0"),
    )

    in_memory_db.refresh(deal)
    in_memory_db.refresh(rec_to_add)
    assert rec_to_add.status == RecommendationStatus.ADDED.value

    # Deal total and lines must have updated
    assert deal.amount_total_cache > old_amount
    updated_order = fake_gateway.get_sale_order(so_id)
    assert any(l.product_id == 104 for l in updated_order.lines)


def test_co_purchase_mining_job(in_memory_db, fake_gateway):
    """Test that mining job analyzes historical confirmed orders and upserts co-purchase counts without losing manual boosts."""
    # Create an initial rule with manual boost
    initial_rule = RecommendationRule(
        odoo_company_id=1,
        trigger_product_id=101,
        suggested_product_id=106,
        co_purchase_count=5,
        manual_boost=Decimal("0.25"),
        source="MANUAL",
        active=True,
    )
    in_memory_db.add(initial_rule)
    in_memory_db.commit()

    # Run mining job
    mining_res = recommendation_service.mine_co_purchase_rules(
        db=in_memory_db,
        gateway=fake_gateway,
        company_id=1,
        lookback_days=365,
    )
    assert mining_res["status"] == "completed"
    assert mining_res["orders_analyzed"] >= 1

    in_memory_db.refresh(initial_rule)
    # manual boost should NOT be wiped
    assert initial_rule.manual_boost == Decimal("0.25")
    assert initial_rule.source == "MINED"

    # New rules must have been mined from fake gateway historical orders
    rules = in_memory_db.query(RecommendationRule).filter(RecommendationRule.odoo_company_id == 1).all()
    assert len(rules) >= 4


def test_recommendation_api_endpoints(client, in_memory_db, fake_gateway, seed_users, seed_rules):
    """Test full REST API suite for recommendations and recommendation rules."""
    manager = seed_users["manager"]
    token = security.create_access_token(
        subject=str(manager.odoo_user_id),
        role=manager.role,
        company_id="1",
        audience="internal",
    )
    headers = {"Authorization": f"Bearer {token}"}

    raw_so = fake_gateway.create_order(partner_id=1, lines=[{"product_id": 101, "qty": 1}])
    deal = deal_service.sync_deal_from_odoo(
        db=in_memory_db,
        gateway=fake_gateway,
        odoo_sale_order_id=raw_so.header.id,
        actor_id=manager.odoo_user_id,
        actor_role=manager.role,
    )
    evaluate_deal(db=in_memory_db, gateway=fake_gateway, deal_id=deal.id)

    # 1. GET /api/v1/deals/{deal_id}/recommendations
    res = client.get(f"/api/v1/deals/{deal.id}/recommendations", headers=headers)
    assert res.status_code == 200
    recs = res.json()["data"]
    assert len(recs) >= 1
    rec_id = recs[0]["id"]

    # 2. POST /api/v1/deals/{deal_id}/recommendations/{rid}/add
    res_add = client.post(
        f"/api/v1/deals/{deal.id}/recommendations/{rec_id}/add",
        headers=headers,
        json={"action": "ADD", "quantity": 1.0},
    )
    assert res_add.status_code == 200
    workspace = res_add.json()["data"]
    assert "deal" in workspace
    assert "lines" in workspace
    assert "recommendations" in workspace

    # 3. Recommendation rules CRUD
    # List
    res_list_rules = client.get("/api/v1/recommendation-rules", headers=headers)
    assert res_list_rules.status_code == 200
    assert len(res_list_rules.json()["data"]) >= 1

    # Create manual rule
    res_create = client.post(
        "/api/v1/recommendation-rules",
        headers=headers,
        json={
            "odoo_company_id": 1,
            "trigger_product_id": 103,
            "suggested_product_id": 105,
            "co_purchase_count": 15,
            "manual_boost": 0.15,
            "source": "MANUAL",
            "active": True,
        },
    )
    assert res_create.status_code == 200
    rule_id = res_create.json()["data"]["id"]

    # Get rule
    res_get = client.get(f"/api/v1/recommendation-rules/{rule_id}", headers=headers)
    assert res_get.status_code == 200
    assert res_get.json()["data"]["co_purchase_count"] == 15

    # Patch rule
    res_patch = client.patch(
        f"/api/v1/recommendation-rules/{rule_id}",
        headers=headers,
        json={"co_purchase_count": 25, "active": True},
    )
    assert res_patch.status_code == 200
    assert res_patch.json()["data"]["co_purchase_count"] == 25

    # Delete rule (Requires ADMIN)
    admin = seed_users["admin"]
    admin_token = security.create_access_token(
        subject=str(admin.odoo_user_id),
        role=admin.role,
        company_id="1",
        audience="internal",
    )
    res_del = client.delete(f"/api/v1/recommendation-rules/{rule_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert res_del.status_code == 200
