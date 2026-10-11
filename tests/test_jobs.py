"""Tests for job creation and status endpoints."""
import pytest
from sqlalchemy.orm import Session

from app.models import Job, Certificate, CertificateStatus, JobStatus


def test_create_job_valid_request(client):
    """Test job creation with valid recipients."""
    request_data = {
        "event_name": "Python Workshop 2024",
        "recipients": [
            {"name": "Alice Smith", "email": "alice@example.com"},
            {"name": "Bob Jones", "email": "bob@example.com"},
            {"name": "Charlie Brown", "email": "charlie@example.com"}
        ]
    }
    
    response = client.post("/jobs", json=request_data)
    
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data
    assert data["status"] == JobStatus.PENDING
    assert data["total"] == 3


def test_create_job_creates_database_rows(client, db_session: Session):
    """Test that job creation inserts job and certificate rows."""
    request_data = {
        "event_name": "Test Event",
        "recipients": [
            {"name": "Test User", "email": "test@example.com"}
        ]
    }
    
    response = client.post("/jobs", json=request_data)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    
    # Check job exists
    job = db_session.query(Job).filter(Job.id == job_id).first()
    assert job is not None
    assert job.event_name == "Test Event"
    assert job.total == 1
    assert job.status == JobStatus.PENDING
    
    # Check certificate exists
    certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).all()
    assert len(certs) == 1
    assert certs[0].status == CertificateStatus.PENDING
    assert certs[0].recipient["name"] == "Test User"
    assert certs[0].idx == 0


def test_create_job_empty_recipients_list(client):
    """Test that empty recipients list is rejected."""
    request_data = {
        "event_name": "Test Event",
        "recipients": []
    }
    
    response = client.post("/jobs", json=request_data)
    assert response.status_code == 422  # Validation error


def test_create_job_missing_event_name(client):
    """Test that missing event_name is rejected."""
    request_data = {
        "recipients": [{"name": "Test", "email": "test@example.com"}]
    }
    
    response = client.post("/jobs", json=request_data)
    assert response.status_code == 422


def test_create_job_too_many_recipients(client):
    """Test that exceeding MAX_RECIPIENTS is rejected."""
    from app.config import settings
    
    request_data = {
        "event_name": "Test Event",
        "recipients": [
            {"name": f"User{i}", "email": f"user{i}@example.com"}
            for i in range(settings.max_recipients + 1)
        ]
    }
    
    response = client.post("/jobs", json=request_data)
    assert response.status_code == 422


def test_create_job_mixed_valid_invalid_recipients(client, db_session: Session):
    """Test that invalid recipients become failed rows, valid ones pending."""
    request_data = {
        "event_name": "Mixed Recipients Test",
        "recipients": [
            {"name": "Valid User", "email": "valid@example.com"},
            {"name": "", "email": "invalid@example.com"},  # Empty name
            {"name": "Another Valid", "email": "another@example.com"},
            {"name": "No Email User", "email": "not-an-email"},  # Invalid email
            {"name": "Third Valid", "email": "third@example.com"}
        ]
    }
    
    response = client.post("/jobs", json=request_data)
    assert response.status_code == 202
    
    data = response.json()
    assert data["total"] == 5
    job_id = data["job_id"]
    
    # Check certificate statuses
    certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).order_by(Certificate.idx).all()
    assert len(certs) == 5
    
    # idx 0: valid
    assert certs[0].status == CertificateStatus.PENDING
    assert certs[0].error is None
    
    # idx 1: invalid (empty name)
    assert certs[1].status == CertificateStatus.FAILED
    assert "name" in certs[1].error.lower() or "empty" in certs[1].error.lower()
    
    # idx 2: valid
    assert certs[2].status == CertificateStatus.PENDING
    assert certs[2].error is None
    
    # idx 3: invalid (bad email)
    assert certs[3].status == CertificateStatus.FAILED
    assert "email" in certs[3].error.lower()
    
    # idx 4: valid
    assert certs[4].status == CertificateStatus.PENDING
    assert certs[4].error is None


def test_create_job_all_invalid_recipients(client, db_session: Session):
    """Test job creation when all recipients are invalid."""
    request_data = {
        "event_name": "All Invalid",
        "recipients": [
            {"name": "", "email": "bad1@example.com"},
            {"name": "User", "email": "not-an-email"}
        ]
    }
    
    response = client.post("/jobs", json=request_data)
    assert response.status_code == 202
    
    job_id = response.json()["job_id"]
    
    # All certificates should be failed
    certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).all()
    assert len(certs) == 2
    assert all(cert.status == CertificateStatus.FAILED for cert in certs)
    assert all(cert.error is not None for cert in certs)


def test_create_job_preserves_recipient_order(client, db_session: Session):
    """Test that recipient order is preserved in idx field."""
    request_data = {
        "event_name": "Order Test",
        "recipients": [
            {"name": "First", "email": "first@example.com"},
            {"name": "Second", "email": "second@example.com"},
            {"name": "Third", "email": "third@example.com"}
        ]
    }
    
    response = client.post("/jobs", json=request_data)
    job_id = response.json()["job_id"]
    
    certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).order_by(Certificate.idx).all()
    assert certs[0].recipient["name"] == "First"
    assert certs[0].idx == 0
    assert certs[1].recipient["name"] == "Second"
    assert certs[1].idx == 1
    assert certs[2].recipient["name"] == "Third"
    assert certs[2].idx == 2


def test_get_job_status_valid_job(client, db_session: Session):
    """Test getting status of an existing job."""
    # Create a job first
    request_data = {
        "event_name": "Status Test",
        "recipients": [
            {"name": "User1", "email": "user1@example.com"},
            {"name": "User2", "email": "user2@example.com"}
        ]
    }
    
    create_response = client.post("/jobs", json=request_data)
    job_id = create_response.json()["job_id"]
    
    # Get status
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    
    data = response.json()
    assert data["job_id"] == job_id
    assert data["event_name"] == "Status Test"
    assert data["status"] == JobStatus.PENDING
    assert data["total"] == 2
    assert data["counts"]["pending"] == 2
    assert data["counts"]["processing"] == 0
    assert data["counts"]["success"] == 0
    assert data["counts"]["failed"] == 0
    assert "created_at" in data
    assert data["started_at"] is None
    assert data["finished_at"] is None


def test_get_job_status_not_found(client):
    """Test 404 for non-existent job."""
    response = client.get("/jobs/nonexistent-job-id")
    assert response.status_code == 404


def test_get_job_status_with_failures(client, db_session: Session):
    """Test that failures list contains failed certificates."""
    request_data = {
        "event_name": "Failures Test",
        "recipients": [
            {"name": "Valid", "email": "valid@example.com"},
            {"name": "", "email": "invalid@example.com"}
        ]
    }
    
    create_response = client.post("/jobs", json=request_data)
    job_id = create_response.json()["job_id"]
    
    response = client.get(f"/jobs/{job_id}")
    data = response.json()
    
    assert data["counts"]["pending"] == 1
    assert data["counts"]["failed"] == 1
    assert len(data["failures"]) == 1
    assert data["failures"][0]["idx"] == 1
    assert data["failures"][0]["error"] is not None


def test_health_endpoint(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
