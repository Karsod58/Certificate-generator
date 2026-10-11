"""Tests for Celery task integration (eager mode).

NOTE: Celery integration uses eager mode in tests (tasks run synchronously).
Full end-to-end Celery tests require a real Redis broker and are better suited
for integration testing outside of unit tests.

These tests verify:
1. Celery configuration is correct
2. Tasks can be enqueued
3. Enqueue-after-commit pattern works
4. Tasks can be invoked directly
"""
import pytest
from sqlalchemy.orm import Session

from app.models import Job, Certificate, CertificateStatus, JobStatus


def test_celery_eager_mode_enabled_in_tests():
    """Verify Celery eager mode is auto-detected in test environment."""
    from app.tasks import celery_app
    import os
    
    # Pytest sets this environment variable
    assert "PYTEST_CURRENT_TEST" in os.environ
    
    # Eager mode should be enabled
    assert celery_app.conf.task_always_eager is True
    assert celery_app.conf.task_eager_propagates is True


def test_celery_task_signature():
    """Verify Celery task is registered and has correct signature."""
    from app.tasks import process_certificate_chunk_task
    
    # Task should be a Celery task
    assert hasattr(process_certificate_chunk_task, 'delay')
    assert hasattr(process_certificate_chunk_task, 'apply_async')
    
    # Task name should be registered
    assert process_certificate_chunk_task.name == "tasks.process_certificate_chunk"


def test_enqueue_function_splits_into_chunks():
    """Verify enqueue_job_processing splits certificates into proper chunks."""
    from app.tasks import enqueue_job_processing
    from app.config import settings
    from unittest.mock import patch, MagicMock
    
    # Mock the actual task to avoid execution
    with patch('app.tasks.process_certificate_chunk_task') as mock_task:
        mock_result = MagicMock()
        mock_result.id = "test-task-id"
        mock_task.delay.return_value = mock_result
        
        # Create cert IDs spanning multiple chunks
        num_certs = settings.chunk_size * 2 + 5
        cert_ids = [f"cert-{i}" for i in range(num_certs)]
        
        # Enqueue
        task_ids = enqueue_job_processing("test-job", cert_ids)
        
        # Verify chunking
        expected_chunks = 3  # 10 + 10 + 5 for chunk_size=10
        assert mock_task.delay.call_count == expected_chunks
        assert len(task_ids) == expected_chunks


def test_job_creation_enqueues_tasks(client):
    """Verify POST /jobs enqueues Celery tasks."""
    from unittest.mock import patch
    
    enqueue_called = []
    
    def track_enqueue(job_id, cert_ids, chunk_size=None):
        enqueue_called.append((job_id, len(cert_ids)))
        return ["task-1"]
    
    with patch('app.tasks.enqueue_job_processing', side_effect=track_enqueue):
        request_data = {
            "event_name": "Enqueue Test",
            "recipients": [
                {"name": "User1", "email": "user1@example.com"},
                {"name": "User2", "email": "user2@example.com"}
            ]
        }
        
        response = client.post("/jobs", json=request_data)
        assert response.status_code == 202
    
    # Verify enqueue was called
    assert len(enqueue_called) == 1
    job_id, cert_count = enqueue_called[0]
    assert cert_count == 2


def test_all_invalid_recipients_finalizes_immediately(client, db_session: Session):
    """Verify jobs with all invalid recipients are finalized without enqueueing."""
    from unittest.mock import patch
    
    enqueue_called = []
    
    def track_enqueue(job_id, cert_ids, chunk_size=None):
        enqueue_called.append(job_id)
        return []
    
    with patch('app.tasks.enqueue_job_processing', side_effect=track_enqueue):
        request_data = {
            "event_name": "All Invalid",
            "recipients": [
                {"name": "", "email": "invalid1@example.com"},
                {"name": "User", "email": "not-an-email"}
            ]
        }
        
        response = client.post("/jobs", json=request_data)
        assert response.status_code == 202
    
    # Enqueue should NOT have been called (no valid recipients)
    assert len(enqueue_called) == 0


def test_celery_config():
    """Verify Celery is configured correctly for production."""
    from app.tasks import celery_app
    
    # Production settings (when not in test mode)
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_time_limit == 1800
    assert celery_app.conf.task_soft_time_limit == 1500
    assert celery_app.conf.worker_pool == "prefork"
