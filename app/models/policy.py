from decimal import Decimal
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, Numeric, String
from app.db.base import Base


class CustomerTier(Base):
    __tablename__ = "customer_tier"

    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    rank = Column(Integer, nullable=False, default=1)
    default_max_discount_pct = Column(Numeric(6, 2), nullable=False, default=Decimal("5.00"))
    is_active = Column(Boolean, nullable=False, default=True)


class CustomerTierAssignment(Base):
    __tablename__ = "customer_tier_assignment"

    odoo_partner_id = Column(Integer, unique=True, nullable=False, index=True)
    tier_code = Column(String(50), ForeignKey("customer_tier.code"), nullable=False)


class DiscountPolicy(Base):
    __tablename__ = "discount_policy"

    __table_args__ = (
        CheckConstraint("max_discount_pct >= 0 AND max_discount_pct <= 100", name="chk_max_discount_pct_range"),
    )

    odoo_company_id = Column(Integer, nullable=False, default=1, index=True)
    name = Column(String(255), nullable=False)
    customer_tier_code = Column(String(50), nullable=True, index=True)
    odoo_product_category_id = Column(Integer, nullable=True, index=True)
    max_discount_pct = Column(Numeric(6, 2), nullable=False)
    minimum_margin_pct = Column(Numeric(6, 2), nullable=True)
    manager_threshold = Column(Numeric(6, 2), nullable=True)
    finance_threshold = Column(Numeric(6, 2), nullable=True)
    single_line_finance_pts = Column(Numeric(6, 2), nullable=True)
    priority = Column(Integer, nullable=False, default=10)
    active = Column(Boolean, nullable=False, default=True)
    effective_from = Column(DateTime(timezone=True), nullable=True)
    effective_to = Column(DateTime(timezone=True), nullable=True)
