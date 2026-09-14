import os
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, Integer, JSON, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://msvmed:msvmed@localhost:5432/msvmed")
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class DicomStudyRecord(Base):
    __tablename__ = "dicom_studies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    orthanc_study_id = Column(String, unique=True, index=True, nullable=False)
    study_instance_uid = Column(String, index=True)
    patient_id = Column(String, index=True)
    patient_name = Column(String)
    modality = Column(String, index=True)
    study_date = Column(String, index=True)
    study_description = Column(Text)
    image_comments = Column(Text)
    series_count = Column(Integer)
    instance_count = Column(Integer)
    raw_tags = Column(JSON)
    embedding = Column(Vector(384))
    ingested_at = Column(DateTime, default=datetime.utcnow)
