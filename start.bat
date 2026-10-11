@echo off
REM Start script for development (Windows)

echo Starting Bulk Certificate Generator...

REM Check if virtual environment exists
if not exist "venv\" (
    echo Virtual environment not found. Run: python -m venv venv
    exit /b 1
)

REM Check if Redis is running
redis-cli ping >nul 2>&1
if errorlevel 1 (
    echo Redis is not running. Start it with: docker-compose up -d redis
    exit /b 1
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Create output directory
if not exist "output\" mkdir output

echo Starting FastAPI application on http://localhost:8000
echo API docs available at http://localhost:8000/docs
echo.

REM Start services in separate windows
start "FastAPI" cmd /k "venv\Scripts\activate.bat && uvicorn app.main:app --reload --port 8000"
start "Celery Worker" cmd /k "venv\Scripts\activate.bat && celery -A app.tasks worker --loglevel=info --pool=solo"

echo.
echo All services started!
echo   API: http://localhost:8000
echo   Docs: http://localhost:8000/docs
echo.
echo Close the command windows to stop the services.
