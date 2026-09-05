from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from app.db.base import Base, UUID_TYPE, JSON_TYPE, utc_now


class AuditEvent(Base):
    __tablename__ = "audit_event"

    deal_id = Column(UUID_TYPE, ForeignKey("deal.id"), nullable=True, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    actor_type = Column(String(20), nullable=False, default="USER")
    actor_id = Column(Integer, nullable=True)
    actor_role = Column(String(50), nullable=True)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(100), nullable=False)
    before_state = Column(JSON_TYPE, nullable=True)
    after_state = Column(JSON_TYPE, nullable=True)
    reason = Column(Text, nullable=True)
    metadata_json = Column(JSON_TYPE, nullable=False, default=dict)
    is_activity = Column(Boolean, nullable=False, default=True)
    source_event_id = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
