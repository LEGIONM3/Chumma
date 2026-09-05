from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.db.base import Base, UUID_TYPE, utc_now
from app.models.enums import ApprovalRequestStatus


class ApprovalRequest(Base):
    __tablename__ = "approval_request"

    deal_id = Column(UUID_TYPE, ForeignKey("deal.id"), nullable=False, index=True)
    risk_assessment_id = Column(UUID_TYPE, ForeignKey("risk_assessment.id"), nullable=False)
    required_level = Column(String(50), nullable=False)  # 'SALES_MANAGER' or 'FINANCE' stage
    sequence = Column(Integer, nullable=False, default=1)
    status = Column(String(50), nullable=False, default=ApprovalRequestStatus.PENDING.value, index=True)
    requested_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    decided_by_odoo_user_id = Column(Integer, nullable=True)
    decision_reason = Column(Text, nullable=True)

    actions = relationship("ApprovalAction", backref="approval_request", order_by="ApprovalAction.created_at.asc()")


class ApprovalAction(Base):
    __tablename__ = "approval_action"

    approval_request_id = Column(UUID_TYPE, ForeignKey("approval_request.id"), nullable=False, index=True)
    actor_odoo_user_id = Column(Integer, nullable=False)
    actor_role = Column(String(50), nullable=False)
    action = Column(String(50), nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
