from typing import List, Optional
import uuid
from sqlalchemy.orm import Session
from app.db.base import utc_now
from app.models.cross_cutting import Notification


def create_notification(
    db: Session,
    recipient_odoo_user_id: int,
    type: str,
    title: str,
    body: str,
    entity_type: str,
    entity_id: str,
    dedupe_key: Optional[str] = None,
) -> Notification:
    if dedupe_key:
        existing = db.query(Notification).filter(Notification.dedupe_key == dedupe_key).first()
        if existing:
            return existing

    notif = Notification(
        recipient_odoo_user_id=recipient_odoo_user_id,
        type=type,
        title=title,
        body=body,
        entity_type=entity_type,
        entity_id=str(entity_id),
        is_read=False,
        dedupe_key=dedupe_key,
        created_at=utc_now(),
    )
    db.add(notif)
    db.flush()
    return notif


def get_user_notifications(
    db: Session,
    recipient_odoo_user_id: int,
    unread_only: bool = False,
    limit: int = 50,
) -> List[Notification]:
    query = db.query(Notification).filter(Notification.recipient_odoo_user_id == recipient_odoo_user_id)
    if unread_only:
        query = query.filter(Notification.is_read == False)
    return query.order_by(Notification.created_at.desc()).limit(limit).all()


def mark_as_read(
    db: Session,
    notification_id: uuid.UUID,
    recipient_odoo_user_id: int,
) -> bool:
    notif = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.recipient_odoo_user_id == recipient_odoo_user_id)
        .first()
    )
    if not notif:
        return False
    notif.is_read = True
    db.commit()
    return True


def mark_all_as_read(
    db: Session,
    recipient_odoo_user_id: int,
) -> int:
    count = (
        db.query(Notification)
        .filter(Notification.recipient_odoo_user_id == recipient_odoo_user_id, Notification.is_read == False)
        .update({Notification.is_read: True}, synchronize_session=False)
    )
    db.commit()
    return count
