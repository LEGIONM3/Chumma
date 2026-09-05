from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String
from app.db.base import Base, UUID_TYPE, JSON_TYPE, utc_now
from app.models.enums import AlertStatus


class DealHealthSnapshot(Base):
    __tablename__ = "deal_health_snapshot"

    deal_id = Column(UUID_TYPE, ForeignKey("deal.id"), nullable=False, index=True)
    health_status = Column(String(50), nullable=False)
    overall_score = Column(Numeric(5, 2), nullable=False)
    stalled_score = Column(Numeric(5, 2), nullable=False, default=0)
    approval_delay_score = Column(Numeric(5, 2), nullable=False, default=0)
    discount_anomaly_score = Column(Numeric(5, 2), nullable=False, default=0)
    delivery_risk_score = Column(Numeric(5, 2), nullable=False, default=0)
    negotiation_score = Column(Numeric(5, 2), nullable=False, default=0)
    detail = Column(JSON_TYPE, nullable=False, default=dict)
    calculated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class DealAlert(Base):
    __tablename__ = "deal_alert"

    deal_id = Column(UUID_TYPE, ForeignKey("deal.id"), nullable=False, index=True)
    type = Column(String(50), nullable=False, index=True)
    severity = Column(String(20), nullable=False)
    status = Column(String(50), nullable=False, default=AlertStatus.OPEN.value, index=True)
    title = Column(String(255), nullable=False)
    detail = Column(JSON_TYPE, nullable=False, default=dict)
    raised_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    acknowledged_by = Column(Integer, nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    last_action = Column(String(50), nullable=True)
    last_action_at = Column(DateTime(timezone=True), nullable=True)
    last_action_by = Column(Integer, nullable=True)


class RepDiscountStats(Base):
    __tablename__ = "rep_discount_stats"

    id = None
    odoo_user_id = Column(Integer, primary_key=True, autoincrement=False)
    sample_size = Column(Integer, nullable=False, default=0)
    avg_weighted_discount_pct = Column(Numeric(6, 2), nullable=False, default=0)
    stddev = Column(Numeric(6, 2), nullable=False, default=0)
    baseline_source = Column(String(20), nullable=False, default="REP")
    computed_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
