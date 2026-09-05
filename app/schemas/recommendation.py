from datetime import datetime
from decimal import Decimal
from typing import Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import RecommendationStatus, RecommendationType


class RecommendationRuleRead(BaseModel):
    id: uuid.UUID
    odoo_company_id: int
    trigger_product_id: int
    suggested_product_id: int
    co_purchase_count: int
    manual_boost: Decimal
    is_promoted_override: Optional[bool] = None
    source: str
    active: bool

    model_config = ConfigDict(from_attributes=True)


class RecommendationRuleCreate(BaseModel):
    odoo_company_id: int = 1
    trigger_product_id: int
    suggested_product_id: int
    co_purchase_count: int = 0
    manual_boost: Decimal = Decimal("0.0")
    is_promoted_override: Optional[bool] = None
    source: str = "MANUAL"
    active: bool = True


class RecommendationRuleUpdate(BaseModel):
    co_purchase_count: Optional[int] = None
    manual_boost: Optional[Decimal] = None
    is_promoted_override: Optional[bool] = None
    active: Optional[bool] = None


class RecommendationRead(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    risk_assessment_id: uuid.UUID
    odoo_product_id: int
    product_name_cache: str
    recommendation_type: RecommendationType
    score: Decimal
    co_purchase_score: Decimal
    promotion_score: Decimal
    margin_score: Decimal
    relevance_score: Decimal
    margin_delta_amount: Decimal
    margin_delta_pct: Decimal
    unit_price_cache: Decimal
    reason: str
    source: str
    status: RecommendationStatus
    created_at: datetime
    added_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class RecommendationActionRequest(BaseModel):
    action: Optional[str] = "ADD"
    quantity: Optional[Decimal] = Decimal("1.0")
