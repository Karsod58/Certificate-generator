"""Business logic for job and certificate processing."""
import uuid
from typing import List, Dict, Any, Tuple
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, update
from pydantic import ValidationError

from app.models import Job, Certificate, JobStatus, CertificateStatus
from app.schemas import RecipientModel
from app.renderer import render, RenderError
from app.storage import write_certificate, get_certificate_path
from app.config import settings


def create_job(db: Session, event_name: str, recipients: List[Dict[str, Any]]) -> Tuple[Job, int]:
    """
    Create a job and all certificate rows in a single transaction.
    
    Two-phase validation:
    1. Request-level (strict): already validated by Pydantic in the endpoint
    2. Per-recipient (lenient): invalid recipients inserted as failed with error messages
    
    Args:
        db: Database session
        event_name: Name of the event
        recipients: List of recipient dictionaries (may contain invalid data)
        
    Returns:
        Tuple of (Job, valid_count) where valid_count is number of valid recipients
    """
    job_id = str(uuid.uuid4())
    total = len(recipients)
    
    # Create job
    job = Job(
        id=job_id,
        event_name=event_name,
        status=JobStatus.PENDING,
        total=total
    )
    db.add(job)
    
    # Validate each recipient and prepare certificate rows
    certificate_rows = []
    valid_count = 0
    
    for idx, recipient_data in enumerate(recipients):
        cert_id = str(uuid.uuid4())
        
        # Per-recipient validation (lenient)
        try:
            # Validate using Pydantic
            validated = RecipientModel(**recipient_data)
            
            # Valid recipient -> pending status
            certificate_rows.append({
                "id": cert_id,
                "job_id": job_id,
                "idx": idx,
                "recipient": recipient_data,  # Store raw input
                "status": CertificateStatus.PENDING,
                "file_path": None,
                "error": None,
                "attempts": 0,
                "updated_at": datetime.utcnow()
            })
            valid_count += 1
            
        except ValidationError as e:
            # Invalid recipient -> failed status with error message
            error_messages = []
            for error in e.errors():
                field = ".".join(str(loc) for loc in error["loc"])
                msg = error["msg"]
                error_messages.append(f"{field}: {msg}")
            
            certificate_rows.append({
                "id": cert_id,
                "job_id": job_id,
                "idx": idx,
                "recipient": recipient_data,  # Store raw input
                "status": CertificateStatus.FAILED,
                "file_path": None,
                "error": "Validation failed: " + "; ".join(error_messages),
                "attempts": 0,
                "updated_at": datetime.utcnow()
            })
    
    # Bulk insert all certificates in one statement
    if certificate_rows:
        db.bulk_insert_mappings(Certificate, certificate_rows)
    
    # Commit transaction
    db.commit()
    db.refresh(job)
    
    return job, valid_count


