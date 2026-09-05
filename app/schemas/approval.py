from datetime import datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict
from app.models.enums import ApprovalActionType, ApprovalRequestStatus


class ApprovalActionCreate(BaseModel):
    action: ApprovalActionType
    reason: Optional[str] = None


class ApprovalActionRead(BaseModel):
    id: uuid.UUID
    approval_request_id: uuid.UUID
    actor_odoo_user_id: int
    actor_role: str
    action: ApprovalActionType
    reason: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApprovalRequestRead(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    risk_assessment_id: uuid.UUID
    required_level: str
    sequence: int
    status: ApprovalRequestStatus
    requested_at: datetime
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    decided_by_odoo_user_id: Optional[int] = None
    decision_reason: Optional[str] = None
    actions: List[ApprovalActionRead] = []

    model_config = ConfigDict(from_attributes=True)


class ApprovalTimelineItem(BaseModel):
    id: uuid.UUID
    sequence: int
    level: str
    status: str
    requested_at: datetime
    completed_at: Optional[datetime] = None
    actor_id: Optional[int] = None
    action: Optional[str] = None
    reason: Optional[str] = None


class PendingApprovalSummary(BaseModel):
    deal_id: uuid.UUID
    deal_reference: str
    odoo_sale_order_id: int
    odoo_order_name: str
    partner_name: Optional[str] = None
    approval_request_id: uuid.UUID
    required_level: str
    risk_score: Decimal
    amount_total: Decimal
    requested_at: datetime

    model_config = ConfigDict(from_attributes=True)
