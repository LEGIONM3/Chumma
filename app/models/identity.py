from datetime import datetime, timezone
import uuid
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from app.db.base import Base, UUID_TYPE, utc_now
from app.models.enums import Role


class DealflowUser(Base):
    __tablename__ = "dealflow_user"

    id = None
    odoo_user_id = Column(Integer, primary_key=True, autoincrement=False)
    login = Column(String(255), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default=Role.SALES_REP.value)
    sales_team_odoo_id = Column(Integer, nullable=True)
    is_portal = Column(Boolean, nullable=False, default=False)
    partner_id = Column(Integer, nullable=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)


User = DealflowUser


class MagicLinkToken(Base):
    __tablename__ = "magic_link_token"

    odoo_partner_id = Column(Integer, nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    deal_id = Column(UUID_TYPE, nullable=True, index=True)
    created_ip = Column(String(50), nullable=True)
