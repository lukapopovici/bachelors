import logging
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi import Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.audit import audit_log as write_audit_log
from src.auth import get_current_user
from src.config import ORTHANC_URL, orthanc_auth
from src.database import DicomStudyRecord, SessionLocal
from src.query.ingest import ingest_study as persist_study
from src.ratelimit import make_rate_limiter
from src.query.repository import DicomStudyRepository
from src.query.search import SearchStrategyFactory
from src.models import (
    BulkIngestionResponse,
    IngestionResponse,
    IngestionStatusResponse,
    RecordListResponse,
    RecordQuery,
    RecordResponse,
    DeleteResponse,
    SearchQuery,
    SearchResponse,
    SearchStrategiesResponse,
)

logger = logging.getLogger("msv-med.query")
router = APIRouter(prefix="/query", tags=["Query"])


def parse_search_query(
    q: str = Query(..., min_length=1, max_length=1000, description="Search query text"),
    strategy: Literal["cosine", "euclidean", "fulltext", "hybrid"] = Query("cosine"),
    limit: int = Query(10, ge=1, le=100),
    modality: Optional[str] = Query(None),
) -> SearchQuery:
    try:
        return SearchQuery(q=q, strategy=strategy, limit=limit, modality=modality)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def parse_record_query(
    modality: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> RecordQuery:
    try:
        return RecordQuery(modality=modality, limit=limit, offset=offset)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/strategies", tags=["Query"], response_model=SearchStrategiesResponse)
def list_strategies(_current_user=Depends(get_current_user)):
    return {
        "strategies": SearchStrategyFactory.available(),
        "descriptions": {
            "cosine": "Semantic similarity via pgvector cosine distance",
            "euclidean": "Nearest neighbor via pgvector L2 distance",
            "fulltext": "PostgreSQL full-text search (exact term matching)",
            "hybrid": "Weighted combination of cosine + full-text (alpha=0.7)",
        },
    }


@router.get("/studies", tags=["Query"], response_model=list[IngestionStatusResponse])
def query_studies(_current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """List all PACS studies with their ingestion status."""
    response = httpx.get(f"{ORTHANC_URL}/studies", auth=orthanc_auth(), timeout=10)
    response.raise_for_status()
    orthanc_ids = response.json()
    ingested_ids = DicomStudyRepository(db).list_ingested_ids()
    return [
        {"orthanc_study_id": study_id, "ingested": study_id in ingested_ids}
        for study_id in orthanc_ids
    ]


@router.post("/ingestions", tags=["Query"], name="create_bulk_ingestion",
             response_model=BulkIngestionResponse)
@router.post("/ingest/all", tags=["Query"], include_in_schema=False,
             response_model=BulkIngestionResponse)
def ingest_all_studies(
    _current_user=Depends(get_current_user),
    _rate_limit=Depends(make_rate_limiter(2, 60)),
    db: Session = Depends(get_db),
):
    """Ingest every study currently in PACS. Skips already-ingested ones."""
    from src.jobs import create_job, update_job

    response = httpx.get(f"{ORTHANC_URL}/studies", auth=orthanc_auth(), timeout=10)
    response.raise_for_status()
    study_ids = response.json()
    ingested_ids = DicomStudyRepository(db).list_ingested_ids()
    job = create_job("ingest", {"study_count": len(study_ids)})
    update_job(job["id"], status="processing", progress={"total": len(study_ids), "done": 0})
    results = {"ingested": [], "skipped": [], "failed": []}

    for index, study_id in enumerate(study_ids, start=1):
        if study_id in ingested_ids:
            results["skipped"].append(study_id)
        else:
            try:
                persist_study(study_id, db)
                results["ingested"].append(study_id)
            except Exception as exc:
                logger.error("Failed to ingest %s: %s", study_id, exc)
                results["failed"].append({"id": study_id, "error": str(exc)})
        update_job(job["id"], progress={"total": len(study_ids), "done": index})

    update_job(
        job["id"],
        status="completed" if not results["failed"] else "completed_with_errors",
        errors=results["failed"],
    )
    results["job_id"] = job["id"]
    return results


@router.post("/ingestions/{study_id}", tags=["Query"], name="create_ingestion",
             response_model=IngestionResponse)
@router.post("/ingest/{study_id}", tags=["Query"], include_in_schema=False,
             response_model=IngestionResponse)
def ingest_one_study(
    study_id: str,
    _current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ingest a single study from PACS into the vector DB. Idempotent."""
    from src.jobs import create_job, update_job

    job = create_job("ingest", {"study_id": study_id, "study_count": 1})
    update_job(job["id"], status="processing", progress={"total": 1, "done": 0})
    try:
        record = persist_study(study_id, db)
    except Exception as exc:
        write_audit_log("ingest", _current_user.username, study_id, "failure", {"error": str(exc)})
        update_job(job["id"], status="failed", errors=[{"study_id": study_id, "error": str(exc)}])
        raise

    update_job(job["id"], status="completed", progress={"total": 1, "done": 1})
    write_audit_log("ingest", _current_user.username, study_id, "success", {"job_id": job["id"]})
    return {
        "job_id": job["id"],
        "id": record.id,
        "orthanc_study_id": record.orthanc_study_id,
        "modality": record.modality,
        "study_date": record.study_date,
    }


@router.get("/search", tags=["Query"], response_model=SearchResponse)
def search(
    params: SearchQuery = Depends(parse_search_query),
    _current_user=Depends(get_current_user),
    _rate_limit=Depends(make_rate_limiter(60, 60)),
    db: Session = Depends(get_db),
):
    search_strategy = SearchStrategyFactory.get(params.strategy)
    results = search_strategy.search(db, params.q, params.limit, params.modality)
    return {"strategy": params.strategy, "count": len(results), "results": results}


@router.get("/records", tags=["Query"], response_model=RecordListResponse)
def list_records(
    params: RecordQuery = Depends(parse_record_query),
    _current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    total, records = DicomStudyRepository(db).list_all(
        params.modality, params.limit, params.offset
    )
    return {
        "total": total,
        "offset": params.offset,
        "limit": params.limit,
        "results": [
            {
                "id": record.id,
                "orthanc_study_id": record.orthanc_study_id,
                "modality": record.modality,
                "study_date": record.study_date,
                "study_description": record.study_description,
                "image_comments": record.image_comments,
                "patient_id": record.patient_id,
                "instance_count": record.instance_count,
                "raw_tags": record.raw_tags,
                "ingested_at": record.ingested_at,
            }
            for record in records
        ],
    }


@router.get("/records/{record_id}", tags=["Query"], response_model=RecordResponse)
def get_record(record_id: int, _current_user=Depends(get_current_user),
               db: Session = Depends(get_db)):
    record = DicomStudyRepository(db).get_by_id(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    return {
        "id": record.id,
        "orthanc_study_id": record.orthanc_study_id,
        "study_instance_uid": record.study_instance_uid,
        "patient_id": record.patient_id,
        "modality": record.modality,
        "study_date": record.study_date,
        "study_description": record.study_description,
        "image_comments": record.image_comments,
        "series_count": record.series_count,
        "instance_count": record.instance_count,
        "raw_tags": record.raw_tags,
        "ingested_at": record.ingested_at,
    }


@router.delete("/records/{record_id}", tags=["Query"], response_model=DeleteResponse)
def delete_record(record_id: int, _current_user=Depends(get_current_user),
                  db: Session = Depends(get_db)):
    repo = DicomStudyRepository(db)
    record = repo.get_by_id(record_id)
    if not record:
        write_audit_log("delete_record", _current_user.username, str(record_id), "failure", {})
        raise HTTPException(status_code=404, detail="Record not found")
    repo.delete(record)
    write_audit_log("delete_record", _current_user.username, str(record_id), "success", {})
    return {"deleted": record_id}
