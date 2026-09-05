from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field

FORBIDDEN_PORTAL_KEYS = {
    "cost_price",
    "cost",
    "margin_pct",
    "margin_pct_cache",
    "margin_amount",
    "current_risk_score",
    "risk_score",
    "current_severity",
    "current_assessment_id",
    "approved_assessment_id",
    "required_level",
    "risk_factors",
    "is_internal",
    "decided_by_odoo_user_id",
    "decision_reason",
    "algorithm_notes",
    "shipping_cost_weight",
    "warehouse_profile",
    "tier_ceiling",
}


class PortalLineRead(BaseModel):
    odoo_sale_order_line_id: int
    odoo_product_id: int
    product_name: str
    product_uom_qty: Decimal
    price_unit: Decimal
    discount_pct: Decimal = Decimal("0.00")
    price_subtotal: Decimal

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PortalCommentPublicRead(BaseModel):
    id: uuid.UUID
    odoo_sale_order_line_id: Optional[int] = None
    author_role: str
    body: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PortalNegotiationRead(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    message: Optional[str] = None
    response_message: Optional[str] = None
    counter_value: Optional[Decimal] = None
    created_at: datetime
    processed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PortalDealRead(BaseModel):
    reference: str
    odoo_sale_order_id: int
    odoo_order_name: str
    partner_name: Optional[str] = None
    currency_code: str
    status: str
    order_discount_pct: Decimal
    amount_untaxed: Decimal
    amount_total: Decimal
    customer_confirmed_pending: bool
    sent_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    promised_delivery_date: Optional[datetime] = None
    lines: List[PortalLineRead] = Field(default_factory=list)
    comments: List[PortalCommentPublicRead] = Field(default_factory=list)
    negotiations: List[PortalNegotiationRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class PortalNegotiationSubmit(BaseModel):
    type: Optional[str] = None
    request_type: Optional[str] = None
    message: Optional[str] = None
    counter_value: Optional[Decimal] = None
    odoo_sale_order_line_id: Optional[int] = None
    odoo_line_id: Optional[int] = None
    requested_qty: Optional[Decimal] = None
    requested_product_id: Optional[int] = None

    @property
    def resolved_type(self) -> str:
        return self.type or self.request_type or "COMMENT"

    @property
    def resolved_line_id(self) -> Optional[int]:
        return self.odoo_sale_order_line_id or self.odoo_line_id


class PortalConfirmDealRequest(BaseModel):
    accepted: bool = True
    signature_name: Optional[str] = None
    comment: Optional[str] = None
