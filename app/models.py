"""SQLAlchemy ORM models for jobs and certificates."""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Index, JSON
from sqlalchemy.orm import relationship
from app.database import Base

# Status constants - use these throughout the app, never raw strings
class CertificateStatus:
    """Certificate processing statuses."""
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


class JobStatus:
    """Job-level statuses (derived from certificate counts)."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class Job(Base):
    """Job represents a batch certificate generation request."""
    __tablename__ = "jobs"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    event_name = Column(String, nullable=False)
    status = Column(String, nullable=False, default=JobStatus.PENDING)
    total = Column(Integer, nullable=False)
    error = Column(String, nullable=True)  # Job-level failures only (e.g., missing template)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    
    # Relationship to certificates
    certificates = relationship("Certificate", back_populates="job", cascade="all, delete-orphan")


class Certificate(Base):
    """Certificate represents a single recipient's certificate within a job."""
    __tablename__ = "certificates"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    idx = Column(Integer, nullable=False)  # Position in original request
    recipient = Column(JSON, nullable=False)  # Raw input as submitted
    status = Column(String, nullable=False, default=CertificateStatus.PENDING)
    file_path = Column(String, nullable=True)
    error = Column(String, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship to job
    job = relationship("Job", back_populates="certificates")
    
    # Composite index for efficient queries
    __table_args__ = (
        Index("ix_certificates_job_id_status", "job_id", "status"),
    )
