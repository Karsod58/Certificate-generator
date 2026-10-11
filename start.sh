#!/bin/bash
# Start script for development

echo "🚀 Starting Bulk Certificate Generator..."

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Run: python -m venv venv"
    exit 1
fi

# Check if Redis is running
if ! redis-cli ping > /dev/null 2>&1; then
    echo "❌ Redis is not running. Start it with: docker-compose up -d redis"
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Create output directory
mkdir -p output

echo "✅ Starting FastAPI application on http://localhost:8000"
echo "📚 API docs available at http://localhost:8000/docs"
echo ""

# Start uvicorn in background
uvicorn app.main:app --reload --port 8000 &
API_PID=$!

echo "✅ Starting Celery worker..."
celery -A app.tasks worker --loglevel=info --pool=prefork --concurrency=4 &
WORKER_PID=$!

# Handle shutdown
trap "kill $API_PID $WORKER_PID 2>/dev/null" EXIT

echo ""
echo "🎉 All services started!"
echo "   API: http://localhost:8000"
echo "   Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all services..."

# Wait for processes
wait
