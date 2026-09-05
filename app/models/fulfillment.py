from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship
from app.db.base import Base, UUID_TYPE, JSON_TYPE, utc_now
from app.models.enums import FulfillmentPlanStatus


class FulfillmentPlan(Base):
    __tablename__ = "fulfillment_plan"

    deal_id = Column(UUID_TYPE, ForeignKey("deal.id"), nullable=False, index=True)
    odoo_sale_order_id = Column(BigInteger, nullable=False, index=True)
    status = Column(String(50), nullable=False, default=FulfillmentPlanStatus.PROPOSED.value, index=True)
    estimated_shipments = Column(Integer, nullable=False, default=1)
    estimated_shipping_cost = Column(Numeric(10, 2), nullable=False, default=0)
    strategy = Column(String(50), nullable=False)
    algorithm_version = Column(String(20), nullable=False, default="1.0")
    algorithm_notes = Column(JSON_TYPE, nullable=False, default=dict)
    generated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    accepted_by_odoo_user_id = Column(Integer, nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    applied_at = Column(DateTime(timezone=True), nullable=True)
    odoo_picking_ids = Column(JSON_TYPE, nullable=True)

    lines = relationship("FulfillmentPlanLine", backref="plan", cascade="all, delete-orphan", lazy="joined")


class FulfillmentPlanLine(Base):
    __tablename__ = "fulfillment_plan_line"

    __table_args__ = (
        CheckConstraint("allocated_qty + backorder_qty = requested_qty", name="chk_plan_line_qty_invariant"),
    )

    fulfillment_plan_id = Column(UUID_TYPE, ForeignKey("fulfillment_plan.id"), nullable=False, index=True)
    odoo_sale_order_line_id = Column(BigInteger, nullable=False)
    odoo_product_id = Column(Integer, nullable=False)
    odoo_warehouse_id = Column(Integer, nullable=True)
    requested_qty = Column(Integer, nullable=False)
    allocated_qty = Column(Integer, nullable=False, default=0)
    backorder_qty = Column(Integer, nullable=False, default=0)
    shipping_cost = Column(Numeric(10, 2), nullable=False, default=0)


class WarehouseProfile(Base):
    __tablename__ = "warehouse_profile"

    odoo_warehouse_id = Column(Integer, unique=True, nullable=False, index=True)
    shipping_cost_weight = Column(Numeric(10, 2), nullable=False, default=10.0)
    priority = Column(Integer, nullable=False, default=1)
    lead_time_days = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
