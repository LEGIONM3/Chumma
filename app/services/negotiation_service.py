from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, EntityNotFoundError, ForbiddenError
from app.db.base import utc_now
from app.guardian.evaluator import evaluate_deal
from app.models.deal import Deal
from app.models.enums import (
    AuditEventType,
    DealStatus,
    NegotiationRequestStatus,
    NegotiationRequestType,
    Role,
    TriggerType,
)
from app.models.identity import DealflowUser
from app.models.negotiation import NegotiationChange, NegotiationRequest, PortalComment
from app.odoo.interface import OdooGateway
from app.schemas.portal import PortalNegotiationSubmit
from app.services import audit_service, deal_service, notification_service


def submit_negotiation_request(
    db: Session,
    deal_id: uuid.UUID,
    customer_partner_id: int,
    actor_user_id: int,
    payload: PortalNegotiationSubmit,
) -> NegotiationRequest:
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise EntityNotFoundError("Deal", str(deal_id))

    if deal.odoo_partner_id != customer_partner_id:
        raise EntityNotFoundError("Deal", str(deal_id))

    if deal.status in [DealStatus.CONFIRMED.value, DealStatus.CANCELLED.value, DealStatus.EXPIRED.value]:
        raise BusinessRuleError(
            code="DEAL_CLOSED",
            message=f"Cannot negotiate on quotation in {deal.status} status.",
        )

    req_type = getattr(payload, "resolved_type", getattr(payload, "type", "COMMENT"))
    req_line_id = getattr(payload, "resolved_line_id", getattr(payload, "odoo_sale_order_line_id", None))

    req = NegotiationRequest(
        deal_id=deal.id,
        odoo_sale_order_id=deal.odoo_sale_order_id,
        customer_partner_id=customer_partner_id,
        requested_by_odoo_user_id=actor_user_id,
        type=req_type,
        status=NegotiationRequestStatus.OPEN.value,
        message=payload.message,
        counter_value=payload.counter_value,
        based_on_assessment_id=deal.current_assessment_id,
        created_at=utc_now(),
    )
    db.add(req)
    db.flush()

    if req_type == NegotiationRequestType.QTY_CHANGE.value:
        change = NegotiationChange(
            negotiation_request_id=req.id,
            odoo_sale_order_line_id=req_line_id,
            field_name="product_uom_qty",
            requested_value=str(payload.requested_qty) if payload.requested_qty is not None else None,
        )
        db.add(change)
    elif req_type == NegotiationRequestType.COUNTER_DISCOUNT.value:
        change = NegotiationChange(
            negotiation_request_id=req.id,
            odoo_sale_order_line_id=req_line_id,
            field_name="discount",
            requested_value=str(payload.counter_value) if payload.counter_value is not None else None,
        )
        db.add(change)
    elif payload.type == NegotiationRequestType.REMOVE_LINE.value:
        change = NegotiationChange(
            negotiation_request_id=req.id,
            odoo_sale_order_line_id=payload.odoo_sale_order_line_id,
            field_name="remove_line",
            requested_value="true",
        )
        db.add(change)
    elif payload.type == NegotiationRequestType.ADD_LINE.value:
        change = NegotiationChange(
            negotiation_request_id=req.id,
            field_name="add_product",
            requested_value=str(payload.requested_product_id) if payload.requested_product_id is not None else None,
        )
        db.add(change)

    deal.status = DealStatus.UNDER_NEGOTIATION.value
    deal.last_activity_at = utc_now()

    audit_service.record_audit_event(
        db=db,
        event_type=AuditEventType.NEGOTIATION_RECEIVED,
        entity_type="negotiation_request",
        entity_id=str(req.id),
        deal_id=deal.id,
        actor_type="CUSTOMER",
        actor_id=customer_partner_id,
        reason=f"Customer submitted negotiation request: {payload.type}",
    )

    notification_service.create_notification(
        db=db,
        recipient_odoo_user_id=deal.owner_odoo_user_id,
        type="NEGOTIATION_RECEIVED",
        title=f"Negotiation on Deal {deal.reference}",
        body=f"Customer submitted {payload.type}: {payload.message or ''}",
        entity_type="deal",
        entity_id=str(deal.id),
    )

    db.commit()
    db.refresh(req)
    return req


