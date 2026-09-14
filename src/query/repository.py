from typing import Optional

from sqlalchemy.orm import Session

from src.database import DicomStudyRecord


class DicomStudyRepository:
    """Database access for indexed DICOM study records."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_orthanc_id(self, orthanc_id: str) -> Optional[DicomStudyRecord]:
        return self.db.query(DicomStudyRecord).filter_by(orthanc_study_id=orthanc_id).first()

    def get_by_id(self, record_id: int) -> Optional[DicomStudyRecord]:
        return self.db.query(DicomStudyRecord).filter_by(id=record_id).first()

    def list_ingested_ids(self) -> set[str]:
        rows = self.db.query(DicomStudyRecord.orthanc_study_id).all()
        return {row.orthanc_study_id for row in rows}

    def save(self, record: DicomStudyRecord) -> DicomStudyRecord:
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def delete(self, record: DicomStudyRecord):
        self.db.delete(record)
        self.db.commit()

    def list_all(self, modality: str = None, limit: int = 50,
                 offset: int = 0) -> tuple[int, list[DicomStudyRecord]]:
        query = self.db.query(DicomStudyRecord)
        if modality:
            query = query.filter(DicomStudyRecord.modality == modality.upper())
        total = query.count()
        return total, query.offset(offset).limit(limit).all()

    def count_with_embeddings(self) -> int:
        return self.db.query(DicomStudyRecord).filter(
            DicomStudyRecord.embedding.isnot(None)
        ).count()
