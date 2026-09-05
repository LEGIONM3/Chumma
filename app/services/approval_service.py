from datetime import datetime
from decimal import Decimal
import math
from typing import Any, Dict, List, Optional, Tuple
import uuid
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, EntityNotFoundError, ForbiddenError
from app.core.pagination import PaginationParams
from app.db.base import utc_now
from app.models.approval import ApprovalAction, ApprovalRequest
from app.models.deal import Deal
from app.models.enums import (
    ApprovalActionType,
    ApprovalLevel,
    ApprovalRequestStatus,
    ApprovalState,
    AuditEventType,
    DealStatus,
    Role,
)
from app.models.identity import DealflowUser
from app.odoo.interface import OdooGateway
from app.schemas.approval import ApprovalTimelineItem, PendingApprovalSummary
from app.schemas.common import PaginationMeta
from app.services import audit_service, notification_service


def list_pending_approvals(
    db: Session,
    actor: DealflowUser,
    pagination: PaginationParams,
) -> Tuple[List[PendingApprovalSummary], PaginationMeta]:
    query = (
        db.query(ApprovalRequest, Deal)
        .join(Deal, ApprovalRequest.deal_id == Deal.id)
        .filter(ApprovalRequest.status == ApprovalRequestStatus.PENDING.value)
    )

    if actor.role == Role.ADMIN.value:
        pass
    elif actor.role == Role.SALES_MANAGER.value:
        query = query.filter(ApprovalRequest.required_level == "SALES_MANAGER")
    elif actor.role == Role.FINANCE.value:
        query = query.filter(ApprovalRequest.required_level == "FINANCE")
    else:
        raise ForbiddenError("Insufficient permissions to view approval queue.")

    total = query.count()
    total_pages = math.ceil(total / pagination.page_size) if pagination.page_size > 0 else 1

    offset = (pagination.page - 1) * pagination.page_size
    records = query.order_by(desc(ApprovalRequest.requested_at)).offset(offset).limit(pagination.page_size).all()

    items: List[PendingApprovalSummary] = []
    for req, deal in records:
        items.append(
            PendingApprovalSummary(
                deal_id=deal.id,
                deal_reference=deal.reference,
                odoo_sale_order_id=deal.odoo_sale_order_id,
                odoo_order_name=deal.odoo_order_name,
                partner_name=deal.partner_name_cache,
                approval_request_id=req.id,
                required_level=req.required_level,
                risk_score=deal.current_risk_score or Decimal("0.00"),
                amount_total=deal.amount_total_cache or Decimal("0.00"),
                requested_at=req.requested_at,
            )
        )

    meta = PaginationMeta(
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
        total_pages=total_pages,
    )
    return items, meta


