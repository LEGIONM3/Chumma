from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from app.models.enums import FulfillmentPlanStatus


class FulfillmentPlanLineRead(BaseModel):
    id: uuid.UUID
    fulfillment_plan_id: uuid.UUID
    odoo_sale_order_line_id: int
    odoo_product_id: int
    odoo_warehouse_id: Optional[int] = None
    requested_qty: int
    allocated_qty: int
    backorder_qty: int
    shipping_cost: Decimal

    model_config = ConfigDict(from_attributes=True)


class FulfillmentPlanRead(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    odoo_sale_order_id: int
    status: FulfillmentPlanStatus
    estimated_shipments: int
    estimated_shipping_cost: Decimal
    strategy: str
    algorithm_version: str
    algorithm_notes: Dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime
    accepted_by_odoo_user_id: Optional[int] = None
    accepted_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None
    odoo_picking_ids: Optional[Any] = None
    lines: List[FulfillmentPlanLineRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class FulfillmentPlanGenerateRequest(BaseModel):
    strategy: Optional[str] = None


class FulfillmentOverrideLineItem(BaseModel):
    odoo_sale_order_line_id: Optional[int] = None
    line_id: Optional[int] = None
    odoo_product_id: Optional[int] = None
    product_id: Optional[int] = None
    odoo_warehouse_id: Optional[int] = None
    warehouse_id: Optional[int] = None
    allocated_qty: Optional[int] = None
    qty: Optional[int] = None
    backorder_qty: Optional[int] = 0
    shipping_cost: Optional[Decimal] = Decimal("0.00")


class FulfillmentPlanOverrideRequest(BaseModel):
    allocations: Optional[List[FulfillmentOverrideLineItem]] = None
    lines: Optional[List[FulfillmentOverrideLineItem]] = None
    reason: str = Field(..., min_length=1, description="Reason required for manual fulfillment override")


class FulfillmentConsolidateRequest(BaseModel):
    warehouse_id: Optional[int] = None
    qty: Optional[int] = None


class WarehouseProfileRead(BaseModel):
    id: uuid.UUID
    odoo_warehouse_id: int
    shipping_cost_weight: Decimal
    priority: int
    lead_time_days: int
    active: bool

    model_config = ConfigDict(from_attributes=True)


class WarehouseProfileCreate(BaseModel):
    odoo_warehouse_id: int
    shipping_cost_weight: Decimal = Decimal("10.00")
    priority: int = 1
    lead_time_days: int = 0
    active: bool = True


class WarehouseProfileUpdate(BaseModel):
    shipping_cost_weight: Optional[Decimal] = None
    priority: Optional[int] = None
    lead_time_days: Optional[int] = None
    active: Optional[bool] = None


class FulfillmentExceptionRead(BaseModel):
    deal_id: uuid.UUID
    deal_reference: str
    odoo_sale_order_id: int
    customer_name: str
    plan_id: uuid.UUID
    product_id: int
    backorder_qty: int
    created_at: datetime


class DealFulfillmentResponse(BaseModel):
    deal_id: uuid.UUID
    plan: Optional[FulfillmentPlanRead] = None
    pickings: List[Dict[str, Any]] = Field(default_factory=list)
    backorders: List[FulfillmentPlanLineRead] = Field(default_factory=list)
