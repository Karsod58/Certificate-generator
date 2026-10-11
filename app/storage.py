"""File storage helpers for certificate PDFs."""
import os
import tempfile
from pathlib import Path
from app.config import settings


def get_certificate_path(job_id: str, cert_id: str) -> str:
    """
    Get the file path for a certificate PDF.
    
    Uses deterministic paths: {OUTPUT_DIR}/{job_id}/{cert_id}.pdf
    
    Args:
        job_id: Job ID
        cert_id: Certificate ID
        
    Returns:
        Absolute path string
    """
    job_dir = Path(settings.output_dir) / job_id
    return str(job_dir / f"{cert_id}.pdf")


def write_certificate(job_id: str, cert_id: str, pdf_bytes: bytes) -> str:
    """
    Write certificate PDF to disk atomically.
    
    Uses temp file + os.replace for atomicity (no partial writes visible).
    Creates parent directories if needed.
    
    Args:
        job_id: Job ID
        cert_id: Certificate ID
        pdf_bytes: PDF content
        
    Returns:
        File path where PDF was written
        
    Raises:
        OSError: If write fails
    """
    # Ensure job directory exists
    job_dir = Path(settings.output_dir) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    
    # Target path
    target_path = job_dir / f"{cert_id}.pdf"
    
    # Write to temp file in same directory (same filesystem for atomic rename)
    fd, temp_path = tempfile.mkstemp(dir=job_dir, suffix=".pdf.tmp")
    try:
        os.write(fd, pdf_bytes)
        os.close(fd)
        
        # Atomic replace
        # On Windows, target must not exist for os.replace to work
        # But we never write to same cert_id twice (UUIDs), so this is safe
        os.replace(temp_path, target_path)
        
        return str(target_path)
        
    except Exception:
        os.close(fd)
        # Clean up temp file on failure
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        raise


def read_certificate(file_path: str) -> bytes:
    """
    Read certificate PDF from disk.
    
    Args:
        file_path: Path to PDF file
        
    Returns:
        PDF bytes
        
    Raises:
        FileNotFoundError: If file doesn't exist
        OSError: If read fails
    """
    with open(file_path, "rb") as f:
        return f.read()


def certificate_exists(file_path: str) -> bool:
    """Check if certificate file exists."""
    return os.path.isfile(file_path)
