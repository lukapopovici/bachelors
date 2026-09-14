from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from src.auth import get_current_user
from src.jobs import create_job, get_job, list_jobs as list_job_records, count_jobs
from src.models import DemoJobResponse, JobListResponse, JobResponse
from src.worker import demo_job_task

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.post("/demo", status_code=202, response_model=DemoJobResponse)
def create_demo_job(
    duration_seconds: int = Query(30, ge=30, le=30),
    _current_user=Depends(get_current_user),
):
    """Queue a 30-second demo job for the job UI."""
    job = create_job("demo", {"duration_seconds": duration_seconds})
    demo_job_task.delay(job["id"], duration_seconds)
    return {"job_id": job["id"], "status": "queued", "duration_seconds": duration_seconds}


@router.get("", response_model=JobListResponse)
def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit:  int           = Query(50, ge=1, le=500),
    offset: int           = Query(0, ge=0),
    _current_user=Depends(get_current_user),
):
    """GET /jobs — list all jobs, newest first. Supports ?status= filter and pagination."""
    jobs = list_job_records(status=status, limit=limit, offset=offset)
    return {
        "total":   count_jobs(status),
        "offset":  offset,
        "limit":   limit,
        "results": jobs,
    }


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, _current_user=Depends(get_current_user)):
    """GET /jobs/{id} — get one job by ID. Returns 200 or 404."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
