from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sys
from typing import Optional
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.db.session import SessionLocal
from app.guardian import evaluator
from app.models.cross_cutting import AppSetting, NumberSequence
from app.models.deal import Deal
from app.models.enums import (
    AlertStatus,
    AlertType,
    ApprovalLevel,
    ApprovalState,
    DealStatus,
    HealthStatus,
    RiskSeverity,
    Role,
)
from app.models.fulfillment import WarehouseProfile
from app.models.health import DealAlert, RepDiscountStats
from app.models.identity import DealflowUser
from app.models.policy import CustomerTier, CustomerTierAssignment, DiscountPolicy
from app.models.recommendation import RecommendationRule
from app.odoo import get_gateway
from app.odoo.fake import FakeOdooGateway
from app.odoo.interface import OdooGateway
from app.services import deal_service, health_service


def seed_policies_and_config(db: Session) -> None:
    """Idempotently seeds tiers, policies, warehouse profiles, recommendation rules, settings and users."""
    # 1. Customer Tiers
    tiers = [
        {"code": "BRONZE", "name": "Bronze Tier", "rank": 1, "default_max_discount_pct": Decimal("5.00")},
        {"code": "SILVER", "name": "Silver Tier", "rank": 2, "default_max_discount_pct": Decimal("10.00")},
        {"code": "GOLD", "name": "Gold Tier", "rank": 3, "default_max_discount_pct": Decimal("15.00")},
    ]
    for t in tiers:
        existing = db.query(CustomerTier).filter(CustomerTier.code == t["code"]).first()
        if not existing:
            db.add(CustomerTier(**t))
        else:
            existing.default_max_discount_pct = t["default_max_discount_pct"]

    # 2. Tier Assignments for Acme (1: GOLD), Beta (2: SILVER), Gamma (3: BRONZE)
    assignments = [
        {"odoo_partner_id": 1, "tier_code": "GOLD"},
        {"odoo_partner_id": 2, "tier_code": "SILVER"},
        {"odoo_partner_id": 3, "tier_code": "BRONZE"},
    ]
    for a in assignments:
        existing = db.query(CustomerTierAssignment).filter(CustomerTierAssignment.odoo_partner_id == a["odoo_partner_id"]).first()
        if not existing:
            db.add(CustomerTierAssignment(**a))
        else:
            existing.tier_code = a["tier_code"]

    # 3. Discount Policies
    policies = [
        # Global fallback (NULL tier, NULL category)
        {
            "name": "Global Baseline Policy",
            "customer_tier_code": None,
            "odoo_product_category_id": None,
            "max_discount_pct": Decimal("5.00"),
            "minimum_margin_pct": Decimal("20.00"),
            "manager_threshold": Decimal("20.00"),
            "finance_threshold": Decimal("50.00"),
            "single_line_finance_pts": Decimal("8.00"),
            "priority": 1,
        },
        # Tier policies
        {
            "name": "Bronze Tier Policy",
            "customer_tier_code": "BRONZE",
            "odoo_product_category_id": None,
            "max_discount_pct": Decimal("5.00"),
            "minimum_margin_pct": Decimal("20.00"),
            "manager_threshold": Decimal("20.00"),
            "finance_threshold": Decimal("50.00"),
            "single_line_finance_pts": Decimal("8.00"),
            "priority": 10,
        },
        {
            "name": "Silver Tier Policy",
            "customer_tier_code": "SILVER",
            "odoo_product_category_id": None,
            "max_discount_pct": Decimal("10.00"),
            "minimum_margin_pct": Decimal("20.00"),
            "manager_threshold": Decimal("20.00"),
            "finance_threshold": Decimal("50.00"),
            "single_line_finance_pts": Decimal("8.00"),
            "priority": 10,
        },
        {
            "name": "Gold Tier Policy",
            "customer_tier_code": "GOLD",
            "odoo_product_category_id": None,
            "max_discount_pct": Decimal("15.00"),
            "minimum_margin_pct": Decimal("20.00"),
            "manager_threshold": Decimal("20.00"),
            "finance_threshold": Decimal("50.00"),
            "single_line_finance_pts": Decimal("8.00"),
            "priority": 10,
        },
        # Category policies
        {
            "name": "Hardware Category Policy",
            "customer_tier_code": None,
            "odoo_product_category_id": 1,
            "max_discount_pct": Decimal("15.00"),
            "minimum_margin_pct": Decimal("20.00"),
            "manager_threshold": Decimal("20.00"),
            "finance_threshold": Decimal("50.00"),
            "single_line_finance_pts": Decimal("8.00"),
            "priority": 20,
        },
        {
            "name": "Services Category Policy",
            "customer_tier_code": None,
            "odoo_product_category_id": 2,
            "max_discount_pct": Decimal("10.00"),
            "minimum_margin_pct": Decimal("25.00"),
            "manager_threshold": Decimal("20.00"),
            "finance_threshold": Decimal("50.00"),
            "single_line_finance_pts": Decimal("8.00"),
            "priority": 20,
        },
        {
            "name": "Subscriptions Category Policy",
            "customer_tier_code": None,
            "odoo_product_category_id": 3,
            "max_discount_pct": Decimal("12.00"),
            "minimum_margin_pct": Decimal("20.00"),
            "manager_threshold": Decimal("20.00"),
            "finance_threshold": Decimal("50.00"),
            "single_line_finance_pts": Decimal("8.00"),
            "priority": 20,
        },
        {
            "name": "Accessories Category Policy",
            "customer_tier_code": None,
            "odoo_product_category_id": 4,
            "max_discount_pct": Decimal("20.00"),
            "minimum_margin_pct": Decimal("20.00"),
            "manager_threshold": Decimal("20.00"),
            "finance_threshold": Decimal("50.00"),
            "single_line_finance_pts": Decimal("8.00"),
            "priority": 20,
        },
        # Combined row (precedence demonstration: GOLD + Services)
        {
            "name": "Gold Tier Services Policy",
            "customer_tier_code": "GOLD",
            "odoo_product_category_id": 2,
            "max_discount_pct": Decimal("10.00"),
            "minimum_margin_pct": Decimal("25.00"),
            "manager_threshold": Decimal("20.00"),
            "finance_threshold": Decimal("50.00"),
            "single_line_finance_pts": Decimal("8.00"),
            "priority": 30,
        },
    ]
    for p in policies:
        existing = (
            db.query(DiscountPolicy)
            .filter(
                DiscountPolicy.customer_tier_code == p["customer_tier_code"],
                DiscountPolicy.odoo_product_category_id == p["odoo_product_category_id"],
            )
            .first()
        )
        if not existing:
            db.add(DiscountPolicy(**p))
        else:
            existing.max_discount_pct = p["max_discount_pct"]
            existing.minimum_margin_pct = p["minimum_margin_pct"]
            existing.priority = p["priority"]

    # 4. Warehouse Profiles (Main: 1, East: 2, West: 3)
    wh_profiles = [
        {"odoo_warehouse_id": 1, "shipping_cost_weight": Decimal("10.00"), "priority": 1, "lead_time_days": 0},
        {"odoo_warehouse_id": 2, "shipping_cost_weight": Decimal("15.00"), "priority": 2, "lead_time_days": 1},
        {"odoo_warehouse_id": 3, "shipping_cost_weight": Decimal("25.00"), "priority": 3, "lead_time_days": 2},
    ]
    for wh in wh_profiles:
        existing = db.query(WarehouseProfile).filter(WarehouseProfile.odoo_warehouse_id == wh["odoo_warehouse_id"]).first()
        if not existing:
            db.add(WarehouseProfile(**wh))
        else:
            existing.shipping_cost_weight = wh["shipping_cost_weight"]
            existing.priority = wh["priority"]
            existing.lead_time_days = wh["lead_time_days"]

    # 5. Recommendation Rules
    rules = [
        # Laptop 14" (101) -> Docking Station (104): 35, promoted
        {"trigger_product_id": 101, "suggested_product_id": 104, "co_purchase_count": 35, "is_promoted_override": True, "manual_boost": Decimal("0.00")},
        # Laptop 14" (101) -> Laptop Bag (105): 42
        {"trigger_product_id": 101, "suggested_product_id": 105, "co_purchase_count": 42, "is_promoted_override": False, "manual_boost": Decimal("0.00")},
        # Laptop 14" (101) -> Premium Support (109): 30
        {"trigger_product_id": 101, "suggested_product_id": 109, "co_purchase_count": 30, "is_promoted_override": False, "manual_boost": Decimal("0.00")},
        # Monitor 27" (103) -> Docking Station (104): 18
        {"trigger_product_id": 103, "suggested_product_id": 104, "co_purchase_count": 18, "is_promoted_override": False, "manual_boost": Decimal("0.00")},
        # Laptop 14" (101) -> Wireless Mouse (106): 12, boost 0.2
        {"trigger_product_id": 101, "suggested_product_id": 106, "co_purchase_count": 12, "is_promoted_override": False, "manual_boost": Decimal("0.20")},
        # Setup Service (107) -> Training Day (108): 9
        {"trigger_product_id": 107, "suggested_product_id": 108, "co_purchase_count": 9, "is_promoted_override": False, "manual_boost": Decimal("0.00")},
    ]
    for r in rules:
        existing = (
            db.query(RecommendationRule)
            .filter(
                RecommendationRule.trigger_product_id == r["trigger_product_id"],
                RecommendationRule.suggested_product_id == r["suggested_product_id"],
            )
            .first()
        )
        if not existing:
            db.add(RecommendationRule(**r))
        else:
            existing.co_purchase_count = r["co_purchase_count"]
            existing.is_promoted_override = r["is_promoted_override"]
            existing.manual_boost = r["manual_boost"]

    # 6. Default App Settings
    settings = [
        {"key": "stalled_days_threshold", "value_json": 7, "description": "Days of inactivity to trigger STALLED_DEAL alert"},
        {"key": "anomaly_stddev_threshold", "value_json": 2.0, "description": "Standard deviations for discount anomaly detection"},
        {"key": "default_currency", "value_json": "INR", "description": "Default base currency"},
        {"key": "approval_sla_hours", "value_json": 24, "description": "SLA hours per approval stage"},
    ]
    for s in settings:
        existing = db.query(AppSetting).filter(AppSetting.key == s["key"]).first()
        if not existing:
            db.add(AppSetting(**s))

    # 7. Number Sequence
    seq = db.query(NumberSequence).filter(NumberSequence.key == "DEAL").first()
    if not seq:
        db.add(NumberSequence(key="DEAL", next_value=1001))

    # 8. Mirror Dealflow Users
    users = [
        {"odoo_user_id": 1, "login": "admin@dealflow.test", "name": "System Admin", "role": Role.ADMIN.value, "sales_team_odoo_id": None},
        {"odoo_user_id": 2, "login": "manager1@dealflow.test", "name": "Sales Manager North", "role": Role.SALES_MANAGER.value, "sales_team_odoo_id": 1},
        {"odoo_user_id": 3, "login": "manager2@dealflow.test", "name": "Sales Manager South", "role": Role.SALES_MANAGER.value, "sales_team_odoo_id": 2},
        {"odoo_user_id": 4, "login": "rep1@dealflow.test", "name": "Sales Rep One", "role": Role.SALES_REP.value, "sales_team_odoo_id": 1},
        {"odoo_user_id": 5, "login": "rep2@dealflow.test", "name": "Sales Rep Two", "role": Role.SALES_REP.value, "sales_team_odoo_id": 2},
        {"odoo_user_id": 6, "login": "finance@dealflow.test", "name": "Finance Officer", "role": Role.FINANCE.value, "sales_team_odoo_id": None},
    ]
    for u in users:
        existing = db.query(DealflowUser).filter(DealflowUser.odoo_user_id == u["odoo_user_id"]).first()
        if not existing:
            db.add(DealflowUser(**u))
        else:
            existing.role = u["role"]
            existing.sales_team_odoo_id = u["sales_team_odoo_id"]

    db.commit()


