"""Database initialization and session management."""

import sys
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import OperationalError, ProgrammingError

from src.config.database import DatabaseConfig
from src.database.models import Base

# Global variables
engine = None
SessionLocal = None


def init_database() -> None:
    """Initialize the database connection and create tables if in development mode.
    
    This function:
    - Creates the database engine
    - Creates a session factory
    - In development mode: automatically creates all tables
    """
    global engine, SessionLocal
    
    try:
        database_url = DatabaseConfig.get_database_url()
        
        # Check if password is set
        if not DatabaseConfig.PASSWORD:
            print("WARNING: DB_PASSWORD not set in environment variables. Database connection will fail.")
            print("Please set DB_PASSWORD in your .env file")
        
        # Create engine
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            echo=False  # Set to True for SQL query logging
        )
        
        # Create session factory
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        
        # Test connection
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            print("✓ Database connection successful")
        
        # In development mode, create tables automatically
        if DatabaseConfig.is_development():
            print("Running in DEVELOPMENT mode - Creating tables if they don't exist...")
            Base.metadata.create_all(bind=engine)
            print("✓ Database tables created/verified")
        else:
            print("Running in PRODUCTION mode - Skipping automatic table creation")
            
    except OperationalError as e:
        print(f"ERROR: Failed to connect to database: {e}")
        print("Please check your database configuration and ensure DB_PASSWORD is set correctly")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Database initialization failed: {e}")
        sys.exit(1)


def get_db() -> Generator[Session, None, None]:
    """Get a database session.
    
    This is a generator function that yields a database session
    and ensures it's properly closed after use.
    
    Yields:
        Database session
    """
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """Get a database session (non-generator version).
    
    Note: The caller is responsible for closing the session.
    
    Returns:
        Database session
    """
    if SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    
    return SessionLocal()


def close_database() -> None:
    """Close database connections and dispose of the engine."""
    global engine
    
    if engine:
        engine.dispose()
        print("✓ Database connections closed")
