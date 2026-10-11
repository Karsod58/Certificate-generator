# Bulk Certificate Generator

Asynchronous certificate generation API built with FastAPI, Celery, and ReportLab. Generates personalized PDF certificates for events with configurable batch processing and robust error handling.

## Architecture

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │ HTTP
       ▼
┌─────────────────────────────────────────┐
│         FastAPI Application              │
│  ┌───────────────────────────────────┐  │
│  │  POST /jobs                       │  │
│  │  ├─ Request validation (strict)   │  │
│  │  ├─ Per-recipient validation      │  │
│  │  ├─ DB: Create job + certificates │  │
│  │  └─ Enqueue Celery tasks          │  │
│  │                                    │  │
│  │  GET /jobs/{id}                   │  │
│  │  └─ Status + counts (derived)     │  │
│  │                                    │  │
│  │  GET /jobs/{id}/download          │  │
│  │  └─ ZIP all successful certs      │  │
│  │                                    │  │
│  │  GET /jobs/{id}/certificates/     │  │
│  │      /{cert_id}/download          │  │
│  │  └─ Stream single PDF             │  │
│  └───────────────────────────────────┘  │
└────────────┬────────────────────────────┘
             │
             ▼
      ┌──────────┐         ┌──────────────┐
      │  SQLite  │         │    Redis     │
      │  (WAL)   │         │  (Broker)    │
      └──────────┘         └──────┬───────┘
                                  │
                                  ▼
                    ┌──────────────────────────┐
                    │    Celery Workers        │
                    │  ┌────────────────────┐  │
                    │  │  Task: Process     │  │
                    │  │  Certificate Chunk │  │
                    │  │  ├─ Atomic claim   │  │
                    │  │  ├─ Render PDF     │  │
                    │  │  ├─ Write to disk  │  │
                    │  │  └─ Finalize job   │  │
                    │  └────────────────────┘  │
                    └──────────────────────────┘
                                  │
                                  ▼
                           ┌──────────────┐
                           │ ./output/    │
                           │  {job_id}/   │
                           │   {cert}.pdf │
                           └──────────────┘
```

## Features

- ✅ **Async Processing**: Non-blocking job submission with Celery + Redis
- ✅ **Two-Phase Validation**: Strict request-level + lenient per-recipient validation
- ✅ **Error Isolation**: Individual certificate failures don't abort the job
- ✅ **Atomic Operations**: Claim pattern prevents duplicate processing
- ✅ **Startup Recovery**: Automatically detects and re-processes stuck jobs
- ✅ **Derived Status**: Job status computed from certificate counts (no counter races)
- ✅ **Chunked Processing**: Configurable batch size for scalability
- ✅ **Transient Error Retry**: Automatic retry for temporary failures (max 3 attempts)
- ✅ **PDF Streaming**: Memory-efficient single and batch downloads
- ✅ **Production Ready**: SQLite WAL mode, PostgreSQL support via config

## Quick Start

### Prerequisites

- Python 3.11+
- Redis (for Celery broker)

### Installation

```bash
# Clone repository
git clone <repo-url>
cd Certificate_generator

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env with your settings
```

### Running with Docker (Recommended)

```bash
# Start Redis
docker-compose up -d redis

# Run FastAPI application
uvicorn app.main:app --reload --port 8000

# In another terminal, start Celery worker
celery -A app.tasks worker --loglevel=info --pool=prefork --concurrency=4
```

### Running without Docker

```bash
# Start Redis (install locally or use Docker)
redis-server

# Run FastAPI application
uvicorn app.main:app --reload --port 8000

# In another terminal, start Celery worker
celery -A app.tasks worker --loglevel=info
```

## API Reference

### POST /jobs

Create a new certificate generation job.

**Request:**
```json
{
  "event_name": "Python Workshop 2024",
  "recipients": [
    {"name": "Alice Smith", "email": "alice@example.com"},
    {"name": "Bob Jones", "email": "bob@example.com"}
  ]
}
```

**Response:** `202 Accepted`
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "total": 2
}
```

**Validation:**
- **Request-level (strict)**: Returns 422 if:
  - `event_name` missing or empty
  - `recipients` not a list
  - `recipients` empty or > MAX_RECIPIENTS (default 1000)
  
- **Per-recipient (lenient)**: Invalid recipients inserted as failed:
  - `name` must be non-empty string ≤ 200 chars
  - `email` must be valid email format ≤ 320 chars
  - Additional fields allowed but ignored

### GET /jobs/{job_id}

Get job status and certificate counts.

