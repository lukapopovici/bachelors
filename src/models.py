from datetime import datetime
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator
from src.config import ORTHANC_URL, ORTHANC_USER, ORTHANC_PASS


def _validate_http_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("must be a valid HTTP or HTTPS URL")
    return value.strip().rstrip("/")


def _validate_required_text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class ForwardRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_study_id: str = Field(min_length=1, max_length=256)
    source_pacs_url: Optional[str] = Field(default=None, max_length=2048)
    target_pacs_url: str = Field(default=ORTHANC_URL, max_length=2048)
    target_pacs_user: str = Field(default=ORTHANC_USER, min_length=1, max_length=256)
    target_pacs_pass: str = Field(default=ORTHANC_PASS, min_length=1, max_length=512)
    anonymize: bool = False
    examination_result: Optional[str] = Field(default=None, max_length=10000)
    notify_url: Optional[str] = Field(default=None, max_length=2048)

    _source_study_id = field_validator("source_study_id", "target_pacs_user", "target_pacs_pass")(
        _validate_required_text
    )
    _urls = field_validator("source_pacs_url", "target_pacs_url", "notify_url")(
        lambda value: _validate_http_url(value) if value is not None else value
    )


class UploadOptions(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    target_pacs_url: str = Field(default=ORTHANC_URL, max_length=2048)
    target_pacs_user: str = Field(default=ORTHANC_USER, min_length=1, max_length=256)
    target_pacs_pass: str = Field(default=ORTHANC_PASS, min_length=1, max_length=512)
    anonymize: bool = False
    examination_result: Optional[str] = Field(default=None, max_length=10000)
    notify_url: Optional[str] = Field(default=None, max_length=2048)

    _credentials = field_validator("target_pacs_user", "target_pacs_pass")(_validate_required_text)
    _urls = field_validator("target_pacs_url", "notify_url")(
        lambda value: _validate_http_url(value) if value is not None else value
    )


class PACSConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=256)
    url: str = Field(min_length=1, max_length=2048)
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=512)

    _required_text = field_validator("name", "username", "password")(_validate_required_text)
    _url = field_validator("url")(_validate_http_url)


class SearchQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    q: str = Field(min_length=1, max_length=1000)
    strategy: Literal["cosine", "euclidean", "fulltext", "hybrid"] = "cosine"
    limit: int = Field(default=10, ge=1, le=100)
    modality: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=16,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    @field_validator("q")
    @classmethod
    def validate_query_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("search query must not be blank")
        return value


class RecordQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    modality: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=16,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool
    created_at: datetime


class JobResponse(BaseModel):
    id: str
    type: str
    status: str
    created_at: datetime
    updated_at: datetime
    params: dict[str, Any]
    instances: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    progress: dict[str, Any]


class JobListResponse(BaseModel):
    total: int
    offset: int
    limit: int
    results: list[JobResponse]


class QueuedJobResponse(BaseModel):
    job_id: str
    status: str = "queued"


class DemoJobResponse(QueuedJobResponse):
    duration_seconds: int


class StudyListItem(BaseModel):
    id: str
    modality: Optional[str] = None
    study_date: Optional[str] = None
    patient_name: Optional[str] = None
    description: Optional[str] = None


class StudyPageResponse(BaseModel):
    total: int
    offset: int
    limit: int
    results: list[StudyListItem]


class SearchResultResponse(BaseModel):
    id: int
    orthanc_study_id: str
    patient_id: Optional[str] = None
    modality: Optional[str] = None
    study_date: Optional[str] = None
    study_description: Optional[str] = None
    image_comments: Optional[str] = None
    instance_count: Optional[int] = None
    similarity: Optional[float] = None


class SearchResponse(BaseModel):
    strategy: str
    count: int
    results: list[SearchResultResponse]


class SearchStrategiesResponse(BaseModel):
    strategies: list[str]
    descriptions: dict[str, str]


class IngestionStatusResponse(BaseModel):
    orthanc_study_id: str
    ingested: bool


class IngestionResponse(BaseModel):
    job_id: str
    id: Optional[int] = None
    orthanc_study_id: Optional[str] = None
    modality: Optional[str] = None
    study_date: Optional[str] = None


class BulkIngestionResponse(BaseModel):
    ingested: list[str]
    skipped: list[str]
    failed: list[dict[str, Any]]
    job_id: str


class RecordResponse(BaseModel):
    id: int
    orthanc_study_id: str
    study_instance_uid: Optional[str] = None
    patient_id: Optional[str] = None
    modality: Optional[str] = None
    study_date: Optional[str] = None
    study_description: Optional[str] = None
    image_comments: Optional[str] = None
    series_count: Optional[int] = None
    instance_count: Optional[int] = None
    raw_tags: Optional[dict[str, Any]] = None
    ingested_at: Optional[datetime] = None


class RecordListResponse(BaseModel):
    total: int
    offset: int
    limit: int
    results: list[RecordResponse]


class DeleteResponse(BaseModel):
    deleted: int


class PACSListItem(BaseModel):
    id: str
    name: str
    url: str


class PACSCreateResponse(BaseModel):
    id: str


class CountResponse(BaseModel):
    deleted_count: int


class RetryJobResponse(BaseModel):
    original_job_id: str
    new_job_id: str


class FailedInstanceResponse(BaseModel):
    id: int
    job_id: str
    job_type: str
    instance_uid: str
    error_message: str
    attempts: int
    failed_at: datetime
    params: dict[str, Any]


class UploadResponse(BaseModel):
    job_id: str
    status: str
    files_received: int
    files_valid: int
    modalities: list[str]
    patient_ids: list[str]


class PACSConnectivityResponse(BaseModel):
    reachable: bool
    latency_ms: Optional[int] = None
    orthanc_version: Optional[str] = None
    dicom_aet: Optional[str] = None
    status_code: Optional[int] = None
    error: Optional[str] = None


class AuditResponse(BaseModel):
    id: int
    timestamp: datetime
    request_id: str
    action: str
    actor: str
    resource_id: Optional[str] = None
    outcome: str
    details: dict[str, Any]


class WorkerResponse(BaseModel):
    name: str
    status: str
    active_tasks: int
    tasks: list[str]
    processed: dict[str, int]


class WorkerListResponse(BaseModel):
    workers: list[WorkerResponse]
    total_online: Optional[int] = None
    note: Optional[str] = None
    error: Optional[str] = None


class AdminStatsResponse(BaseModel):
    jobs: dict[str, Any]
    orthanc: dict[str, Any]
    redis: dict[str, Any]
    pacs_configs: int
    generated_at: datetime
