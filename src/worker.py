import logging
import time
import httpx
from celery import Celery
from src.config import REDIS_URL, orthanc_auth
from src.jobs import get_job, save_failed_instance, update_job
from src.cache import cache_delete, cache_invalidate_prefix
from src.dicom_utils import (
    dicom_bytes_to_dataset, dataset_to_bytes,
    anonymize_dataset, embed_examination_result,
)

logger = logging.getLogger("msv-med.worker")

celery_app = Celery("msv-med", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True
celery_app.conf.task_track_started = True
celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.task_time_limit = 600
celery_app.conf.task_soft_time_limit = 540


@celery_app.task(queue="demo")
def demo_job_task(job_id, duration_seconds=30):
    """Run a 30-second job for the job queue UI."""
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


def _push_instance(raw: bytes, target_url: str, target_user: str, target_pass: str,
                   anonymize: bool, examination_result: str | None,
                   uid_map: dict[str, str]) -> dict:
    ds = dicom_bytes_to_dataset(raw)
    instance_uid = str(ds.get("SOPInstanceUID", "unknown"))

    if anonymize:
        ds = anonymize_dataset(ds, uid_map)
    if examination_result:
        ds = embed_examination_result(ds, examination_result)

    modified = dataset_to_bytes(ds)
    r = httpx.post(
        f"{target_url}/instances",
        content=modified,
        headers={"Content-Type": "application/dicom"},
        auth=(target_user, target_pass),
        timeout=30,
    )
    return {"instance_uid": instance_uid, "status_code": r.status_code, "ok": r.status_code == 200}


def _instance_uid(raw: bytes, fallback: str) -> str:
    try:
        return str(dicom_bytes_to_dataset(raw).get("SOPInstanceUID", fallback))
    except Exception:
        return fallback


@celery_app.task(
    bind=True,
    queue="dicom",
    max_retries=3,
    default_retry_delay=10,
    time_limit=600,
    soft_time_limit=540,
)
def process_upload_task(self, job_id, file_data_list, target_pacs_url,
                        target_pacs_user, target_pacs_pass,
                        anonymize, examination_result, notify_url):
    update_job(job_id, status="processing", progress={"total": len(file_data_list), "done": 0})
    previous_job = get_job(job_id) or {}
    errors, results = [], list(previous_job.get("instances", []))
    completed_uids = {result.get("instance_uid") for result in results}
    uid_map: dict[str, str] = {}

    for idx, raw in enumerate(file_data_list):
        instance_uid = _instance_uid(raw, f"upload-{idx}")
        if instance_uid in completed_uids:
            update_job(job_id, progress={"total": len(file_data_list), "done": idx + 1})
            continue
        try:
            result = _push_instance(raw, target_pacs_url, target_pacs_user, target_pacs_pass,
                                    anonymize, examination_result, uid_map)
            if not result["ok"] and result["status_code"] >= 500:
                raise Exception(f"PACS 5xx: {result['status_code']}")
            results.append(result)
            completed_uids.add(result["instance_uid"])
            update_job(job_id, instances=results)
        except Exception as exc:
            logger.error(f"Job {job_id} instance {idx} failed: {exc}")
            errors.append({"index": idx, "error": str(exc)})
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                save_failed_instance(
                    job_id=job_id,
                    job_type="upload",
                    instance_uid=_instance_uid(raw, f"upload-{idx}"),
                    error_message=str(exc),
                    attempts=getattr(self.request, "retries", 3) + 1,
                    raw_bytes=raw,
                    params={
                        "target_pacs_url": target_pacs_url,
                        "anonymize": anonymize,
                        "examination_result": examination_result,
                    },
                )

        update_job(job_id, progress={"total": len(file_data_list), "done": idx + 1})

    _finish_job(job_id, results, errors, notify_url)


@celery_app.task(
    bind=True,
    queue="forward",
    max_retries=3,
    default_retry_delay=15,
    time_limit=600,
    soft_time_limit=540,
)
def forward_study_task(self, job_id, source_study_id, source_pacs_url,
                       target_pacs_url, target_pacs_user, target_pacs_pass,
                       anonymize, examination_result, notify_url):
    update_job(job_id, status="fetching_instances")

    try:
        r = httpx.get(f"{source_pacs_url}/studies/{source_study_id}/instances",
                      auth=orthanc_auth(), timeout=15)
        r.raise_for_status()
        instances = r.json()
    except Exception as exc:
        update_job(job_id, status="failed", errors=[{"error": str(exc)}])
        raise

    update_job(job_id, status="processing", progress={"total": len(instances), "done": 0})
    previous_job = get_job(job_id) or {}
    errors, results = [], list(previous_job.get("instances", []))
    uid_map: dict[str, str] = {}
    completed_source_ids = {
        result.get("source_instance_id") for result in results
    }

    for idx, meta in enumerate(instances):
        instance_id = meta.get("ID") or meta.get("id", "")
        if instance_id in completed_source_ids:
            update_job(job_id, progress={"total": len(instances), "done": idx + 1})
            continue
        raw = None
        try:
            dl = httpx.get(f"{source_pacs_url}/instances/{instance_id}/file",
                           auth=orthanc_auth(), timeout=30)
            dl.raise_for_status()
            result = _push_instance(dl.content, target_pacs_url, target_pacs_user,
                                    target_pacs_pass, anonymize, examination_result, uid_map)
            if not result["ok"] and result["status_code"] >= 500:
                raise Exception(f"PACS 5xx: {result['status_code']}")
            result["source_instance_id"] = instance_id
            results.append(result)
            completed_source_ids.add(instance_id)
            update_job(job_id, instances=results)
        except Exception as exc:
            logger.error(f"Job {job_id} forward {instance_id} failed: {exc}")
            errors.append({"instance_id": instance_id, "error": str(exc)})
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                if raw is not None:
                    save_failed_instance(
                        job_id=job_id,
                        job_type="forward",
                        instance_uid=_instance_uid(raw, instance_id or f"forward-{idx}"),
                        error_message=str(exc),
                        attempts=getattr(self.request, "retries", 3) + 1,
                        raw_bytes=raw,
                        params={
                            "target_pacs_url": target_pacs_url,
                            "anonymize": anonymize,
                            "examination_result": examination_result,
                        },
                    )
                else:
                    logger.error(
                        "Job %s instance %s exhausted retries before DICOM download",
                        job_id,
                        instance_id,
                    )

        update_job(job_id, progress={"total": len(instances), "done": idx + 1})

    _finish_job(job_id, results, errors, notify_url)


def _finish_job(job_id, results, errors, notify_url):
    status = "completed" if not errors else "completed_with_errors"
    update_job(job_id, status=status, instances=results, errors=errors)
    if status == "completed":
        cache_delete("studies:list")
        cache_invalidate_prefix("studies:list:")
    if notify_url:
        try:
            httpx.post(notify_url, json={"job_id": job_id, "status": status}, timeout=10)
        except Exception as e:
            logger.warning(f"Webhook failed for {job_id}: {e}")
