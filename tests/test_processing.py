"""Tests for certificate processing, finalization, and recovery."""
import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
from sqlalchemy.orm import Session

from app.models import Job, Certificate, CertificateStatus, JobStatus
from app.services import (
    process_chunk,
    finalize_job,
    recover_stuck_jobs,
    create_job
)
from app.renderer import RenderError
from app.storage import certificate_exists


def test_process_chunk_success(client, db_session: Session, temp_output_dir):
    """Test successful processing of a chunk."""
    # Create a job with valid recipients
    request_data = {
        "event_name": "Test Event",
        "recipients": [
            {"name": "Alice", "email": "alice@example.com"},
            {"name": "Bob", "email": "bob@example.com"}
        ]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    # Get certificate IDs
    certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).all()
    cert_ids = [cert.id for cert in certs]
    
    # Process the chunk
    process_chunk(db_session, job_id, cert_ids)
    
    # Check all certificates succeeded
    for cert in db_session.query(Certificate).filter(Certificate.job_id == job_id).all():
        assert cert.status == CertificateStatus.SUCCESS
        assert cert.file_path is not None
        assert cert.error is None
        
        # Check file exists on disk
        assert certificate_exists(cert.file_path)
        
        # Check file is valid PDF
        with open(cert.file_path, "rb") as f:
            content = f.read()
            assert content.startswith(b"%PDF")


def test_process_chunk_sets_job_started_at(client, db_session: Session, temp_output_dir):
    """Test that processing sets job.started_at."""
    request_data = {
        "event_name": "Test Event",
        "recipients": [{"name": "User", "email": "user@example.com"}]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    # Job should not have started_at yet
    job = db_session.query(Job).filter(Job.id == job_id).first()
    assert job.started_at is None
    
    # Process chunk
    certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).all()
    process_chunk(db_session, job_id, [cert.id for cert in certs])
    
    # Job should now have started_at
    db_session.refresh(job)
    assert job.started_at is not None
    # Note: job may be completed already since we only have one certificate


