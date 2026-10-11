"""Celery tasks for async certificate processing."""
import os
from celery import Celery
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.services import process_chunk

# Create Celery app
celery_app = Celery(
    "certificate_generator",
    broker=settings.redis_url,
    backend=settings.redis_url
)

# Celery configuration
# task_acks_late: Don't ack until task completes (survive worker crashes)
# worker_prefetch_multiplier: Only fetch one task at a time (even distribution)
# task_time_limit: Hard timeout (kills worker process)
# task_soft_time_limit: Soft timeout (raises exception)

# Check if running in test mode (pytest sets PYTEST_CURRENT_TEST)
is_testing = "PYTEST_CURRENT_TEST" in os.environ

celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=1800,  # 30 minutes hard limit
    task_soft_time_limit=1500,  # 25 minutes soft limit
    worker_pool="prefork",  # Prefork pool (CPU-bound PDF rendering)
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Enable eager mode in tests (tasks run synchronously, in same process)
    task_always_eager=is_testing,
    task_eager_propagates=is_testing,
)


@celery_app.task(name="tasks.process_certificate_chunk")
def process_certificate_chunk_task(job_id: str, certificate_ids: list[str]) -> dict:
    """
    Celery task to process a chunk of certificates.
    
    Thin wrapper around services.process_chunk.
    Creates its own DB session (don't share sessions across threads/workers).
    
    Args:
        job_id: Job ID
        certificate_ids: List of certificate IDs to process
        
    Returns:
        Dict with status info
    """
    db = SessionLocal()
    try:
        process_chunk(db, job_id, certificate_ids)
        return {
            "status": "completed",
            "job_id": job_id,
            "processed": len(certificate_ids)
        }
    finally:
        db.close()


def enqueue_job_processing(job_id: str, certificate_ids: list[str], chunk_size: int = None) -> list[str]:
    """
    Enqueue certificate processing tasks for a job.
    
    Splits certificates into chunks and creates one Celery task per chunk.
    Called AFTER the job is committed to the database.
    
    Args:
        job_id: Job ID
        certificate_ids: List of certificate IDs to process
        chunk_size: Certificates per task (defaults to settings.chunk_size)
        
    Returns:
        List of Celery task IDs
    """
    if chunk_size is None:
        chunk_size = settings.chunk_size
    
    task_ids = []
    
    # Split into chunks
    for i in range(0, len(certificate_ids), chunk_size):
        chunk = certificate_ids[i:i + chunk_size]
        
        # Enqueue task
        result = process_certificate_chunk_task.delay(job_id, chunk)
        task_ids.append(result.id)
    
    return task_ids