def seed_deals(db: Session, gateway: Optional[OdooGateway] = None) -> None:
    """Imports existing sale orders from Odoo into Deal rows and runs an initial evaluation for each."""
    if not gateway:
        gateway = get_gateway()

    # If FakeOdooGateway has no orders yet, seed canonical initial orders
    if isinstance(gateway, FakeOdooGateway) and not gateway.orders:
        # Order 1: Acme with Laptop Pro 14" (qty 2, disc 5%)
        gateway.create_order(partner_id=1, user_id=4, team_id=1, lines=[{"product_id": 101, "qty": 2, "discount_pct": 5.0}])
        # Order 2: Beta with Monitor 27" (qty 4, disc 10%)
        gateway.create_order(partner_id=2, user_id=5, team_id=2, lines=[{"product_id": 103, "qty": 4, "discount_pct": 10.0}])
        # Order 3: Gamma with Wireless Mouse (qty 12, disc 0%)
        gateway.create_order(partner_id=3, user_id=4, team_id=1, lines=[{"product_id": 106, "qty": 12, "discount_pct": 0.0}])

    headers = gateway.list_sale_orders()
    for h in headers:
        deal = deal_service.sync_deal_from_odoo(
            db=db,
            gateway=gateway,
            odoo_sale_order_id=h.id,
            actor_id=h.user_id,
            actor_role=Role.SALES_REP.value,
        )
        # Evaluate deal
        evaluator.evaluate_deal(
            db=db,
            gateway=gateway,
            deal_id=deal.id,
            odoo_sale_order_id=h.id,
            trigger_type="SYNC",
            actor_id=h.user_id,
            actor_role=Role.SALES_REP.value,
        )

    db.commit()


