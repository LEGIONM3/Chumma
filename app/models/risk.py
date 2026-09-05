from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Numeric, String, Text
from app.db.base import Base, UUID_TYPE, JSON_TYPE, utc_now


class RiskAssessment(Base):
    __tablename__ = "risk_assessment"

    __table_args__ = (
        CheckConstraint("risk_score >= 0 AND risk_score <= 100", name="chk_risk_score_range"),
    )

    deal_id = Column(UUID_TYPE, ForeignKey("deal.id"), nullable=False, index=True)
    risk_score = Column(Numeric(5, 2), nullable=False, index=True)
    severity = Column(String(20), nullable=False)
    required_level = Column(String(50), nullable=False)
    decision = Column(String(50), nullable=False)
    trigger_type = Column(String(50), nullable=False)
    trigger_event_id = Column(String(100), nullable=True)
    policy_version = Column(String(64), nullable=False)
    resolved_policy = Column(JSON_TYPE, nullable=False, default=dict)
    line_snapshot = Column(JSON_TYPE, nullable=False, default=list)
    totals = Column(JSON_TYPE, nullable=False, default=dict)
    calculated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class RiskFactor(Base):
    __tablename__ = "risk_factor"

    risk_assessment_id = Column(UUID_TYPE, ForeignKey("risk_assessment.id"), nullable=False, index=True)
    factor_type = Column(String(50), nullable=False, index=True)
    source_reference = Column(String(100), nullable=True)
    raw_value = Column(Numeric(14, 4), nullable=False, default=0)
    weight = Column(Numeric(10, 4), nullable=False, default=1)
    contribution = Column(Numeric(6, 2), nullable=False, default=0)
    reason = Column(Text, nullable=False)
    detail = Column(JSON_TYPE, nullable=False, default=dict)
