from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship
from app.db.base import Base, UUID_TYPE, utc_now
from app.models.enums import NegotiationRequestStatus


class NegotiationRequest(Base):
    __tablename__ = "negotiation_request"

    deal_id = Column(UUID_TYPE, ForeignKey("deal.id"), nullable=False, index=True)
    odoo_sale_order_id = Column(BigInteger, nullable=False, index=True)
    customer_partner_id = Column(Integer, nullable=False, index=True)
    requested_by_odoo_user_id = Column(Integer, nullable=False)
    type = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False, default=NegotiationRequestStatus.OPEN.value, index=True)
    message = Column(Text, nullable=True)
    based_on_assessment_id = Column(UUID_TYPE, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    processed_by_odoo_user_id = Column(Integer, nullable=True)
    response_message = Column(Text, nullable=True)
    counter_value = Column(Numeric(14, 2), nullable=True)
    applied_assessment_id = Column(UUID_TYPE, nullable=True)

    changes = relationship("NegotiationChange", backref="negotiation_request")


class NegotiationChange(Base):
    __tablename__ = "negotiation_change"

    negotiation_request_id = Column(UUID_TYPE, ForeignKey("negotiation_request.id"), nullable=False, index=True)
    odoo_sale_order_line_id = Column(BigInteger, nullable=True)
    field_name = Column(String(50), nullable=False)
    old_value = Column(Text, nullable=True)
    requested_value = Column(Text, nullable=True)


class PortalComment(Base):
    __tablename__ = "portal_comment"

    deal_id = Column(UUID_TYPE, ForeignKey("deal.id"), nullable=False, index=True)
    odoo_sale_order_line_id = Column(BigInteger, nullable=True)
    author_odoo_user_id = Column(Integer, nullable=False)
    author_role = Column(String(50), nullable=False)
    body = Column(Text, nullable=False)
    is_internal = Column(Boolean, nullable=False, default=False)
