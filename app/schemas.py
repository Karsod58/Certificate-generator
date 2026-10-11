"""Pydantic models for request/response validation."""
from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, field_validator
from app.config import settings


class RecipientModel(BaseModel):
    """
    Pydantic model for per-recipient validation.
    
    Applied manually per item, NOT in the request schema.
    Invalid recipients become failed certificate rows.
    """
    name: str = Field(..., min_length=1, max_length=settings.max_recipient_name_length)
    email: EmailStr = Field(..., max_length=settings.max_recipient_email_length)
    
    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be empty or whitespace only")
        return v.strip()
    
    model_config = {"extra": "allow"}  # Allow additional fields


class JobCreateRequest(BaseModel):
    """Request schema for POST /jobs."""
    event_name: str = Field(..., min_length=1, max_length=settings.max_event_name_length)
    recipients: List[Dict[str, Any]] = Field(..., min_length=1, max_length=settings.max_recipients)
    
    @field_validator("event_name")
    @classmethod
    def event_name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Event name cannot be empty or whitespace only")
        return v.strip()


class JobCreateResponse(BaseModel):
    """Response for successful job creation."""
    job_id: str
    status: str
    total: int


class CertificateStatusCounts(BaseModel):
    """Certificate counts by status."""
    pending: int = 0
    processing: int = 0
    success: int = 0
    failed: int = 0


class FailureDetail(BaseModel):
    """Details about a failed certificate."""
    idx: int
    name: Optional[str] = None
    error: Optional[str] = None


class JobStatusResponse(BaseModel):
    """Response for GET /jobs/{job_id}."""
    job_id: str
    event_name: str
    status: str
    total: int
    counts: CertificateStatusCounts
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    failures: List[FailureDetail]
    has_more_failures: bool = False
    
    model_config = {"from_attributes": True}
