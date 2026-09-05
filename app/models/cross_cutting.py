from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from app.db.base import Base, UUID_TYPE, JSON_TYPE, utc_now


class ProcessedEvent(Base):
    __tablename__ = "processed_event"

    id = None
    event_id = Column(String(100), primary_key=True)
    source = Column(String(50), nullable=False, default="ODOO")
    event_type = Column(String(100), nullable=False)
    deal_id = Column(UUID_TYPE, nullable=True)
    received_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    processed_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    result = Column(String(50), nullable=False, default="PROCESSED")


class Notification(Base):
    __tablename__ = "notification"

    recipient_odoo_user_id = Column(Integer, nullable=False, index=True)
    type = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(100), nullable=False)
    is_read = Column(Boolean, nullable=False, default=False)
    dedupe_key = Column(String(255), nullable=True, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class AppSetting(Base):
    __tablename__ = "app_setting"

    key = Column(String(100), unique=True, nullable=False, index=True)
    value_json = Column(JSON_TYPE, nullable=False)
    description = Column(Text, nullable=True)


class NumberSequence(Base):
    __tablename__ = "number_sequence"

    key = Column(String(50), unique=True, nullable=False, index=True)
    next_value = Column(Integer, nullable=False, default=1001)
