#!/usr/bin/env python3
"""Initialize the database schema."""
from app.database import engine, Base
from app.models import Job, Certificate


def main():
    """Create all tables."""
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("✓ Database initialized successfully")


if __name__ == "__main__":
    main()
