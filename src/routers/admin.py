"""
admin.py — Admin endpoints.
PACS config CRUD, system stats, job management, audit log, worker health.
"""

import uuid
import time
import httpx
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from src.auth import require_role
from src.audit import AuditLog, audit_log as write_audit_log
from src.models import (
    AdminStatsResponse,
    AuditResponse,
    CountResponse,
    FailedInstanceResponse,
    PACSConfig,
    PACSConnectivityResponse,
    PACSCreateResponse,
    PACSListItem,
    QueuedJobResponse,
    RetryJobResponse,
    WorkerListResponse,
)
from src.jobs import (
    PACS_CONFIGS,
    FailedInstance,
    count_jobs,
    delete_job_record,
    delete_jobs,
    get_job,
    list_jobs as list_job_records,
)
from src.config import ORTHANC_URL, ORTHANC_USER, ORTHANC_PASS, REDIS_URL, orthanc_auth
from src.database import DicomStudyRecord, SessionLocal
from src.cache import cache_get, cache_set

logger = logging.getLogger("msv-med.admin")

router = APIRouter(prefix="/admin", tags=["Admin"])


def _failed_instance_dict(failed_instance: FailedInstance) -> dict:
    return {
        "id": failed_instance.id,
        "job_id": failed_instance.job_id,
        "job_type": failed_instance.job_type,
        "instance_uid": failed_instance.instance_uid,
        "error_message": failed_instance.error_message,
        "attempts": failed_instance.attempts,
        "failed_at": failed_instance.failed_at.isoformat(),
        "params": failed_instance.params,
    }


@router.delete("/indexed-records", name="delete_indexed_records", response_model=CountResponse)
@router.delete("/database", include_in_schema=False, response_model=CountResponse)
def clear_database(_current_user=Depends(require_role("admin"))):
    """Delete all indexed DICOM study records owned by this application."""
    db = SessionLocal()
    try:
        deleted_count = db.query(DicomStudyRecord).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    logger.warning("Cleared %d indexed study records from PostgreSQL", deleted_count)
    return {"deleted_count": deleted_count}


# PACS configuration

@router.get("/pacs", response_model=list[PACSListItem])
def list_pacs(_current_user=Depends(require_role("admin"))):
    """GET /admin/pacs — list all PACS configs (passwords excluded)."""
    return [{"id": v["id"], "name": v["name"], "url": v["url"]} for v in PACS_CONFIGS.values()]


@router.post("/pacs", status_code=201, response_model=PACSCreateResponse)
def add_pacs(config: PACSConfig, _current_user=Depends(require_role("admin"))):
    """POST /admin/pacs — create a new PACS config. Returns 201 Created."""
    pacs_id = str(uuid.uuid4())
    PACS_CONFIGS[pacs_id] = {"id": pacs_id, **config.dict()}
    logger.info(f"PACS config added: {config.name} ({config.url})")
    return {"id": pacs_id}


@router.delete("/pacs/{pacs_id}", status_code=204)
def delete_pacs(pacs_id: str, _current_user=Depends(require_role("admin"))):
    """DELETE /admin/pacs/{id} — delete a PACS config. Returns 204 No Content."""
    if pacs_id not in PACS_CONFIGS:
        raise HTTPException(status_code=404, detail="PACS config not found")
    name = PACS_CONFIGS[pacs_id].get("name")
    del PACS_CONFIGS[pacs_id]
    logger.info(f"PACS config deleted: {name}")
    return None


@router.get("/pacs/{pacs_id}", response_model=PACSListItem)
def get_pacs(pacs_id: str, _current_user=Depends(require_role("admin"))):
    """GET /admin/pacs/{id} — get one PACS config (password excluded)."""
    cfg = PACS_CONFIGS.get(pacs_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="PACS config not found")
    return {"id": cfg["id"], "name": cfg["name"], "url": cfg["url"]}


@router.get("/pacs/{pacs_id}/connectivity", response_model=PACSConnectivityResponse)
def test_pacs(pacs_id: str, _current_user=Depends(require_role("admin"))):
    """
    GET /admin/pacs/{id}/connectivity — test connectivity to a PACS.
    Return connectivity information for a configured PACS.
    """
    cfg = PACS_CONFIGS.get(pacs_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="PACS config not found")
    try:
        t0 = time.time()
        r  = httpx.get(f"{cfg['url']}/system",
                       auth=(cfg["username"], cfg["password"]), timeout=5)
        latency_ms = round((time.time() - t0) * 1000)
        if r.status_code == 200:
            info = r.json()
            return {
                "reachable":       True,
                "latency_ms":      latency_ms,
                "orthanc_version": info.get("Version"),
                "dicom_aet":       info.get("DicomAet"),
            }
        return {"reachable": False, "status_code": r.status_code}
    except Exception as e:
        return {"reachable": False, "error": str(e)}


# System statistics

