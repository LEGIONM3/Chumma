from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import uuid
from sqlalchemy.orm import Session
from app.core.errors import NotFoundError, VersionConflictError
from app.core.pagination import PaginationParams, paginate_query
from app.db.base import utc_now
from app.models.deal import Deal
from app.models.enums import (
    ApprovalLevel,
    ApprovalState,
    AuditEventType,
    DealStatus,
    HealthStatus,
    NextBestActionType,
)
from app.odoo.interface import OdooGateway
from app.schemas.common import PaginationMeta
from app.schemas.deal import DealFilterParams, DealLineRead, DealPatchRequest
from app.services import audit_service, deal_context_builder, number_sequence_service


def sync_deal_from_odoo(
    db: Session,
    gateway: OdooGateway,
    odoo_sale_order_id: int,
    expected_version: Optional[int] = None,
    actor_id: Optional[int] = None,
    actor_role: Optional[str] = None,
) -> Deal:
    deal = db.query(Deal).filter(Deal.odoo_sale_order_id == odoo_sale_order_id).first()
    ctx = deal_context_builder.build_deal_context(
        db=db,
        gateway=gateway,
        order_id=odoo_sale_order_id,
        deal_id=deal.id if deal else None,
    )

    if not deal:
        ref = number_sequence_service.get_next_sequence(db, key="DEAL", prefix="D")
        deal = Deal(
            reference=ref,
            odoo_sale_order_id=ctx.odoo_sale_order_id,
            odoo_order_name=ctx.odoo_order_name,
            odoo_partner_id=ctx.partner_id,
            partner_name_cache=ctx.partner_name,
            tier_code=ctx.tier_code,
            owner_odoo_user_id=ctx.user_id,
            sales_team_odoo_id=ctx.team_id,
            odoo_company_id=ctx.company_id,
            currency_code=ctx.currency_code,
            status=DealStatus.DRAFT.value,
            approval_state=ApprovalState.NOT_EVALUATED.value,
            health_status=HealthStatus.HEALTHY.value,
            required_level=ApprovalLevel.NONE.value,
            order_discount_pct=ctx.blended_discount_pct,
            amount_total_cache=ctx.amount_total,
            amount_untaxed_cache=ctx.amount_untaxed,
            margin_pct_cache=ctx.total_margin_pct,
            one_time_total_cache=ctx.one_time_total,
            recurring_first_cycle_total_cache=ctx.recurring_first_cycle_total,
            promised_delivery_date=ctx.delivery_date,
            version=1,
            last_activity_at=utc_now(),
            last_synced_at=utc_now(),
        )
        db.add(deal)
        db.commit()
        db.refresh(deal)

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.DEAL_CREATED,
            entity_type="deal",
            entity_id=str(deal.id),
            deal_id=deal.id,
            actor_type="USER" if actor_id else "SYSTEM",
            actor_id=actor_id,
            actor_role=actor_role,
            reason=f"Deal created from Odoo sale.order {ctx.odoo_order_name}",
        )
    else:
        if expected_version is not None and deal.version != expected_version:
            raise VersionConflictError(current_version=deal.version, attempted_version=expected_version)

        deal.version += 1
        deal.partner_name_cache = ctx.partner_name
        deal.order_discount_pct = ctx.blended_discount_pct
        deal.amount_total_cache = ctx.amount_total
        deal.amount_untaxed_cache = ctx.amount_untaxed
        deal.margin_pct_cache = ctx.total_margin_pct
        deal.one_time_total_cache = ctx.one_time_total
        deal.recurring_first_cycle_total_cache = ctx.recurring_first_cycle_total
        if ctx.delivery_date:
            deal.promised_delivery_date = ctx.delivery_date
        deal.last_synced_at = utc_now()
        deal.last_activity_at = utc_now()

        db.commit()
        db.refresh(deal)

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.DEAL_SYNCED,
            entity_type="deal",
            entity_id=str(deal.id),
            deal_id=deal.id,
            actor_type="USER" if actor_id else "SYSTEM",
            actor_id=actor_id,
            actor_role=actor_role,
            reason=f"Deal synced with Odoo sale.order {ctx.odoo_order_name}",
        )

    return deal