def test_individual_failure_isolation(client, db_session: Session, temp_output_dir):
    """Test that one failure doesn't abort the chunk."""
    request_data = {
        "event_name": "Isolation Test",
        "recipients": [
            {"name": "Good1", "email": "good1@example.com"},
            {"name": "Bad", "email": "bad@example.com"},
            {"name": "Good2", "email": "good2@example.com"}
        ]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    # Get certificates in order
    certs = db_session.query(Certificate).filter(
        Certificate.job_id == job_id
    ).order_by(Certificate.idx).all()
    
    cert_ids = [cert.id for cert in certs]
    middle_cert_id = cert_ids[1]
    
    # Monkeypatch render to fail for middle certificate
    original_render = __import__("app.renderer", fromlist=["render"]).render
    
    def mock_render(recipient, event_name):
        if recipient.get("name") == "Bad":
            raise RenderError("Simulated render failure")
        return original_render(recipient, event_name)
    
    with patch("app.services.render", side_effect=mock_render):
        process_chunk(db_session, job_id, cert_ids)
    
    # Check results
    certs_after = db_session.query(Certificate).filter(
        Certificate.job_id == job_id
    ).order_by(Certificate.idx).all()
    
    # First certificate: success
    assert certs_after[0].status == CertificateStatus.SUCCESS
    assert certs_after[0].file_path is not None
    assert certificate_exists(certs_after[0].file_path)
    
    # Middle certificate: failed
    assert certs_after[1].status == CertificateStatus.FAILED
    assert "Render error" in certs_after[1].error
    assert certs_after[1].file_path is None
    
    # Third certificate: success
    assert certs_after[2].status == CertificateStatus.SUCCESS
    assert certs_after[2].file_path is not None
    assert certificate_exists(certs_after[2].file_path)
    
    # Job should be completed_with_errors
    job = db_session.query(Job).filter(Job.id == job_id).first()
    db_session.refresh(job)
    assert job.finished_at is not None
    
    # Get final status from get_job_status (which derives it)
    response = client.get(f"/jobs/{job_id}")
    assert response.json()["status"] == JobStatus.COMPLETED_WITH_ERRORS


def test_claim_idempotency(client, db_session: Session, temp_output_dir):
    """Test that processing the same chunk twice doesn't duplicate work."""
    request_data = {
        "event_name": "Idempotency Test",
        "recipients": [{"name": "User", "email": "user@example.com"}]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).all()
    cert_ids = [cert.id for cert in certs]
    
    # Process once
    process_chunk(db_session, job_id, cert_ids)
    
    # Get result
    cert1 = db_session.query(Certificate).filter(Certificate.id == cert_ids[0]).first()
    assert cert1.status == CertificateStatus.SUCCESS
    file_path1 = cert1.file_path
    attempts1 = cert1.attempts
    
    # Process again (simulating duplicate delivery or retry)
    process_chunk(db_session, job_id, cert_ids)
    
    # Certificate should still have same state (claim failed, nothing happened)
    cert2 = db_session.query(Certificate).filter(Certificate.id == cert_ids[0]).first()
    assert cert2.status == CertificateStatus.SUCCESS
    assert cert2.file_path == file_path1
    assert cert2.attempts == attempts1  # No additional attempt


def test_finalize_job_completed(client, db_session: Session, temp_output_dir):
    """Test job finalization when all certificates succeed."""
    request_data = {
        "event_name": "Finalize Test",
        "recipients": [
            {"name": "User1", "email": "user1@example.com"},
            {"name": "User2", "email": "user2@example.com"}
        ]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    # Process all certificates
    certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).all()
    process_chunk(db_session, job_id, [cert.id for cert in certs])
    
    # Job should be finalized
    job = db_session.query(Job).filter(Job.id == job_id).first()
    assert job.finished_at is not None
    
    # Check derived status
    response = client.get(f"/jobs/{job_id}")
    data = response.json()
    assert data["status"] == JobStatus.COMPLETED
    assert data["counts"]["success"] == 2
    assert data["counts"]["failed"] == 0


def test_finalize_job_failed(client, db_session: Session, temp_output_dir):
    """Test job finalization when all certificates fail."""
    request_data = {
        "event_name": "All Fail Test",
        "recipients": [
            {"name": "User1", "email": "user1@example.com"},
            {"name": "User2", "email": "user2@example.com"}
        ]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    # Mock render to always fail
    with patch("app.services.render", side_effect=RenderError("All fail")):
        certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).all()
        process_chunk(db_session, job_id, [cert.id for cert in certs])
    
    # Check derived status
    response = client.get(f"/jobs/{job_id}")
    data = response.json()
    assert data["status"] == JobStatus.FAILED
    assert data["counts"]["success"] == 0
    assert data["counts"]["failed"] == 2


def test_finalize_race_guard(client, db_session: Session, temp_output_dir):
    """Test that finalize_job can be called multiple times safely."""
    request_data = {
        "event_name": "Race Test",
        "recipients": [{"name": "User", "email": "user@example.com"}]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    # Process certificate
    certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).all()
    process_chunk(db_session, job_id, [cert.id for cert in certs])
    
    # Get finished_at timestamp
    job = db_session.query(Job).filter(Job.id == job_id).first()
    first_finished_at = job.finished_at
    assert first_finished_at is not None
    
    # Call finalize again (simulating concurrent workers)
    finalize_job(db_session, job_id)
    
    # finished_at should not change
    db_session.refresh(job)
    assert job.finished_at == first_finished_at


def test_finalize_not_called_when_pending(client, db_session: Session, temp_output_dir):
    """Test that finalize doesn't run if work remains."""
    request_data = {
        "event_name": "Partial Test",
        "recipients": [
            {"name": "User1", "email": "user1@example.com"},
            {"name": "User2", "email": "user2@example.com"}
        ]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    # Process only first certificate
    certs = db_session.query(Certificate).filter(
        Certificate.job_id == job_id
    ).order_by(Certificate.idx).all()
    
    process_chunk(db_session, job_id, [certs[0].id])
    
    # Job should NOT be finalized (second cert still pending)
    job = db_session.query(Job).filter(Job.id == job_id).first()
    assert job.finished_at is None


def test_recovery_resets_stale_certificates(client, db_session: Session, temp_output_dir):
    """Test that recovery resets stuck processing certificates."""
    from app.config import settings
    
    request_data = {
        "event_name": "Recovery Test",
        "recipients": [
            {"name": "User1", "email": "user1@example.com"},
            {"name": "User2", "email": "user2@example.com"}
        ]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    # Manually mark certificates as stale processing
    stale_time = datetime.utcnow() - timedelta(minutes=settings.stale_minutes + 1)
    
    certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).all()
    for cert in certs:
        cert.status = CertificateStatus.PROCESSING
        cert.updated_at = stale_time
    db_session.commit()
    
    # Run recovery
    reset_count = recover_stuck_jobs(db_session)
    
    # Both certificates should be reset
    assert reset_count == 2
    
    # Check they're back to pending
    for cert in db_session.query(Certificate).filter(Certificate.job_id == job_id).all():
        assert cert.status == CertificateStatus.PENDING


def test_recovery_ignores_fresh_processing(client, db_session: Session, temp_output_dir):
    """Test that recovery doesn't touch fresh processing certificates."""
    request_data = {
        "event_name": "Fresh Test",
        "recipients": [{"name": "User", "email": "user@example.com"}]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    # Mark certificate as recently processing
    cert = db_session.query(Certificate).filter(Certificate.job_id == job_id).first()
    cert.status = CertificateStatus.PROCESSING
    cert.updated_at = datetime.utcnow()  # Fresh timestamp
    db_session.commit()
    
    # Run recovery
    reset_count = recover_stuck_jobs(db_session)
    
    # Should not reset anything
    assert reset_count == 0
    
    # Certificate should still be processing
    db_session.refresh(cert)
    assert cert.status == CertificateStatus.PROCESSING


def test_transient_error_retry(client, db_session: Session, temp_output_dir):
    """Test that transient errors reset certificate to pending for retry."""
    request_data = {
        "event_name": "Transient Test",
        "recipients": [{"name": "User", "email": "user@example.com"}]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).all()
    cert_id = certs[0].id
    
    # Mock write_certificate to raise OSError
    with patch("app.services.write_certificate", side_effect=OSError("Disk full")):
        process_chunk(db_session, job_id, [cert_id])
    
    # Certificate should be reset to pending (for retry)
    cert = db_session.query(Certificate).filter(Certificate.id == cert_id).first()
    assert cert.status == CertificateStatus.PENDING
    assert cert.attempts == 1
    assert "Transient error" in cert.error


def test_transient_error_max_attempts(client, db_session: Session, temp_output_dir):
    """Test that after max attempts, transient errors mark as failed."""
    request_data = {
        "event_name": "Max Attempts Test",
        "recipients": [{"name": "User", "email": "user@example.com"}]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    cert = db_session.query(Certificate).filter(Certificate.job_id == job_id).first()
    cert_id = cert.id
    
    # Process 3 times with transient error
    with patch("app.services.write_certificate", side_effect=OSError("Disk full")):
        for _ in range(3):
            # Reset to pending for next attempt
            cert.status = CertificateStatus.PENDING
            db_session.commit()
            process_chunk(db_session, job_id, [cert_id])
    
    # After 3 attempts, should be permanently failed
    cert = db_session.query(Certificate).filter(Certificate.id == cert_id).first()
    assert cert.status == CertificateStatus.FAILED
    assert cert.attempts == 3
    assert "Failed after" in cert.error
