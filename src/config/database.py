"""Database configuration."""

import os
from dotenv import load_dotenv

load_dotenv()


class DatabaseConfig:
    """Database configuration settings."""
    
    # Connection details - can be overridden via environment variables
    HOST = os.getenv("DB_HOST", "ai-financial-assistant-bollette.e.aivencloud.com")
    PORT = int(os.getenv("DB_PORT", "22782"))
    DATABASE = os.getenv("DB_NAME", "defaultdb")
    USER = os.getenv("DB_USER", "avnadmin")
    PASSWORD = os.getenv("DB_PASSWORD", "")
    SSL_MODE = os.getenv("DB_SSL_MODE", "require")
    
    @classmethod
    def get_database_url(cls) -> str:
        """Get the database URL for SQLAlchemy.
        
        Returns:
            Database connection URL
        """
        return (
            f"postgresql://{cls.USER}:{cls.PASSWORD}@"
            f"{cls.HOST}:{cls.PORT}/{cls.DATABASE}?sslmode={cls.SSL_MODE}"
        )
    
    @classmethod
    def is_development(cls) -> bool:
        """Check if the application is running in development mode.
        
        Returns:
            True if in development mode, False otherwise
        """
        env = os.getenv("ENVIRONMENT", "development").lower()
        return env == "development"
