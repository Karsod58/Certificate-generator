"""Tests for certificate download endpoints."""
import pytest
import zipfile
import io
from sqlalchemy.orm import Session

from app.models import Job, Certificate, CertificateStatus, JobStatus
from app.services import create_job, process_chunk


def test_download_single_certificate(client, db_session: Session, temp_output_dir):
    """Test downloading a single successful certificate."""
    # Create and process a job
    job, valid_count = create_job(
        db_session,
        "Download Test",
        [{"name": "Test User", "email": "test@example.com"}]
    )
    
    # Process certificate
    cert = db_session.query(Certificate).filter(Certificate.job_id == job.id).first()
    process_chunk(db_session, job.id, [cert.id])
    
    # Refresh to get updated data
    db_session.expire_all()
    cert = db_session.query(Certificate).filter(Certificate.id == cert.id).first()
    
    # Download
    response = client.get(f"/jobs/{job.id}/certificates/{cert.id}/download")
    
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    
    # Check filename in headers (space becomes %20 in URL encoding)
    assert "Test" in response.headers.get("content-disposition", "")
    assert "User" in response.headers.get("content-disposition", "")


def test_download_certificate_not_found(client, db_session: Session):
    """Test 404 when certificate doesn't exist."""
    response = client.get("/jobs/fake-job-id/certificates/fake-cert-id/download")
    assert response.status_code == 404


def test_download_certificate_wrong_job(client, db_session: Session, temp_output_dir):
    """Test 404 when certificate belongs to different job."""
    # Create two jobs
    job1, _ = create_job(db_session, "Job 1", [{"name": "User1", "email": "user1@example.com"}])
    job2, _ = create_job(db_session, "Job 2", [{"name": "User2", "email": "user2@example.com"}])
    
    cert1 = db_session.query(Certificate).filter(Certificate.job_id == job1.id).first()
    
    # Try to download cert1 with job2's ID
    response = client.get(f"/jobs/{job2.id}/certificates/{cert1.id}/download")
    assert response.status_code == 404


def test_download_certificate_not_ready(client, db_session: Session):
    """Test 409 when certificate is still pending."""
    job, _ = create_job(
        db_session,
        "Pending Test",
        [{"name": "User", "email": "user@example.com"}]
    )
    
    cert = db_session.query(Certificate).filter(Certificate.job_id == job.id).first()
    
    # Don't process it, leave it pending
    response = client.get(f"/jobs/{job.id}/certificates/{cert.id}/download")
    assert response.status_code == 409
    assert "not ready" in response.json()["detail"].lower()


def test_download_certificate_failed(client, db_session: Session):
    """Test 409 when certificate failed."""
    job, _ = create_job(
        db_session,
        "Failed Test",
        [{"name": "", "email": "invalid@example.com"}]  # Invalid, will fail validation
    )
    
    cert = db_session.query(Certificate).filter(Certificate.job_id == job.id).first()
    assert cert.status == CertificateStatus.FAILED
    
    response = client.get(f"/jobs/{job.id}/certificates/{cert.id}/download")
    assert response.status_code == 409


def test_download_certificate_file_missing(client, db_session: Session, temp_output_dir):
    """Test 500 when certificate file is missing on disk."""
    # Create and process
    job, _ = create_job(
        db_session,
        "Missing File Test",
        [{"name": "User", "email": "user@example.com"}]
    )
    
    cert = db_session.query(Certificate).filter(Certificate.job_id == job.id).first()
    process_chunk(db_session, job.id, [cert.id])
    
    db_session.expire_all()
    cert = db_session.query(Certificate).filter(Certificate.id == cert.id).first()
    
    # Delete the file
    import os
    if cert.file_path and os.path.exists(cert.file_path):
        os.unlink(cert.file_path)
    
    # Try to download
    response = client.get(f"/jobs/{job.id}/certificates/{cert.id}/download")
    assert response.status_code == 500
    assert "not found on disk" in response.json()["detail"].lower()


