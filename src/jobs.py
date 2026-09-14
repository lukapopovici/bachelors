import uuid
import time
from datetime import datetime
import logging

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, LargeBinary, String

from src.database import Base, SessionLocal

PACS_CONFIGS: dict[str, dict] = {}
logger = logging.getLogger("msv-med.jobs")


class JobRecord(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    type = Column(String, nullable=False)
    status = Column(String, nullable=False)
    progress = Column(JSON, nullable=False)
    instances = Column(JSON, nullable=False)
    errors = Column(JSON, nullable=False)
    params = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class FailedInstance(Base):
    __tablename__ = "failed_instances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    job_type = Column(String, nullable=False)
    instance_uid = Column(String, nullable=False)
    error_message = Column(String, nullable=False)
    attempts = Column(Integer, nullable=False)
    failed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    raw_bytes = Column(LargeBinary, nullable=False)
    params = Column(JSON, nullable=False)


def _job_dict(job: JobRecord) -> dict:
    return {
        "id": job.id,
        "type": job.type,
        "status": job.status,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "params": job.params,
        "instances": job.instances,
        "errors": job.errors,
        "progress": job.progress,
    }


def create_job(job_type: str, params: dict) -> dict:
    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        job = JobRecord(
            id=job_id,
            type=job_type,
            status="queued",
            progress={"total": 0, "done": 0},
            instances=[],
            errors=[],
            params=params,
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return _job_dict(job)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def update_job(job_id: str, **kwargs):
    db = SessionLocal()
    try:
        job = db.get(JobRecord, job_id)
        if job is None:
            return None
        for key, value in kwargs.items():
            if hasattr(JobRecord, key):
                setattr(job, key, value)
        job.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(job)
        return _job_dict(job)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_job(job_id: str) -> dict | None:
    db = SessionLocal()
    try:
        job = db.get(JobRecord, job_id)
        return _job_dict(job) if job else None
    finally:
        db.close()


def list_jobs(status: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    db = SessionLocal()
    try:
        query = db.query(JobRecord)
        if status:
            query = query.filter(JobRecord.status == status)
        jobs = query.order_by(JobRecord.created_at.desc()).offset(offset).limit(limit).all()
        return [_job_dict(job) for job in jobs]
    finally:
        db.close()


def count_jobs(status: str | None = None) -> int:
    db = SessionLocal()
    try:
        query = db.query(JobRecord)
        if status:
            query = query.filter(JobRecord.status == status)
        return query.count()
    finally:
        db.close()


def delete_job_record(job_id: str) -> bool:
    db = SessionLocal()
    try:
        job = db.get(JobRecord, job_id)
        if job is None:
            return False
        db.delete(job)
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def delete_jobs(status: str | None = None) -> int:
    db = SessionLocal()
    try:
        query = db.query(JobRecord)
        if status:
            query = query.filter(JobRecord.status == status)
        deleted = query.delete(synchronize_session=False)
        db.commit()
        return deleted
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def save_failed_instance(
    job_id: str,
    job_type: str,
    instance_uid: str,
    error_message: str,
    attempts: int,
    raw_bytes: bytes,
    params: dict,
) -> FailedInstance:
    db = SessionLocal()
    try:
        existing = db.query(FailedInstance).filter(
            FailedInstance.job_id == job_id,
            FailedInstance.instance_uid == instance_uid,
        ).first()
        if existing:
            existing.error_message = error_message
            existing.attempts = max(existing.attempts, attempts)
            existing.raw_bytes = raw_bytes
            existing.params = params
            existing.failed_at = datetime.utcnow()
            db.commit()
            db.refresh(existing)
            return existing
        failed_instance = FailedInstance(
            job_id=job_id,
            job_type=job_type,
            instance_uid=instance_uid,
            error_message=error_message,
            attempts=attempts,
            raw_bytes=raw_bytes,
            params=params,
        )
        db.add(failed_instance)
        db.commit()
        db.refresh(failed_instance)
        return failed_instance
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_demo_job(job_id: str, duration_seconds: int = 30):
    """Run the UI demo job in the API process so progress is observable."""
    update_job(
        job_id,
        status="processing",
        progress={"total": duration_seconds, "done": 0},
    )
    for second in range(1, duration_seconds + 1):
        time.sleep(1)
        update_job(
            job_id,
            progress={"total": duration_seconds, "done": second},
        )
    update_job(job_id, status="completed", instances=[], errors=[])
