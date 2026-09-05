from datetime import datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import NegotiationRequestStatus, NegotiationRequestType


class NegotiationChangeCreate(BaseModel):
    odoo_sale_order_line_id: Optional[int] = None
    field_name: str
    old_value: Optional[str] = None
    requested_value: Optional[str] = None


class NegotiationChangeRead(BaseModel):
    id: uuid.UUID
    negotiation_request_id: uuid.UUID
    odoo_sale_order_line_id: Optional[int] = None
    field_name: str
    old_value: Optional[str] = None
    requested_value: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class NegotiationRequestCreate(BaseModel):
    type: NegotiationRequestType
    message: Optional[str] = None
    counter_value: Optional[Decimal] = None
    changes: List[NegotiationChangeCreate] = Field(default_factory=list)


class NegotiationRequestRead(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    odoo_sale_order_id: int
    customer_partner_id: int
    requested_by_odoo_user_id: int
    type: NegotiationRequestType
    status: NegotiationRequestStatus
    message: Optional[str] = None
    based_on_assessment_id: Optional[uuid.UUID] = None
    created_at: datetime
    processed_at: Optional[datetime] = None
    processed_by_odoo_user_id: Optional[int] = None
    response_message: Optional[str] = None
    counter_value: Optional[Decimal] = None
    applied_assessment_id: Optional[uuid.UUID] = None
    changes: List[NegotiationChangeRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class NegotiationRespondRequest(BaseModel):
    action: Optional[str] = None
    decision: Optional[str] = None
    response_message: Optional[str] = None
    message: Optional[str] = None
    counter_value: Optional[Decimal] = None

    def get_action(self) -> str:
        act = (self.action or self.decision or "ACCEPT").strip().upper()
        if act in ("ACCEPT", "ACCEPTED"):
            return "ACCEPT"
        if act in ("REJECT", "REJECTED"):
            return "REJECT"
        if act == "COUNTER":
            return "COUNTER"
        return act

    def get_message(self) -> Optional[str]:
        return self.response_message or self.message


class PortalCommentCreate(BaseModel):
    body: str = Field(..., min_length=1)
    odoo_sale_order_line_id: Optional[int] = None
    is_internal: bool = False


class PortalCommentRead(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    odoo_sale_order_line_id: Optional[int] = None
    author_odoo_user_id: int
    author_role: str
    body: str
    is_internal: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
