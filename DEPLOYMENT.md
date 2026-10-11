# Deployment Guide

This guide covers deploying the Bulk Certificate Generator to production.

## Pre-Deployment Checklist

- [ ] Switch from SQLite to PostgreSQL
- [ ] Configure S3 or object storage for PDFs
- [ ] Set up authentication (JWT, OAuth2)
- [ ] Enable rate limiting
- [ ] Configure structured logging
- [ ] Set up monitoring (Sentry, CloudWatch)
- [ ] Configure backup strategy
- [ ] Load test the application
- [ ] Set up CI/CD pipeline
- [ ] Configure HTTPS/SSL

## Database Migration (SQLite → PostgreSQL)

### 1. Install PostgreSQL driver
```bash
pip install psycopg2-binary
```

### 2. Update environment variable
```bash
DATABASE_URL=postgresql://user:password@localhost:5432/certificates
```

### 3. Create database and tables
```bash
# Create database
createdb certificates

# Initialize schema
python init_db.py
```

## Docker Deployment

### Build and run with Docker Compose

```bash
# Build images
docker-compose build

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Scale workers
docker-compose up -d --scale worker=4
```

### Production docker-compose.yml

```yaml
version: '3.8'

services:
  db:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: certificates
      POSTGRES_USER: certuser
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U certuser"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  api:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://certuser:${DB_PASSWORD}@db:5432/certificates
      - REDIS_URL=redis://redis:6379/0
      - OUTPUT_DIR=/app/output
    volumes:
      - ./output:/app/output
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  worker:
    build: .
    command: celery -A app.tasks worker --loglevel=info --concurrency=4
    environment:
      - DATABASE_URL=postgresql://certuser:${DB_PASSWORD}@db:5432/certificates
      - REDIS_URL=redis://redis:6379/0
      - OUTPUT_DIR=/app/output
    volumes:
      - ./output:/app/output
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
```

## AWS Deployment

### Option 1: ECS (Elastic Container Service)

1. **Push images to ECR**
   ```bash
   aws ecr create-repository --repository-name cert-api
   aws ecr create-repository --repository-name cert-worker
   
   docker tag cert-api:latest ${AWS_ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/cert-api:latest
   docker push ${AWS_ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/cert-api:latest
   ```

2. **Create ECS task definitions** for API and worker

3. **Set up services**
   - API: ECS Service with ALB
   - Worker: ECS Service (no load balancer)
   - RDS PostgreSQL
   - ElastiCache Redis
   - S3 bucket for PDFs

### Option 2: Lambda + Step Functions

For serverless deployment:
- API Gateway → Lambda (FastAPI)
- Step Functions for orchestration
- SQS instead of Celery
- Aurora Serverless
- S3 for storage

## Environment Variables (Production)

```bash
# Database (use RDS endpoint)
DATABASE_URL=postgresql://user:pass@prod-db.region.rds.amazonaws.com:5432/certificates

# Redis (use ElastiCache endpoint)
REDIS_URL=redis://prod-redis.cache.amazonaws.com:6379/0

# Storage (use S3)
OUTPUT_DIR=s3://my-certificates-bucket/pdfs/

# Limits (tune for production)
MAX_RECIPIENTS=10000
CHUNK_SIZE=50
STALE_MINUTES=30

# Security
SECRET_KEY=<long-random-string>
ALLOWED_HOSTS=api.example.com

# Monitoring
SENTRY_DSN=https://...@sentry.io/...
LOG_LEVEL=INFO

# Rate limiting (enable in production)
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=100
RATE_LIMIT_PERIOD=60
```

## Monitoring

### Celery Monitoring with Flower

```bash
# Install Flower
pip install flower

# Run Flower
celery -A app.tasks flower --port=5555

# Access at http://localhost:5555
```

### Application Monitoring

```python
# Add to app/main.py
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

sentry_sdk.init(
    dsn=settings.sentry_dsn,
    integrations=[FastApiIntegration()],
    traces_sample_rate=0.1,
)
```

### Health Checks

Add to load balancer:
- **Health check path**: `/health`
- **Interval**: 30 seconds
- **Timeout**: 5 seconds
- **Unhealthy threshold**: 2
- **Healthy threshold**: 2

## Scaling

### Horizontal Scaling

**API Servers**:
```bash
# Add more API instances
docker-compose up -d --scale api=3
```

**Workers**:
```bash
# Add more workers
docker-compose up -d --scale worker=8
```

### Vertical Scaling

**Worker concurrency**:
```bash
celery -A app.tasks worker --concurrency=16
```

**API workers** (uvicorn):
```bash
uvicorn app.main:app --workers 8
```

### Auto-scaling (AWS)

Set up ECS auto-scaling based on:
- **API**: CPU utilization > 70%
- **Worker**: SQS queue depth > 100

## Backup Strategy

### Database Backups

```bash
# Automated daily backups (RDS)
# Enable automated backups in RDS console
# Retention: 7-30 days

# Manual backup
pg_dump -h prod-db.rds.amazonaws.com -U certuser certificates > backup.sql
```

### File Backups (S3)

- Enable S3 versioning
- Configure lifecycle rules
- Set up cross-region replication

## Security

### SSL/TLS

Use a reverse proxy (Nginx, ALB) for HTTPS:

```nginx
server {
    listen 443 ssl;
    server_name api.example.com;
    
    ssl_certificate /etc/ssl/certs/cert.pem;
    ssl_certificate_key /etc/ssl/private/key.pem;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Environment Secrets

**Never commit secrets!** Use:
- AWS Secrets Manager
- HashiCorp Vault
- Environment variables from CI/CD

## Troubleshooting Production Issues

### High CPU on Workers

```bash
# Check Celery stats
celery -A app.tasks inspect stats

# Reduce concurrency
celery -A app.tasks worker --concurrency=2
```

### Database Connection Pool Exhausted

```python
# Increase pool size in database.py
engine = create_engine(
    settings.database_url,
    pool_size=20,
    max_overflow=40
)
```

### Redis Memory Issues

```bash
# Check Redis memory
redis-cli INFO memory

# Set maxmemory policy
redis-cli CONFIG SET maxmemory 2gb
redis-cli CONFIG SET maxmemory-policy allkeys-lru
```

## Rollback Procedure

```bash
# 1. Stop services
docker-compose down

# 2. Restore database from backup
psql certificates < backup.sql

# 3. Deploy previous version
docker-compose up -d

# 4. Verify
curl http://localhost:8000/health
```

## Support

For production support:
- Check logs: `docker-compose logs -f`
- Monitor Sentry for errors
- Review Celery Flower dashboard
- Check CloudWatch metrics (AWS)
