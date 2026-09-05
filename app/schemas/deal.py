from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import ApprovalLevel, ApprovalState, DealStatus, HealthStatus, RiskSeverity


class DealLineRead(BaseModel):
    odoo_sale_order_line_id: int
    odoo_product_id: int
    product_name: str
    product_uom_qty: Decimal
    price_unit: Decimal
    discount_pct: Decimal = Decimal("0.00")
    price_subtotal: Decimal
    cost_price: Decimal = Decimal("0.00")
    margin_amount: Decimal = Decimal("0.00")
    margin_pct: Decimal = Decimal("0.00")
    is_recurring: bool = False
    odoo_product_category_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class DealRead(BaseModel):
    id: uuid.UUID
    reference: str
    odoo_sale_order_id: int
    odoo_order_name: str
    odoo_partner_id: int
    partner_name_cache: Optional[str] = None
    tier_code: Optional[str] = None
    owner_odoo_user_id: int
    sales_team_odoo_id: Optional[int] = None
    odoo_company_id: int = 1
    currency_code: str = "INR"
    status: DealStatus
    approval_state: ApprovalState
    health_status: HealthStatus
    current_risk_score: Optional[Decimal] = None
    current_severity: Optional[str] = None
    current_assessment_id: Optional[uuid.UUID] = None
    approved_assessment_id: Optional[uuid.UUID] = None
    required_level: ApprovalLevel
    order_discount_pct: Decimal
    amount_total_cache: Decimal
    amount_untaxed_cache: Decimal
    margin_pct_cache: Decimal
    one_time_total_cache: Decimal
    recurring_first_cycle_total_cache: Decimal
    customer_confirmed_pending: bool
    sent_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    promised_delivery_date: Optional[datetime] = None
    last_activity_at: datetime
    last_synced_at: datetime
    odoo_write_date_seen: Optional[str] = None
    version: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class DealDetailRead(DealRead):
    lines: List[DealLineRead] = Field(default_factory=list)
    next_best_actions: List[dict[str, Any]] = Field(default_factory=list)


class DealLineCreate(BaseModel):
    product_id: int
    qty: float = 1.0
    discount_pct: float = 0.0


class DealCreateDirectRequest(BaseModel):
    partner_id: int
    currency: Optional[str] = "INR"
    lines: List[DealLineCreate] = Field(default_factory=list)


class DealCreateFromOdoo(BaseModel):
    odoo_sale_order_id: Optional[int] = None
    partner_id: Optional[int] = None
    currency: Optional[str] = "INR"
    lines: Optional[List[DealLineCreate]] = None


class DealSyncRequest(BaseModel):
    force: bool = False


class DealPatchRequest(BaseModel):
    promised_delivery_date: Optional[datetime] = None
    tier_code: Optional[str] = None


class DealFilterParams(BaseModel):
    status: Optional[DealStatus] = None
    approval_state: Optional[ApprovalState] = None
    health_status: Optional[HealthStatus] = None
    owner_id: Optional[int] = None
    sales_team_id: Optional[int] = None
    partner_id: Optional[int] = None
    min_risk_score: Optional[Decimal] = None
    max_risk_score: Optional[Decimal] = None
    query: Optional[str] = None
