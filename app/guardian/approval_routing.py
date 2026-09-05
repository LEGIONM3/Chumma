import uuid
from typing import Optional
from sqlalchemy.orm import Session
from app.db.base import utc_now
from app.models.approval import ApprovalRequest
from app.models.deal import Deal
from app.models.enums import ApprovalLevel, ApprovalRequestStatus, ApprovalState, AuditEventType
from app.services import audit_service


def apply_approval_routing(
    db: Session,
    deal: Deal,
    risk_assessment_id: uuid.UUID,
    required_level: ApprovalLevel,
    is_covered: bool = False,
    actor_id: Optional[int] = None,
    actor_role: Optional[str] = None,
) -> ApprovalState:
    # 1. If previously approved and new risk is covered, maintain approved state
    if is_covered and deal.approval_state == ApprovalState.APPROVED.value:
        return ApprovalState.APPROVED

    # 2. Invalidate any existing PENDING approval requests
    pending_requests = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.deal_id == deal.id, ApprovalRequest.status == ApprovalRequestStatus.PENDING.value)
        .all()
    )
    for req in pending_requests:
        req.status = ApprovalRequestStatus.INVALIDATED.value
        req.completed_at = utc_now()
        req.decision_reason = "Superseded by re-evaluation"
        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.APPROVAL_INVALIDATED,
            entity_type="approval_request",
            entity_id=str(req.id),
            deal_id=deal.id,
            actor_type="SYSTEM",
            reason="Invalidated by new risk evaluation",
        )

    # 3. Handle routing
    if required_level == ApprovalLevel.NONE:
        deal.approval_state = ApprovalState.EVALUATED_NO_APPROVAL.value
        return ApprovalState.EVALUATED_NO_APPROVAL

    elif required_level in (ApprovalLevel.MANAGER, ApprovalLevel.MANAGER_AND_FINANCE):
        new_req = ApprovalRequest(
            deal_id=deal.id,
            risk_assessment_id=risk_assessment_id,
            required_level="SALES_MANAGER",
            sequence=1,
            status=ApprovalRequestStatus.PENDING.value,
            requested_at=utc_now(),
        )
        db.add(new_req)
        db.flush()

        deal.approval_state = ApprovalState.PENDING_MANAGER.value

        audit_service.record_audit_event(
            db=db,
            event_type=AuditEventType.APPROVAL_CREATED,
            entity_type="approval_request",
            entity_id=str(new_req.id),
            deal_id=deal.id,
            actor_type="SYSTEM",
            reason=f"Stage 1 manager approval required ({required_level.value})",
        )
        return ApprovalState.PENDING_MANAGER

    deal.approval_state = ApprovalState.NOT_EVALUATED.value
    return ApprovalState.NOT_EVALUATED
