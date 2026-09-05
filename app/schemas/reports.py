from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class ReportPeriod(str, Enum):
    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    CUSTOM = "custom"


class ReportFormat(str, Enum):
    JSON = "json"
    PDF = "pdf"
    XLSX = "xlsx"


class ReportFilterParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    period: Optional[str] = Field("month", description="today | week | month | quarter | custom")
    from_date: Optional[datetime] = Field(None, alias="from")
    to_date: Optional[datetime] = Field(None, alias="to")
    team_id: Optional[int] = None
    rep_id: Optional[int] = None
    approval_status: Optional[str] = None
    product_id: Optional[int] = None
    category_id: Optional[int] = None
    format: Optional[str] = "json"


# 1. Summary Report Schemas
class ReportSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period: str
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    quotations_created: int = 0
    quotations_sent: int = 0
    quotations_confirmed: int = 0
    win_rate: Decimal = Decimal("0.00")
    revenue_confirmed: Decimal = Decimal("0.00")
    avg_discount: Decimal = Decimal("0.00")
    avg_margin: Decimal = Decimal("0.00")
    avg_approval_turnaround_hours: Decimal = Decimal("0.00")
    pending_approvals_count: int = 0
    generated_at: datetime


# 2. Deals / Quotations Report Schemas
class ReportDealItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    deal_id: str
    reference: str
    odoo_order_name: str
    partner_name: str
    owner_odoo_user_id: int
    sales_team_odoo_id: Optional[int] = None
    status: str
    approval_state: str
    health_status: str
    current_risk_score: Optional[Decimal] = None
    current_severity: Optional[str] = None
    amount_total: Decimal = Decimal("0.00")
    order_discount_pct: Decimal = Decimal("0.00")
    margin_pct: Decimal = Decimal("0.00")
    created_at: datetime
    sent_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None


class ReportDealsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: List[ReportDealItem] = Field(default_factory=list)
    total: int = 0
    period: str
    generated_at: datetime


# Alias for backwards compatibility with quotations wording
ReportQuotationsResponse = ReportDealsResponse


# 3. Approvals Report Schemas
class ApprovalStatusBreakdown(BaseModel):
    status: str
    count: int = 0


class ApprovalStageTurnaround(BaseModel):
    required_level: str
    total_requests: int = 0
    avg_turnaround_hours: Decimal = Decimal("0.00")
    approved_count: int = 0
    rejected_count: int = 0
    returned_count: int = 0


class RejectionReasonCount(BaseModel):
    reason: str
    count: int = 0


class ReportApprovalsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    by_status: List[ApprovalStatusBreakdown] = Field(default_factory=list)
    turnaround_by_stage: List[ApprovalStageTurnaround] = Field(default_factory=list)
    top_rejection_reasons: List[RejectionReasonCount] = Field(default_factory=list)
    total_requests: int = 0
    completed_requests: int = 0
    approval_rate: Decimal = Decimal("0.00")
    period: str
    generated_at: datetime


# 4. Products Report Schemas
class ProductPerformanceItem(BaseModel):
    product_id: int
    product_name: str
    category_id: Optional[int] = None
    qty_sold: float = 0.0
    revenue: Decimal = Decimal("0.00")
    avg_discount_pct: Decimal = Decimal("0.00")


class ReportProductsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    best_selling_by_qty: List[ProductPerformanceItem] = Field(default_factory=list)
    best_selling_by_revenue: List[ProductPerformanceItem] = Field(default_factory=list)
    most_discounted: List[ProductPerformanceItem] = Field(default_factory=list)
    period: str
    generated_at: datetime


# 5. Discounts Report Schemas
class RepDiscountPerformanceItem(BaseModel):
    rep_id: int
    rep_name: str
    team_id: Optional[int] = None
    deals_count: int = 0
    total_revenue: Decimal = Decimal("0.00")
    avg_weighted_discount_pct: Decimal = Decimal("0.00")
    overage_count: int = 0
    overage_frequency_pct: Decimal = Decimal("0.00")
    anomaly_count: int = 0
    max_discount_pct: Decimal = Decimal("0.00")


class ReportDiscountsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rep_stats: List[RepDiscountPerformanceItem] = Field(default_factory=list)
    company_avg_discount_pct: Decimal = Decimal("0.00")
    total_anomalies: int = 0
    total_overages: int = 0
    period: str
    generated_at: datetime


# 6. Pipeline & Risk Report Schemas
class PipelineStageItem(BaseModel):
    status: str
    count: int = 0
    total_value: Decimal = Decimal("0.00")
    avg_discount_pct: Decimal = Decimal("0.00")
    avg_margin_pct: Decimal = Decimal("0.00")


class RiskSeverityBreakdown(BaseModel):
    severity: str
    count: int = 0
    total_value: Decimal = Decimal("0.00")
    avg_risk_score: Decimal = Decimal("0.00")


class TopRiskFactorItem(BaseModel):
    factor_type: str
    count: int = 0
    avg_contribution: Decimal = Decimal("0.00")


class ReportPipelineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pipeline_funnel: List[PipelineStageItem] = Field(default_factory=list)
    risk_distribution: List[RiskSeverityBreakdown] = Field(default_factory=list)
    top_risk_factors: List[TopRiskFactorItem] = Field(default_factory=list)
    total_pipeline_value: Decimal = Decimal("0.00")
    total_deals_count: int = 0
    period: str
    generated_at: datetime


ReportRiskResponse = ReportPipelineResponse


# 7. Fulfillment Report Schemas
class WarehouseFulfillmentItem(BaseModel):
    warehouse_id: int
    warehouse_name: str
    shipment_count: int = 0
    total_allocated_qty: int = 0
    total_shipping_cost: Decimal = Decimal("0.00")


class ReportFulfillmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    warehouse_breakdown: List[WarehouseFulfillmentItem] = Field(default_factory=list)
    split_rate: Decimal = Decimal("0.00")
    backorder_rate: Decimal = Decimal("0.00")
    on_time_pct: Decimal = Decimal("0.00")
    total_plans: int = 0
    total_shipments: int = 0
    period: str
    generated_at: datetime


# 8. Billing Report Schemas
class ReportBillingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_invoiced: Decimal = Decimal("0.00")
    total_paid: Decimal = Decimal("0.00")
    total_outstanding: Decimal = Decimal("0.00")
    overdue_amount: Decimal = Decimal("0.00")
    overdue_count: int = 0
    mrr: Decimal = Decimal("0.00")
    active_subscriptions_count: int = 0
    total_credit_notes_amount: Decimal = Decimal("0.00")
    credit_notes_count: int = 0
    period: str
    generated_at: datetime


# Legacy Metric Classes for backward compatibility
class PipelineFunnelMetric(BaseModel):
    stage: str
    count: int
    total_value: Decimal
    avg_discount_pct: Decimal
    avg_cycle_days: Decimal


class DiscountComplianceMetric(BaseModel):
    period: str
    total_deals: int
    within_policy_deals: int
    over_policy_deals: int
    compliance_pct: Decimal
    avg_discount_pct: Decimal
    max_discount_pct: Decimal


class TurnaroundMetric(BaseModel):
    approval_level: str
    total_requests: int
    avg_hours_to_decision: Decimal
    sla_breach_count: int
    approval_rate_pct: Decimal


class MarginLeakageMetric(BaseModel):
    category_name: str
    list_price_total: Decimal
    actual_revenue: Decimal
    discount_loss_amount: Decimal
    effective_margin_pct: Decimal
    target_margin_pct: Decimal
    leakage_amount: Decimal


class ReportComplianceResponse(BaseModel):
    items: List[DiscountComplianceMetric]
    generated_at: datetime


class ReportTurnaroundResponse(BaseModel):
    items: List[TurnaroundMetric]
    generated_at: datetime


class ReportLeakageResponse(BaseModel):
    items: List[MarginLeakageMetric]
    generated_at: datetime
