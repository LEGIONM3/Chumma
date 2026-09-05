from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import BusinessRuleError, EntityNotFoundError
from app.core.pagination import PaginationParams
from app.db.base import utc_now
from app.guardian.evaluator import evaluate_deal
from app.guardian.recommend import evaluate_recommendations
from app.models.deal import Deal
from app.models.enums import AuditEventType, RecommendationStatus, TriggerType
from app.models.identity import DealflowUser
from app.models.policy import DiscountPolicy
from app.models.recommendation import Recommendation, RecommendationRule
from app.odoo.interface import OdooGateway
from app.schemas.common import PaginationMeta
from app.schemas.recommendation import RecommendationRuleCreate, RecommendationRuleUpdate
from app.services import audit_service, deal_context_builder, deal_service


def generate_and_persist_recommendations(
    db: Session,
    gateway: OdooGateway,
    deal: Deal,
    risk_assessment_id: uuid.UUID,
) -> List[Recommendation]:
    ctx = deal_context_builder.build_deal_context(
        db=db,
        gateway=gateway,
        order_id=deal.odoo_sale_order_id,
        deal_id=deal.id,
    )

    if not ctx.lines:
        return []

    deal_pids = [l.product_id for l in ctx.lines]

    # Find candidate rules triggered by lines on the deal
    rules = (
        db.query(RecommendationRule)
        .filter(
            RecommendationRule.odoo_company_id == deal.odoo_company_id,
            RecommendationRule.trigger_product_id.in_(deal_pids),
            RecommendationRule.active == True,
        )
        .all()
    )

    if not rules:
        return []

    # Identify candidate suggested products not already in deal
    candidate_sugg_pids = list({r.suggested_product_id for r in rules if r.suggested_product_id not in deal_pids})
    if not candidate_sugg_pids:
        return []

    # Fetch product metadata for triggers and candidates
    all_needed_pids = list(set(deal_pids + candidate_sugg_pids))
    products_map = gateway.get_products(all_needed_pids)

    # Fetch customer pricelist prices
    prices_map = {
        pid: gateway.get_price(partner_id=deal.odoo_partner_id, product_id=pid, qty=1.0)
        for pid in candidate_sugg_pids
    }

    # Fetch dismissed product IDs for this deal
    dismissed = (
        db.query(Recommendation.odoo_product_id)
        .filter(
            Recommendation.deal_id == deal.id,
            Recommendation.status == RecommendationStatus.DISMISSED.value,
        )
        .all()
    )
    dismissed_product_ids = {r[0] for r in dismissed}

    # Check minimum policy margin floor if applicable
    min_policy_margin_pct = None
    company_policy = (
        db.query(DiscountPolicy)
        .filter(DiscountPolicy.odoo_company_id == deal.odoo_company_id, DiscountPolicy.active == True)
        .first()
    )
    if company_policy and company_policy.minimum_margin_pct is not None:
        min_policy_margin_pct = company_policy.minimum_margin_pct

    # Evaluate recommendations
    candidates = evaluate_recommendations(
        deal_lines=ctx.lines,
        deal_amount_untaxed=ctx.amount_untaxed,
        deal_total_cost=ctx.total_cost,
        deal_total_margin_pct=ctx.total_margin_pct,
        rules=rules,
        products_map=products_map,
        prices_map=prices_map,
        dismissed_product_ids=dismissed_product_ids,
        min_policy_margin_pct=min_policy_margin_pct,
    )

    # Clear prior ACTIVE recommendations for this deal (preserving ADDED / DISMISSED history)
    db.query(Recommendation).filter(
        Recommendation.deal_id == deal.id,
        Recommendation.status == RecommendationStatus.ACTIVE.value,
    ).delete(synchronize_session=False)

    persisted: List[Recommendation] = []
    for c in candidates:
        rec = Recommendation(
            deal_id=deal.id,
            risk_assessment_id=risk_assessment_id,
            odoo_product_id=c.odoo_product_id,
            product_name_cache=c.product_name,
            recommendation_type=c.recommendation_type,
            score=c.score,
            co_purchase_score=c.co_purchase_score,
            promotion_score=c.promotion_score,
            margin_score=c.margin_score,
            relevance_score=c.relevance_score,
            margin_delta_amount=c.margin_delta_amount,
            margin_delta_pct=c.margin_delta_pct,
            unit_price_cache=c.unit_price,
            reason=c.reason,
            source=c.source,
            status=RecommendationStatus.ACTIVE.value,
            created_at=utc_now(),
        )
        db.add(rec)
        persisted.append(rec)

    db.flush()
    return persisted


