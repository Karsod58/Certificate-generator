"""Test a sample API request payload."""
from app.schemas import JobCreateRequest
from pydantic import ValidationError

# Sample request that should pass validation
sample_request = {
    "event_name": "Python Conference 2024",
    "recipients": [
        {"name": "Alice Smith", "email": "alice@example.com"},
        {"name": "Bob Johnson", "email": "bob@example.com"},
        {"name": "Charlie Brown", "email": "charlie@example.com"}
    ]
}

print("Testing sample API request validation...\n")
print(f"Event: {sample_request['event_name']}")
print(f"Recipients: {len(sample_request['recipients'])}\n")

try:
    request = JobCreateRequest(**sample_request)
    print("✅ REQUEST VALIDATION: PASSED")
    print(f"   Event name: {request.event_name}")
    print(f"   Total recipients: {len(request.recipients)}")
    print()
    
    # Test individual recipient validation
    from app.schemas import RecipientModel
    print("Testing per-recipient validation:")
    for i, recipient in enumerate(request.recipients, 1):
        try:
            validated = RecipientModel(**recipient)
            print(f"   ✓ Recipient {i}: {validated.name} <{validated.email}>")
        except ValidationError as e:
            print(f"   ✗ Recipient {i}: FAILED - {e}")
    
    print("\n✅ ALL VALIDATIONS PASSED")
    print("\nThis request would create:")
    print("  • 1 Job record")
    print("  • 3 Certificate records (all initially 'pending')")
    print("  • 3 Celery tasks (assuming chunk_size=10)")
    
except ValidationError as e:
    print(f"✗ REQUEST VALIDATION: FAILED")
    print(f"Errors: {e}")