@router.get("/system-stats", name="get_system_stats", response_model=AdminStatsResponse)
@router.get("/stats", include_in_schema=False, response_model=AdminStatsResponse)
def system_stats(_current_user=Depends(require_role("admin"))):
    """GET /admin/stats — system health overview."""
    cached = cache_get("admin:stats")
    if cached is not None:
        return cached

    all_jobs   = list_job_records(limit=count_jobs())
    completed  = sum(1 for j in all_jobs if j["status"] == "completed")
    with_errors= sum(1 for j in all_jobs if j["status"] == "completed_with_errors")
    failed     = sum(1 for j in all_jobs if j["status"] == "failed")
    finished   = completed + with_errors + failed
    cutoff     = datetime.utcnow() - timedelta(hours=24)

    orthanc_info = {}
    try:
        r = httpx.get(f"{ORTHANC_URL}/system", auth=orthanc_auth(), timeout=4)
        if r.status_code == 200:
            d = r.json()
            orthanc_info = {"reachable": True, "version": d.get("Version"), "aet": d.get("DicomAet")}
        else:
            orthanc_info = {"reachable": False}
    except Exception as e:
        orthanc_info = {"reachable": False, "error": str(e)}

    try:
        sr = httpx.get(f"{ORTHANC_URL}/studies",   auth=orthanc_auth(), timeout=4)
        ir = httpx.get(f"{ORTHANC_URL}/instances", auth=orthanc_auth(), timeout=4)
        orthanc_info["studies"]   = len(sr.json()) if sr.status_code == 200 else "?"
        orthanc_info["instances"] = len(ir.json()) if ir.status_code == 200 else "?"
    except Exception:
        orthanc_info.setdefault("studies",   "?")
        orthanc_info.setdefault("instances", "?")

    redis_ok = False
    try:
        import redis as redis_lib
        redis_lib.from_url(REDIS_URL, socket_timeout=2).ping()
        redis_ok = True
    except Exception:
        pass

    stats = {
        "jobs": {
            "total":                 len(all_jobs),
            "completed":             completed,
            "completed_with_errors": with_errors,
            "failed":                failed,
            "queued":                sum(1 for j in all_jobs if j["status"] == "queued"),
            "processing":            sum(1 for j in all_jobs if j["status"] == "processing"),
            "success_rate_pct":      round(completed / finished * 100, 1) if finished else None,
            "total_instances":       sum(len(j.get("instances", [])) for j in all_jobs),
            "last_24h":              sum(1 for j in all_jobs if datetime.fromisoformat(j["created_at"]) > cutoff),
        },
        "orthanc":      orthanc_info,
        "redis":        {"reachable": redis_ok},
        "pacs_configs": len(PACS_CONFIGS),
        "generated_at": datetime.utcnow().isoformat(),
    }
    cache_set("admin:stats", stats, 15)
    return stats


# Job management

@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, _current_user=Depends(require_role("admin"))):
    """DELETE /admin/jobs/{id} — delete one job record. Returns 204 No Content."""
    if not delete_job_record(job_id):
        write_audit_log("delete_job", _current_user.username, job_id, "failure", {})
        raise HTTPException(status_code=404, detail="Job not found")
    write_audit_log("delete_job", _current_user.username, job_id, "success", {})
    return None


@router.delete("/jobs", status_code=200, response_model=CountResponse)
def purge_jobs(
    status: Optional[str] = Query(None, description="Filter by status to purge selectively"),
    _current_user=Depends(require_role("admin")),
):
    """DELETE /admin/jobs — bulk delete jobs. Optional ?status= filter."""
    deleted_count = delete_jobs(status)
    write_audit_log(
        "purge_jobs",
        _current_user.username,
        None,
        "success",
        {"status": status, "deleted_count": deleted_count},
    )
    logger.info(f"Purged {deleted_count} jobs (filter: {status or 'all'})")
    return {"deleted_count": deleted_count}


@router.get("/failed-instances", response_model=list[FailedInstanceResponse])
def list_failed_instances(_current_user=Depends(require_role("admin"))):
    """List permanently failed DICOM instances without returning raw bytes."""
    db = SessionLocal()
    try:
        failed_instances = db.query(FailedInstance).order_by(
            FailedInstance.failed_at.desc()
        ).all()
        return [_failed_instance_dict(instance) for instance in failed_instances]
    finally:
        db.close()


@router.post("/failed-instances/{failed_instance_id}/retries", status_code=202,
             name="create_failed_instance_retry", response_model=QueuedJobResponse)
@router.post("/failed-instances/{failed_instance_id}/retry", status_code=202,
             include_in_schema=False, response_model=QueuedJobResponse)