def get_job_status(
    db: Session,
    job_id: str,
    failures_offset: int = 0,
    failures_limit: int = 100
) -> Dict[str, Any]:
    """
    Get job status with certificate counts and failure details.
    
    Job status is DERIVED from certificate counts, not stored.
    
    Args:
        db: Database session
        job_id: Job ID
        failures_offset: Offset for failure pagination
        failures_limit: Limit for failures list
        
    Returns:
        Dictionary with job status and counts
        
    Raises:
        ValueError: If job not found
    """
    # Get job
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise ValueError(f"Job {job_id} not found")
    
    # Compute counts by status using GROUP BY
    counts_query = (
        db.query(
            Certificate.status,
            func.count(Certificate.id).label("count")
        )
        .filter(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    )
    
    counts = {CertificateStatus.PENDING: 0, CertificateStatus.PROCESSING: 0,
              CertificateStatus.SUCCESS: 0, CertificateStatus.FAILED: 0}
    
    for status, count in counts_query:
        counts[status] = count
    
    # Derive job status from certificate counts
    pending_or_processing = counts[CertificateStatus.PENDING] + counts[CertificateStatus.PROCESSING]
    failed = counts[CertificateStatus.FAILED]
    success = counts[CertificateStatus.SUCCESS]
    
    if pending_or_processing > 0:
        derived_status = JobStatus.PROCESSING if job.started_at else JobStatus.PENDING
    elif failed == 0:
        derived_status = JobStatus.COMPLETED
    elif failed == job.total:
        derived_status = JobStatus.FAILED
    else:
        derived_status = JobStatus.COMPLETED_WITH_ERRORS
    
    # Get failures (paginated)
    failures_query = (
        db.query(Certificate)
        .filter(Certificate.job_id == job_id, Certificate.status == CertificateStatus.FAILED)
        .order_by(Certificate.idx)
        .offset(failures_offset)
        .limit(failures_limit + 1)  # +1 to check if there are more
    )
    
    failed_certs = failures_query.all()
    has_more = len(failed_certs) > failures_limit
    failures = [
        {
            "idx": cert.idx,
            "name": cert.recipient.get("name") if cert.recipient else None,
            "error": cert.error
        }
        for cert in failed_certs[:failures_limit]
    ]
    
    return {
        "job_id": job.id,
        "event_name": job.event_name,
        "status": derived_status,
        "total": job.total,
        "counts": counts,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "failures": failures,
        "has_more_failures": has_more
    }



def process_chunk(db: Session, job_id: str, certificate_ids: List[str]) -> None:
    """
    Process a chunk of certificates for a job.
    
    For each certificate:
    1. CLAIM atomically (UPDATE with WHERE status='pending')
    2. Render PDF and write to disk
    3. Mark success or failed based on outcome
    
    Individual failures never abort the chunk. Each certificate is isolated
    with its own try/except.
    
    This is called synchronously in tests and via Celery tasks in production.
    
    Args:
        db: Database session
        job_id: Job ID
        certificate_ids: List of certificate IDs to process
    """
    # Get job to check for job-level errors
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return  # Job deleted, nothing to do
    
    if job.error:
        # Job-wide failure (e.g., template missing), skip processing
        return
    
    # Mark job as started on first chunk
    if not job.started_at:
        job.started_at = datetime.utcnow()
        job.status = JobStatus.PROCESSING
        db.commit()
    
    # Process each certificate independently
    for cert_id in certificate_ids:
        try:
            _process_single_certificate(db, job_id, cert_id)
        except Exception as e:
            # Catch any unexpected errors to prevent chunk abort
            # The certificate should already be marked failed by _process_single_certificate
            # but log this as a safety net
            print(f"Unexpected error processing certificate {cert_id}: {e}")


def _process_single_certificate(db: Session, job_id: str, cert_id: str) -> None:
    """
    Process a single certificate with atomic claim and error isolation.
    
    Claim pattern: UPDATE ... WHERE id=:id AND status='pending'
    If rowcount == 0, another worker claimed it or it's already done - skip silently.
    
    Error handling:
    - RenderError (deterministic): mark failed, no retry
    - Validation errors: mark failed, no retry  
    - OSError (transient): reset to pending or retry with backoff
    - Other errors: mark failed after max attempts
    """
    # ATOMIC CLAIM: Update status to processing only if currently pending
    # This prevents duplicate processing by concurrent workers
    stmt = (
        update(Certificate)
        .where(and_(Certificate.id == cert_id, Certificate.status == CertificateStatus.PENDING))
        .values(
            status=CertificateStatus.PROCESSING,
            attempts=Certificate.attempts + 1,
            updated_at=datetime.utcnow()
        )
    )
    result = db.execute(stmt)
    db.commit()
    
    if result.rowcount == 0:
        # Already claimed by another worker or already processed
        return
    
    # Get certificate (we successfully claimed it)
    cert = db.query(Certificate).filter(Certificate.id == cert_id).first()
    if not cert:
        return
    
    # Get job for event_name
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return
    
    try:
        # Render PDF
        pdf_bytes = render(cert.recipient, job.event_name)
        
        # Write to disk atomically
        file_path = write_certificate(job_id, cert_id, pdf_bytes)
        
        # Mark success
        cert.status = CertificateStatus.SUCCESS
        cert.file_path = file_path
        cert.error = None
        db.commit()
        
    except RenderError as e:
        # Deterministic rendering failure - don't retry
        cert.status = CertificateStatus.FAILED
        cert.error = f"Render error: {str(e)}"
        db.commit()
        
    except (OSError, IOError) as e:
        # Transient error (disk full, permissions, etc.)
        # Reset to pending for retry if under max attempts
        if cert.attempts < 3:
            cert.status = CertificateStatus.PENDING
            cert.error = f"Transient error (attempt {cert.attempts}): {str(e)}"
        else:
            cert.status = CertificateStatus.FAILED
            cert.error = f"Failed after {cert.attempts} attempts: {str(e)}"
        db.commit()
        
    except Exception as e:
        # Unexpected error - mark failed
        cert.status = CertificateStatus.FAILED
        cert.error = f"Unexpected error: {str(e)}"
        db.commit()
    
    finally:
        # Always try to finalize job after each certificate
        # (handles the case where this is the last certificate)
        try:
            finalize_job(db, job_id)
        except Exception as e:
            print(f"Error finalizing job {job_id}: {e}")


def finalize_job(db: Session, job_id: str) -> None:
    """
    Finalize a job if all certificates are done (no pending/processing).
    
    Computes final status from certificate counts:
    - all success: completed
    - all failed: failed
    - mix: completed_with_errors
    
    Race guard: WHERE finished_at IS NULL ensures only one worker finalizes.
    Safe to call multiple times - idempotent.
    
    Args:
        db: Database session
        job_id: Job ID
    """
    # Check if any certificates are still pending or processing
    pending_or_processing = db.query(func.count(Certificate.id)).filter(
        and_(
            Certificate.job_id == job_id,
            Certificate.status.in_([CertificateStatus.PENDING, CertificateStatus.PROCESSING])
        )
    ).scalar()
    
    if pending_or_processing > 0:
        # Still work to do, don't finalize
        return
    
    # All certificates are done - compute final status
    counts = {}
    counts_query = (
        db.query(
            Certificate.status,
            func.count(Certificate.id).label("count")
        )
        .filter(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    )
    
    for status, count in counts_query:
        counts[status] = count
    
    success = counts.get(CertificateStatus.SUCCESS, 0)
    failed = counts.get(CertificateStatus.FAILED, 0)
    total = success + failed
    
    # Determine final status
    if failed == 0:
        final_status = JobStatus.COMPLETED
    elif failed == total:
        final_status = JobStatus.FAILED
    else:
        final_status = JobStatus.COMPLETED_WITH_ERRORS
    
    # Update job with race guard: only update if not already finalized
    # This ensures exactly one worker finalizes even if multiple call this
    stmt = (
        update(Job)
        .where(and_(Job.id == job_id, Job.finished_at == None))
        .values(status=final_status, finished_at=datetime.utcnow())
    )
    result = db.execute(stmt)
    db.commit()
    
    # Note: result.rowcount tells us if we won the race, but we don't need to act on it


def recover_stuck_jobs(db: Session) -> int:
    """
    Recover stuck jobs by resetting stale processing certificates to pending.
    
    A certificate is stale if:
    - status = 'processing'
    - updated_at older than STALE_MINUTES
    
    This handles worker crashes, network issues, etc.
    Safe to call repeatedly (idempotent).
    Called on application startup.
    
    Args:
        db: Database session
        
    Returns:
        Number of certificates reset
    """
    stale_threshold = datetime.utcnow() - timedelta(minutes=settings.stale_minutes)
    
    # Find and reset stale certificates
    stmt = (
        update(Certificate)
        .where(and_(
            Certificate.status == CertificateStatus.PROCESSING,
            Certificate.updated_at < stale_threshold
        ))
        .values(status=CertificateStatus.PENDING, updated_at=datetime.utcnow())
    )
    result = db.execute(stmt)
    db.commit()
    
    reset_count = result.rowcount
    
    if reset_count > 0:
        print(f"Recovery: reset {reset_count} stale certificates to pending")
        
        # Get unique job IDs that need re-processing
        # (we'll re-enqueue them in Stage 5 when Celery is wired)
        jobs_with_pending = (
            db.query(Certificate.job_id)
            .filter(Certificate.status == CertificateStatus.PENDING)
            .distinct()
            .all()
        )
        
        print(f"Recovery: {len(jobs_with_pending)} jobs have pending work")
    
    return reset_count
