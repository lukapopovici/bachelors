import httpx
from fastapi import APIRouter, HTTPException, Depends
from src.config import ORTHANC_URL, orthanc_auth
from src.auth import get_current_user
from src.dicom_utils import dicom_bytes_to_dataset

router = APIRouter(prefix="/instances", tags=["Instances"])


@router.get("/{instance_id}")
def get_instance(instance_id: str, _current_user=Depends(get_current_user)):
    """GET /instances/{id} — get parsed DICOM metadata for one instance."""
    r = httpx.get(f"{ORTHANC_URL}/instances/{instance_id}/file", auth=orthanc_auth())
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Instance not found")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="PACS error")

    ds = dicom_bytes_to_dataset(r.content)
    return {
        "id":               instance_id,
        "sop_instance_uid": str(ds.get("SOPInstanceUID", "")),
        "study_instance_uid": str(ds.get("StudyInstanceUID", "")),
        "series_instance_uid": str(ds.get("SeriesInstanceUID", "")),
        "patient_name":     str(ds.get("PatientName", "")),
        "patient_id":       str(ds.get("PatientID", "")),
        "modality":         str(ds.get("Modality", "")),
        "study_date":       str(ds.get("StudyDate", "")),
        "image_comments":   str(ds.get("ImageComments", "")),
    }
