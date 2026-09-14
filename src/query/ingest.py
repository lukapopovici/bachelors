import logging

import httpx

from src.config import ORTHANC_URL, orthanc_auth
from src.database import DicomStudyRecord
from src.query.embeddings import build_embedding_text, compute_embedding
from src.query.repository import DicomStudyRepository

logger = logging.getLogger("msv-med.ingest")


def fetch_study_details(study_id: str) -> dict:
    response = httpx.get(
        f"{ORTHANC_URL}/studies/{study_id}",
        auth=orthanc_auth(),
        timeout=10,
    )
    if response.status_code == 404:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Study {study_id} not found in PACS")
    response.raise_for_status()
    return response.json()


def fetch_instance_tags(instance_id: str) -> dict:
    response = httpx.get(
        f"{ORTHANC_URL}/instances/{instance_id}/simplified-tags",
        auth=orthanc_auth(),
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def ingest_study(study_id: str, db) -> DicomStudyRecord:
    repository = DicomStudyRepository(db)
    existing = repository.get_by_orthanc_id(study_id)
    if existing:
        return existing

    study = fetch_study_details(study_id)
    instances = study.get("Instances", [])
    tags = {}
    if instances:
        instance_id = instances[0].get("ID", "") if isinstance(instances[0], dict) else instances[0]
        try:
            tags = fetch_instance_tags(instance_id)
        except Exception as exc:
            logger.warning("Could not fetch instance tags for %s: %s", study_id, exc)

    tags.update(study.get("PatientMainDicomTags", {}))
    tags.update(study.get("MainDicomTags", {}))
    embedding_vector = compute_embedding(build_embedding_text(tags))

    record = DicomStudyRecord(
        orthanc_study_id=study_id,
        study_instance_uid=tags.get("StudyInstanceUID"),
        patient_id=tags.get("PatientID"),
        patient_name=tags.get("PatientName"),
        modality=tags.get("Modality"),
        study_date=tags.get("StudyDate"),
        study_description=tags.get("StudyDescription"),
        image_comments=tags.get("ImageComments"),
        series_count=len(study.get("Series", [])),
        instance_count=len(instances),
        raw_tags=tags,
        embedding=embedding_vector,
    )
    return repository.save(record)
