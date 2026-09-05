from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from app.db.base import Base, UUID_TYPE, utc_now
from app.models.enums import RecommendationStatus


class RecommendationRule(Base):
    __tablename__ = "recommendation_rule"

    odoo_company_id = Column(Integer, nullable=False, default=1)
    trigger_product_id = Column(Integer, nullable=False, index=True)
    suggested_product_id = Column(Integer, nullable=False, index=True)
    co_purchase_count = Column(Integer, nullable=False, default=0)
    manual_boost = Column(Numeric(4, 2), nullable=False, default=0.0)
    is_promoted_override = Column(Boolean, nullable=True)
    source = Column(String(20), nullable=False, default="MANUAL")
    active = Column(Boolean, nullable=False, default=True)


class Recommendation(Base):
    __tablename__ = "recommendation"

    deal_id = Column(UUID_TYPE, ForeignKey("deal.id"), nullable=False, index=True)
    risk_assessment_id = Column(UUID_TYPE, ForeignKey("risk_assessment.id"), nullable=False)
    odoo_product_id = Column(Integer, nullable=False, index=True)
    product_name_cache = Column(String(255), nullable=False)
    recommendation_type = Column(String(50), nullable=False)
    score = Column(Numeric(4, 3), nullable=False)
    co_purchase_score = Column(Numeric(4, 3), nullable=False, default=0)
    promotion_score = Column(Numeric(4, 3), nullable=False, default=0)
    margin_score = Column(Numeric(4, 3), nullable=False, default=0)
    relevance_score = Column(Numeric(4, 3), nullable=False, default=0)
    margin_delta_amount = Column(Numeric(14, 2), nullable=False, default=0)
    margin_delta_pct = Column(Numeric(6, 2), nullable=False, default=0)
    unit_price_cache = Column(Numeric(14, 2), nullable=False, default=0)
    reason = Column(Text, nullable=False)
    source = Column(String(50), nullable=False, default="RULE")
    status = Column(String(50), nullable=False, default=RecommendationStatus.ACTIVE.value, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    added_at = Column(DateTime(timezone=True), nullable=True)
    dismissed_at = Column(DateTime(timezone=True), nullable=True)


DealRecommendation = Recommendation
