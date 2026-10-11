"""FastAPI application with routes and startup hooks."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy.orm import Session
import zipfile
import tempfile
import os
from pathlib import Path

from app.database import get_db, Base, engine, SessionLocal
from app.schemas import JobCreateRequest, JobCreateResponse, JobStatusResponse
from app.services import create_job, get_job_status, recover_stuck_jobs
from app.models import JobStatus, CertificateStatus, Certificate
from app.storage import certificate_exists, read_certificate


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    # Startup: Create tables and recover stuck jobs
    Base.metadata.create_all(bind=engine)
    
    # Recovery: reset stuck processing certificates and re-enqueue
    db = SessionLocal()
    try:
        reset_count = recover_stuck_jobs(db)
        if reset_count > 0:
            # Re-enqueue jobs with pending work
            from app.tasks import enqueue_job_processing
            
            jobs_with_pending = (
                db.query(Certificate.job_id)
                .filter(Certificate.status == CertificateStatus.PENDING)
                .distinct()
                .all()
            )
            
            for (job_id,) in jobs_with_pending:
                # Get pending certificate IDs for this job
                cert_ids = [
                    cert.id for cert in db.query(Certificate)
                    .filter(
                        Certificate.job_id == job_id,
                        Certificate.status == CertificateStatus.PENDING
                    )
                    .all()
                ]
                
                if cert_ids:
                    task_ids = enqueue_job_processing(job_id, cert_ids)
                    print(f"Recovery: re-enqueued {len(task_ids)} tasks for job {job_id}")
    finally:
        db.close()
    
    yield
    
    # Shutdown: nothing to do (Celery workers handle their own cleanup)


app = FastAPI(
    title="Bulk Certificate Generator",
    description="Async certificate generation API",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/jobs", status_code=status.HTTP_202_ACCEPTED, response_model=JobCreateResponse)
async def create_job_endpoint(
    request: JobCreateRequest,
    db: Session = Depends(get_db)
):
    """
    Create a new certificate generation job.
    
    Request-level validation (strict): FastAPI/Pydantic validates event_name and recipients list.
    Per-recipient validation (lenient): Invalid recipients inserted as failed with error messages.
    
    Returns 202 Accepted with job_id, status, and total count.
    """
    try:
        # Create job and all certificate rows in one transaction
        job, valid_count = create_job(db, request.event_name, request.recipients)
        
        # ENQUEUE AFTER COMMIT: Now that job is committed, enqueue processing
        # Only enqueue if there are valid certificates to process
        if valid_count > 0:
            from app.tasks import enqueue_job_processing
            from app.models import Certificate
            
            # Get IDs of pending certificates
            pending_cert_ids = [
                cert.id for cert in db.query(Certificate)
                .filter(
                    Certificate.job_id == job.id,
                    Certificate.status == CertificateStatus.PENDING
                )
                .all()
            ]
            
            # Enqueue tasks (chunked)
            task_ids = enqueue_job_processing(job.id, pending_cert_ids)
            print(f"Enqueued {len(task_ids)} tasks for job {job.id}")
        else:
            # All recipients were invalid, job is already failed
            # Finalize it immediately (no async work needed)
            from app.services import finalize_job
            finalize_job(db, job.id)
            print(f"Job {job.id}: all recipients invalid, no tasks enqueued")
        
        return JobCreateResponse(
            job_id=job.id,
            status=job.status,
            total=job.total
        )
        
    except Exception as e:
        # Log error and return generic message (never leak stack traces)
        print(f"Error creating job: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create job"
        )


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status_endpoint(
    job_id: str,
    failures_offset: int = 0,
    failures_limit: int = 100,
    db: Session = Depends(get_db)
):
    """
    Get job status with certificate counts and failure details.
    
    Returns 404 if job not found.
    Failures list is paginated with has_more_failures flag.
    """
    try:
        job_data = get_job_status(db, job_id, failures_offset, failures_limit)
        return JobStatusResponse(**job_data)
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        print(f"Error getting job status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get job status"
        )



@app.get("/jobs/{job_id}/certificates/{cert_id}/download")
async def download_certificate(
    job_id: str,
    cert_id: str,
    db: Session = Depends(get_db)
):
    """
    Download a single certificate PDF.
    
    Returns 404 if certificate not found or doesn't belong to job.
    Returns 409 if certificate is not yet successful.
    Returns 500 if file is missing on disk.
    """
    # Get certificate
    cert = db.query(Certificate).filter(
        Certificate.id == cert_id,
        Certificate.job_id == job_id
    ).first()
    
    if not cert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate {cert_id} not found in job {job_id}"
        )
    
    # Check if certificate is ready
    if cert.status != CertificateStatus.SUCCESS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Certificate is not ready (status: {cert.status})"
        )
    
    # Check if file exists
    if not cert.file_path or not certificate_exists(cert.file_path):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Certificate file not found on disk"
        )
    
    # Get recipient name for filename
    recipient_name = cert.recipient.get("name", "certificate")
    safe_name = "".join(c for c in recipient_name if c.isalnum() or c in (' ', '-', '_')).strip()
    filename = f"{safe_name}_{cert.idx}.pdf"
    
    # Stream the PDF
    return FileResponse(
        path=cert.file_path,
        media_type="application/pdf",
        filename=filename
    )


@app.get("/jobs/{job_id}/download")
async def download_job_certificates(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Download all successful certificates for a job as a ZIP file.
    
    Returns 404 if job not found.
    Returns 409 if job is still pending or processing.
    """
    # Get job
    from app.models import Job
    job = db.query(Job).filter(Job.id == job_id).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )
    
    # Check if job is finished
    if job.finished_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job is still processing"
        )
    
    # Get all successful certificates
    successful_certs = db.query(Certificate).filter(
        Certificate.job_id == job_id,
        Certificate.status == CertificateStatus.SUCCESS
    ).order_by(Certificate.idx).all()
    
    if not successful_certs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No successful certificates found for this job"
        )
    
    # Create ZIP in temp file
    # Use a temp file that persists until after the response is sent
    temp_fd, temp_path = tempfile.mkstemp(suffix=".zip")
    
    try:
        with os.fdopen(temp_fd, 'wb') as temp_file:
            with zipfile.ZipFile(temp_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for cert in successful_certs:
                    # Check file exists
                    if cert.file_path and certificate_exists(cert.file_path):
                        # Create safe filename: idx_name.pdf
                        recipient_name = cert.recipient.get("name", "certificate")
                        safe_name = "".join(c for c in recipient_name if c.isalnum() or c in (' ', '-', '_')).strip()
                        zip_filename = f"{cert.idx:04d}_{safe_name}.pdf"
                        
                        # Add to ZIP
                        zipf.write(cert.file_path, arcname=zip_filename)
        
        # Schedule cleanup after response is sent
        def cleanup_temp_file():
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except Exception:
                pass
        
        background_tasks.add_task(cleanup_temp_file)
        
        # Return ZIP file
        return FileResponse(
            path=temp_path,
            media_type="application/zip",
            filename=f"{job.event_name}_certificates.zip"
        )
        
    except Exception as e:
        # Clean up temp file on error
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create ZIP file: {str(e)}"
        )
