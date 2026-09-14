from contextvars import ContextVar
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String
import logging

from src.database import Base, SessionLocal

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")
logger = logging.getLogger("msv-med.audit")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    request_id = Column(String, nullable=False)
    action = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    resource_id = Column(String)
    outcome = Column(String, nullable=False)
    details = Column(JSON, nullable=False, default=dict)


def audit_log(action, actor, resource_id, outcome, details):
    db = SessionLocal()
    try:
        record = AuditLog(
            request_id=request_id_context.get(),
            action=action,
            actor=actor,
            resource_id=str(resource_id) if resource_id is not None else None,
            outcome=outcome,
            details=details or {},
        )
        db.add(record)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Audit write failed: %s", exc)
    finally:
        db.close()