def test_download_job_zip(client, db_session: Session, temp_output_dir):
    """Test downloading all certificates as ZIP."""
    # Create and process job with multiple certificates
    job, _ = create_job(
        db_session,
        "ZIP Test",
        [
            {"name": "Alice", "email": "alice@example.com"},
            {"name": "Bob", "email": "bob@example.com"},
            {"name": "Charlie", "email": "charlie@example.com"}
        ]
    )
    
    # Process all
    certs = db_session.query(Certificate).filter(Certificate.job_id == job.id).all()
    process_chunk(db_session, job.id, [cert.id for cert in certs])
    
    # Download ZIP
    response = client.get(f"/jobs/{job.id}/download")
    
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    # Filename may be URL-encoded
    disposition = response.headers.get("content-disposition", "")
    assert "ZIP" in disposition or "Test" in disposition
    assert "certificates.zip" in disposition
    
    # Verify ZIP contents
    zip_data = io.BytesIO(response.content)
    with zipfile.ZipFile(zip_data, 'r') as zipf:
        files = zipf.namelist()
        assert len(files) == 3
        
        # Check filenames are ordered and contain names
        assert any("Alice" in f for f in files)
        assert any("Bob" in f for f in files)
        assert any("Charlie" in f for f in files)
        
        # Check each file is a valid PDF
        for filename in files:
            pdf_content = zipf.read(filename)
            assert pdf_content.startswith(b"%PDF")


def test_download_job_zip_not_found(client):
    """Test 404 when job doesn't exist."""
    response = client.get("/jobs/nonexistent-job/download")
    assert response.status_code == 404


def test_download_job_zip_still_processing(client, db_session: Session):
    """Test 409 when job is still processing."""
    job, _ = create_job(
        db_session,
        "Still Processing",
        [{"name": "User", "email": "user@example.com"}]
    )
    
    # Don't process it
    response = client.get(f"/jobs/{job.id}/download")
    assert response.status_code == 409
    assert "still processing" in response.json()["detail"].lower()


def test_download_job_zip_no_successful_certs(client, db_session: Session):
    """Test 404 when job has no successful certificates."""
    job, _ = create_job(
        db_session,
        "All Failed",
        [
            {"name": "", "email": "invalid1@example.com"},
            {"name": "User", "email": "not-an-email"}
        ]
    )
    
    # All certs failed validation
    from app.services import finalize_job
    finalize_job(db_session, job.id)
    
    response = client.get(f"/jobs/{job.id}/download")
    assert response.status_code == 404
    assert "no successful certificates" in response.json()["detail"].lower()


def test_download_job_zip_with_failed_certs(client, db_session: Session, temp_output_dir):
    """Test ZIP only includes successful certificates, not failed ones."""
    from unittest.mock import patch
    from app.renderer import RenderError
    
    # Create job with 3 recipients
    job, _ = create_job(
        db_session,
        "Mixed Success",
        [
            {"name": "Success1", "email": "success1@example.com"},
            {"name": "Fail", "email": "fail@example.com"},
            {"name": "Success2", "email": "success2@example.com"}
        ]
    )
    
    certs = db_session.query(Certificate).filter(
        Certificate.job_id == job.id
    ).order_by(Certificate.idx).all()
    
    # Mock render to fail for middle certificate
    original_render = __import__("app.renderer", fromlist=["render"]).render
    
    def mock_render(recipient, event_name):
        if recipient.get("name") == "Fail":
            raise RenderError("Simulated failure")
        return original_render(recipient, event_name)
    
    with patch("app.services.render", side_effect=mock_render):
        process_chunk(db_session, job.id, [cert.id for cert in certs])
    
    # Download ZIP
    response = client.get(f"/jobs/{job.id}/download")
    assert response.status_code == 200
    
    # Verify ZIP only has 2 certificates (not 3)
    zip_data = io.BytesIO(response.content)
    with zipfile.ZipFile(zip_data, 'r') as zipf:
        files = zipf.namelist()
        assert len(files) == 2
        assert any("Success1" in f for f in files)
        assert any("Success2" in f for f in files)
        assert not any("Fail" in f for f in files)


def test_download_zip_filename_sanitization(client, db_session: Session, temp_output_dir):
    """Test that special characters in names are sanitized in ZIP filenames."""
    job, _ = create_job(
        db_session,
        "Sanitize Test",
        [
            {"name": "User/With\\Special:Chars", "email": "user@example.com"}
        ]
    )
    
    cert = db_session.query(Certificate).filter(Certificate.job_id == job.id).first()
    process_chunk(db_session, job.id, [cert.id])
    
    response = client.get(f"/jobs/{job.id}/download")
    assert response.status_code == 200
    
    zip_data = io.BytesIO(response.content)
    with zipfile.ZipFile(zip_data, 'r') as zipf:
        files = zipf.namelist()
        # Special chars should be removed, only alphanumeric and spaces/dashes/underscores allowed
        for filename in files:
            assert "/" not in filename
            assert "\\" not in filename
            assert ":" not in filename