def respond_negotiation_request(
    db: Session,
    gateway: OdooGateway,
    request_id: uuid.UUID,
    actor: DealflowUser,
    action: str,
    response_message: Optional[str] = None,
    counter_value: Optional[Decimal] = None,
) -> NegotiationRequest:
    req = db.query(NegotiationRequest).filter(NegotiationRequest.id == request_id).first()
    if not req:
        raise EntityNotFoundError("NegotiationRequest", str(request_id))

    if req.status != NegotiationRequestStatus.OPEN.value:
        raise BusinessRuleError(
            code="NEGOTIATION_NOT_OPEN",
            message=f"Negotiation request is not open (current status: {req.status}).",
        )

    deal = db.query(Deal).filter(Deal.id == req.deal_id).first()
    if not deal:
        raise EntityNotFoundError("Deal", str(req.deal_id))

    req.processed_at = utc_now()
    req.processed_by_odoo_user_id = actor.odoo_user_id
    req.response_message = response_message

    if action == "ACCEPT":
        req.status = NegotiationRequestStatus.ACCEPTED.value

        # Apply accepted changes to Odoo
        changes = db.query(NegotiationChange).filter(NegotiationChange.negotiation_request_id == req.id).all()
        for chg in changes:
            if chg.field_name == "product_uom_qty" and chg.odoo_sale_order_line_id and chg.requested_value:
                gateway.apply_line_changes(
                    deal.odoo_sale_order_id,
                    [{"line_id": chg.odoo_sale_order_line_id, "qty": float(chg.requested_value)}],
                )
            elif chg.field_name == "remove_line" and chg.odoo_sale_order_line_id:
                gateway.apply_line_changes(
                    deal.odoo_sale_order_id,
                    [{"line_id": chg.odoo_sale_order_line_id, "qty": 0.0}],
                )
            elif chg.field_name == "add_product" and chg.requested_value:
                gateway.add_line(
                    order_id=deal.odoo_sale_order_id,
                    product_id=int(chg.requested_value),
                    qty=1.0,
                )
            elif chg.field_name in ("counter_value", "discount") and chg.requested_value:
                if chg.odoo_sale_order_line_id:
                    gateway.apply_line_changes(
                        deal.odoo_sale_order_id,
                        [{"line_id": chg.odoo_sale_order_line_id, "discount": float(chg.requested_value)}],
                    )
                else:
                    raw_order = gateway.get_sale_order(deal.odoo_sale_order_id)
                    line_updates = [{"line_id": l.id, "discount": float(chg.requested_value)} for l in raw_order.lines]
                    if line_updates:
                        gateway.apply_line_changes(deal.odoo_sale_order_id, line_updates)

        # Resync deal from Odoo
        deal_service.sync_deal_from_odoo(
            db=db,
            gateway=gateway,
            odoo_sale_order_id=deal.odoo_sale_order_id,
            actor_id=actor.odoo_user_id,
            actor_role=actor.role,
        )

        # Re-evaluate governance
        eval_res = evaluate_deal(
            db=db,
            gateway=gateway,
            deal_id=deal.id,
            trigger_type=TriggerType.NEGOTIATION_ACCEPTED,
            actor_id=actor.odoo_user_id,
            actor_role=actor.role,
        )
        req.applied_assessment_id = eval_res.risk_assessment.id
        deal.status = DealStatus.SENT.value

    elif action == "REJECT":
        req.status = NegotiationRequestStatus.REJECTED.value
        deal.status = DealStatus.SENT.value

    elif action == "COUNTER":
        req.status = NegotiationRequestStatus.COUNTERED.value
        req.counter_value = counter_value
        deal.status = DealStatus.UNDER_NEGOTIATION.value

    deal.last_activity_at = utc_now()

    audit_service.record_audit_event(
        db=db,
        event_type=AuditEventType.NEGOTIATION_RESPONDED,
        entity_type="negotiation_request",
        entity_id=str(req.id),
        deal_id=deal.id,
        actor_type="USER",
        actor_id=actor.odoo_user_id,
        actor_role=actor.role,
        reason=f"Rep responded with {action}: {response_message or 'No message'}",
    )

    db.commit()
    db.refresh(req)
    return req


def list_negotiation_requests(db: Session, deal_id: uuid.UUID) -> List[NegotiationRequest]:
    return (
        db.query(NegotiationRequest)
        .filter(NegotiationRequest.deal_id == deal_id)
        .order_by(desc(NegotiationRequest.created_at))
        .all()
    )


def add_portal_comment(
    db: Session,
    deal_id: uuid.UUID,
    author_odoo_user_id: int,
    author_role: str,
    body: str,
    is_internal: bool = False,
    odoo_sale_order_line_id: Optional[int] = None,
) -> PortalComment:
    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise EntityNotFoundError("Deal", str(deal_id))

    if author_role == Role.CUSTOMER.value:
        is_internal = False

    comment = PortalComment(
        deal_id=deal.id,
        odoo_sale_order_line_id=odoo_sale_order_line_id,
        author_odoo_user_id=author_odoo_user_id,
        author_role=author_role,
        body=body,
        is_internal=is_internal,
        created_at=utc_now(),
    )
    db.add(comment)
    deal.last_activity_at = utc_now()
    db.commit()
    db.refresh(comment)
    return comment


def list_portal_comments(
    db: Session,
    deal_id: uuid.UUID,
    include_internal: bool = False,
) -> List[PortalComment]:
    query = db.query(PortalComment).filter(PortalComment.deal_id == deal_id)
    if not include_internal:
        query = query.filter(PortalComment.is_internal == False)
    return query.order_by(PortalComment.created_at.asc()).all()
