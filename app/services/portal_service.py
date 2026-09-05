from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, EntityNotFoundError, ForbiddenError
from app.db.base import utc_now
from app.models.deal import Deal
from app.models.enums import ApprovalState, AuditEventType, DealStatus, Role
from app.odoo.interface import OdooGateway
from app.schemas.portal import (
    FORBIDDEN_PORTAL_KEYS,
    PortalCommentPublicRead,
    PortalConfirmDealRequest,
    PortalDealRead,
    PortalLineRead,
    PortalNegotiationRead,
)
from app.services import audit_service, negotiation_service, notification_service


def get_deal_for_portal(
    db: Session,
    gateway: OdooGateway,
    deal_id: uuid.UUID,
    customer_partner_id: int,
) -> PortalDealRead:
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise EntityNotFoundError("Deal", str(deal_id))

    if deal.odoo_partner_id != customer_partner_id:
        raise EntityNotFoundError("Deal", str(deal_id))

    raw_order = gateway.get_sale_order(deal.odoo_sale_order_id)

    portal_lines: List[PortalLineRead] = []
    for line in raw_order.lines:
        line_subtotal = round(
            Decimal(str(line.price_unit))
            * Decimal(str(line.qty))
            * (Decimal("1.0") - Decimal(str(line.discount_pct)) / Decimal("100.0")),
            2,
        )
        portal_lines.append(
            PortalLineRead(
                odoo_sale_order_line_id=line.id,
                odoo_product_id=line.product_id,
                product_name=line.product_name,
                product_uom_qty=Decimal(str(line.qty)),
                price_unit=Decimal(str(line.price_unit)),
                discount_pct=Decimal(str(line.discount_pct)),
                price_subtotal=line_subtotal,
            )
        )

    comments = negotiation_service.list_portal_comments(db=db, deal_id=deal.id, include_internal=False)
    portal_comments = [
        PortalCommentPublicRead(
            id=c.id,
            odoo_sale_order_line_id=c.odoo_sale_order_line_id,
            author_role=c.author_role,
            body=c.body,
            created_at=c.created_at,
        )
        for c in comments
    ]

    negs = negotiation_service.list_negotiation_requests(db=db, deal_id=deal.id)
    portal_negs = [
        PortalNegotiationRead(
            id=n.id,
            type=n.type,
            status=n.status,
            message=n.message,
            response_message=n.response_message,
            counter_value=n.counter_value,
            created_at=n.created_at,
            processed_at=n.processed_at,
        )
        for n in negs
    ]

    portal_status = (
        "UNDER_REVIEW"
        if deal.customer_confirmed_pending
        and deal.status not in (DealStatus.CONFIRMED.value, DealStatus.INVOICED.value, DealStatus.PAID.value)
        else deal.status
    )

    portal_deal = PortalDealRead(
        reference=deal.reference,
        odoo_sale_order_id=deal.odoo_sale_order_id,
        odoo_order_name=deal.odoo_order_name,
        partner_name=deal.partner_name_cache,
        currency_code=deal.currency_code,
        status=portal_status,
        order_discount_pct=deal.order_discount_pct,
        amount_untaxed=deal.amount_untaxed_cache,
        amount_total=deal.amount_total_cache,
        customer_confirmed_pending=deal.customer_confirmed_pending,
        sent_at=deal.sent_at,
        confirmed_at=deal.confirmed_at,
        promised_delivery_date=deal.promised_delivery_date,
        lines=portal_lines,
        comments=portal_comments,
        negotiations=portal_negs,
    )

    # Strict whitelist audit assertion: Ensure NO internal/margin/risk keys are present
    dumped = portal_deal.model_dump()
    for forbidden in FORBIDDEN_PORTAL_KEYS:
        assert forbidden not in dumped, f"CRITICAL LEAK: Forbidden key '{forbidden}' found in portal payload"

    return portal_deal


def confirm_deal_by_customer(
    db: Session,
    gateway: OdooGateway,
    deal_id: uuid.UUID,
    customer_partner_id: int,
    payload: PortalConfirmDealRequest,
) -> PortalDealRead:
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise EntityNotFoundError("Deal", str(deal_id))

    if deal.odoo_partner_id != customer_partner_id:
        raise EntityNotFoundError("Deal", str(deal_id))

    if deal.status in [DealStatus.CONFIRMED.value, DealStatus.INVOICED.value, DealStatus.PAID.value]:
        return get_deal_for_portal(db, gateway, deal_id, customer_partner_id)

    if deal.approval_state == ApprovalState.APPROVED.value:
        # Deal is already approved by governance -> immediately confirm in Odoo
        try:
            gateway.confirm(order_id=deal.odoo_sale_order_id)
        except Exception:
            pass

        deal.status = DealStatus.CONFIRMED.value
        deal.confirmed_at = utc_now()
        deal.customer_confirmed_pending = False
        deal.last_activity_at = utc_now()

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.CUSTOMER_CONFIRMED,
            entity_type="deal",
            entity_id=str(deal.id),
            deal_id=deal.id,
            actor_type="CUSTOMER",
            actor_id=customer_partner_id,
            reason=f"Customer confirmed approved quotation. Signature: {payload.signature_name or 'Digital'}",
        )

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.ORDER_CONFIRMED,
            entity_type="deal",
            entity_id=str(deal.id),
            deal_id=deal.id,
            actor_type="SYSTEM",
            reason="Confirmed sale order in Odoo upon customer confirmation of approved deal",
        )

        notification_service.create_notification(
            db=db,
            recipient_odoo_user_id=deal.owner_odoo_user_id,
            type="ORDER_CONFIRMED",
            title=f"Deal {deal.reference} Confirmed",
            body=f"Customer confirmed deal {deal.reference} ({deal.odoo_order_name}). Order is confirmed in Odoo.",
            entity_type="deal",
            entity_id=str(deal.id),
        )
    else:
        # Deal is not yet approved -> set pending flag, keep order locked
        deal.customer_confirmed_pending = True
        deal.last_activity_at = utc_now()

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.CUSTOMER_CONFIRMED,
            entity_type="deal",
            entity_id=str(deal.id),
            deal_id=deal.id,
            actor_type="CUSTOMER",
            actor_id=customer_partner_id,
            reason=f"Customer accepted quotation pending internal governance approval. Signature: {payload.signature_name or 'Digital'}",
        )

        notification_service.create_notification(
            db=db,
            recipient_odoo_user_id=deal.owner_odoo_user_id,
            type="CUSTOMER_ACCEPTED_PENDING",
            title=f"Customer Accepted Quotation {deal.reference}",
            body=f"Customer accepted quotation {deal.reference}. Deal is locked awaiting governance approval before confirmation.",
            entity_type="deal",
            entity_id=str(deal.id),
        )

    if payload.comment:
        negotiation_service.add_portal_comment(
            db=db,
            deal_id=deal.id,
            author_odoo_user_id=0,
            author_role=Role.CUSTOMER.value,
            body=f"Customer Confirmation Note: {payload.comment}",
            is_internal=False,
        )

    db.commit()
    return get_deal_for_portal(db, gateway, deal_id, customer_partner_id)