**Response:** `200 OK`
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "event_name": "Python Workshop 2024",
  "status": "completed",
  "total": 100,
  "counts": {
    "pending": 0,
    "processing": 0,
    "success": 98,
    "failed": 2
  },
  "created_at": "2024-01-15T10:30:00Z",
  "started_at": "2024-01-15T10:30:01Z",
  "finished_at": "2024-01-15T10:31:45Z",
  "failures": [
    {
      "idx": 5,
      "name": "Invalid User",
      "error": "Validation failed: email: value is not a valid email address"
    }
  ],
  "has_more_failures": false
}
```

**Job Status (derived from certificates):**
- `pending`: No certificates claimed yet
- `processing`: Has pending or processing certificates
- `completed`: All successful (failed = 0)
- `completed_with_errors`: Mix of success and failed (0 < failed < total)
- `failed`: All failed (failed = total)

**Query Parameters:**
- `failures_offset`: Pagination offset for failures list (default: 0)
- `failures_limit`: Max failures to return (default: 100)

### GET /jobs/{job_id}/certificates/{cert_id}/download

Download a single certificate PDF.

**Response:** `200 OK` (streams PDF)
- Content-Type: `application/pdf`
- Content-Disposition: `attachment; filename="Alice_Smith_0.pdf"`

**Errors:**
- `404`: Certificate not found or doesn't belong to job
- `409`: Certificate not ready (still pending/processing/failed)
- `500`: Certificate file missing on disk

### GET /jobs/{job_id}/download

Download all successful certificates as a ZIP file.

**Response:** `200 OK` (streams ZIP)
- Content-Type: `application/zip`
- Content-Disposition: `attachment; filename="Python_Workshop_2024_certificates.zip"`

**ZIP Contents:**
- Filenames: `{idx:04d}_{name}.pdf` (e.g., `0000_Alice_Smith.pdf`)
- Only successful certificates included
- Ordered by recipient index

**Errors:**
- `404`: Job not found or no successful certificates
- `409`: Job still processing

### GET /health

Health check endpoint.

**Response:** `200 OK`
```json
{"status": "ok"}
```

## Configuration

All configuration via environment variables (see `.env.example`):

### Database
```bash
DATABASE_URL=sqlite:///./certificates.db
# For production: postgresql://user:pass@host:5432/dbname
```

### Redis
```bash
REDIS_URL=redis://localhost:6379/0
```

### Storage
```bash
OUTPUT_DIR=./output
```

### Limits
```bash
MAX_RECIPIENTS=1000              # Max recipients per job
CHUNK_SIZE=10                    # Certificates per Celery task
MAX_EVENT_NAME_LENGTH=200
MAX_RECIPIENT_NAME_LENGTH=200
MAX_RECIPIENT_EMAIL_LENGTH=320
```

### Recovery
```bash
STALE_MINUTES=30  # Certificates stuck in 'processing' > this are reset
```

### Optional Rate Limiting
```bash
RATE_LIMIT_ENABLED=false
RATE_LIMIT_REQUESTS=10
RATE_LIMIT_PERIOD=60  # seconds
```

## Development

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_jobs.py -v

# Run with coverage
pytest tests/ --cov=app --cov-report=html
```

### Project Structure

```
Certificate_generator/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app, routes, startup hooks
│   ├── config.py        # Environment-based settings
│   ├── database.py      # SQLAlchemy engine, session, SQLite pragmas
│   ├── models.py        # Job, Certificate ORM models
│   ├── schemas.py       # Pydantic request/response models
│   ├── services.py      # Business logic (create_job, process_chunk, etc.)
│   ├── renderer.py      # Pure PDF generation function
│   ├── storage.py       # File path handling and I/O
│   └── tasks.py         # Celery app and task definitions
├── tests/
│   ├── conftest.py      # Pytest fixtures
│   ├── test_jobs.py     # Job creation and status tests
│   ├── test_processing.py    # Processing logic tests
│   ├── test_celery_integration.py  # Celery wiring tests
│   └── test_downloads.py    # Download endpoint tests
├── docker-compose.yml   # Redis + optional API/worker containers
├── Dockerfile           # Container image for API/worker
├── requirements.txt     # Python dependencies (pinned)
├── .env.example         # Environment template
└── README.md           # This file
```

## Design Decisions

### 1. Why Async (FastAPI + Celery)?

**Problem**: PDF generation is CPU-intensive. Synchronous processing would block the API.

**Solution**: FastAPI handles HTTP fast, Celery workers handle heavy processing. Jobs return `202 Accepted` immediately.

**Trade-off**: Added complexity (Redis, workers) but necessary for production scale.

### 2. Why Celery + Redis?

**Celery**: Industry-standard task queue with mature retry logic, monitoring, and failure handling.

**Redis**: Fast, simple broker. For production, consider RabbitMQ for guaranteed delivery.