def get_deal_by_id(db: Session, deal_id: uuid.UUID) -> Deal:
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise NotFoundError(f"Deal with ID {deal_id} not found.")
    return deal


def get_deal_detail(
    db: Session,
    gateway: OdooGateway,
    deal_id: uuid.UUID,
) -> Tuple[Deal, List[DealLineRead], List[Dict[str, Any]]]:
    deal = get_deal_by_id(db, deal_id)
    ctx = deal_context_builder.build_deal_context(
        db=db,
        gateway=gateway,
        order_id=deal.odoo_sale_order_id,
        deal_id=deal.id,
    )

    lines: List[DealLineRead] = [
        DealLineRead(
            odoo_sale_order_line_id=l.line_id,
            odoo_product_id=l.product_id,
            product_name=l.product_name,
            product_uom_qty=l.qty,
            price_unit=l.price_unit,
            discount_pct=l.discount_pct,
            price_subtotal=l.subtotal,
            cost_price=l.unit_cost,
            margin_amount=l.margin_amount,
            margin_pct=l.margin_pct,
            is_recurring=l.is_recurring,
            odoo_product_category_id=l.category_id,
        )
        for l in ctx.lines
    ]

    # Generate Next Best Action based on deterministic rule hierarchy (§7.8)
    next_best_actions: List[Dict[str, Any]] = []
    try:
        from app.services import health_service
        calc_res, nba = health_service.recompute_deal_health(
            db=db,
            gateway=gateway,
            deal=deal,
        )
        if nba:
            next_best_actions.append({
                "action": nba.type,
                "action_type": nba.type,
                "type": nba.type,
                "title": nba.title,
                "description": nba.explanation,
                "priority": nba.priority,
                "payload": nba.payload,
                "cta_endpoint": nba.cta_endpoint,
            })
    except Exception:
        pass

    return deal, lines, next_best_actions


def list_deals(
    db: Session,
    params: DealFilterParams,
    pagination: PaginationParams,
) -> Tuple[List[Deal], PaginationMeta]:
    query = db.query(Deal)

    if params.status:
        query = query.filter(Deal.status == params.status.value)
    if params.approval_state:
        query = query.filter(Deal.approval_state == params.approval_state.value)
    if params.health_status:
        query = query.filter(Deal.health_status == params.health_status.value)
    if params.owner_id:
        query = query.filter(Deal.owner_odoo_user_id == params.owner_id)
    if params.sales_team_id:
        query = query.filter(Deal.sales_team_odoo_id == params.sales_team_id)
    if params.partner_id:
        query = query.filter(Deal.odoo_partner_id == params.partner_id)
    if params.min_risk_score is not None:
        query = query.filter(Deal.current_risk_score >= params.min_risk_score)
    if params.max_risk_score is not None:
        query = query.filter(Deal.current_risk_score <= params.max_risk_score)
    if params.query:
        search_pat = f"%{params.query}%"
        query = query.filter(
            (Deal.reference.ilike(search_pat))
            | (Deal.odoo_order_name.ilike(search_pat))
            | (Deal.partner_name_cache.ilike(search_pat))
        )

    items, meta = paginate_query(query.order_by(Deal.last_activity_at.desc()), pagination)
    return items, meta


def patch_deal(
    db: Session,
    deal_id: uuid.UUID,
    patch: DealPatchRequest,
    actor_id: Optional[int] = None,
    actor_role: Optional[str] = None,
) -> Deal:
    deal = get_deal_by_id(db, deal_id)

    if patch.promised_delivery_date is not None:
        deal.promised_delivery_date = patch.promised_delivery_date
    if patch.tier_code is not None:
        deal.tier_code = patch.tier_code

    deal.last_activity_at = utc_now()
    db.commit()
    db.refresh(deal)

    audit_service.record_audit_event(
        db=db,
        event_type=AuditEventType.LINE_CHANGED,
        entity_type="deal",
        entity_id=str(deal.id),
        deal_id=deal.id,
        actor_type="USER" if actor_id else "SYSTEM",
        actor_id=actor_id,
        actor_role=actor_role,
        reason="Deal metadata updated",
    )

    return deal
