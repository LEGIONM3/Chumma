from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import AlertStatus, AlertType, HealthStatus, NextBestActionType


class NextBestActionRead(BaseModel):
    type: str
    priority: str
    title: str
    explanation: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    cta_endpoint: Optional[str] = None


class DealHealthSnapshotRead(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    health_status: HealthStatus
    overall_score: Decimal
    stalled_score: Decimal
    approval_delay_score: Decimal
    discount_anomaly_score: Decimal
    delivery_risk_score: Decimal
    negotiation_score: Decimal
    detail: Dict[str, Any] = Field(default_factory=dict)
    calculated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DealAlertRead(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    type: str
    severity: str
    status: str
    title: str
    detail: Dict[str, Any] = Field(default_factory=dict)
    raised_at: datetime
    acknowledged_by: Optional[int] = None
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    last_action: Optional[str] = None
    last_action_at: Optional[datetime] = None
    last_action_by: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class DealAlertActionRequest(BaseModel):
    action: str = Field(..., pattern="^(ACKNOWLEDGE|RESOLVE|NUDGE|ESCALATE)$")
    message: Optional[str] = None
    note: Optional[str] = None


class RepDiscountStatsRead(BaseModel):
    odoo_user_id: int
    sample_size: int
    avg_weighted_discount_pct: Decimal
    stddev: Decimal
    baseline_source: str
    computed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DealHealthDetailResponse(BaseModel):
    deal_id: uuid.UUID
    health_status: HealthStatus
    overall_score: Decimal
    components: Dict[str, Decimal]
    detail: Dict[str, Any] = Field(default_factory=dict)
    alerts: List[DealAlertRead] = Field(default_factory=list)
    next_best_action: Optional[NextBestActionRead] = None
    calculated_at: datetime


class ControlTowerKPIs(BaseModel):
    pipeline_value: Decimal
    at_risk_count: int
    pending_approvals: int
    discount_exposure_amount: Decimal
    stalled_count: int
    avg_approval_hours: Decimal
    fulfillment_risk_count: int


class ActionQueueItem(BaseModel):
    id: str
    item_type: str  # "ALERT" or "APPROVAL"
    deal_id: uuid.UUID
    deal_reference: Optional[str] = None
    partner_name: Optional[str] = None
    title: str
    severity: str  # "HIGH", "MEDIUM", "LOW"
    priority: str
    deep_link: str
    created_at: datetime
    detail: Dict[str, Any] = Field(default_factory=dict)


class ControlTowerResponse(BaseModel):
    kpis: ControlTowerKPIs
    action_queue: List[ActionQueueItem] = Field(default_factory=list)
    generated_at: datetime


class RecomputeAlertsResponse(BaseModel):
    scanned_deals: int
    stalled_alerts: int
    anomaly_alerts: int
    slippage_alerts: int
    message: str
