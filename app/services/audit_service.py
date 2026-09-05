from typing import Any, Dict, List, Optional
import uuid
from sqlalchemy.orm import Session
from app.db.base import utc_now
from app.models.audit import AuditEvent
from app.models.deal import Deal
from app.models.enums import AuditEventType


def record_audit_event(
    db: Session,
    event_type: AuditEventType,
    entity_type: str,
    entity_id: str,
    deal_id: Optional[uuid.UUID] = None,
    actor_type: str = "USER",
    actor_id: Optional[int] = None,
    actor_role: Optional[str] = None,
    before_state: Optional[Dict[str, Any]] = None,
    after_state: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    is_activity: bool = True,
    source_event_id: Optional[str] = None,
) -> AuditEvent:
    event = AuditEvent(
        deal_id=deal_id,
        event_type=event_type.value if isinstance(event_type, AuditEventType) else str(event_type),
        actor_type=actor_type,
        actor_id=actor_id,
        actor_role=actor_role,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before_state=before_state,
        after_state=after_state,
        reason=reason,
        metadata_json=metadata or {},
        is_activity=is_activity,
        source_event_id=source_event_id,
        created_at=utc_now(),
    )
    db.add(event)

    if deal_id:
        deal = db.query(Deal).filter(Deal.id == deal_id).first()
        if deal:
            deal.last_activity_at = utc_now()

    db.flush()
    return event


def get_deal_timeline(
    db: Session,
    deal_id: uuid.UUID,
    is_activity_only: bool = False,
) -> List[AuditEvent]:
    query = db.query(AuditEvent).filter(AuditEvent.deal_id == deal_id)
    if is_activity_only:
        query = query.filter(AuditEvent.is_activity == True)
    return query.order_by(AuditEvent.created_at.asc()).all()
