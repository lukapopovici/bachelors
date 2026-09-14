from fastapi import APIRouter, Depends
from src.auth import get_current_user
from src.audit import audit_log
from src.models import ForwardRequest, QueuedJobResponse
from src.ratelimit import make_rate_limiter
from src.jobs import create_job
from src.worker import forward_study_task
from src.config import ORTHANC_URL

# Forwarding is an action on a study.
router = APIRouter(prefix="/studies", tags=["Studies"])


@router.post("/{study_id}/forwards", status_code=202, name="create_forward_job",
             response_model=QueuedJobResponse)
@router.post("/{study_id}/forward", status_code=202, include_in_schema=False,
             response_model=QueuedJobResponse)
def forward_study(
    study_id: str,
    req: ForwardRequest,
    _rate_limit=Depends(make_rate_limiter(20, 60)),
    _current_user=Depends(get_current_user),
):
    """
    POST /studies/{id}/forward — forward an existing study to a target PACS.
    Returns 202 Accepted with a job_id.
    The study_id from the URL is used as source_study_id.
    """
    req.source_study_id = study_id
    job = create_job("forward", req.dict())
    forward_study_task.delay(
        job["id"],
        study_id,
        req.source_pacs_url or ORTHANC_URL,
        req.target_pacs_url,
        req.target_pacs_user,
        req.target_pacs_pass,
        req.anonymize,
        req.examination_result,
        req.notify_url,
    )
    audit_log("forward", _current_user.username, job["id"], "success", {"study_id": study_id})
    return {"job_id": job["id"], "status": "queued"}
