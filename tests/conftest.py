"""Pytest fixtures for testing."""
import os
import tempfile
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.config import settings


@pytest.fixture(scope="function")
def temp_db():
    """Create a temporary SQLite database for each test."""
    # Create temp database file
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    database_url = f"sqlite:///{db_path}"
    
    # Create engine and tables
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    
    # Create session factory
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    yield TestingSessionLocal, engine
    
    # Cleanup
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture(scope="function")
def db_session(temp_db):
    """Provide a database session for tests."""
    TestingSessionLocal, engine = temp_db
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(temp_db):
    """Provide a FastAPI test client with temp database."""
    TestingSessionLocal, engine = temp_db
    
    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app.dependency_overrides[get_db] = override_get_db
    
    with TestClient(app) as test_client:
        yield test_client
    
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def temp_output_dir():
    """Create a temporary output directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Override settings for tests
        original_output_dir = settings.output_dir
        settings.output_dir = tmpdir
        yield tmpdir
        settings.output_dir = original_output_dir
