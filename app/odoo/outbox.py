import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session
from app.db.base import Base
from app.odoo.interface import OdooCapabilityMissing, OdooGateway

logger = logging.getLogger("dealflow360.odoo.outbox")


class OdooOutbox(Base):
    __tablename__ = "odoo_outbox"

    method = Column(String(100), nullable=False)
    args_json = Column(Text, nullable=False, default="[]")
    kwargs_json = Column(Text, nullable=False, default="{}")
    status = Column(String(20), nullable=False, default="PENDING")  # PENDING, PROCESSED, FAILED
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)


def queue_mutation(
    session: Session, method: str, args: Optional[List[Any]] = None, kwargs: Optional[Dict[str, Any]] = None
) -> OdooOutbox:
    """Queue an Odoo mutating operation into transactional outbox."""
    record = OdooOutbox(
        method=method,
        args_json=json.dumps(args or []),
        kwargs_json=json.dumps(kwargs or {}),
        status="PENDING",
        attempts=0,
    )
    session.add(record)
    return record


def drain_outbox(session: Session, gateway: OdooGateway, max_records: int = 50) -> int:
    """Process pending mutations in the outbox table."""
    records = (
        session.query(OdooOutbox)
        .filter(OdooOutbox.status.in_(["PENDING", "FAILED"]))
        .filter(OdooOutbox.attempts < 5)
        .order_by(OdooOutbox.created_at.asc())
        .limit(max_records)
        .all()
    )

    processed_count = 0
    for rec in records:
        rec.attempts += 1
        try:
            method_to_call = getattr(gateway, rec.method, None)
            if not method_to_call:
                raise AttributeError(f"Gateway has no method '{rec.method}'")
            args = json.loads(rec.args_json)
            kwargs = json.loads(rec.kwargs_json)
            method_to_call(*args, **kwargs)
            rec.status = "PROCESSED"
            rec.processed_at = datetime.now(timezone.utc)
            rec.last_error = None
            processed_count += 1
        except OdooCapabilityMissing as e:
            rec.status = "FAILED"
            rec.last_error = f"Odoo capability missing: {e.method_name}"
            logger.error(f"Outbox task {rec.id} failed: {rec.last_error}")
        except Exception as e:
            rec.status = "FAILED" if rec.attempts >= 5 else "PENDING"
            rec.last_error = str(e)
            logger.error(f"Outbox task {rec.id} failed attempt {rec.attempts}: {e}")

    session.commit()
    return processed_count
