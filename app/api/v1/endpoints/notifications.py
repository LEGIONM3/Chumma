from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.deps import get_current_active_user, get_db
from app.core.errors import NotFoundError
from app.models.cross_cutting import Notification
from app.models.identity import DealflowUser
from app.schemas.common import DataResponse, MessageResponse
from app.services import notification_service

router = APIRouter()


@router.get("", response_model=DataResponse[List[Dict[str, Any]]])
def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    notifs = notification_service.get_user_notifications(
        db=db,
        recipient_odoo_user_id=current_user.odoo_user_id,
        unread_only=unread_only,
        limit=limit,
    )
    data = [
        {
            "id": str(n.id),
            "recipient_odoo_user_id": n.recipient_odoo_user_id,
            "type": n.type,
            "title": n.title,
            "body": n.body,
            "entity_type": n.entity_type,
            "entity_id": n.entity_id,
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notifs
    ]
    return DataResponse(data=data)


@router.post("/{notification_id}/read", response_model=DataResponse[MessageResponse])
def mark_notification_read(
    notification_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    success = notification_service.mark_as_read(
        db=db,
        notification_id=notification_id,
        recipient_odoo_user_id=current_user.odoo_user_id,
    )
    if not success:
        raise NotFoundError("Notification not found.")
    return DataResponse(data=MessageResponse(message="Notification marked as read."))


@router.post("/read-all", response_model=DataResponse[MessageResponse])
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: DealflowUser = Depends(get_current_active_user),
):
    count = notification_service.mark_all_as_read(
        db=db,
        recipient_odoo_user_id=current_user.odoo_user_id,
    )
    return DataResponse(data=MessageResponse(message=f"{count} notifications marked as read."))
