from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional
import uuid
from sqlalchemy.orm import Session
from app.models.policy import DiscountPolicy


@dataclass
class ResolvedPolicy:
    policy_id: Optional[uuid.UUID]
    name: str
    max_discount_pct: Decimal
    minimum_margin_pct: Decimal
    manager_threshold: Decimal
    finance_threshold: Decimal
    single_line_finance_pts: Decimal
    version: str = "v1"


def resolve_policy_for_line(
    db: Session,
    company_id: int,
    tier_code: str,
    category_id: Optional[int],
    tier_default_ceiling: Decimal = Decimal("5.00"),
) -> ResolvedPolicy:
    policies = (
        db.query(DiscountPolicy)
        .filter(DiscountPolicy.active == True, DiscountPolicy.odoo_company_id == company_id)
        .all()
    )

    best_policy: Optional[DiscountPolicy] = None
    best_score = -1

    for p in policies:
        cat_match = p.odoo_product_category_id is not None and p.odoo_product_category_id == category_id
        tier_match = p.customer_tier_code is not None and p.customer_tier_code == tier_code

        cat_wildcard = p.odoo_product_category_id is None
        tier_wildcard = p.customer_tier_code is None

        if cat_match and tier_match:
            score = 400 - p.priority
        elif cat_match and tier_wildcard:
            score = 300 - p.priority
        elif cat_wildcard and tier_match:
            score = 200 - p.priority
        elif cat_wildcard and tier_wildcard:
            score = 100 - p.priority
        else:
            continue

        if score > best_score:
            best_score = score
            best_policy = p

    if best_policy:
        return ResolvedPolicy(
            policy_id=best_policy.id,
            name=best_policy.name,
            max_discount_pct=best_policy.max_discount_pct,
            minimum_margin_pct=best_policy.minimum_margin_pct or Decimal("20.00"),
            manager_threshold=best_policy.manager_threshold or Decimal("20.00"),
            finance_threshold=best_policy.finance_threshold or Decimal("50.00"),
            single_line_finance_pts=best_policy.single_line_finance_pts or Decimal("10.00"),
            version=f"policy_{best_policy.id}",
        )

    # Global default fallback
    return ResolvedPolicy(
        policy_id=None,
        name=f"Default Tier Policy ({tier_code})",
        max_discount_pct=tier_default_ceiling,
        minimum_margin_pct=Decimal("20.00"),
        manager_threshold=Decimal("20.00"),
        finance_threshold=Decimal("50.00"),
        single_line_finance_pts=Decimal("10.00"),
        version="default_v1",
    )
