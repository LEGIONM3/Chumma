import logging
from sqlalchemy.orm import Session
from app.models.cross_cutting import NumberSequence

logger = logging.getLogger("dealflow360.sequence")


def get_next_sequence(session: Session, key: str = "DEAL", prefix: str = "D") -> str:
    """Atomically increment and return next reference sequence string (e.g. D-1001)."""
    try:
        seq_record = (
            session.query(NumberSequence)
            .filter(NumberSequence.key == key)
            .with_for_update()
            .first()
        )
    except Exception:
        # Fallback if dialect does not support with_for_update (e.g. sqlite test)
        seq_record = (
            session.query(NumberSequence)
            .filter(NumberSequence.key == key)
            .first()
        )

    if not seq_record:
        seq_record = NumberSequence(key=key, next_value=1001)
        session.add(seq_record)
        session.flush()

    current_val = seq_record.next_value
    seq_record.next_value = current_val + 1
    session.flush()

    return f"{prefix}-{current_val}"
