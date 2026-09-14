from io import BytesIO

import pydicom
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from fastapi.responses import JSONResponse
from src.auth import get_current_user
from src.audit import audit_log
from src.dicom_utils import validate_dicom
from src.jobs import create_job
from src.models import UploadOptions, UploadResponse
from src.ratelimit import make_rate_limiter
from src.worker import process_upload_task

# Uploads are study resources.
router = APIRouter(prefix="/studies", tags=["Studies"])

MAX_UPLOAD_FILES = 100
MAX_UPLOAD_SIZE = 50 * 1024 * 1024


@router.post("", status_code=202, response_model=UploadResponse)
async def upload_study(
    files: list[UploadFile] = File(...),
    options: UploadOptions = Depends(),
    _rate_limit=Depends(make_rate_limiter(10, 60)),
    _current_user=Depends(get_current_user),
):
    """
    POST /studies — upload one or more DICOM files.
    Returns 202 Accepted with a job_id to track async processing.
    The upload is processed asynchronously.
    """
    if not files or len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"Upload between 1 and {MAX_UPLOAD_FILES} DICOM files",
        )

    file_data = []
    total_size = 0
    for file in files:
        content = await file.read()
        total_size += len(content)
        if total_size > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail="Upload exceeds the 50 MiB limit")
        file_data.append((file.filename or "unnamed", content))

    invalid_files = []
    datasets = []
    for filename, content in file_data:
        valid, error = validate_dicom(content)
        if not valid:
            invalid_files.append({"filename": filename, "error": error})
            continue
        datasets.append(pydicom.dcmread(BytesIO(content), stop_before_pixels=True, force=False))

    if invalid_files:
        audit_log(
            "upload",
            _current_user.username,
            None,
            "failure",
            {"errors": invalid_files},
        )
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid DICOM files", "errors": invalid_files},
        )

    modalities = sorted({str(ds.get("Modality", "")) for ds in datasets if ds.get("Modality")})
    patient_ids = sorted({str(ds.get("PatientID", "")) for ds in datasets if ds.get("PatientID")})
    raw_file_data = [content for _, content in file_data]
    job = create_job("upload", {
        "target_pacs_url":    options.target_pacs_url,
        "anonymize":          options.anonymize,
        "examination_result": options.examination_result,
        "notify_url":          options.notify_url,
        "file_count":         len(raw_file_data),
    })
    process_upload_task.delay(
        job["id"], raw_file_data, options.target_pacs_url, options.target_pacs_user,
        options.target_pacs_pass, options.anonymize, options.examination_result,
        options.notify_url,
    )
    audit_log(
        "upload",
        _current_user.username,
        job["id"],
        "success",
        {"files_received": len(raw_file_data), "modalities": modalities},
    )
    return {
        "job_id":         job["id"],
        "status":         "queued",
        "files_received": len(raw_file_data),
        "files_valid":    len(raw_file_data),
        "modalities":     modalities,
        "patient_ids":    patient_ids,
    }
