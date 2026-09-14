import httpx
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from src.config import ORTHANC_URL, orthanc_auth
from src.auth import get_current_user
from src.cache import cache_get, cache_set
from src.database import DicomStudyRecord, SessionLocal
from src.models import StudyPageResponse

router = APIRouter(prefix="/studies", tags=["Studies"])


@router.get("", response_model=StudyPageResponse)
def list_studies(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    modality: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    _current_user=Depends(get_current_user),
):
    """List a page of PACS studies with optional DICOM tag filters."""
    cache_key = _study_list_cache_key(limit, offset, modality, date_from, date_to)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    r = httpx.get(f"{ORTHANC_URL}/studies", auth=orthanc_auth())
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch studies from PACS")
    study_ids = r.json()
    page_ids = study_ids[offset: offset + limit]
    indexed = _indexed_study_metadata(page_ids)
    results = []

    for study_id in page_ids:
        metadata = indexed.get(study_id)
        if metadata is None:
            metadata = _fetch_study_metadata(study_id)
        if _matches_filters(metadata, modality, date_from, date_to):
            results.append({"id": study_id, **metadata})

    response = {
        "total": len(study_ids),
        "offset": offset,
        "limit": limit,
        "results": results,
    }
    cache_set(cache_key, response, 30)
    return response


def _study_list_cache_key(
    limit: int,
    offset: int,
    modality: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
) -> str:
    values = [str(limit), str(offset), modality or "", date_from or "", date_to or ""]
    return "studies:list:" + ":".join(values)


def _indexed_study_metadata(study_ids: list[str]) -> dict[str, dict]:
    if not study_ids:
        return {}

    db = SessionLocal()
    try:
        records = db.query(DicomStudyRecord).filter(
            DicomStudyRecord.orthanc_study_id.in_(study_ids)
        ).all()
        return {
            record.orthanc_study_id: {
                "modality": record.modality or "",
                "study_date": record.study_date or "",
                "patient_name": record.patient_name or "",
                "description": record.study_description or "",
            }
            for record in records
        }
    except Exception:
        db.rollback()
        return {}
    finally:
        db.close()


def _fetch_study_metadata(study_id: str) -> dict:
    response = httpx.get(f"{ORTHANC_URL}/studies/{study_id}", auth=orthanc_auth())
    if response.status_code == 404:
        return {"modality": "", "study_date": "", "patient_name": "", "description": ""}
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch study metadata from PACS")

    tags = response.json().get("MainDicomTags", {})
    return {
        "modality": str(tags.get("Modality", "")),
        "study_date": str(tags.get("StudyDate", "")),
        "patient_name": str(tags.get("PatientName", "")),
        "description": str(tags.get("StudyDescription", "")),
    }


def _matches_filters(
    metadata: dict,
    modality: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
) -> bool:
    if modality and metadata["modality"].upper() != modality.upper():
        return False
    if date_from and metadata["study_date"] < date_from:
        return False
    if date_to and metadata["study_date"] > date_to:
        return False
    return True


@router.get("/{study_id}")
def get_study(study_id: str, _current_user=Depends(get_current_user)):
    """GET /studies/{id} — get one study. Returns 200 or 404."""
    cache_key = f"studies:{study_id}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    r = httpx.get(f"{ORTHANC_URL}/studies/{study_id}", auth=orthanc_auth())
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Study not found")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="PACS error")
    study = r.json()
    cache_set(cache_key, study, 300)
    return study


@router.get("/{study_id}/instances")
def list_study_instances(study_id: str, _current_user=Depends(get_current_user)):
    """GET /studies/{id}/instances — list instances belonging to a study."""
    r = httpx.get(f"{ORTHANC_URL}/studies/{study_id}/instances", auth=orthanc_auth())
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Study not found")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="PACS error")
    return r.json()
