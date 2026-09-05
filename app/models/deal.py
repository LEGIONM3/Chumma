from decimal import Decimal
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, Numeric, String
from app.db.base import Base, UUID_TYPE, utc_now
from app.models.enums import ApprovalLevel, ApprovalState, DealStatus, HealthStatus


class Deal(Base):
    __tablename__ = "deal"

    reference = Column(String(50), unique=True, nullable=False, index=True)
    odoo_sale_order_id = Column(BigInteger, unique=True, nullable=False, index=True)
    odoo_order_name = Column(String(100), nullable=False)
    odoo_partner_id = Column(Integer, nullable=False, index=True)
    partner_name_cache = Column(String(255), nullable=True)
    tier_code = Column(String(50), nullable=True)
    owner_odoo_user_id = Column(Integer, nullable=False, index=True)
    sales_team_odoo_id = Column(Integer, nullable=True, index=True)
    odoo_company_id = Column(Integer, nullable=False, default=1)
    currency_code = Column(String(10), nullable=False, default="INR")
    status = Column(String(50), nullable=False, default=DealStatus.DRAFT.value, index=True)
    approval_state = Column(String(50), nullable=False, default=ApprovalState.NOT_EVALUATED.value, index=True)
    health_status = Column(String(50), nullable=False, default=HealthStatus.HEALTHY.value, index=True)
    current_risk_score = Column(Numeric(5, 2), nullable=True)
    current_severity = Column(String(20), nullable=True)
    current_assessment_id = Column(UUID_TYPE, nullable=True)
    approved_assessment_id = Column(UUID_TYPE, nullable=True)
    required_level = Column(String(50), nullable=False, default=ApprovalLevel.NONE.value)
    order_discount_pct = Column(Numeric(6, 2), nullable=False, default=Decimal("0.00"))
    amount_total_cache = Column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    amount_untaxed_cache = Column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    margin_pct_cache = Column(Numeric(6, 2), nullable=False, default=Decimal("0.00"))
    one_time_total_cache = Column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    recurring_first_cycle_total_cache = Column(Numeric(14, 2), nullable=False, default=Decimal("0.00"))
    customer_confirmed_pending = Column(Boolean, nullable=False, default=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    promised_delivery_date = Column(DateTime(timezone=True), nullable=True)
    last_activity_at = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    last_synced_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    odoo_write_date_seen = Column(String(100), nullable=True)
    version = Column(Integer, nullable=False, default=1)
