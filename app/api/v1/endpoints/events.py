from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.deps import get_db, get_odoo_gateway
from app.core.errors import UnauthorizedError
from app.db.base import utc_now
from app.models.cross_cutting import ProcessedEvent
from app.models.deal import Deal
from app.models.enums import AuditEventType, DealStatus
from app.odoo.interface import OdooGateway
from app.schemas.common import DataResponse
from app.services import audit_service

router = APIRouter()


class OdooEventPayload(BaseModel):
    event_id: str = Field(..., min_length=1)
    event_type: str = Field(..., min_length=1)
    model: Optional[str] = None
    record_id: Optional[int] = None
    order_id: Optional[int] = None
    data: Optional[Dict[str, Any]] = None
    payload: Optional[Dict[str, Any]] = None
    timestamp: Optional[str] = None

    @property
    def resolved_record_id(self) -> int:
        return self.record_id or self.order_id or 0

    @property
    def resolved_model(self) -> str:
        return self.model or "sale.order"

    @property
    def resolved_data(self) -> Dict[str, Any]:
        return self.data if self.data is not None else (self.payload or {})


@router.post("/odoo", response_model=DataResponse[Dict[str, Any]])
def handle_odoo_inbound_event(
    payload: OdooEventPayload,
    db: Session = Depends(get_db),
    gateway: OdooGateway = Depends(get_odoo_gateway),
    x_odoo_signature: Optional[str] = Header(None, alias="X-Odoo-Signature"),
    authorization: Optional[str] = Header(None),
):
    # 1. Security verification
    if settings.DEALFLOW_ODOO_MODE != "fake" and settings.ENVIRONMENT not in ("test", "testing"):
        if settings.ODOO_WEBHOOK_SECRET:
            secret = settings.ODOO_WEBHOOK_SECRET
            auth_token = None
            if authorization and authorization.startswith("Bearer "):
                auth_token = authorization.replace("Bearer ", "").strip()

            if x_odoo_signature != secret and auth_token != secret:
                raise UnauthorizedError("Invalid or missing webhook signature.")
    elif x_odoo_signature or authorization:
        secret = settings.ODOO_WEBHOOK_SECRET
        auth_token = authorization.replace("Bearer ", "").strip() if (authorization and authorization.startswith("Bearer ")) else None
        if x_odoo_signature and x_odoo_signature != secret:
            raise UnauthorizedError("Invalid webhook signature.")
        if auth_token and auth_token != secret:
            raise UnauthorizedError("Invalid webhook signature.")

    # 2. Idempotency check
    existing = db.query(ProcessedEvent).filter(ProcessedEvent.event_id == payload.event_id).first()
    if existing:
        return DataResponse(
            data={
                "status": "duplicate",
                "event_id": payload.event_id,
                "message": "Event has already been processed.",
            }
        )

    # 3. Register processing state
    processed_rec = ProcessedEvent(
        event_id=payload.event_id,
        source="ODOO",
        event_type=payload.event_type,
        received_at=utc_now(),
        result="PROCESSING",
    )
    db.add(processed_rec)
    db.flush()

    # 4. Dispatch logic
    target_record_id = payload.resolved_record_id
    deal = db.query(Deal).filter(Deal.odoo_sale_order_id == target_record_id).first()
    if deal:
        processed_rec.deal_id = deal.id

    if payload.resolved_model == "sale.order":
        if "cancel" in payload.event_type.lower():
            if deal:
                deal.status = DealStatus.CANCELLED.value
                deal.last_activity_at = utc_now()
                audit_service.record_audit_event(
                    db=db,
                    event_type=AuditEventType.ORDER_CONFIRMED,
                    entity_type="deal",
                    entity_id=str(deal.id),
                    deal_id=deal.id,
                    actor_type="SYSTEM",
                    reason=f"Order {target_record_id} cancelled in Odoo",
                    source_event_id=payload.event_id,
                )
        else:
            if deal:
                deal.last_synced_at = utc_now()
                audit_service.record_audit_event(
                    db=db,
                    event_type=AuditEventType.DEAL_SYNCED,
                    entity_type="deal",
                    entity_id=str(deal.id),
                    deal_id=deal.id,
                    actor_type="SYSTEM",
                    reason=f"Webhook sync for order {target_record_id}",
                    source_event_id=payload.event_id,
                )

    processed_rec.result = "PROCESSED"
    processed_rec.processed_at = utc_now()
    db.commit()

    return DataResponse(
        data={
            "status": "processed",
            "event_id": payload.event_id,
            "deal_id": str(deal.id) if deal else None,
        }
    )
