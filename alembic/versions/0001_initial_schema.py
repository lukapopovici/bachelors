"""Create the application schema.

Revision ID: 0001_initial_schema
Revises:
"""
from alembic import op
from sqlalchemy import text

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.execute(text("SELECT to_regclass('public.dicom_studies')")).scalar() is not None:
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
        CREATE TABLE IF NOT EXISTS dicom_studies (
            id SERIAL PRIMARY KEY,
            orthanc_study_id VARCHAR NOT NULL UNIQUE,
            study_instance_uid VARCHAR,
            patient_id VARCHAR,
            patient_name VARCHAR,
            modality VARCHAR,
            study_date VARCHAR,
            study_description TEXT,
            image_comments TEXT,
            series_count INTEGER,
            instance_count INTEGER,
            raw_tags JSON,
            embedding vector(384),
            ingested_at TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_dicom_studies_orthanc_study_id ON dicom_studies (orthanc_study_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dicom_studies_study_instance_uid ON dicom_studies (study_instance_uid)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dicom_studies_patient_id ON dicom_studies (patient_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dicom_studies_modality ON dicom_studies (modality)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dicom_studies_study_date ON dicom_studies (study_date)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id VARCHAR PRIMARY KEY,
            type VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            progress JSON NOT NULL,
            instances JSON NOT NULL,
            errors JSON NOT NULL,
            params JSON NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY,
            username VARCHAR NOT NULL UNIQUE,
            hashed_password VARCHAR NOT NULL,
            role VARCHAR NOT NULL,
            is_active BOOLEAN NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_username ON users (username)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP NOT NULL,
            request_id VARCHAR NOT NULL,
            action VARCHAR NOT NULL,
            actor VARCHAR NOT NULL,
            resource_id VARCHAR,
            outcome VARCHAR NOT NULL,
            details JSON NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS failed_instances (
            id SERIAL PRIMARY KEY,
            job_id VARCHAR NOT NULL REFERENCES jobs(id),
            job_type VARCHAR NOT NULL,
            instance_uid VARCHAR NOT NULL,
            error_message VARCHAR NOT NULL,
            attempts INTEGER NOT NULL,
            failed_at TIMESTAMP NOT NULL,
            raw_bytes BYTEA NOT NULL,
            params JSON NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_failed_instances_job_id ON failed_instances (job_id)")
    op.execute("ALTER TABLE dicom_studies DROP COLUMN IF EXISTS pixel_data_present")
    op.execute("ALTER TABLE dicom_studies DROP COLUMN IF EXISTS edge_embedding")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS failed_instances")
    op.execute("DROP TABLE IF EXISTS audit_logs")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS jobs")
    op.execute("DROP TABLE IF EXISTS dicom_studies")