def seed_history(db: Session, gateway: Optional[OdooGateway] = None) -> None:
    """Generates 12 historical confirmed orders for rep1, 6 for rep2, a stalled SENT deal, and a slipping deal."""
    if not gateway:
        gateway = get_gateway()

    now = utc_now()

    # 1. Rep 1 historical orders (12 orders, avg ~5.5% discount)
    rep1_discounts = [4.0, 5.0, 6.0, 5.0, 7.0, 5.0, 6.0, 5.5, 4.5, 6.5, 5.0, 5.5]
    for idx, disc in enumerate(rep1_discounts, 1):
        so_id = 8000 + idx
        if isinstance(gateway, FakeOdooGateway):
            gateway.order_seq = so_id - 1
            gateway.create_order(partner_id=1, user_id=4, team_id=1, lines=[{"product_id": 101, "qty": 1, "discount_pct": disc}])
            gateway.confirm(so_id)

        deal = Deal(
            reference=f"D-HIST-R1-{idx:02d}",
            odoo_sale_order_id=so_id,
            odoo_order_name=f"SO{so_id}",
            odoo_partner_id=1,
            partner_name_cache="Acme Corp",
            owner_odoo_user_id=4,
            sales_team_odoo_id=1,
            status=DealStatus.CONFIRMED.value,
            approval_state=ApprovalState.APPROVED.value,
            health_status=HealthStatus.HEALTHY.value,
            amount_total_cache=Decimal("47500.00"),
            order_discount_pct=Decimal(str(disc)),
            margin_pct_cache=Decimal("26.00"),
            confirmed_at=now - timedelta(days=idx * 3),
            created_at=now - timedelta(days=idx * 3 + 2),
        )
        db.add(deal)

    # 2. Rep 2 historical orders (6 orders, avg ~8.0% discount)
    rep2_discounts = [7.0, 8.0, 9.0, 8.5, 7.5, 8.0]
    for idx, disc in enumerate(rep2_discounts, 1):
        so_id = 9000 + idx
        if isinstance(gateway, FakeOdooGateway):
            gateway.order_seq = so_id - 1
            gateway.create_order(partner_id=2, user_id=5, team_id=2, lines=[{"product_id": 103, "qty": 2, "discount_pct": disc}])
            gateway.confirm(so_id)

        deal = Deal(
            reference=f"D-HIST-R2-{idx:02d}",
            odoo_sale_order_id=so_id,
            odoo_order_name=f"SO{so_id}",
            odoo_partner_id=2,
            partner_name_cache="Beta Industries",
            owner_odoo_user_id=5,
            sales_team_odoo_id=2,
            status=DealStatus.CONFIRMED.value,
            approval_state=ApprovalState.APPROVED.value,
            health_status=HealthStatus.HEALTHY.value,
            amount_total_cache=Decimal("38000.00"),
            order_discount_pct=Decimal(str(disc)),
            margin_pct_cache=Decimal("22.00"),
            confirmed_at=now - timedelta(days=idx * 4),
            created_at=now - timedelta(days=idx * 4 + 2),
        )
        db.add(deal)

    # 3. One Stalled Deal (SENT, 12 days ago without activity)
    stalled_so_id = 9991
    if isinstance(gateway, FakeOdooGateway):
        gateway.order_seq = stalled_so_id - 1
        gateway.create_order(partner_id=2, user_id=5, team_id=2, lines=[{"product_id": 101, "qty": 3, "discount_pct": 10.0}])

    stalled_deal = Deal(
        reference="D-STALLED-01",
        odoo_sale_order_id=stalled_so_id,
        odoo_order_name=f"SO{stalled_so_id}",
        odoo_partner_id=2,
        partner_name_cache="Beta Industries",
        owner_odoo_user_id=5,
        sales_team_odoo_id=2,
        status=DealStatus.SENT.value,
        approval_state=ApprovalState.APPROVED.value,
        health_status=HealthStatus.WATCH.value,
        current_risk_score=Decimal("35.00"),
        current_severity=RiskSeverity.MEDIUM.value,
        amount_total_cache=Decimal("135000.00"),
        order_discount_pct=Decimal("10.00"),
        margin_pct_cache=Decimal("22.00"),
        sent_at=now - timedelta(days=12),
        last_activity_at=now - timedelta(days=12),
        created_at=now - timedelta(days=14),
    )
    db.add(stalled_deal)
    db.flush()

    # Add stalled alert
    db.add(
        DealAlert(
            deal_id=stalled_deal.id,
            type=AlertType.STALLED_DEAL.value,
            severity=RiskSeverity.MEDIUM.value,
            status=AlertStatus.OPEN.value,
            title="Deal stalled: 12 days since last activity",
            raised_at=now - timedelta(days=5),
        )
    )

    # 4. One Slipping Deal (CONFIRMED, promised delivery date in past)
    slipping_so_id = 9992
    if isinstance(gateway, FakeOdooGateway):
        gateway.order_seq = slipping_so_id - 1
        gateway.create_order(partner_id=1, user_id=4, team_id=1, lines=[{"product_id": 101, "qty": 5, "discount_pct": 5.0}])
        gateway.confirm(slipping_so_id)

    slipping_deal = Deal(
        reference="D-SLIPPING-01",
        odoo_sale_order_id=slipping_so_id,
        odoo_order_name=f"SO{slipping_so_id}",
        odoo_partner_id=1,
        partner_name_cache="Acme Corp",
        owner_odoo_user_id=4,
        sales_team_odoo_id=1,
        status=DealStatus.CONFIRMED.value,
        approval_state=ApprovalState.APPROVED.value,
        health_status=HealthStatus.AT_RISK.value,
        current_risk_score=Decimal("65.00"),
        current_severity=RiskSeverity.HIGH.value,
        amount_total_cache=Decimal("237500.00"),
        order_discount_pct=Decimal("5.00"),
        margin_pct_cache=Decimal("26.00"),
        promised_delivery_date=now - timedelta(days=3),
        confirmed_at=now - timedelta(days=10),
        created_at=now - timedelta(days=12),
    )
    db.add(slipping_deal)
    db.flush()

    # Add delivery slippage alert
    db.add(
        DealAlert(
            deal_id=slipping_deal.id,
            type=AlertType.DELIVERY_SLIPPAGE.value,
            severity=RiskSeverity.HIGH.value,
            status=AlertStatus.OPEN.value,
            title="Promised delivery date slipped by 3 days",
            raised_at=now - timedelta(days=2),
        )
    )

    # 5. One Anomaly alert on rep2
    db.add(
        DealAlert(
            deal_id=stalled_deal.id,
            type=AlertType.DISCOUNT_ANOMALY.value,
            severity=RiskSeverity.HIGH.value,
            status=AlertStatus.OPEN.value,
            title="Discount anomaly: 18.5% exceeds baseline threshold",
            raised_at=now - timedelta(days=1),
        )
    )

    # 6. Precompute RepDiscountStats
    health_service.calculate_rep_discount_baseline(db, odoo_user_id=4, sales_team_id=1)
    health_service.calculate_rep_discount_baseline(db, odoo_user_id=5, sales_team_id=2)

    db.commit()


def seed_all(db: Session, gateway: Optional[OdooGateway] = None) -> None:
    seed_policies_and_config(db)
    seed_deals(db, gateway)
    seed_history(db, gateway)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "seed"
    db = SessionLocal()
    try:
        gw = get_gateway()
        if action in ("seed", "make-seed"):
            print("Running seed_policies_and_config...")
            seed_policies_and_config(db)
            print("Seed complete.")
        elif action in ("seed-deals", "make-seed-deals"):
            print("Running seed_deals...")
            seed_deals(db, gw)
            print("Seed deals complete.")
        elif action in ("seed-history", "make-seed-history"):
            print("Running seed_history...")
            seed_history(db, gw)
            print("Seed history complete.")
        elif action == "all":
            print("Running seed_all...")
            seed_all(db, gw)
            print("Seed all complete.")
        else:
            print(f"Unknown action '{action}'. Options: seed, seed-deals, seed-history, all")
    finally:
        db.close()