def list_deal_recommendations(db: Session, deal_id: uuid.UUID) -> List[Recommendation]:
    return (
        db.query(Recommendation)
        .filter(
            Recommendation.deal_id == deal_id,
            Recommendation.status == RecommendationStatus.ACTIVE.value,
        )
        .order_by(desc(Recommendation.score))
        .all()
    )


def apply_recommendation_action(
    db: Session,
    gateway: OdooGateway,
    deal_id: uuid.UUID,
    recommendation_id: uuid.UUID,
    actor: DealflowUser,
    action: str,
    quantity: Decimal = Decimal("1.0"),
) -> Dict[str, Any]:
    rec = (
        db.query(Recommendation)
        .filter(Recommendation.id == recommendation_id, Recommendation.deal_id == deal_id)
        .first()
    )
    if not rec:
        raise EntityNotFoundError("Recommendation", str(recommendation_id))

    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise EntityNotFoundError("Deal", str(deal_id))

    if action == "ADD":
        if rec.status != RecommendationStatus.ACTIVE.value:
            raise BusinessRuleError(
                code="REC_NOT_ACTIVE",
                message=f"Recommendation is already {rec.status}.",
            )

        # Append line to Odoo
        line_id = gateway.add_line(
            order_id=deal.odoo_sale_order_id,
            product_id=rec.odoo_product_id,
            qty=float(quantity),
        )

        rec.status = RecommendationStatus.ADDED.value
        rec.added_at = utc_now()
        deal.last_activity_at = utc_now()

        # Resync deal from Odoo to update cached margins and totals
        deal_service.sync_deal_from_odoo(
            db=db,
            gateway=gateway,
            odoo_sale_order_id=deal.odoo_sale_order_id,
            actor_id=actor.odoo_user_id,
            actor_role=actor.role,
        )

        # Trigger LINE_ADDED governance re-evaluation
        eval_res = evaluate_deal(
            db=db,
            gateway=gateway,
            deal_id=deal.id,
            trigger_type=TriggerType.LINE_ADDED,
            actor_id=actor.odoo_user_id,
            actor_role=actor.role,
        )

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.RECOMMENDATION_ADDED,
            entity_type="recommendation",
            entity_id=str(rec.id),
            deal_id=deal.id,
            actor_type="USER",
            actor_id=actor.odoo_user_id,
            actor_role=actor.role,
            reason=f"Added recommended product {rec.product_name_cache} (line {line_id})",
        )

        db.commit()

        # Return updated workspace bundle
        updated_deal, updated_lines, nbas = deal_service.get_deal_detail(db, gateway, deal.id)
        updated_recs = list_deal_recommendations(db, deal.id)
        return {
            "deal": updated_deal,
            "lines": updated_lines,
            "recommendations": updated_recs,
            "next_best_actions": nbas,
            "evaluation": eval_res,
        }

    elif action == "DISMISS":
        rec.status = RecommendationStatus.DISMISSED.value
        rec.dismissed_at = utc_now()
        deal.last_activity_at = utc_now()

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.RECOMMENDATION_DISMISSED,
            entity_type="recommendation",
            entity_id=str(rec.id),
            deal_id=deal.id,
            actor_type="USER",
            actor_id=actor.odoo_user_id,
            actor_role=actor.role,
            reason=f"Dismissed recommendation for {rec.product_name_cache}",
        )

        db.commit()
        db.refresh(rec)
        return {"recommendation": rec, "status": "dismissed"}

    else:
        raise BusinessRuleError(code="INVALID_ACTION", message=f"Unknown recommendation action '{action}'.")


