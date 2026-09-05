from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import ApprovalLevel, ApprovalState, RiskSeverity, TriggerType


class CustomerTierRead(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    rank: int
    default_max_discount_pct: Decimal
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class CustomerTierCreate(BaseModel):
    code: str
    name: str
    rank: int = 1
    default_max_discount_pct: Decimal = Decimal("5.00")
    is_active: bool = True


class CustomerTierAssignmentRead(BaseModel):
    id: uuid.UUID
    odoo_partner_id: int
    tier_code: str

    model_config = ConfigDict(from_attributes=True)


class CustomerTierAssignmentUpdate(BaseModel):
    tier_code: str


class DiscountPolicyRead(BaseModel):
    id: uuid.UUID
    odoo_company_id: int
    name: str
    customer_tier_code: Optional[str] = None
    odoo_product_category_id: Optional[int] = None
    max_discount_pct: Decimal
    minimum_margin_pct: Optional[Decimal] = None
    manager_threshold: Optional[Decimal] = None
    finance_threshold: Optional[Decimal] = None
    single_line_finance_pts: Optional[Decimal] = None
    priority: int
    active: bool
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class DiscountPolicyCreate(BaseModel):
    odoo_company_id: int = 1
    name: str
    customer_tier_code: Optional[str] = None
    odoo_product_category_id: Optional[int] = None
    max_discount_pct: Decimal = Field(..., ge=0, le=100)
    minimum_margin_pct: Optional[Decimal] = Field(default=None, ge=0, le=100)
    manager_threshold: Optional[Decimal] = Field(default=None, ge=0, le=100)
    finance_threshold: Optional[Decimal] = Field(default=None, ge=0, le=100)
    single_line_finance_pts: Optional[Decimal] = Field(default=None, ge=0, le=100)
    priority: int = 10
    active: bool = True
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None


class DiscountPolicyUpdate(BaseModel):
    name: Optional[str] = None
    customer_tier_code: Optional[str] = None
    odoo_product_category_id: Optional[int] = None
    max_discount_pct: Optional[Decimal] = Field(default=None, ge=0, le=100)
    minimum_margin_pct: Optional[Decimal] = Field(default=None, ge=0, le=100)
    manager_threshold: Optional[Decimal] = Field(default=None, ge=0, le=100)
    finance_threshold: Optional[Decimal] = Field(default=None, ge=0, le=100)
    single_line_finance_pts: Optional[Decimal] = Field(default=None, ge=0, le=100)
    priority: Optional[int] = None
    active: Optional[bool] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None


class RiskFactorRead(BaseModel):
    factor_type: str
    source_reference: Optional[str] = None
    raw_value: Decimal
    weight: Decimal
    contribution: Decimal
    reason: str
    detail: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class RiskAssessmentRead(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    risk_score: Decimal
    severity: str
    required_level: str
    decision: str
    trigger_type: str
    trigger_event_id: Optional[str] = None
    policy_version: str
    resolved_policy: Dict[str, Any] = Field(default_factory=dict)
    totals: Dict[str, Any] = Field(default_factory=dict)
    line_snapshot: List[Dict[str, Any]] = Field(default_factory=list)
    factors: List[RiskFactorRead] = Field(default_factory=list)
    calculated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvaluationRequest(BaseModel):
    deal_id: Optional[uuid.UUID] = None
    odoo_sale_order_id: Optional[int] = None
    trigger_type: TriggerType = TriggerType.MANUAL


class EvaluationResponse(BaseModel):
    deal_id: uuid.UUID
    risk_assessment: RiskAssessmentRead
    approval_state: ApprovalState
    required_level: ApprovalLevel
    decision: str
    material_changes: List[str] = Field(default_factory=list)


class WhatIfSimulateRequest(BaseModel):
    deal_id: uuid.UUID
    order_discount_pct: Optional[Decimal] = None
    line_discounts: Optional[Dict[int, Decimal]] = None
    line_quantities: Optional[Dict[int, Decimal]] = None


class WhatIfSimulateResponse(BaseModel):
    simulated_risk_score: Decimal
    simulated_severity: str
    simulated_required_level: ApprovalLevel
    simulated_decision: str
    factors: List[RiskFactorRead] = Field(default_factory=list)
    margin_pct: Decimal
    amount_total: Decimal
