from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from app.core.config import settings
from app.models.enums import RecommendationType
from app.models.policy import DiscountPolicy
from app.models.recommendation import RecommendationRule
from app.odoo.interface import RawProduct


@dataclass
class LineContextLike:
    product_id: int
    product_name: str
    category_id: int
    category_path: List[int]
    price_unit: Decimal
    qty: Decimal


@dataclass
class RecommendationCandidate:
    odoo_product_id: int
    product_name: str
    recommendation_type: str
    score: Decimal
    co_purchase_score: Decimal
    promotion_score: Decimal
    margin_score: Decimal
    relevance_score: Decimal
    margin_delta_amount: Decimal
    margin_delta_pct: Decimal
    unit_price: Decimal
    reason: str
    source: str = "RULE"


def evaluate_recommendations(
    deal_lines: List[Any],
    deal_amount_untaxed: Decimal,
    deal_total_cost: Decimal,
    deal_total_margin_pct: Decimal,
    rules: List[RecommendationRule],
    products_map: Dict[int, RawProduct],
    prices_map: Dict[int, Decimal],
    dismissed_product_ids: Set[int],
    min_policy_margin_pct: Optional[Decimal] = None,
    rec_min_margin_pct: Optional[Decimal] = None,
    rec_max: Optional[int] = None,
    rec_promotion_score: Optional[Decimal] = None,
    rec_weights: Optional[Dict[str, float]] = None,
) -> List[RecommendationCandidate]:
    """Pure-Python deterministic recommendation evaluation engine.
    
    Scores candidate products triggered by current deal lines using co-purchase counts,
    promotions, profit margins, and catalog category relevance.
    """
    if not deal_lines or not rules:
        return []

    min_margin = rec_min_margin_pct if rec_min_margin_pct is not None else settings.REC_MIN_MARGIN_PCT
    max_recs = rec_max if rec_max is not None else settings.REC_MAX
    promo_score_val = float(rec_promotion_score if rec_promotion_score is not None else settings.REC_PROMOTION_SCORE)
    weights = rec_weights if rec_weights is not None else settings.REC_WEIGHTS

    deal_product_ids = {getattr(l, "product_id") for l in deal_lines}

    # Filter applicable rules
    candidate_rules: List[RecommendationRule] = []
    for r in rules:
        if not r.active:
            continue
        if r.trigger_product_id not in deal_product_ids:
            continue
        if r.suggested_product_id in deal_product_ids:
            continue
        if r.suggested_product_id in dismissed_product_ids:
            continue
        if r.suggested_product_id not in products_map:
            continue
        candidate_rules.append(r)

    if not candidate_rules:
        return []

    # Max co-purchase count across applicable rules for normalization
    max_co = max([r.co_purchase_count for r in candidate_rules] or [1])
    if max_co <= 0:
        max_co = 1

    # Group candidate rules by suggested product (keep rule with highest co_purchase_count)
    best_rule_per_product: Dict[int, RecommendationRule] = {}
    for r in candidate_rules:
        existing = best_rule_per_product.get(r.suggested_product_id)
        if not existing or (r.co_purchase_count + float(r.manual_boost or 0)) > (existing.co_purchase_count + float(existing.manual_boost or 0)):
            best_rule_per_product[r.suggested_product_id] = r

    candidates: List[RecommendationCandidate] = []

    for sugg_id, rule in best_rule_per_product.items():
        prod = products_map[sugg_id]
        price = Decimal(str(prices_map.get(sugg_id, prod.list_price)))
        cost = Decimal(str(prod.standard_price))

        if price <= Decimal("0"):
            continue

        candidate_margin_pct = ((price - cost) / price) * Decimal("100")

        # Floor margin filters
        if candidate_margin_pct < min_margin:
            continue
        if min_policy_margin_pct is not None and candidate_margin_pct < min_policy_margin_pct:
            continue

        # 1. Co-purchase score
        raw_co = float(rule.co_purchase_count) / float(max_co)
        co_score = min(1.0, max(0.0, raw_co + float(rule.manual_boost or Decimal("0.0"))))

        # 2. Promotion score
        is_promoted = rule.is_promoted_override if rule.is_promoted_override is not None else ("DealFlow Promo" in getattr(prod, "tags", []))
        promo_score = 1.0 if is_promoted else 0.0

        # 3. Margin score
        raw_margin = float((price - cost) / price)
        margin_score = min(1.0, max(0.0, raw_margin))

        # 4. Relevance score based on category hierarchy
        trigger_line = next((l for l in deal_lines if getattr(l, "product_id") == rule.trigger_product_id), None)
        relevance_score = 0.3
        if trigger_line:
            trigger_cat = getattr(trigger_line, "category_id", None)
            trigger_path = getattr(trigger_line, "category_path", []) or []
            prod_path = getattr(prod, "category_path", []) or []

            if prod.category_id == trigger_cat or prod.category_id in trigger_path:
                relevance_score = 1.0
            elif len(prod_path) > 1 and len(trigger_path) > 1 and prod_path[-2] == trigger_path[-2]:
                # Sibling categories sharing parent
                relevance_score = 0.6
            elif trigger_cat in prod_path:
                relevance_score = 1.0

        # Total score
        total_score = (
            weights.get("co", 0.45) * co_score
            + weights.get("promo", 0.10) * promo_score
            + weights.get("margin", 0.30) * margin_score
            + weights.get("rel", 0.15) * relevance_score
        )
        final_score = round(Decimal(str(total_score)), 3)

        # Margin deltas
        margin_delta_amount = round(price - cost, 2)
        new_untaxed = deal_amount_untaxed + price
        new_cost = deal_total_cost + cost
        new_margin_pct = ((new_untaxed - new_cost) / new_untaxed * Decimal("100")) if new_untaxed > Decimal("0") else Decimal("0")
        margin_delta_pct = round(new_margin_pct - deal_total_margin_pct, 2)

        # Recommendation type
        trigger_prod_name = getattr(trigger_line, "product_name", f"Product {rule.trigger_product_id}") if trigger_line else f"Product {rule.trigger_product_id}"
        if trigger_line and getattr(trigger_line, "category_id", None) == prod.category_id:
            rec_type = RecommendationType.UPSELL.value
        else:
            rec_type = RecommendationType.CROSS_SELL.value

        # Explainable reason string
        promo_str = ", currently promoted" if is_promoted else ""
        sign_str = "+" if margin_delta_pct >= Decimal("0") else ""
        reason = (
            f"Frequently bought with {trigger_prod_name} ({rule.co_purchase_count} deals){promo_str}; "
            f"adds ₹{margin_delta_amount:,.2f} projected margin ({sign_str}{margin_delta_pct} pts)"
        )

        candidates.append(
            RecommendationCandidate(
                odoo_product_id=sugg_id,
                product_name=prod.name,
                recommendation_type=rec_type,
                score=final_score,
                co_purchase_score=round(Decimal(str(co_score)), 3),
                promotion_score=round(Decimal(str(promo_score)), 3),
                margin_score=round(Decimal(str(margin_score)), 3),
                relevance_score=round(Decimal(str(relevance_score)), 3),
                margin_delta_amount=margin_delta_amount,
                margin_delta_pct=margin_delta_pct,
                unit_price=price,
                reason=reason,
                source=rule.source,
            )
        )

    # Sort descending by score, tiebreak by co-purchase count and margin delta
    candidates.sort(
        key=lambda c: (c.score, c.co_purchase_score, c.margin_delta_amount),
        reverse=True,
    )

    return candidates[:max_recs]