def mine_co_purchase_rules(
    db: Session,
    gateway: OdooGateway,
    company_id: int = 1,
    lookback_days: int = 365,
) -> Dict[str, Any]:
    since_str = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    orders = gateway.get_confirmed_orders_for_mining(since=since_str)

    pair_counts: Dict[Tuple[int, int], int] = defaultdict(int)
    for o in orders:
        pids = sorted(list(set(o.get("product_ids", []))))
        for i in range(len(pids)):
            for j in range(len(pids)):
                if i != j:
                    pair_counts[(pids[i], pids[j])] += 1

    upsert_count = 0
    for (trig_id, sugg_id), count in pair_counts.items():
        rule = (
            db.query(RecommendationRule)
            .filter(
                RecommendationRule.odoo_company_id == company_id,
                RecommendationRule.trigger_product_id == trig_id,
                RecommendationRule.suggested_product_id == sugg_id,
            )
            .first()
        )
        if rule:
            rule.co_purchase_count = count
            rule.source = "MINED"
        else:
            rule = RecommendationRule(
                odoo_company_id=company_id,
                trigger_product_id=trig_id,
                suggested_product_id=sugg_id,
                co_purchase_count=count,
                manual_boost=Decimal("0.0"),
                source="MINED",
                active=True,
            )
            db.add(rule)
        upsert_count += 1

    db.commit()
    return {
        "status": "completed",
        "orders_analyzed": len(orders),
        "rules_upserted": upsert_count,
    }


def list_rules(
    db: Session,
    company_id: int = 1,
    pagination: Optional[PaginationParams] = None,
) -> Tuple[List[RecommendationRule], PaginationMeta]:
    query = db.query(RecommendationRule).filter(RecommendationRule.odoo_company_id == company_id)
    total = query.count()

    if not pagination:
        pagination = PaginationParams(page=1, page_size=50)

    total_pages = math.ceil(total / pagination.page_size) if pagination.page_size > 0 else 1
    offset = (pagination.page - 1) * pagination.page_size

    records = (
        query.order_by(desc(RecommendationRule.co_purchase_count))
        .offset(offset)
        .limit(pagination.page_size)
        .all()
    )

    meta = PaginationMeta(
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
        total_pages=total_pages,
    )
    return records, meta


def create_rule(db: Session, payload: RecommendationRuleCreate) -> RecommendationRule:
    rule = RecommendationRule(
        odoo_company_id=payload.odoo_company_id,
        trigger_product_id=payload.trigger_product_id,
        suggested_product_id=payload.suggested_product_id,
        co_purchase_count=payload.co_purchase_count,
        manual_boost=payload.manual_boost,
        is_promoted_override=payload.is_promoted_override,
        source=payload.source,
        active=payload.active,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def get_rule(db: Session, rule_id: uuid.UUID) -> RecommendationRule:
    rule = db.query(RecommendationRule).filter(RecommendationRule.id == rule_id).first()
    if not rule:
        raise EntityNotFoundError("RecommendationRule", str(rule_id))
    return rule


def update_rule(
    db: Session,
    rule_id: uuid.UUID,
    payload: RecommendationRuleUpdate,
) -> RecommendationRule:
    rule = get_rule(db, rule_id)
    if payload.co_purchase_count is not None:
        rule.co_purchase_count = payload.co_purchase_count
    if payload.manual_boost is not None:
        rule.manual_boost = payload.manual_boost
    if payload.is_promoted_override is not None:
        rule.is_promoted_override = payload.is_promoted_override
    if payload.active is not None:
        rule.active = payload.active
    db.commit()
    db.refresh(rule)
    return rule


def delete_rule(db: Session, rule_id: uuid.UUID) -> bool:
    rule = get_rule(db, rule_id)
    db.delete(rule)
    db.commit()
    return True