def retry_failed_instance(
    failed_instance_id: int,
    _current_user=Depends(require_role("admin")),
):
    """Re-queue one dead-lettered instance as a new upload job."""
    db = SessionLocal()
    try:
        failed_instance = db.get(FailedInstance, failed_instance_id)
        if failed_instance is None:
            raise HTTPException(status_code=404, detail="Failed instance not found")

        from src.jobs import create_job
        from src.worker import process_upload_task

        params = failed_instance.params or {}
        job = create_job(
            "upload",
            {
                "target_pacs_url": params.get("target_pacs_url", ORTHANC_URL),
                "anonymize": params.get("anonymize", False),
                "examination_result": params.get("examination_result"),
                "file_count": 1,
            },
        )
        process_upload_task.delay(
            job["id"],
            [failed_instance.raw_bytes],
            params.get("target_pacs_url", ORTHANC_URL),
            ORTHANC_USER,
            ORTHANC_PASS,
            params.get("anonymize", False),
            params.get("examination_result"),
            None,
        )
        db.delete(failed_instance)
        db.commit()
        return {"job_id": job["id"], "status": "queued"}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.delete("/failed-instances/{failed_instance_id}", status_code=204)
def discard_failed_instance(
    failed_instance_id: int,
    _current_user=Depends(require_role("admin")),
):
    db = SessionLocal()
    try:
        failed_instance = db.get(FailedInstance, failed_instance_id)
        if failed_instance is None:
            raise HTTPException(status_code=404, detail="Failed instance not found")
        db.delete(failed_instance)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return None


@router.delete("/failed-instances", response_model=CountResponse)
def discard_all_failed_instances(_current_user=Depends(require_role("admin"))):
    db = SessionLocal()
    try:
        deleted_count = db.query(FailedInstance).delete(synchronize_session=False)
        db.commit()
        return {"deleted_count": deleted_count}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/jobs/{job_id}/retries", status_code=202, name="create_job_retry",
             response_model=RetryJobResponse)
@router.post("/jobs/{job_id}/retry", status_code=202, include_in_schema=False,
             response_model=RetryJobResponse)
def retry_job(job_id: str, _current_user=Depends(require_role("admin"))):
    """
    POST /admin/jobs/{id}/retry — re-queue a failed job.
    Returns 202 Accepted — the retry itself is async.
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] not in ("failed", "completed_with_errors"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot retry job with status '{job['status']}'"
        )

    from src.jobs import create_job
    from src.worker import forward_study_task

    params  = job.get("params", {})
    new_job = create_job(job["type"], params)

    if job["type"] == "forward":
        forward_study_task.delay(
            new_job["id"],
            params.get("source_study_id"),
            params.get("source_pacs_url", ORTHANC_URL),
            params.get("target_pacs_url", ORTHANC_URL),
            params.get("target_pacs_user", ORTHANC_USER),
            params.get("target_pacs_pass", ORTHANC_PASS),
            params.get("anonymize", False),
            params.get("examination_result"),
            params.get("notify_url"),
        )

    logger.info(f"Retried job {job_id} → new job {new_job['id']}")
    return {"original_job_id": job_id, "new_job_id": new_job["id"]}


# Audit log

@router.get("/audit", response_model=list[AuditResponse])
def audit_log(
    limit:  int           = Query(50, ge=1, le=500),
    action: Optional[str] = Query(None),
    actor: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    _current_user=Depends(require_role("admin")),
):
    """GET /admin/audit — persistent audit events, newest first."""
    db = SessionLocal()
    try:
        query = db.query(AuditLog)
        if action:
            query = query.filter(AuditLog.action == action)
        if actor:
            query = query.filter(AuditLog.actor == actor)
        if from_date:
            query = query.filter(AuditLog.timestamp >= from_date)
        if to_date:
            query = query.filter(AuditLog.timestamp <= to_date)
        records = query.order_by(AuditLog.timestamp.desc()).limit(limit).all()
        return [
            {
                "id": record.id,
                "timestamp": record.timestamp.isoformat(),
                "request_id": record.request_id,
                "action": record.action,
                "actor": record.actor,
                "resource_id": record.resource_id,
                "outcome": record.outcome,
                "details": record.details,
            }
            for record in records
        ]
    finally:
        db.close()


# Worker health

@router.get("/workers", response_model=WorkerListResponse)
def worker_health(_current_user=Depends(require_role("admin"))):
    """GET /admin/workers — Celery worker status."""
    try:
        from src.worker import celery_app
        inspect = celery_app.control.inspect(timeout=3)
        active  = inspect.active() or {}
        stats   = inspect.stats()  or {}
        workers = [
            {
                "name":         name,
                "status":       "online",
                "active_tasks": len(tasks),
                "tasks":        [t.get("name") for t in tasks],
                "processed":    stats.get(name, {}).get("total", {}),
            }
            for name, tasks in active.items()
        ]
        if not workers:
            return {"workers": [], "note": "No workers online or inspect timed out."}
        return {"workers": workers, "total_online": len(workers)}
    except Exception as e:
        return {"workers": [], "error": str(e)}
