import io
from collections.abc import MutableMapping

import pydicom
from pydicom.errors import InvalidDicomError
from pydicom.uid import generate_uid

REQUIRED_UIDS = ("SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID")
PHI_TAGS = (
    "PatientName", "PatientID", "PatientBirthDate", "PatientSex", "PatientAge",
    "PatientAddress", "PatientTelephoneNumbers", "ReferringPhysicianName",
    "InstitutionName", "InstitutionAddress", "StudyDescription", "SeriesDescription",
    "OperatorsName", "PerformingPhysicianName", "RequestingPhysician", "ImageComments",
)
UID_TAGS = ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID", "FrameOfReferenceUID")


def validate_dicom(raw_bytes: bytes) -> tuple[bool, str]:
    try:
        dataset = pydicom.dcmread(io.BytesIO(raw_bytes), stop_before_pixels=True, force=False)
        missing = [name for name in REQUIRED_UIDS if not dataset.get(name)]
        if missing:
            return False, f"Missing required DICOM identifiers: {', '.join(missing)}"
        return True, ""
    except InvalidDicomError as exc:
        return False, str(exc) or exc.__class__.__name__
    except Exception as exc:
        return False, str(exc) or exc.__class__.__name__


def dicom_bytes_to_dataset(raw: bytes) -> pydicom.Dataset:
    return pydicom.dcmread(io.BytesIO(raw))


def dataset_to_bytes(ds: pydicom.Dataset) -> bytes:
    buf = io.BytesIO()
    pydicom.dcmwrite(buf, ds, write_like_original=False)
    return buf.getvalue()


def anonymize_dataset(
    ds: pydicom.Dataset,
    uid_map: MutableMapping[str, str] | None = None,
) -> pydicom.Dataset:
    """
    Basic anonymization following DICOM PS3.15 Basic Application Level
    Confidentiality Profile. Replace with a vetted library (e.g.
    dicomanonymizer) for clinical production use.
    """
    uid_map = uid_map if uid_map is not None else {}
    for uid_name in UID_TAGS:
        original_uid = ds.get(uid_name)
        if original_uid:
            original_uid = str(original_uid)
            ds[uid_name].value = uid_map.setdefault(original_uid, generate_uid())

    for tag in PHI_TAGS:
        if tag in ds:
            del ds[tag]
    ds.remove_private_tags()
    for element in ds:
        if element.VR == "SQ":
            for item in element.value:
                anonymize_dataset(item, uid_map)
    return ds


def embed_examination_result(ds: pydicom.Dataset, result: str) -> pydicom.Dataset:
    """Inject examination result text into ImageComments (0020,4000)."""
    ds.ImageComments = result
    return ds