def decide_approval_request(
    db: Session,
    gateway: OdooGateway,
    request_id: uuid.UUID,
    actor: DealflowUser,
    action: ApprovalActionType,
    reason: Optional[str] = None,
) -> ApprovalRequest:
    req = db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).first()
    if not req:
        raise EntityNotFoundError("ApprovalRequest", str(request_id))

    if req.status != ApprovalRequestStatus.PENDING.value:
        raise BusinessRuleError(
            code="REQUEST_NOT_PENDING",
            message=f"Approval request is not pending (current status: {req.status}).",
        )

    deal = db.query(Deal).filter(Deal.id == req.deal_id).first()
    if not deal:
        raise EntityNotFoundError("Deal", str(req.deal_id))

    # Self-approval guard: Sales rep cannot approve a deal they own
    if deal.owner_odoo_user_id == actor.odoo_user_id:
        raise BusinessRuleError(
            code="OWN_DEAL",
            message="Sales reps cannot approve deals they own.",
        )

    # Role authorization guard
    if actor.role == Role.ADMIN.value:
        pass
    elif req.required_level == "SALES_MANAGER":
        if actor.role != Role.SALES_MANAGER.value:
            raise ForbiddenError("Only Sales Managers can decide stage 1 approval requests.")
    elif req.required_level == "FINANCE":
        if actor.role != Role.FINANCE.value:
            raise ForbiddenError("Only Finance can decide stage 2 approval requests.")
    else:
        raise ForbiddenError(f"User role {actor.role} not authorized for required level {req.required_level}.")

    # Record approval action
    action_val = action.value if hasattr(action, "value") else str(action)
    action_entry = ApprovalAction(
        approval_request_id=req.id,
        actor_odoo_user_id=actor.odoo_user_id,
        actor_role=actor.role,
        action=action_val,
        reason=reason,
        created_at=utc_now(),
    )
    db.add(action_entry)

    if action == ApprovalActionType.APPROVE:
        req.status = ApprovalRequestStatus.APPROVED.value
        req.completed_at = utc_now()
        req.decided_by_odoo_user_id = actor.odoo_user_id
        req.decision_reason = reason

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.APPROVAL_APPROVED,
            entity_type="approval_request",
            entity_id=str(req.id),
            deal_id=deal.id,
            actor_type="USER",
            actor_id=actor.odoo_user_id,
            actor_role=actor.role,
            reason=f"Approved at level {req.required_level}. Reason: {reason or 'None'}",
        )

        # Check if deal requires 2-stage approval and stage 1 just passed
        if (
            deal.required_level == ApprovalLevel.MANAGER_AND_FINANCE.value
            and req.required_level == "SALES_MANAGER"
        ):
            # Create Stage 2 (FINANCE)
            stage2_req = ApprovalRequest(
                deal_id=deal.id,
                risk_assessment_id=req.risk_assessment_id,
                required_level="FINANCE",
                sequence=2,
                status=ApprovalRequestStatus.PENDING.value,
                requested_at=utc_now(),
            )
            db.add(stage2_req)
            deal.approval_state = ApprovalState.PENDING_FINANCE.value
            deal.last_activity_at = utc_now()

            audit_service.record_audit_event(
                db=db,
                event_type=AuditEventType.APPROVAL_CREATED,
                entity_type="approval_request",
                entity_id=str(stage2_req.id),
                deal_id=deal.id,
                actor_type="SYSTEM",
                reason="Stage 2 finance approval required following manager approval",
            )
        else:
            # Final approval stage passed
            deal.approval_state = ApprovalState.APPROVED.value
            deal.approved_assessment_id = req.risk_assessment_id
            deal.last_activity_at = utc_now()

            # Unlock quotation in Odoo
            try:
                gateway.set_governance(
                    order_id=deal.odoo_sale_order_id,
                    approval_state=ApprovalState.APPROVED.value,
                    risk_score=float(deal.current_risk_score or Decimal("0.00")),
                    locked=False,
                )
            except Exception:
                pass

            # If customer previously accepted, auto-confirm in Odoo
            if deal.customer_confirmed_pending:
                try:
                    gateway.confirm(order_id=deal.odoo_sale_order_id)
                    deal.status = DealStatus.CONFIRMED.value
                    deal.confirmed_at = utc_now()
                    deal.customer_confirmed_pending = False

                    audit_service.record_audit_event(
                        db=db,
                        event_type=AuditEventType.ORDER_CONFIRMED,
                        entity_type="deal",
                        entity_id=str(deal.id),
                        deal_id=deal.id,
                        actor_type="SYSTEM",
                        reason="Sale order confirmed in Odoo following final governance approval of customer-accepted deal",
                    )
                except Exception:
                    pass

            # Notify sales rep of final approval
            notification_service.create_notification(
                db=db,
                recipient_odoo_user_id=deal.owner_odoo_user_id,
                type="APPROVAL_APPROVED",
                title=f"Deal {deal.reference} Approved",
                body=f"Deal {deal.reference} ({deal.odoo_order_name}) has received final governance approval.",
                entity_type="deal",
                entity_id=str(deal.id),
                dedupe_key=f"app_approved_{deal.id}",
            )

    elif action == ApprovalActionType.REJECT:
        req.status = ApprovalRequestStatus.REJECTED.value
        req.completed_at = utc_now()
        req.decided_by_odoo_user_id = actor.odoo_user_id
        req.decision_reason = reason

        deal.approval_state = ApprovalState.REJECTED.value
        deal.last_activity_at = utc_now()

        # Keep locked in Odoo
        try:
            gateway.set_governance(
                order_id=deal.odoo_sale_order_id,
                approval_state=ApprovalState.REJECTED.value,
                risk_score=float(deal.current_risk_score or Decimal("0.00")),
                locked=True,
            )
        except Exception:
            pass

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.APPROVAL_REJECTED,
            entity_type="approval_request",
            entity_id=str(req.id),
            deal_id=deal.id,
            actor_type="USER",
            actor_id=actor.odoo_user_id,
            actor_role=actor.role,
            reason=f"Rejected: {reason or 'No reason specified'}",
        )

        notification_service.create_notification(
            db=db,
            recipient_odoo_user_id=deal.owner_odoo_user_id,
            type="APPROVAL_REJECTED",
            title=f"Deal {deal.reference} Rejected",
            body=f"Deal {deal.reference} was rejected by {actor.role}: {reason or 'No reason specified'}",
            entity_type="deal",
            entity_id=str(deal.id),
        )

    elif action == ApprovalActionType.RETURN:
        req.status = ApprovalRequestStatus.RETURNED.value
        req.completed_at = utc_now()
        req.decided_by_odoo_user_id = actor.odoo_user_id
        req.decision_reason = reason

        deal.approval_state = ApprovalState.RETURNED.value
        deal.last_activity_at = utc_now()

        # Unlock quotation in Odoo for sales rep editing
        try:
            gateway.set_governance(
                order_id=deal.odoo_sale_order_id,
                approval_state=ApprovalState.RETURNED.value,
                risk_score=float(deal.current_risk_score or Decimal("0.00")),
                locked=False,
            )
        except Exception:
            pass

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.APPROVAL_RETURNED,
            entity_type="approval_request",
            entity_id=str(req.id),
            deal_id=deal.id,
            actor_type="USER",
            actor_id=actor.odoo_user_id,
            actor_role=actor.role,
            reason=f"Returned for revision: {reason or 'No reason specified'}",
        )

        notification_service.create_notification(
            db=db,
            recipient_odoo_user_id=deal.owner_odoo_user_id,
            type="APPROVAL_RETURNED",
            title=f"Deal {deal.reference} Returned",
            body=f"Deal {deal.reference} was returned for revisions: {reason or 'No reason specified'}",
            entity_type="deal",
            entity_id=str(deal.id),
        )

    elif action == ApprovalActionType.ESCALATE:
        req.decision_reason = reason
        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.APPROVAL_ESCALATED,
            entity_type="approval_request",
            entity_id=str(req.id),
            deal_id=deal.id,
            actor_type="USER",
            actor_id=actor.odoo_user_id,
            actor_role=actor.role,
            reason=f"Escalated: {reason or 'No reason specified'}",
        )

        notification_service.create_notification(
            db=db,
            recipient_odoo_user_id=deal.owner_odoo_user_id,
            type="APPROVAL_ESCALATED",
            title=f"Deal {deal.reference} Escalated",
            body=f"Deal {deal.reference} has been escalated: {reason or 'No reason specified'}",
            entity_type="deal",
            entity_id=str(deal.id),
        )

    db.commit()
    db.refresh(req)
    return req


def get_deal_approval_timeline(db: Session, deal_id: uuid.UUID) -> List[ApprovalTimelineItem]:
    requests = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.deal_id == deal_id)
        .order_by(ApprovalRequest.sequence.asc(), ApprovalRequest.requested_at.asc())
        .all()
    )

    items: List[ApprovalTimelineItem] = []
    for req in requests:
        last_action = req.actions[-1].action if req.actions else None
        last_reason = req.actions[-1].reason if req.actions and req.actions[-1].reason else req.decision_reason
        last_actor = req.actions[-1].actor_odoo_user_id if req.actions else req.decided_by_odoo_user_id
        items.append(
            ApprovalTimelineItem(
                id=req.id,
                sequence=req.sequence,
                level=req.required_level,
                status=req.status,
                requested_at=req.requested_at,
                completed_at=req.completed_at,
                actor_id=last_actor,
                action=last_action,
                reason=last_reason,
            )
        )
    return items