**Alternative considered**: Background threads (rejected - doesn't scale across processes).

### 3. Why Database as Source of Truth?

**Pattern**: All state in DB, Celery tasks are stateless workers.

**Benefits**:
- Survive worker crashes (acks_late=True)
- Query job status directly from DB
- Startup recovery: re-enqueue stuck jobs

**Trade-off**: More DB writes, but worth it for reliability.

### 4. Why Derived Job Status (No Counters)?

**Anti-pattern**: Storing `success_count`, `failed_count` leads to race conditions with concurrent workers.

**Solution**: Compute counts with `GROUP BY` on read. Single source of truth (certificate rows).

**Performance**: GROUP BY is fast enough for typical job sizes (<10K certificates).

### 5. Two-Phase Validation: Why?

**Request-level (strict)**: Fail fast for malformed requests (422).

**Per-recipient (lenient)**: Don't reject entire batch for one bad email.

**Reasoning**: Better UX. Job succeeds with clear per-recipient errors.

### 6. Enqueue After Commit: Why Critical?

**Dual-write problem**: If we enqueue before commit, tasks might start before rows exist.

**Solution**: Commit transaction FIRST, THEN enqueue tasks.

**Mitigation**: Startup recovery handles any missed enqueues (e.g., crash between commit and enqueue).

### 7. Chunked Tasks: Why Not One Task Per Certificate?

**Problem**: 10,000 certificates = 10,000 task messages (Redis overhead).

**Solution**: Chunk into batches (default 10). 10,000 certs = 1,000 tasks.

**Trade-off**: Slightly coarser error handling, but much better throughput.

### 8. Atomic Claim Pattern: How It Works

```sql
UPDATE certificates 
SET status='processing', attempts=attempts+1 
WHERE id=:id AND status='pending'
```

**Why**: If `rowcount == 0`, another worker already claimed it. Skip silently.

**Benefit**: Duplicate task delivery (rare) doesn't cause duplicate work.

## Known Limitations

### 1. Dual-Write Window

**Issue**: Between DB commit and Celery enqueue, app could crash.

**Mitigation**: Startup recovery re-enqueues any pending certificates.

**Future**: Transactional outbox pattern.

### 2. No Auth / Ownership

**Issue**: Job IDs are UUIDs (unguessable) but no user ownership model.

**Production**: Add authentication (JWT, OAuth) and user foreign keys.

### 3. Local Disk Storage

**Issue**: PDFs on local filesystem don't work for multi-server deployments.

**Production**: Use S3, Azure Blob, or similar. Update `storage.py` to abstract this.

### 4. No Idempotency Key on POST /jobs

**Issue**: Retry of POST creates duplicate jobs.

**Production**: Add `Idempotency-Key` header, store in Redis for 24h.

### 5. SQLite Write Contention

**Issue**: SQLite has single-writer limit. High concurrency = lock timeouts.

**Production**: Use PostgreSQL (just change `DATABASE_URL`).

### 6. Font Limitation

**Current**: ReportLab uses built-in Latin fonts only.

**Unicode**: Configure custom TTF font in `renderer.py` for non-Latin scripts.

## Production Checklist

- [ ] Replace SQLite with PostgreSQL
- [ ] Move PDFs to S3 or object storage
- [ ] Add authentication and user ownership
- [ ] Implement idempotency keys
- [ ] Add structured logging (JSON logs to stdout)
- [ ] Configure Celery monitoring (Flower, CloudWatch, etc.)
- [ ] Set up alerting (Sentry, PagerDuty)
- [ ] Enable CORS if needed
- [ ] Add rate limiting
- [ ] Configure backup strategy for DB
- [ ] Set up CI/CD pipeline
- [ ] Load test (Locust, k6)
- [ ] Configure HTTPS (reverse proxy or API Gateway)
- [ ] Set resource limits (Celery concurrency, memory)

## Example Usage

### Submit a job
```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "event_name": "Python Conference 2024",
    "recipients": [
      {"name": "Alice Smith", "email": "alice@example.com"},
      {"name": "Bob Jones", "email": "bob@example.com"}
    ]
  }'

# Response:
# {
#   "job_id": "550e8400-e29b-41d4-a716-446655440000",
#   "status": "pending",
#   "total": 2
# }
```

### Check status
```bash
curl http://localhost:8000/jobs/550e8400-e29b-41d4-a716-446655440000
```

### Download single certificate
```bash
curl -O http://localhost:8000/jobs/550e8400-e29b-41d4-a716-446655440000/certificates/{cert_id}/download
```

### Download all certificates (ZIP)
```bash
curl -O http://localhost:8000/jobs/550e8400-e29b-41d4-a716-446655440000/download
```

## Troubleshooting

### Worker not processing jobs

```bash
# Check Redis connection
redis-cli ping

# Check Celery worker logs
celery -A app.tasks worker --loglevel=debug

# Verify tasks are in queue
redis-cli -c "KEYS celery*"
```

### Database locked errors (SQLite)

- Reduce Celery concurrency: `--concurrency=2`
- Or switch to PostgreSQL

### Stuck jobs after worker crash

- Restart app (automatic recovery runs)
- Or manually: `curl http://localhost:8000/health` (triggers lifespan recovery)

## License

MIT

## Contributing

1. Fork the repository
2. Create a feature branch
3. Write tests for new functionality
4. Ensure all tests pass: `pytest tests/ -v`
5. Submit a pull request

---

Built with ❤️ using FastAPI, Celery, and ReportLab
